"""Exercise actual local DNS, TLS, HTTP and bundled asset delivery; never run JS."""

import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

ADDON = Path(__file__).resolve().parents[1] / "relapse_local"
sys.path.insert(0, str(ADDON))
spec = importlib.util.spec_from_file_location("relapse_host", ADDON / "host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)



def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def recv_exact(sock, size):
    result = b""
    while len(result) < size:
        piece = sock.recv(size - len(result))
        if not piece:
            raise EOFError("DNS TCP response was truncated")
        result += piece
    return result


class HostingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="relapse-host-test-")
        cls.data = Path(cls.temporary.name)
        cls.http_port, cls.https_port, cls.dns_port, cls.admin_port = [unused_port() for _ in range(4)]
        cls.log = (cls.data / "host.log").open("w+")
        cls.process = subprocess.Popen([
            sys.executable, "-u", str(ADDON / "host.py"),
            "--server-ip", "192.0.2.10", "--listen", "127.0.0.1",
            "--data-dir", str(cls.data), "--http-port", str(cls.http_port),
            "--https-port", str(cls.https_port), "--dns-port", str(cls.dns_port),
            "--admin-port", str(cls.admin_port), "--admin-peer", "127.0.0.1",
        ], stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError(cls.log.read())
            try:
                connection = http.client.HTTPConnection("127.0.0.1", cls.http_port, timeout=1)
                connection.request("GET", "/health")
                if connection.getresponse().status == 200:
                    connection.close()
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Local test service did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        if cls.process.returncode != 0:
            raise AssertionError((cls.data / "host.log").read_text())
        cls.temporary.cleanup()

    def request(self, path, tls=False, method="GET", body=None, headers=None, chunked=False):
        if tls:
            # A self-signed local guide certificate is intentional.
            conn = http.client.HTTPSConnection("127.0.0.1", self.https_port,
                                              context=ssl._create_unverified_context(), timeout=3)
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.http_port, timeout=3)
        conn.request(method, path, body, headers or {}, encode_chunked=chunked)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def dns_query(self, name, qtype=1, tcp=False):
        wire_name = b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0"
        request = struct.pack("!HHHHHH", 0xABCD, 0x0100, 1, 0, 0, 0) + wire_name + struct.pack("!HH", qtype, 1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            if tcp:
                sock.connect(("127.0.0.1", self.dns_port))
                sock.sendall(struct.pack("!H", len(request)) + request)
                response = recv_exact(sock, struct.unpack("!H", recv_exact(sock, 2))[0])
            else:
                sock.sendto(request, ("127.0.0.1", self.dns_port))
                response = sock.recv(4096)
        identity, flags, _, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
        self.assertEqual(identity, 0xABCD)
        return flags & 15, answers, response

    def test_guide_dns_over_udp_and_tcp(self):
        for name in host.LOCAL_NAMES:
            for tcp in (False, True):
                with self.subTest(name=name, tcp=tcp):
                    rcode, answers, response = self.dns_query(name, tcp=tcp)
                    self.assertEqual((rcode, answers), (0, 1))
                    self.assertEqual(response[-4:], socket.inet_aton("192.0.2.10"))

    def test_no_external_or_ipv6_resolution(self):
        for name in ("example.com", "fus01.ps5.update.playstation.net", "playstation.com.evil.example"):
            for qtype in (1, 28):
                with self.subTest(name=name, qtype=qtype):
                    rcode, answers, _ = self.dns_query(name, qtype)
                    self.assertEqual((rcode, answers), (3, 0))
        for name in host.LOCAL_NAMES:
            for qtype in (28, 16, 65):
                _, answers, _ = self.dns_query(name, qtype)
                self.assertEqual(answers, 0)

    def test_landing_for_guide_paths_and_tls(self):
        for path in ("/", "/en-gb/support/hardware/ps5/user-guide/", "/document/gb/ps5/index.html"):
            status, headers, body = self.request(path, tls=True, headers={"Host": "attacker.example"})
            self.assertEqual(status, 302)
            self.assertEqual(headers["Location"], "http://192.0.2.10:8088/")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Connection"], "close")
            self.assertEqual(body, b"")
            # Connect only to loopback while following the redirect's local path.
            status, _, body = self.request(urlsplit(headers["Location"]).path)
            self.assertEqual(status, 200)
            self.assertIn(b">Relapse</a>", body)
            self.assertNotIn(b'<script src=', body)

    def test_direct_exploit_links_move_to_http_and_preserve_query(self):
        for path, target in (("/relapse/", "/relapse/index.html"),
                             ("/payload-menu", "/payload-menu/relapse.html"),
                             ("/payload-menu/relapse.html", "/payload-menu/relapse.html")):
            status, headers, body = self.request(path + "?log=debug", tls=True, method="HEAD")
            self.assertEqual(status, 302)
            self.assertEqual(headers["Location"], "http://192.0.2.10:8088" + target + "?log=debug")
            self.assertEqual(body, b"")
            self.assertEqual(self.request(target + "?log=debug")[0], 200)
        self.assertEqual(self.request("/api/catalog", tls=True)[0], 200)

    def test_connectivity_get_head_post_over_http_and_https(self):
        for tls in (False, True):
            for path, expected in (("/netstart/icst", b"Success"), ("/networktest/get_2m?nonce=1", b"OK")):
                for method in ("GET", "HEAD", "POST"):
                    with self.subTest(tls=tls, path=path, method=method):
                        status, headers, body = self.request(path, tls=tls, method=method,
                            body=b"probe-data" if method == "POST" else None,
                            headers={"Host": "ena.net.playstation.net"})
                        self.assertEqual(status, 200)
                        self.assertEqual(body, b"" if method == "HEAD" else expected)
                        self.assertEqual(headers["Content-Type"], "text/plain")
                        self.assertEqual(headers["Content-Length"], str(len(expected)))
                        self.assertEqual(headers["Cache-Control"], "no-store")
                        self.assertNotIn("Location", headers)
        status, _, body = self.request("/networktest/post", method="POST",
                                      body=iter((b"one", b"two")), chunked=True)
        self.assertEqual((status, body), (200, b"OK"))

    def test_probe_limits_do_not_relax_api_access(self):
        for headers, expected in (({"Content-Length": str(host.MAX_CONNECTIVITY_BODY + 1)}, 413),
                                  ({"Content-Length": "1", "Transfer-Encoding": "chunked"}, 400)):
            status, _, _ = self.request("/networktest/post", method="POST", body=b"", headers=headers)
            self.assertEqual(status, expected)
        for path in ("/api/session", "/api/upload", "/networktest/../api/upload"):
            status, _, _ = self.request(path, method="POST", body=b'{"fresh":true}',
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(status, 403)
        self.assertEqual(self.request("/networktest/../api/catalog")[0], 404)
        self.assertEqual(self.request("/netstart/icst-extra", method="POST")[0], 403)

    def test_health_identifies_port80_requirement(self):
        health = json.loads(self.request("/health", tls=True)[2])
        self.assertEqual(health["public_http_url"], "http://192.0.2.10:8088")
        self.assertFalse(health["connectivity_http_port80"])

    def test_every_upstream_file_is_delivered_unchanged(self):
        manifest = json.loads((ADDON / "UPSTREAM.json").read_text())
        for name, expected in manifest["sha256"].items():
            with self.subTest(file=name):
                status, headers, body = self.request("/relapse/" + name)
                self.assertEqual(status, 200)
                self.assertEqual(hashlib.sha256(body).hexdigest(), expected)
                self.assertEqual(headers["Cache-Control"], "no-store")
                if name.endswith(".js"):
                    self.assertEqual(headers["Content-type"], "application/javascript")

    def test_tls_payload_and_head(self):
        path = "/relapse/payloads/elfldr-ps5-1360.elf"
        status, _, body = self.request(path, tls=True)
        self.assertEqual(status, 200)
        self.assertEqual(body, (ADDON / "www" / path.lstrip("/")).read_bytes())
        status, headers, body = self.request(path, tls=True, method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_menu_assets_and_payload_bytes_over_http_and_tls(self):
        manifest = json.loads((ADDON / "PAYLOAD_MENU.json").read_text())
        for name, expected in manifest["sha256"].items():
            for tls in (False, True):
                with self.subTest(file=name, tls=tls):
                    status, headers, body = self.request("/payload-menu/" + name, tls=tls)
                    if tls and name == "relapse.html":
                        self.assertEqual(status, 302)
                        status, _, body = self.request(urlsplit(headers["Location"]).path)
                    self.assertEqual(status, 200)
                    self.assertEqual(hashlib.sha256(body).hexdigest(), expected)
        self.assertEqual(self.request("/payload-menu/")[1]["Location"], "/payload-menu/relapse.html")
        self.assertEqual(self.request("/payload-menu/log/test")[0], 204)

    def test_health_and_directory_protection(self):
        status, _, body = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        for path in ("/relapse/../../host.py", "/relapse/%2e%2e/private-key.pem", "/relapse/src/",
                     "/payload-menu/../host.py", "/payload-menu/payloads/", "/payload-menu/payloads/missing.elf"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/relapse/")[0], 302)

    def test_bad_ip_options_rejected(self):
        for value in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "192.0.2.10\nserver=8.8.8.8"):
            with self.assertRaises(ValueError):
                host.validate_ip(value)


class BundleTests(unittest.TestCase):
    def test_http_origin_and_invalid_ports(self):
        self.assertEqual(host.http_origin("192.0.2.10", 80), "http://192.0.2.10")
        self.assertEqual(host.http_origin("192.0.2.10", 9090), "http://192.0.2.10:9090")
        for port in (0, -1, 65536):
            with self.assertRaises(ValueError):
                host.http_origin("192.0.2.10", port)

    def test_certificate_migration_adds_connectivity_name_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            with patch.object(host, "LOCAL_NAMES", host.GUIDE_NAMES):
                certificate, key = host.ensure_certificate(data, "192.0.2.10")
            old = certificate.read_bytes()
            # Simulate the marker used by releases up to 0.4.1.
            (data / "certificate-ip.txt").write_text("192.0.2.10")
            host.ensure_certificate(data, "192.0.2.10")
            updated = certificate.read_bytes()
            self.assertNotEqual(old, updated)
            decoded = subprocess.check_output(["openssl", "x509", "-in", str(certificate),
                                               "-noout", "-ext", "subjectAltName"], text=True)
            self.assertIn("DNS:ena.net.playstation.net", decoded)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            host.ensure_certificate(data, "192.0.2.10")
            self.assertEqual(updated, certificate.read_bytes())

    def test_startup_reads_supervisor_mapping_and_rejects_disabled_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake_python = Path(temporary) / "python3"
            fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake_python.chmod(0o755)
            for api in ("app", "addon"):
                for mapping in ("80", "8088", "", "null", "0", "65536", "bad;value"):
                    with self.subTest(api=api, mapping=mapping):
                        # Only stub the Supervisor lookup and interpreter; run
                        # the shipping startup script with its real validation.
                        script = ('function bashio::' + api + '.port() { printf "%s" "$MAPPING"; }\n'
                                  'function bashio::log.fatal() { printf "%s\\n" "$*" >&2; }\n'
                                  'source "$STARTUP"\n')
                        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env={**os.environ, "PATH": temporary + ":" + os.environ["PATH"],
                                 "MAPPING": mapping, "STARTUP": str(ADDON / "run.sh")})
                        if mapping in ("80", "8088"):
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout.splitlines()[-2:], ["--public-http-port", mapping])
                        else:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn("Enable a valid HTTP port", result.stderr)

    def test_engine_tamper_stops_startup_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            addon = Path(temporary) / "relapse_local"
            shutil.copytree(ADDON, addon)
            (addon / "www/payload-menu/payloads/elfldr-ps5-1360.elf").write_bytes(b"wrong file")
            with patch.object(host, "ROOT", addon), self.assertRaisesRegex(ValueError, "Payload menu integrity"):
                host.verify_bundle(addon / "www")

    def test_only_required_engine_payloads_are_in_the_app(self):
        for directory in ("relapse", "payload-menu"):
            actual = {p.name for p in (ADDON / "www" / directory / "payloads").iterdir()}
            self.assertEqual(actual, {"elfldr-ps5-1360.elf", "kexp_2026_05_25.bin"})
        self.assertFalse((ADDON / "third_party").exists())
        self.assertEqual(json.loads((ADDON / "PAYLOAD_MENU.json").read_text())["payloads"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
