"""Actual HA/dnsmasq/relay tests against a loopback UDP nanoDNS stand-in.

The stand-in applies the supplied example rules. This does not execute a PS5
payload or claim that the console's nanoDNS build has been tested.
"""

import fnmatch
import http.client
import json
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_host import ADDON, host, recv_exact, unused_port


def question(packet):
    offset, labels = 12, []
    while packet[offset]:
        length = packet[offset]
        offset += 1
        labels.append(packet[offset:offset + length].decode("ascii").lower())
        offset += length
    offset += 1
    return ".".join(labels), struct.unpack_from("!H", packet, offset)[0], offset + 4


class StubHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        if len(packet) < 12 or packet[2] & 0x80:
            return
        name, qtype, end = question(packet)
        self.server.queries.append((name, qtype))
        if not self.server.available:
            return
        if self.server.loop_target:
            sock.sendto(packet, self.server.loop_target)
            return
        blocked = any(fnmatch.fnmatchcase(name, mask) for mask in self.server.rules)
        count = int(qtype == 1)
        response = packet[:2] + struct.pack("!HHHHH", 0x8180, 1, count, 0, 0) + packet[12:end]
        if count:
            response += struct.pack("!HHHLH", 0xC00C, 1, 1, 60, 4)
            response += socket.inet_aton("0.0.0.0" if blocked else "198.51.100.42")
        if self.server.wrong_id_first:
            sock.sendto(bytes([response[0] ^ 0xFF]) + response[1:], self.client_address)
        sock.sendto(response, self.client_address)


class Stub:
    def __init__(self):
        # Intentionally UDP only, matching nanoDNS 0.4's public listener.
        self.server = socketserver.UDPServer(("127.0.0.1", 0), StubHandler)
        self.port = self.server.server_address[1]
        self.server.available = True
        self.server.loop_target = None
        self.server.wrong_id_first = False
        self.server.queries = []
        rules, exceptions, section = [], [], ""
        for line in (ADDON / "examples/nanodns.ini.example").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                section = line
            elif section == "[overrides]":
                mask, address = line.split("=", 1)
                if address != "0.0.0.0":
                    raise AssertionError("Example override unexpectedly allows a destination")
                rules.append(mask)
            elif section == "[exceptions]":
                exceptions.append(line)
        if not rules or exceptions:
            raise AssertionError("Example must contain blocking rules and no exceptions")
        self.server.rules = rules
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


class RunningHost:
    def __init__(self, stub, *, enabled=True, loop=False):
        self.temp = tempfile.TemporaryDirectory(prefix="relapse-nanodns-test-")
        self.data = Path(self.temp.name)
        self.http_port, self.https_port, self.dns_port, self.admin_port = [unused_port() for _ in range(4)]
        options = {"server_ip": "192.0.2.10"}
        if enabled:
            options["nanodns_ip"] = "192.0.2.20"
        (self.data / "options.json").write_text(json.dumps(options))
        if loop:
            stub.server.loop_target = ("127.0.0.1", self.dns_port)
        launcher = self.data / "launch.py"
        launcher.write_text(
            "import sys\nsys.path.insert(0, " + repr(str(ADDON)) + ")\nimport host\n"
            "real_relay = host.NanoDNSRelay\n"
            # Only substitute the physical PS5 UDP endpoint. Run the shipping
            # option parsing, local relay, generated dnsmasq config and health.
            "def fixture_relay(address):\n"
            "    assert address == '192.0.2.20'\n"
            "    return real_relay('127.0.0.1', " + str(stub.port) + ")\n"
            "host.NanoDNSRelay = fixture_relay\nhost.main()\n")
        self.log = (self.data / "host.log").open("w+")
        self.process = subprocess.Popen([
            sys.executable, "-u", str(launcher), "--listen", "127.0.0.1",
            "--data-dir", str(self.data), "--http-port", str(self.http_port),
            "--https-port", str(self.https_port), "--dns-port", str(self.dns_port),
            "--admin-port", str(self.admin_port), "--admin-peer", "127.0.0.1",
        ], stdout=self.log, stderr=self.log)
        try:
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError((self.data / "host.log").read_text())
                try:
                    self.health()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Local forwarding fixture did not start")
        except BaseException:
            self.close()
            raise

    def health(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.http_port, timeout=1)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            if response.status != 200:
                raise RuntimeError("Health endpoint failed")
            return json.loads(response.read())
        finally:
            conn.close()

    def query(self, name, qtype=1, *, tcp=False, timeout=5):
        labels = b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0"
        packet = struct.pack("!HHHHHH", 0xABCD, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", qtype, 1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            if tcp:
                sock.connect(("127.0.0.1", self.dns_port))
                sock.sendall(struct.pack("!H", len(packet)) + packet)
                response = recv_exact(sock, struct.unpack("!H", recv_exact(sock, 2))[0])
            else:
                sock.sendto(packet, ("127.0.0.1", self.dns_port))
                response = sock.recv(4096)
        identity, flags, _, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
        if identity != 0xABCD:
            raise AssertionError("Transaction ID changed")
        return flags & 15, answers, response

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        code = self.process.returncode
        log = (self.data / "host.log").read_text()
        self.log.close()
        self.temp.cleanup()
        if code != 0:
            raise AssertionError(log)


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        self.stub = Stub()
        self.addCleanup(self.stub.close)

    def start_host(self, **options):
        service = RunningHost(self.stub, **options)
        self.addCleanup(service.close)
        return service

    def test_existing_options_stay_offline(self):
        service = self.start_host(enabled=False)
        self.assertEqual(service.health()["dns_mode"], "offline")
        self.assertEqual(service.health()["nanodns_ip"], "")
        for tcp in (False, True):
            self.assertEqual(service.query("example.com", tcp=tcp)[:2], (3, 0))
        self.assertEqual(self.stub.server.queries, [])

    def test_allowed_and_blocked_queries_over_both_transports(self):
        service = self.start_host()
        self.assertEqual(service.health()["dns_mode"], "nanodns")
        self.assertEqual(service.health()["nanodns_ip"], "192.0.2.20")
        for tcp in (False, True):
            for name, address in (("example.com", "198.51.100.42"),
                                  ("fus01.ps5.update.playstation.net", "0.0.0.0"),
                                  ("feature.api.playstation.com", "0.0.0.0"),
                                  ("gst.prod.dl.playstation.net", "0.0.0.0"),
                                  ("auth.sonyentertainmentnetwork.com", "0.0.0.0")):
                with self.subTest(tcp=tcp, name=name):
                    rcode, answers, packet = service.query(name, tcp=tcp)
                    self.assertEqual((rcode, answers), (0, 1))
                    self.assertEqual(packet[-4:], socket.inet_aton(address))
        self.assertEqual(sum(name == "example.com" for name, _ in self.stub.server.queries), 2)

    def test_guide_and_connectivity_never_reach_nanodns(self):
        service = self.start_host()
        for name in host.LOCAL_NAMES:
            for tcp in (False, True):
                rcode, answers, packet = service.query(name, tcp=tcp)
                self.assertEqual((rcode, answers), (0, 1))
                self.assertEqual(packet[-4:], socket.inet_aton("192.0.2.10"))
                for qtype in (28, 16, 65):
                    self.assertEqual(service.query(name, qtype, tcp=tcp)[1], 0)
        self.assertFalse(any(name in host.LOCAL_NAMES for name, _ in self.stub.server.queries))

    def test_failure_is_closed_and_recovers_without_restarting_ha(self):
        service = self.start_host()
        conf = (service.data / "dnsmasq.conf").read_text().splitlines()
        self.assertIn("no-resolv", conf)
        self.assertEqual(sum(line.startswith("server=") for line in conf), 1)
        self.stub.server.available = False
        for tcp in (False, True):
            self.assertEqual(service.query("ena.net.playstation.net", tcp=tcp)[:2], (0, 1))
            self.assertEqual(service.query("unavailable.example", tcp=tcp)[:2], (2, 0))
        self.stub.server.available = True
        self.assertEqual(service.query("example.com")[:2], (0, 1))

    def test_wrong_transaction_ids_are_ignored(self):
        service = self.start_host()
        self.stub.server.wrong_id_first = True
        for tcp in (False, True):
            rcode, answers, packet = service.query("example.com", tcp=tcp)
            self.assertEqual((rcode, answers), (0, 1))
            self.assertEqual(packet[-4:], socket.inet_aton("198.51.100.42"))

    def test_bad_upstream_loop_is_detected_and_guide_stays_local(self):
        service = self.start_host(loop=True)
        for _ in range(100):
            if "loop detected" in (service.data / "host.log").read_text().lower():
                break
            time.sleep(0.05)
        else:
            self.fail((service.data / "host.log").read_text())
        self.assertEqual(service.query("manuals.playstation.net")[:2], (0, 1))
        self.assertNotEqual(service.query("example.com")[0], 0)


class ConfigurationTests(unittest.TestCase):
    def test_optional_address_and_rejected_self_public_or_injected_values(self):
        for value in (None, ""):
            self.assertEqual(host.validate_nanodns_ip(value, "192.0.2.10"), "")
        self.assertEqual(host.validate_nanodns_ip("192.0.2.20", "192.0.2.10"), "192.0.2.20")
        for value in ("192.0.2.10", "127.0.0.1", "0.0.0.0", "8.8.8.8", "::1",
                      "http://192.0.2.20", "192.0.2.20:53", "192.0.2.20\nserver=8.8.8.8", 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.validate_nanodns_ip(value, "192.0.2.10")

    def test_forwarding_requires_a_separate_relay(self):
        for port in (None, 0, -1, 65536, 1053):
            with self.subTest(port=port), self.assertRaises(ValueError):
                host.dns_config("192.0.2.10", 1053, "0.0.0.0", "192.0.2.20", port)


if __name__ == "__main__":
    unittest.main(verbosity=2)
