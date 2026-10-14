"""Loopback-only browser fixture server. Never executes an exploit or a payload."""
from functools import partial
import http.client
import http.server
import io
import json
from pathlib import Path
import re
import signal
import ssl
import sys
import tempfile
import threading

ADDON = Path(__file__).resolve().parents[1] / "relapse_local"
sys.path.insert(0, str(ADDON))
from host import Server, Handler, ensure_certificate
from http_api import AdminHandler
from payload_store import PayloadStore, SessionStore

PREFIX = "/api/hassio_ingress/test-session"


class AdminTestHandler(AdminHandler):
    def post_route(self):
        assert self.headers.get("Transfer-Encoding") == "chunked"
        assert self.headers.get("Content-Length") is None
        return super().post_route()

    def log_message(self, *args):
        pass


class IngressTestHandler(http.server.BaseHTTPRequestHandler):
    """Model Supervisor's ingress_stream POST framing, not HA authentication.

    Supervisor strips Content-Length and forwards request.content as a stream:
    https://github.com/home-assistant/supervisor/blob/main/supervisor/api/ingress.py
    """
    def forward(self):
        if not self.path.startswith(PREFIX + "/"):
            self.send_error(404)
            return
        path = self.path[len(PREFIX):]
        headers = {name: value for name, value in self.headers.items()
                   if name.lower() not in ("content-length", "transfer-encoding", "connection", "host")}
        def chunks():
            remaining = int(self.headers["Content-Length"])
            while remaining:
                chunk = self.rfile.read(min(remaining, 64 * 1024))
                if not chunk:
                    raise IOError("Test client disconnected")
                remaining -= len(chunk)
                yield chunk
        connection = http.client.HTTPConnection("127.0.0.1", self.server.backend_port, timeout=20)
        try:
            post = self.command == "POST"
            connection.request(self.command, path, chunks() if post else None,
                               headers, encode_chunked=post)
            response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ("connection", "transfer-encoding", "server", "date"):
                    self.send_header(name, value)
            self.end_headers()
            while chunk := response.read(64 * 1024):
                self.wfile.write(chunk)
        finally:
            connection.close()

    do_GET = do_POST = forward

    def log_message(self, *args):
        pass


class PublicTestHandler(Handler):
    def log_message(self, *args):
        pass

    def send_head(self):
        if self.path.startswith(("/ui-test", "/payload-menu/relapse.html")):
            # Use the shipping markup and controller, but strip EVERY original
            # script. Only mock native socket writes; exercise the real HTTP APIs.
            html = (ADDON / "www/payload-menu/relapse.html").read_text()
            html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html)
            html = html.replace('href="payload-ui.css"', 'href="/payload-menu/payload-ui.css"')
            fresh = "false" if "existing=1" in self.path else "true"
            harness = '''<script src="/payload-menu/payload-ui.js"></script>
<script src="/payload-menu/payload-sender.js"></script>
<script>
window.sentIDs = [];
window.payloadUI = createPayloadUI({document, fetch: (u,o)=>fetch(u,o),
    send: entry=>window.mockSend(entry), sleep: ms=>new Promise(r=>setTimeout(r,Math.min(ms,20)))});
window.mockSend = createLocalPayloadSender({buffer: new Uint8Array(1024), fetch: (u,o)=>fetch(u,o),
    claim: entry=>{window.sendingID=entry.id;return payloadUI.claim(entry);},
    result: async(entry,status)=>{if(status==='sent')sentIDs.push(entry.id);await payloadUI.result(entry,status);},
    connect: async()=>3, write: async(_s,_o,n)=>{if(window.failID===window.sendingID)throw new Error('Test transfer failure');return n;},close:async()=>{}});
payloadUI.prepare().then(()=>payloadUI.ready(FRESH)).then(()=>{window.testReady=true;}).catch(e=>{window.testError=e.message;});
</script>'''.replace("FRESH", fresh)
            body = html.replace("</body>", harness + "</body>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        return super().send_head()


with tempfile.TemporaryDirectory(prefix="ha-relapse-ui-") as temp:
    data = Path(temp)
    store = PayloadStore(data)
    sessions = SessionStore(data, store)
    servers = []
    for handler, directory in ((AdminTestHandler, ADDON / "admin"), (PublicTestHandler, ADDON / "www")):
        server = Server(("127.0.0.1", 0), partial(handler, directory=str(directory)))
        server.payloads = store
        server.sessions = sessions
        server.admin_peer = "127.0.0.1"
        server.upload_slots = threading.BoundedSemaphore(2)
        server.commit = "ui-test"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    proxy = Server(("127.0.0.1", 0), IngressTestHandler)
    proxy.backend_port = servers[0].server_port
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    servers.append(proxy)
    # Exercise the shipping HTTPS handoff. Only the destination exploit page
    # is replaced with the inert controller fixture above.
    tls = Server(("127.0.0.1", 0), partial(Handler, directory=str(ADDON / "www")))
    tls.payloads = store
    tls.sessions = sessions
    tls.commit = "ui-test"
    tls.public_http_origin = f"http://127.0.0.1:{servers[1].server_port}"
    tls.public_http_port = servers[1].server_port
    tls.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.tls_context.load_cert_chain(*ensure_certificate(data, "192.0.2.10"))
    threading.Thread(target=tls.serve_forever, daemon=True).start()
    servers.append(tls)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(json.dumps({"admin": f"http://127.0.0.1:{proxy.server_port}{PREFIX}/", "public": tls.public_http_origin,
                      "guide": f"https://127.0.0.1:{tls.server_port}/document/gb/ps5/index.html"}), flush=True)
    stop.wait()
    for server in servers:
        server.shutdown()
        server.server_close()
