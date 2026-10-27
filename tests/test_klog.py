"""Klog capture and ingress API: loopback fake servers only, never a PS5."""
import base64
from functools import partial
import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[1] / "relapse_local"
sys.path.insert(0, str(ADDON))
from host import Server, Handler
from http_api import AdminHandler
from klog import KlogStore, destination
from payload_store import PayloadStore, StoreError


def wait_for(condition):
    deadline = time.monotonic() + 7
    while time.monotonic() < deadline:
        if condition(): return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for local test fixture")


class KlogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(2)
        self.addCleanup(self.listener.close)
        self.store = self.new_store()

    def new_store(self, **kwargs):
        def connect(address, timeout):
            self.assertEqual(address, ("192.168.50.20", 3232))
            return socket.create_connection(self.listener.getsockname(), timeout)
        store = KlogStore(Path(self.temp.name), connector=connect, **kwargs)
        self.addCleanup(store.close)
        return store

    def connect(self, store=None):
        store = store or self.store
        record = store.start("192.168.50.20")
        remote, _ = self.listener.accept()
        self.addCleanup(remote.close)
        wait_for(lambda: store.catalog()["active"]["state"] == "connected")
        return record["id"], remote

    def finished(self, store=None):
        store = store or self.store
        wait_for(lambda: not store.worker.is_alive())

    def test_destination_and_invalid_values_do_not_connect(self):
        for address in ("127.0.0.1", "0.0.0.0", "169.254.1.1", "8.8.8.8", "::1", "example.com", "10.0.0.1\n", None, 123):
            with self.subTest(address=address), self.assertRaises(StoreError):
                self.store.start(address)
        for port in (0, 65536, True, 3.5, "3232"):
            with self.assertRaises(StoreError): self.store.start("192.168.50.20", port)
        self.assertEqual(destination("172.16.2.3", 3232), ("172.16.2.3", 3232))
        self.assertEqual(self.store.catalog()["records"], [])

    def test_stop_raw_bytes_persistence_and_no_automatic_reconnect(self):
        identity, remote = self.connect()
        payload = "[game] café\n<script>alert(1)</script>\n".encode() + b"\xff\x00"
        remote.sendall(payload[:13]); remote.sendall(payload[13:])
        wait_for(lambda: self.store.catalog()["bytes"] == len(payload))
        value = self.store.read(identity, 0, 14)
        self.assertEqual(base64.b64decode(value["data"]), payload[:14])
        with self.assertRaisesRegex(StoreError, "Stop the current"): self.store.start("192.168.50.20")
        with self.assertRaisesRegex(StoreError, "before deleting"): self.store.delete(identity)
        self.store.stop(); self.finished()
        self.assertEqual(self.store.catalog()["records"][0]["state"], "stopped")
        self.assertEqual(base64.b64decode(self.store.read(identity)["data"]), payload)
        self.assertEqual((self.store.root / (identity + ".log")).stat().st_mode & 0o777, 0o600)
        self.store.close()
        with patch("klog.socket.create_connection") as dial:
            restored = KlogStore(Path(self.temp.name))
            self.addCleanup(restored.close)
            self.assertEqual(restored.catalog()["connection"], {"host":"192.168.50.20", "port":3232})
            self.assertEqual(base64.b64decode(restored.read(identity)["data"]), payload)
            dial.assert_not_called()
        restored.delete(identity)
        self.assertEqual(restored.catalog()["bytes"], 0)
        with self.assertRaises(StoreError): restored.read(identity)

    def test_download_snapshot_is_bounded_and_survives_delete(self):
        identity, remote = self.connect()
        remote.sendall(b"first\n")
        wait_for(lambda: self.store.catalog()["bytes"] == 6)
        stream, length, _ = self.store.open_recording(identity)
        self.addCleanup(stream.close)
        remote.sendall(b"second\n")
        wait_for(lambda: self.store.catalog()["bytes"] == 13)
        self.assertEqual(length, 6)
        self.store.stop(); self.finished(); self.store.delete(identity)
        self.assertEqual(stream.read(), b"first\n")
        self.assertEqual(stream.read(), b"")

    def test_disconnect_failure_and_retry_keep_history(self):
        identity, remote = self.connect()
        remote.sendall(b"before disconnect\n"); remote.shutdown(socket.SHUT_WR)
        self.finished()
        self.assertEqual(self.store.catalog()["records"][0]["state"], "disconnected")
        with patch.object(self.store, "connector", side_effect=ConnectionRefusedError):
            failed = self.store.start("192.168.50.20")["id"]
            self.finished()
        self.assertEqual(self.store.read(failed)["record"]["state"], "error")
        again, _ = self.connect()
        self.assertEqual(len(self.store.catalog()["records"]), 3)
        self.assertNotEqual(identity, again)

    def test_storage_recording_and_count_limits_never_remove_history(self):
        self.store.close()
        limited = self.new_store(max_recording=5, max_storage=8, max_records=2)
        first, remote = self.connect(limited)
        remote.sendall(b"123456789"); self.finished(limited)
        self.assertEqual(base64.b64decode(limited.read(first)["data"]), b"12345")
        self.assertEqual(limited.read(first)["record"]["state"], "limit")
        second, remote = self.connect(limited)
        remote.sendall(b"abcdef"); self.finished(limited)
        self.assertEqual(base64.b64decode(limited.read(second)["data"]), b"abc")
        with self.assertRaisesRegex(StoreError, "storage is full"): limited.start("192.168.50.20")
        limited.delete(first)
        third, _ = self.connect(limited)
        limited.stop(); self.finished(limited)
        self.assertEqual(len(limited.catalog()["records"]), 2)
        with self.assertRaises(StoreError): limited.start("192.168.50.20")
        self.assertEqual(limited.read(second)["record"]["bytes"], 3)

    def test_restart_recovers_incomplete_and_orphan_recordings(self):
        identity = "a" * 32
        (self.store.root / (identity + ".log")).write_bytes(b"kept\n")
        record = {"id":identity,"host":"192.168.50.20","port":3232,"started":time.time(),
                  "ended":None,"bytes":0,"state":"connected","message":"Connected"}
        (self.store.root / (identity + ".json")).write_text(json.dumps(record))
        orphan = "b" * 32
        (self.store.root / (orphan + ".log")).write_bytes(b"recovered\n")
        restored = self.new_store()
        self.assertEqual(restored.catalog()["bytes"], 15)
        self.assertEqual(restored.read(identity)["record"]["state"], "interrupted")
        self.assertEqual(base64.b64decode(restored.read(orphan)["data"]), b"recovered\n")
        self.assertIsNone(restored.catalog()["active"])

    def test_read_validation_and_symlink_rejection(self):
        identity, _ = self.connect()
        for bad in ("../connection", "../../etc/passwd", "x" * 32, None):
            with self.assertRaises(StoreError): self.store.read(bad)
        for position in (-1, True, "0", 1):
            with self.assertRaises(StoreError): self.store.read(identity, position)
        for limit in (0, 65537, True):
            with self.assertRaises(StoreError): self.store.read(identity, 0, limit)
        self.store.stop(); self.finished()
        path = self.store.root / (identity + ".log")
        path.unlink(); path.symlink_to(self.store.root / "connection.json")
        with self.assertRaises(OSError): self.store.open_recording(identity)


class KlogHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KlogStore(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.admin = Server(("127.0.0.1", 0), partial(AdminHandler, directory=str(ADDON / "admin")))
        self.admin.admin_peer, self.admin.klog = "127.0.0.1", self.store
        self.admin.payloads = PayloadStore(Path(self.temp.name))
        self.public = Server(("127.0.0.1", 0), partial(Handler, directory=str(ADDON / "www")))
        self.public.payloads = self.admin.payloads
        for server in (self.admin, self.public):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

    def request(self, path, data=None, public=False, headers=None):
        server = self.public if public else self.admin
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        body = None if data is None else json.dumps(data).encode()
        connection.request("GET" if data is None else "POST", path, body, headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_ingress_only_headers_and_invalid_actions(self):
        self.assertEqual(self.request("/api/klog/status")[0], 200)
        self.assertEqual(self.request("/klog.js")[0], 200)
        for path in ("status", "read?id=" + "a" * 32, "download?id=" + "a" * 32):
            self.assertEqual(self.request("/api/klog/" + path, public=True)[0], 404)
        self.assertEqual(self.request("/api/klog/connect", {"host":"192.168.50.20"},
                                      headers={"Content-Type":"application/json"})[0], 403)
        headers = {"Content-Type":"application/json", "X-Relapse-Request":"1"}
        self.assertEqual(self.request("/api/klog/connect", {"host":"8.8.8.8"}, headers=headers)[0], 400)
        self.assertEqual(self.request("/api/klog/stop", {}, public=True, headers=headers)[0], 403)
        self.admin.admin_peer = "192.0.2.1"
        self.assertEqual(self.request("/api/klog/status", headers={"X-Forwarded-For":"192.0.2.1"})[0], 403)

    def test_read_download_and_delete_over_http(self):
        identity = "c" * 32
        payload = b"test log\n<script>untrusted</script>\n"
        (self.store.root / (identity + ".log")).write_bytes(payload)
        self.store.records[identity] = {"id":identity,"started":time.time(),"state":"stopped","bytes":len(payload)}
        self.store.total = len(payload)
        code, _, data = self.request("/api/klog/read?id=" + identity)
        self.assertEqual(code, 200)
        self.assertEqual(base64.b64decode(json.loads(data)["data"]), payload)
        code, headers, data = self.request("/api/klog/download?id=" + identity)
        self.assertEqual((code, data), (200, payload))
        self.assertIn("attachment; filename=", headers["Content-Disposition"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("/api/klog/read?id=" + identity + "&offset=-1")[0], 400)
        self.assertEqual(self.request("/api/klog/read?id=" + identity + "&id=" + identity)[0], 400)
        headers = {"Content-Type":"application/json", "X-Relapse-Request":"1"}
        self.assertEqual(self.request("/api/klog/delete", {"id":identity}, headers=headers)[0], 200)
        self.assertEqual(self.request("/api/klog/download?id=" + identity)[0], 404)


if __name__ == "__main__":
    unittest.main()
