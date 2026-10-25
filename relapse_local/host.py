"""Serve pinned Relapse assets with local DNS and optional nanoDNS forwarding."""

import argparse
import functools
import hashlib
import http.server
import io
import ipaddress
import json
import os
from pathlib import Path
import pwd
import signal
import ssl
import subprocess
import tempfile
import threading
from urllib.parse import unquote, urlsplit
from payload_store import PayloadStore, SessionStore, StoreError
from http_api import AdminHandler, PublicApi, VERSION
from dns_forwarder import NanoDNSRelay

GUIDE_NAMES = (
    "support.playstation.com",
    "manuals.playstation.net",
    "www.playstation.com",
    "relapse.home.arpa",
)
CONNECTIVITY_NAMES = ("ena.net.playstation.net",)
LOCAL_NAMES = GUIDE_NAMES + CONNECTIVITY_NAMES
MAX_CONNECTIVITY_BODY = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parent


def validate_ip(value):
    ip = ipaddress.IPv4Address(value)
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback or not ip.is_private:
        raise ValueError("server_ip must be the Home Assistant computer's private LAN IPv4 address")
    return str(ip)


def validate_nanodns_ip(value, server_ip):
    if value is None or value == "":
        return ""
    try:
        if not isinstance(value, str):
            raise ValueError
        address = validate_ip(value)
    except (ValueError, TypeError):
        raise ValueError("nanodns_ip must be the PS5's private LAN IPv4 address, or empty") from None
    if address == server_ip:
        raise ValueError("nanodns_ip must be the PS5's address, not Home Assistant's address")
    return address


def http_origin(server_ip, port):
    if not 1 <= port <= 65535:
        raise ValueError("public HTTP port must be between 1 and 65535")
    return "http://" + validate_ip(server_ip) + (f":{port}" if port != 80 else "")


def connectivity_response(path):
    # Match the PS5 routes answered by owendswang's offline ESP32 host.
    # These are static replies, not a proxy or a connection to PlayStation.
    if path == "/netstart/icst":
        return b"Success"
    if path.startswith("/networktest/") and ".." not in path.split("/") and "\\" not in path and "\x00" not in path:
        return b"OK"
    return None


def verify_bundle(webroot):
    manifest = json.loads((ROOT / "UPSTREAM.json").read_text())
    for name, expected in manifest["sha256"].items():
        path = webroot / "relapse" / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Relapse bundle integrity check failed: {name}")
    menu = json.loads((ROOT / "PAYLOAD_MENU.json").read_text())
    for name, expected in menu["sha256"].items():
        path = webroot / "payload-menu" / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Payload menu integrity check failed: {name}")
    return manifest["commit"]


def dns_config(server_ip, port, listen, nanodns_ip="", relay_port=None):
    nanodns_ip = validate_nanodns_ip(nanodns_ip, server_ip)
    # Never consult the container/router resolver or use an external fallback.
    lines = [
        f"port={port}", f"listen-address={listen}", "bind-interfaces",
        "no-resolv", "no-hosts", "cache-size=0",
        "log-facility=-", "pid-file=", f"user={pwd.getpwuid(os.getuid()).pw_name}",
    ]
    if nanodns_ip:
        if not isinstance(relay_port, int) or not 1 <= relay_port <= 65535 or relay_port == port:
            raise ValueError("nanoDNS forwarding requires a separate local relay port")
        lines.extend([f"server=127.0.0.1#{relay_port}", "dns-loop-detect"])
    else:
        lines.append("local=/#/")
    for name in LOCAL_NAMES:
        lines.extend([f"address=/{name}/{server_ip}", f"local=/{name}/"])
    return "\n".join(lines) + "\n"


def ensure_certificate(data, server_ip):
    certificate, key = data / "certificate.pem", data / "private-key.pem"
    marker = data / "certificate-ip.txt"
    identity = server_ip + "\n" + "\n".join(LOCAL_NAMES)
    if not (certificate.is_file() and key.is_file() and marker.is_file()
            and marker.read_text() == identity):
        names = ",".join(f"DNS:{name}" for name in LOCAL_NAMES) + f",IP:{server_ip}"
        with tempfile.TemporaryDirectory(dir=data) as temporary:
            temp = Path(temporary)
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                "-nodes", "-days", "825", "-subj", "/CN=support.playstation.com",
                "-addext", "subjectAltName=" + names,
                "-keyout", str(temp / "key.pem"), "-out", str(temp / "cert.pem"),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (temp / "key.pem").chmod(0o600)
            (temp / "key.pem").replace(key)
            (temp / "cert.pem").replace(certificate)
        marker.write_text(identity)
    return certificate, key


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(15)
        context = getattr(self, "tls_context", None)
        if context:
            sock = context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, address


class Handler(PublicApi, http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".js": "application/javascript", ".elf": "application/octet-stream",
                      ".bin": "application/octet-stream"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def probe_head(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        return io.BytesIO(body)

    def do_POST(self):
        body = connectivity_response(unquote(urlsplit(self.path).path))
        if body is None:
            return super().do_POST()
        try:
            # Only these static probes bypass the UI request header. Consume
            # framed bodies so an uploading client can finish before we close.
            if "Content-Length" in self.headers or "Transfer-Encoding" in self.headers:
                self.read_body(MAX_CONNECTIVITY_BODY)
            with self.probe_head(body) as stream:
                self.wfile.write(stream.read())
        except StoreError as error:
            self.close_connection = True
            self.write_json({"error": str(error)}, error.status)
        except (OSError, TimeoutError):
            self.close_connection = True
            self.write_json({"error": "Connectivity request interrupted"}, 408)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()

    def send_head(self):
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        if ".." in path.split("/") or "\\" in path or "\x00" in path:
            self.send_error(404)
            return None
        probe = connectivity_response(path)
        if probe is not None:
            return self.probe_head(probe)
        if path == "/health":
            body = json.dumps({"status": "ok", "version": VERSION, "relapse_commit": self.server.commit,
                               "public_http_url": getattr(self.server, "public_http_origin", None),
                               "connectivity_http_port80": getattr(self.server, "public_http_port", None) == 80,
                               "dns_mode": "nanodns" if getattr(self.server, "nanodns_ip", "") else "offline",
                               "nanodns_ip": getattr(self.server, "nanodns_ip", "")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        if path.startswith(("/api/", "/user-payload/")):
            try:
                return self.public_head(path)
            except StoreError as error:
                return self.json_response({"error": str(error)}, error.status)
        if path.startswith("/admin"):
            return self.json_response({"error": "Open the management page in Home Assistant"}, 403)
        entry = {"/relapse": "/relapse/index.html", "/relapse/": "/relapse/index.html",
                 "/relapse/index.html": "/relapse/index.html",
                 "/payload-menu": "/payload-menu/relapse.html", "/payload-menu/": "/payload-menu/relapse.html",
                 "/payload-menu/relapse.html": "/payload-menu/relapse.html"}.get(path)
        if entry and (entry != path or getattr(self.server, "tls_context", None)):
            target = entry + ("?" + parts.query if parts.query else "")
            if getattr(self.server, "tls_context", None):
                target = self.server.public_http_origin + target
            self.redirect(target)
            return None
        if path.startswith("/payload-menu/log/"):
            # Upstream's same-origin progress beacons need no persistent storage.
            self.send_response(204)
            self.end_headers()
            return None
        if not path.startswith(("/relapse/", "/payload-menu/")):
            # User's Guide paths vary by locale and firmware. All land on the menu.
            if getattr(self.server, "tls_context", None):
                # Use validated config, never the request's Host header. Subsequent
                # scripts, workers, beacons and payloads share this local HTTP origin.
                self.redirect(self.server.public_http_origin + "/")
                return None
            self.path = "/index.html"
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404)
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-ip")
    parser.add_argument("--nanodns-ip", help="PS5 LAN IPv4 address; empty disables forwarding")
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--webroot", type=Path, default=ROOT / "www")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--public-http-port", type=int, default=8088,
                        help="Host-side HTTP port, passed from Supervisor by run.sh")
    parser.add_argument("--https-port", type=int, default=8443)
    parser.add_argument("--dns-port", type=int, default=1053)
    parser.add_argument("--admin-port", type=int, default=8099)
    parser.add_argument("--admin-peer", default="172.30.32.2", help="Ingress proxy; loopback override only for local tests")
    args = parser.parse_args()
    if args.admin_peer != "172.30.32.2" and not (args.admin_peer == args.listen == "127.0.0.1"):
        parser.error("The admin peer override is only allowed when all listeners are bound to loopback")
    for port in (args.http_port, args.public_http_port, args.https_port, args.dns_port, args.admin_port):
        if not 1 <= port <= 65535:
            parser.error("ports must be between 1 and 65535")
    options = json.loads((args.data_dir / "options.json").read_text()) if not args.server_ip else {}
    server_ip = validate_ip(args.server_ip or options["server_ip"])
    nanodns_ip = validate_nanodns_ip(
        args.nanodns_ip if args.nanodns_ip is not None else options.get("nanodns_ip", ""), server_ip)
    public_http_origin = http_origin(server_ip, args.public_http_port)
    commit = verify_bundle(args.webroot)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    payloads = PayloadStore(args.data_dir)
    sessions = SessionStore(args.data_dir, payloads)
    certificate, key = ensure_certificate(args.data_dir, server_ip)
    conf = args.data_dir / "dnsmasq.conf"

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    servers, threads = [], []
    dns = None
    relay = None
    try:
        if nanodns_ip:
            relay = NanoDNSRelay(nanodns_ip)
            relay.start()
        conf.write_text(dns_config(server_ip, args.dns_port, args.listen, nanodns_ip,
                                   relay.port if relay else None))
        subprocess.run(["dnsmasq", "--test", "--conf-file=" + str(conf)], check=True)
        handler = functools.partial(Handler, directory=str(args.webroot))
        for port, tls in ((args.http_port, False), (args.https_port, True)):
            server = Server((args.listen, port), handler)
            servers.append(server)
            server.commit = commit
            server.public_http_origin = public_http_origin
            server.public_http_port = args.public_http_port
            server.nanodns_ip = nanodns_ip
            server.payloads = payloads
            server.sessions = sessions
            if tls:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(certificate, key)
                server.tls_context = context
        admin = Server((args.listen, args.admin_port), functools.partial(
            AdminHandler, directory=str(ROOT / "admin")))
        admin.admin_peer = args.admin_peer
        admin.payloads = payloads
        admin.upload_slots = threading.BoundedSemaphore(2)
        servers.append(admin)
        dns = subprocess.Popen(["dnsmasq", "--keep-in-foreground", "--conf-file=" + str(conf)])
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        print(f"Relapse {commit}: verified. DNS redirects guide and connectivity domains to {server_ip}.", flush=True)
        print("Ready: local HTTP, HTTPS and DNS.", flush=True)
        if nanodns_ip:
            print(f"Other DNS names go only to nanoDNS at {nanodns_ip}:53; no fallback resolver. "
                  "Filtering requires nanoDNS to be running and configured on the PS5.", flush=True)
        else:
            print("Other DNS names stay offline. nanoDNS forwarding is disabled.", flush=True)
        print(f"User's Guide handoff: {public_http_origin}/", flush=True)
        if args.public_http_port != 80:
            print("Connectivity checks need host HTTP port 80. Current mapping is "
                  f"{args.public_http_port}; free port 80 before changing the app's Network setting.", flush=True)
        print(f"HA Relapse {VERSION}: ingress management on {args.admin_port}; user payloads in persistent storage.", flush=True)
        while not stop.wait(0.25):
            if (dns.poll() is not None or not all(t.is_alive() for t in threads)
                    or (relay is not None and not relay.healthy())):
                raise RuntimeError("A hosting service stopped; exiting so Supervisor can restart the app")
    finally:
        if dns is not None and dns.poll() is None:
            dns.terminate()
            try:
                dns.wait(timeout=3)
            except subprocess.TimeoutExpired:
                dns.kill()
                dns.wait()
        for server, thread in zip(servers, threads):
            server.shutdown()
            thread.join(timeout=2)
        for server in servers:
            server.server_close()
        if relay is not None:
            relay.close()


if __name__ == "__main__":
    main()
