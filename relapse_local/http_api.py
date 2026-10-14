"""HA ingress management and read/send-history API shared by the local host."""

import http.server
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from payload_store import MAX_PAYLOAD, StoreError

VERSION = "0.4.4"


class ApiMixin:
    def json_response(self, value, status=200):
        data = json.dumps(value, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        return io.BytesIO(data)

    def body(self, maximum):
        if self.headers.get_all("X-Relapse-Request", []) != ["1"]:
            raise StoreError("Use the HA Relapse interface for this request", 403)
        return self.read_body(maximum)

    def read_body(self, maximum):
        """Decode a bounded body; callers enforce their own access policy."""
        encodings = self.headers.get_all("Transfer-Encoding", [])
        lengths = self.headers.get_all("Content-Length", [])
        if encodings:
            # Supervisor removes Content-Length and streams Ingress POST bodies.
            # BaseHTTPRequestHandler does not decode HTTP chunks for us.
            if lengths:
                raise StoreError("Conflicting request lengths")
            if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
                raise StoreError("Unsupported request transfer encoding")
            return self.chunked_body(maximum)
        if not lengths:
            raise StoreError("Content-Length required", 411)
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise StoreError("Invalid upload length")
        if len(lengths[0]) > 20 or int(lengths[0]) > maximum:
            raise StoreError("Upload exceeds the allowed size", 413)
        return self.read_exact(int(lengths[0]))

    def read_exact(self, size):
        data = self.rfile.read(size)
        if len(data) != size:
            raise StoreError("Upload interrupted; active payload was not changed")
        return data

    def chunk_line(self):
        line = self.rfile.readline(8193)
        if len(line) > 8192 or not line.endswith(b"\r\n"):
            raise StoreError("Invalid or interrupted chunked request")
        return line

    def chunked_body(self, maximum):
        with io.BytesIO() as body:
            while True:
                size_field = self.chunk_line()[:-2].split(b";", 1)[0]
                if not re.fullmatch(rb"[0-9A-Fa-f]+", size_field):
                    raise StoreError("Invalid upload chunk length")
                size = int(size_field, 16)
                if size > maximum - body.tell():
                    raise StoreError("Upload exceeds the allowed size", 413)
                if size == 0:
                    # Consume bounded trailers, without treating them as headers.
                    trailer_bytes = 0
                    while True:
                        line = self.chunk_line()
                        trailer_bytes += len(line)
                        if trailer_bytes > 64 * 1024:
                            raise StoreError("Upload trailers are too large")
                        if line == b"\r\n":
                            return body.getvalue()
                        if not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[^\r\n]*\r\n", line):
                            raise StoreError("Invalid upload trailer")
                remaining = size
                while remaining:
                    part = self.read_exact(min(remaining, 64 * 1024))
                    body.write(part)
                    remaining -= len(part)
                if self.read_exact(2) != b"\r\n":
                    raise StoreError("Invalid upload chunk ending")

    def json_body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise StoreError("Expected application/json", 415)
        try:
            value = json.loads(self.body(64 * 1024))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, StoreError):
                raise
            raise StoreError("Invalid JSON request")
        if not isinstance(value, dict):
            raise StoreError("Expected a JSON object")
        return value

    def write_json(self, value, status=200):
        with self.json_response(value, status) as stream:
            self.wfile.write(stream.read())

    def catalog(self):
        return {"version": VERSION, **self.server.payloads.catalog()}

    def do_POST(self):
        try:
            result = self.post_route()
            self.write_json(result or {"ok": True})
        except StoreError as error:
            self.close_connection = True
            self.write_json({"error": str(error)}, error.status)
        except (KeyError, TypeError):
            self.close_connection = True
            self.write_json({"error": "Missing or invalid request fields"}, 400)
        except (OSError, TimeoutError):
            self.close_connection = True
            self.write_json({"error": "Storage or transfer error. Refresh to check the active version."}, 503)


class PublicApi(ApiMixin):
    def public_head(self, path):
        if path == "/api/catalog":
            return self.json_response(self.catalog())
        if path.startswith("/user-payload/"):
            parts = path.split("/")
            if len(parts) != 4 or not parts[3].endswith(".elf"):
                raise StoreError("Unknown payload", 404)
            stream, active = self.server.payloads.open_active(parts[2], parts[3][:-4])
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(active["bytes"]))
            self.send_header("X-Payload-SHA256", active["sha256"])
            self.end_headers()
            return stream
        raise StoreError("Unknown endpoint", 404)

    def post_route(self):
        path = urlsplit(self.path).path
        if path not in ("/api/session", "/api/claim", "/api/result"):
            raise StoreError("This interface is read-only. Manage uploads in Home Assistant.", 403)
        data = self.json_body()
        peer = self.client_address[0]
        sessions = self.server.sessions
        if path == "/api/session":
            return sessions.start(peer, data["fresh"])
        if path == "/api/claim":
            sessions.claim(peer, data["token"], data["id"], data["sha256"])
        else:
            sessions.finish(peer, data["token"], data["id"], data["status"])


class AdminHandler(ApiMixin, http.server.SimpleHTTPRequestHandler):
    """Only Supervisor's ingress proxy may access upload/management endpoints."""

    def allowed(self):
        # Never trust X-Forwarded-For or other caller-provided identity headers.
        return self.client_address[0] == self.server.admin_peer

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'self'")
        super().end_headers()

    def send_head(self):
        if not self.allowed():
            return self.json_response({"error": "Open this page through Home Assistant"}, 403)
        path = unquote(urlsplit(self.path).path)
        if path == "/api/catalog":
            return self.json_response(self.catalog())
        if path not in ("/", "/index.html", "/app.js", "/style.css"):
            return self.json_response({"error": "Not found"}, 404)
        return super().send_head()

    def list_directory(self, path):
        return self.json_response({"error": "Not found"}, 404)

    def post_route(self):
        if not self.allowed():
            raise StoreError("Open payload management through Home Assistant", 403)
        parts = urlsplit(self.path)
        store = self.server.payloads
        if parts.path == "/api/upload":
            query = parse_qs(parts.query, keep_blank_values=True)
            if any(len(values) != 1 for values in query.values()):
                raise StoreError("Duplicate upload field")
            values = {key: items[0] for key, items in query.items()}
            if self.headers.get("Content-Type") != "application/octet-stream":
                raise StoreError("Upload the ELF file directly", 415)
            # At most two large bodies may be held in memory at the same time.
            with self.server.upload_slots:
                entry = store.upload(values.get("kind"), values.get("filename"),
                                     self.body(MAX_PAYLOAD), values.get("label", ""), values.get("id"))
            return {"ok": True, "entry": entry}
        data = self.json_body()
        if parts.path == "/api/edit":
            store.edit(data["id"], data["changes"])
        elif parts.path == "/api/rollback":
            store.rollback(data["id"])
        elif parts.path == "/api/remove":
            store.remove(data["id"])
        elif parts.path == "/api/order":
            store.reorder(data["ids"])
        else:
            raise StoreError("Unknown management action", 404)
