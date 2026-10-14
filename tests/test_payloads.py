"""Persistent uploads and actual HTTP workflows; inert ELF fixtures only."""
import copy
import hashlib
import http.client
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from functools import partial

ADDON = Path(__file__).resolve().parents[1] / "relapse_local"
sys.path.insert(0, str(ADDON))
from payload_store import PayloadStore, SessionStore, StoreError
from host import Server, Handler
from http_api import AdminHandler


def elf(marker=1, size=256):
    data = bytearray(size)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<H", data, 18, 62)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 1)
    data[-1] = marker
    return bytes(data)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.store = PayloadStore(self.data)

    def test_empty_library_generic_primary_and_filename_preservation(self):
        self.assertEqual(self.store.catalog(), {"revision": 0, "primary": None, "additional": []})
        entry = self.store.upload("primary", "Future combined payload β v1.bin", elf())
        self.assertEqual(entry["active"]["filename"], "Future combined payload β v1.bin")
        self.assertEqual(entry["active"]["label"], "")
        self.assertEqual(PayloadStore(self.data).catalog(), self.store.catalog())

    def test_replacement_rollback_restores_file_and_label_without_losing_settings(self):
        first = self.store.upload("additional", "First.elf", elf(), "First label")
        identity = first["id"]
        self.store.edit(identity, {"restore": True, "wait_seconds": 12})
        self.store.upload("additional", "Different name.elf", elf(2), "Second label", identity)
        self.store.rollback(identity)
        entry = self.store.catalog()["additional"][0]
        self.assertEqual(entry["active"], first["active"])
        self.assertEqual(entry["previous"]["label"], "Second label")
        self.assertTrue(entry["restore"])
        self.assertEqual(entry["wait_seconds"], 12)
        self.store.rollback(identity)
        self.assertEqual(self.store.entry(identity)["active"]["filename"], "Different name.elf")

    def test_bad_uploads_and_failed_commit_preserve_current_and_previous(self):
        self.store.upload("primary", "original.elf", elf())
        self.store.upload("primary", "second.elf", elf(2))
        before = self.store.catalog()
        for name, data in [("bad.elf", b"<html>" * 40), ("cut.elf", elf()[:80]), ("../bad.elf", elf(3)), ("x\\bad.elf", elf(3)), ("bad\n.elf", elf(3))]:
            with self.subTest(name=name), self.assertRaises(StoreError):
                self.store.upload("primary", name, data)
            self.assertEqual(before, self.store.catalog())
        with patch("payload_store.atomic_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.store.upload("primary", "third.elf", elf(3))
        self.assertEqual(PayloadStore(self.data).catalog(), before)
        with self.assertRaisesRegex(StoreError, "already active"):
            self.store.upload("primary", "second.elf", elf(2))
        self.assertEqual(before, self.store.catalog())

    def test_reorder_delete_and_same_filename_replacement(self):
        first = self.store.upload("additional", "a.elf", elf())
        second = self.store.upload("additional", "b.elf", elf(2))
        replacement = self.store.upload("additional", "a.elf", elf(3))
        self.assertEqual(replacement["id"], first["id"])
        self.store.reorder([second["id"], first["id"]])
        with self.assertRaises(StoreError):
            self.store.reorder([second["id"], second["id"]])
        self.store.remove(first["id"])
        self.assertEqual([e["id"] for e in self.store.catalog()["additional"]], [second["id"]])
        self.assertFalse(self.store.blob(first["active"]["sha256"]).exists())

    def test_download_snapshot_survives_replace_and_stale_selection_is_rejected(self):
        first = self.store.upload("primary", "a.elf", elf())
        stream, _ = self.store.open_active("primary", first["active"]["sha256"])
        with stream:
            self.store.upload("primary", "b.elf", elf(2))
            self.store.upload("primary", "c.elf", elf(3))
            self.assertEqual(stream.read(), elf())
        with self.assertRaisesRegex(StoreError, "changed"):
            self.store.open_active("primary", first["active"]["sha256"])

    def test_sessions_survive_host_restart_block_duplicate_bytes_and_reset_for_new_loader(self):
        first = self.store.upload("primary", "a.elf", elf())
        duplicate = self.store.upload("additional", "alias.elf", elf())
        sessions = SessionStore(self.data, self.store)
        token = sessions.start("console", True)["token"]
        digest = first["active"]["sha256"]
        sessions.claim("console", token, "primary", digest)
        sessions.finish("console", token, "primary", "failed")
        sessions = SessionStore(self.data, self.store)
        self.assertEqual(sessions.start("console", False)["token"], token)
        for identity in ("primary", duplicate["id"]):
            with self.assertRaisesRegex(StoreError, "Already sent"):
                sessions.claim("console", token, identity, digest)
        with self.assertRaises(StoreError):
            sessions.claim("different-console", token, "primary", digest)
        fresh = sessions.start("console", True)
        self.assertNotEqual(fresh["token"], token)
        sessions.claim("console", fresh["token"], "primary", digest)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.store = PayloadStore(self.data)
        self.sessions = SessionStore(self.data, self.store)
        self.admin = self.server(AdminHandler, ADDON / "admin")
        self.admin.admin_peer = "127.0.0.1"
        self.public = self.server(Handler, ADDON / "www")

    def server(self, handler, directory):
        server = Server(("127.0.0.1", 0), partial(handler, directory=str(directory)))
        server.payloads = self.store
        server.sessions = self.sessions
        server.commit = "test"
        server.upload_slots = threading.BoundedSemaphore(2)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown(); server.server_close(); thread.join(3)
        self.addCleanup(cleanup)
        return server

    def request(self, path, method="GET", data=None, server=None, headers=None, chunked=False):
        server = server or self.admin
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = headers or {}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers.setdefault("Content-Type", "application/json")
        if method == "POST": headers.setdefault("X-Relapse-Request", "1")
        if chunked:
            raw = data
            chunk_size = 37 if len(raw) < 8192 else 8192
            data = (raw[offset:offset + chunk_size] for offset in range(0, len(raw), chunk_size))
        try:
            try:
                conn.request(method, path, data, headers, encode_chunked=chunked)
            except BrokenPipeError:
                # Access checks can reject a stream before the client finishes.
                # Read the rejection already sent by the server.
                pass
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def upload(self, kind="primary", marker=1, name="Original Name.elf", server=None):
        return self.request("/api/upload?" + urlencode({"kind": kind, "filename": name}), "POST", elf(marker), server,
                            {"Content-Type": "application/octet-stream"})

    def raw_upload(self, framing, body):
        with socket.create_connection(("127.0.0.1", self.admin.server_port)) as sock:
            sock.settimeout(5)
            sock.sendall(b"POST /api/upload?kind=primary&filename=stream.elf HTTP/1.1\r\n"
                         b"Host: localhost\r\nContent-Type: application/octet-stream\r\n"
                         b"X-Relapse-Request: 1\r\n" + framing + b"\r\n" + body)
            sock.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(sock)
            response.begin()
            result = response.status, json.loads(response.read())
            response.close()
            return result

    def test_ha_streamed_upload_download_replace_and_json_rollback(self):
        path = "/api/upload?kind=primary&filename=kstuff.elf"
        headers = {"Content-Type": "application/octet-stream"}
        for marker in (1, 2):
            result = self.request(path, "POST", elf(marker), headers=headers, chunked=True)
            self.assertEqual(result[0], 200, result[2])
            entry = self.store.catalog()["primary"]["active"]
            self.assertEqual(entry["filename"], "kstuff.elf")
            self.assertEqual(entry["sha256"], hashlib.sha256(elf(marker)).hexdigest())
            url = "/user-payload/primary/" + entry["sha256"] + ".elf"
            self.assertEqual(self.request(url, server=self.public)[2], elf(marker))
        result = self.request("/api/rollback", "POST", {"id": "primary"}, chunked=True)
        self.assertEqual(result[0], 200, result[2])
        self.assertEqual(self.store.catalog()["primary"]["active"]["sha256"], hashlib.sha256(elf()).hexdigest())

    def test_streamed_requests_keep_access_checks(self):
        path = "/api/upload?kind=primary&filename=kstuff.elf"
        headers = {"Content-Type": "application/octet-stream"}
        self.assertEqual(self.request(path, "POST", elf(), self.public, headers, chunked=True)[0], 403)
        self.assertEqual(self.request(path, "POST", elf(), headers={**headers, "X-Relapse-Request": ""}, chunked=True)[0], 403)
        self.admin.admin_peer = "172.30.32.2"
        self.assertEqual(self.request(path, "POST", elf(), headers={**headers, "X-Forwarded-For": "172.30.32.2"}, chunked=True)[0], 403)
        self.assertIsNone(self.store.catalog()["primary"])

    def test_broken_chunk_framing_preserves_active_and_previous(self):
        self.upload()
        self.upload(marker=2)
        before = self.store.catalog()
        complete_chunk = b"100\r\n" + elf(3) + b"\r\n"
        for body in (b"xyz\r\n", b"-1\r\n", b"+1\r\n", b"0x10\r\n", b"1_0\r\n",
                     b"100\n", b"100\r\nshort", complete_chunk[:-2] + b"xx",
                     complete_chunk, complete_chunk + b"0\r\n", b"1" * 8193 + b"\r\n",
                     complete_chunk + b"0\r\ninvalid-trailer\r\n\r\n"):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.raw_upload(b"Transfer-Encoding: chunked\r\n", body)[0], 400)
                self.assertEqual(self.store.catalog(), before)

    def test_ambiguous_or_unsupported_lengths_preserve_library(self):
        before = self.store.catalog()
        for framing in (b"Transfer-Encoding: chunked\r\nContent-Length: 256\r\n",
                        b"Transfer-Encoding: gzip, chunked\r\n",
                        b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
                        b"Content-Length: 256\r\nContent-Length: 256\r\n",
                        b"Content-Length: +256\r\n", b"Content-Length: 2_56\r\n"):
            with self.subTest(framing=framing):
                self.assertEqual(self.raw_upload(framing, elf())[0], 400)
                self.assertEqual(self.store.catalog(), before)

    def test_chunk_limits_apply_to_total_decoded_upload_and_json(self):
        self.upload()
        before = self.store.catalog()
        with patch("http_api.MAX_PAYLOAD", 256):
            # Exactly the limit is accepted, including chunk extensions/trailers.
            body = b"100;proxy=test\r\n" + elf(2) + b"\r\n0\r\nX-Upload-Note: test\r\n\r\n"
            self.assertEqual(self.raw_upload(b"Transfer-Encoding: Chunked\r\n", body)[0], 200)
            before = self.store.catalog()
            for body in (b"101\r\n", b"100\r\n" + elf(3) + b"\r\n1\r\nx\r\n0\r\n\r\n"):
                self.assertEqual(self.raw_upload(b"Transfer-Encoding: chunked\r\n", body)[0], 413)
                self.assertEqual(self.store.catalog(), before)
        result = self.request("/api/edit", "POST", {"id": "primary", "padding": "x" * 65536}, chunked=True)
        self.assertEqual(result[0], 413)
        self.assertEqual(self.store.catalog(), before)

    def test_empty_to_upload_public_download_replacement_and_rollback(self):
        self.assertIsNone(json.loads(self.request("/api/catalog")[2])["primary"])
        self.assertEqual(self.upload()[0], 200)
        entry = self.store.catalog()["primary"]
        url = "/user-payload/primary/" + entry["active"]["sha256"] + ".elf"
        status, headers, body = self.request(url, server=self.public)
        self.assertEqual((status, body), (200, elf()))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Payload-SHA256"], hashlib.sha256(elf()).hexdigest())
        self.assertEqual(self.upload(marker=2)[0], 200)
        self.assertEqual(self.request(url, server=self.public)[0], 409)
        self.assertEqual(self.request("/api/rollback", "POST", {"id": "primary"})[0], 200)
        self.assertEqual(self.request(url, server=self.public)[2], elf())

    def test_uploads_require_ingress_and_cannot_be_reached_via_ps5_server(self):
        self.assertEqual(self.upload(server=self.public)[0], 403)
        self.assertEqual(self.request("/admin/", server=self.public)[0], 403)
        self.admin.admin_peer = "172.30.32.2"
        self.assertEqual(self.upload()[0], 403)
        self.assertEqual(self.request("/", headers={"X-Forwarded-For": "172.30.32.2"})[0], 403)
        self.assertIsNone(self.store.catalog()["primary"])

    def test_mutations_require_non_form_request_header_and_bad_bodies_do_not_change_library(self):
        before = self.store.catalog()
        self.assertEqual(self.request("/api/remove", "POST", {"id": "primary"}, headers={"X-Relapse-Request": ""})[0], 403)
        self.assertEqual(self.request("/api/upload?kind=primary&filename=bad.elf", "POST", b"bad", headers={"Content-Type": "application/octet-stream"})[0], 400)
        self.assertEqual(self.request("/api/order", "POST", {"ids": [None]})[0], 400)
        self.assertEqual(self.store.catalog(), before)

    def test_interrupted_upload_cannot_replace_active_file(self):
        self.upload()
        before = self.store.catalog()
        with socket.create_connection(("127.0.0.1", self.admin.server_port)) as sock:
            sock.settimeout(5)
            sock.sendall(b"POST /api/upload?kind=primary&filename=cut.elf HTTP/1.0\r\nContent-Length: 1000\r\nContent-Type: application/octet-stream\r\nX-Relapse-Request: 1\r\n\r\n" + elf(2))
            sock.shutdown(socket.SHUT_WR)
            self.assertIn(b"400", sock.recv(1024))
        self.assertEqual(self.store.catalog(), before)

    def test_session_claim_and_result_are_not_a_remote_sender(self):
        self.upload()
        digest = self.store.catalog()["primary"]["active"]["sha256"]
        token = json.loads(self.request("/api/session", "POST", {"fresh": True}, self.public)[2])["token"]
        claim = {"token": token, "id": "primary", "sha256": digest}
        self.assertEqual(self.request("/api/claim", "POST", claim, self.public)[0], 200)
        self.assertEqual(self.request("/api/claim", "POST", claim, self.public)[0], 409)
        self.assertEqual(self.request("/api/result", "POST", {"token": token, "id": "primary", "status": "sent"}, self.public)[0], 200)
        self.assertEqual(self.sessions.sessions["127.0.0.1"]["attempts"]["primary"]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
