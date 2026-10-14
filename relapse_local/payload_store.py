"""Persistent, versioned user payloads. Files are stored, never executed here."""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import threading
import time
import uuid

MAX_PAYLOAD = 64 * 1024 * 1024
MAX_ADDITIONAL = 64


class StoreError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_json(path, value):
    """Commit metadata only after the complete replacement is on disk."""
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_elf(data):
    # Structural validation only: no firmware, vendor or dependency allowlist.
    if not 64 <= len(data) <= MAX_PAYLOAD:
        raise StoreError("Payload must be between 64 bytes and 64 MiB")
    if data[:7] != b"\x7fELF\x02\x01\x01" or struct.unpack_from("<H", data, 18)[0] != 62:
        raise StoreError("Upload a 64-bit little-endian x86-64 ELF payload")
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    if size != 56 or not count or offset < 64 or offset + size * count > len(data):
        raise StoreError("Payload has an invalid ELF program-header table")
    for index in range(count):
        header = offset + size * index
        start = struct.unpack_from("<Q", data, header + 8)[0]
        length = struct.unpack_from("<Q", data, header + 32)[0]
        if start + length > len(data):
            raise StoreError("Payload has a truncated ELF segment")


def filename(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 240
            or value in (".", "..") or any(c in value for c in ("/", "\\"))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise StoreError("Use a filename without folders or control characters")
    return value


def label(value):
    if not isinstance(value, str) or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise StoreError("Display name must be at most 120 characters")
    return value.strip()


class PayloadStore:
    def __init__(self, data):
        self.root = Path(data) / "payloads"
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "catalog.json"
        self.lock = threading.RLock()
        if not self.path.exists():
            atomic_json(self.path, {"revision": 0, "primary": None, "additional": []})
        self.state = json.loads(self.path.read_text())
        for entry in self.entries():
            for version in (entry["active"], entry.get("previous")):
                if version:
                    blob = self.blob(version["sha256"])
                    if not blob.is_file() or hashlib.sha256(blob.read_bytes()).hexdigest() != version["sha256"]:
                        raise StoreError("Stored payload is missing or damaged: " + version["filename"])

    def blob(self, digest):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise StoreError("Invalid payload revision", 404)
        return self.objects / digest

    def entries(self, state=None):
        state = self.state if state is None else state
        return ([state["primary"]] if state["primary"] else []) + state["additional"]

    def entry(self, identity, state=None):
        for entry in self.entries(state):
            if entry["id"] == identity:
                return entry
        raise StoreError("Payload no longer exists. Refresh the list.", 404)

    def catalog(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def commit(self, state):
        state["revision"] = self.state["revision"] + 1
        atomic_json(self.path, state)
        self.state = state
        # Open download descriptors remain valid during a concurrent replacement.
        retained = {v["sha256"] for e in self.entries() for v in (e["active"], e.get("previous")) if v}
        for path in self.objects.iterdir():
            if path.name not in retained:
                try:
                    path.unlink()
                except OSError:
                    pass  # An unused object can be collected on the next edit.

    def upload(self, kind, original_name, data, display_name="", identity=None):
        if kind not in ("primary", "additional"):
            raise StoreError("Choose Primary payload or Additional payloads")
        original_name, display_name = filename(original_name), label(display_name)
        validate_elf(data)
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            state = self.catalog()
            if kind == "primary":
                entry = state["primary"]
                if identity not in (None, "primary"):
                    raise StoreError("Invalid primary payload slot")
            else:
                entry = self.entry(identity, state) if identity else next(
                    (e for e in state["additional"] if e["active"]["filename"] == original_name), None)
                if entry and entry["id"] == "primary":
                    raise StoreError("Primary payload must use its own tab")
                if not entry and len(state["additional"]) >= MAX_ADDITIONAL:
                    raise StoreError("Additional payload limit reached (64)")
            if entry and entry["active"]["sha256"] == digest:
                raise StoreError("This exact file is already active; the previous version was kept", 409)
            active = {"filename": original_name, "label": display_name,
                      "sha256": digest, "bytes": len(data), "uploaded_at": int(time.time())}
            if entry:
                entry["previous"], entry["active"] = entry["active"], active
            else:
                entry = {"id": "primary" if kind == "primary" else uuid.uuid4().hex,
                         "active": active, "previous": None, "restore": False, "wait_seconds": 5}
                if kind == "primary":
                    state["primary"] = entry
                else:
                    state["additional"].append(entry)
            destination = self.blob(digest)
            if not destination.exists():
                fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.objects)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, destination)
                    directory = os.open(self.objects, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            self.commit(state)
            return copy.deepcopy(entry)

    def edit(self, identity, changes):
        if set(changes) - {"label", "restore", "wait_seconds"}:
            raise StoreError("Unknown payload setting")
        with self.lock:
            state = self.catalog()
            entry = self.entry(identity, state)
            if "label" in changes:
                entry["active"]["label"] = label(changes["label"])
            if "restore" in changes:
                if identity == "primary" or type(changes["restore"]) is not bool:
                    raise StoreError("Restore applies only to additional payloads")
                entry["restore"] = changes["restore"]
            if "wait_seconds" in changes:
                value = changes["wait_seconds"]
                if type(value) is not int or not 0 <= value <= 60:
                    raise StoreError("Wait time must be 0–60 seconds")
                entry["wait_seconds"] = value
            self.commit(state)

    def reorder(self, identities):
        with self.lock:
            state = self.catalog()
            current = [e["id"] for e in state["additional"]]
            if (not isinstance(identities, list) or any(not isinstance(i, str) for i in identities)
                    or len(identities) != len(current) or set(identities) != set(current)):
                raise StoreError("Order must contain every additional payload exactly once")
            state["additional"] = [self.entry(i, state) for i in identities]
            self.commit(state)

    def rollback(self, identity):
        with self.lock:
            state = self.catalog()
            entry = self.entry(identity, state)
            if not entry.get("previous"):
                raise StoreError("No previous version is available", 409)
            entry["active"], entry["previous"] = entry["previous"], entry["active"]
            self.commit(state)

    def remove(self, identity):
        with self.lock:
            state = self.catalog()
            self.entry(identity, state)
            if identity == "primary":
                state["primary"] = None
            else:
                state["additional"] = [e for e in state["additional"] if e["id"] != identity]
            self.commit(state)

    def open_active(self, identity, digest):
        with self.lock:
            active = self.entry(identity)["active"]
            if digest != active["sha256"]:
                raise StoreError("Payload changed. Refresh the list before loading.", 409)
            return self.blob(digest).open("rb"), copy.deepcopy(active)


class SessionStore:
    """Persistent send attempts per console IP; not proof that a payload is running."""

    def __init__(self, data, payloads):
        self.path = Path(data) / "sessions.json"
        self.payloads = payloads
        self.lock = threading.RLock()
        self.sessions = json.loads(self.path.read_text()) if self.path.exists() else {}

    def start(self, peer, fresh):
        if type(fresh) is not bool:
            raise StoreError("Invalid loader session")
        with self.lock:
            if fresh or peer not in self.sessions:
                self.sessions[peer] = {"token": uuid.uuid4().hex, "attempts": {}, "started": int(time.time())}
                # Bound storage without expiring a live console's duplicate guard.
                if len(self.sessions) > 64:
                    oldest = min(self.sessions, key=lambda p: self.sessions[p]["started"])
                    if oldest != peer:
                        del self.sessions[oldest]
                atomic_json(self.path, self.sessions)
            return copy.deepcopy(self.sessions[peer])

    def claim(self, peer, token, identity, digest):
        with self.lock, self.payloads.lock:
            session = self.sessions.get(peer)
            if not session or session["token"] != token:
                raise StoreError("Session changed. Reopen Relapse from the guide.", 409)
            active = self.payloads.entry(identity)["active"]
            if active["sha256"] != digest:
                raise StoreError("Payload changed. Refresh the list before loading.", 409)
            attempts = session["attempts"]
            if identity in attempts or digest in [a["sha256"] for a in attempts.values()]:
                raise StoreError("Already sent or attempted in this loader session. Reboot before retrying.", 409)
            attempts[identity] = {"sha256": digest, "filename": active["filename"], "status": "attempted"}
            atomic_json(self.path, self.sessions)

    def finish(self, peer, token, identity, status):
        if status not in ("sent", "failed"):
            raise StoreError("Invalid transfer result")
        with self.lock:
            session = self.sessions.get(peer)
            if not session or session["token"] != token or identity not in session["attempts"]:
                raise StoreError("Unknown payload attempt", 409)
            session["attempts"][identity]["status"] = status
            atomic_json(self.path, self.sessions)
