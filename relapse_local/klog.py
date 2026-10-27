"""Bounded, receive-only klog recordings kept in the app's persistent data."""
import base64
import copy
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from payload_store import StoreError, atomic_json

MAX_RECORDING = 64 * 1024 * 1024
MAX_STORAGE = 512 * 1024 * 1024
MAX_RECORDS = 200
READ_SIZE = 64 * 1024
ACTIVE = {"connecting", "connected", "stopping"}
PRIVATE = tuple(ipaddress.IPv4Network(value) for value in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def destination(host, port):
    try:
        address = ipaddress.IPv4Address(host) if isinstance(host, str) else None
    except ipaddress.AddressValueError:
        address = None
    if address is None or not any(address in network for network in PRIVATE):
        raise StoreError("Enter the PS5's private LAN IPv4 address")
    if type(port) is not int or not 1 <= port <= 65535:
        raise StoreError("Port must be between 1 and 65535")
    return str(address), port


class Snapshot(io.IOBase):
    """A download ends at its initial size even while recording continues."""
    def __init__(self, stream, size):
        self.stream, self.remaining = stream, size

    def read(self, size=-1):
        data = self.stream.read(self.remaining if size < 0 else min(size, self.remaining))
        self.remaining -= len(data)
        return data

    def close(self):
        self.stream.close()
        super().close()


class KlogStore:
    def __init__(self, data, connector=None, max_recording=MAX_RECORDING,
                 max_storage=MAX_STORAGE, max_records=MAX_RECORDS):
        self.root = Path(data) / "klog"
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.lock = threading.RLock()
        self.connector = connector or socket.create_connection
        self.max_recording, self.max_storage, self.max_records = max_recording, max_storage, max_records
        self.records = {}
        self.worker = self.connection = self.current = None
        self.cancel = threading.Event()
        self.closed = False
        self.preferences = {"host": "", "port": 3232}
        try:
            saved = json.loads((self.root / "connection.json").read_text())
            host, port = destination(saved["host"], saved["port"])
            self.preferences = {"host": host, "port": port}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        for path in self.root.glob("*.json"):
            if not re.fullmatch(r"[a-f0-9]{32}", path.stem) or path.is_symlink():
                continue
            try:
                record = json.loads(path.read_text())
                if (record["id"] != path.stem or not isinstance(record["started"], (int, float))
                        or not isinstance(record["state"], str)):
                    continue
                logfile = self._path(record["id"], ".log")
                if not logfile.is_file() or logfile.is_symlink():
                    continue
                record["bytes"] = logfile.stat().st_size
                if record["state"] in ACTIVE:
                    record.update(state="interrupted", ended=time.time(), message="Host restarted")
                    atomic_json(path, record)
                self.records[record["id"]] = record
            except (OSError, ValueError, KeyError, TypeError):
                continue
        # Recover raw data if a crash interrupted the first metadata commit.
        for path in self.root.glob("*.log"):
            if re.fullmatch(r"[a-f0-9]{32}", path.stem) and not path.is_symlink() and path.stem not in self.records:
                record = {"id": path.stem, "host": "", "port": 3232, "started": path.stat().st_mtime,
                          "ended": path.stat().st_mtime, "bytes": path.stat().st_size,
                          "state": "interrupted", "message": "Recovered after host restart"}
                self.records[path.stem] = record
                atomic_json(self._path(path.stem, ".json"), record)
        self.total = sum(record["bytes"] for record in self.records.values())

    def _path(self, identity, suffix):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise StoreError("Unknown recording", 404)
        return self.root / (identity + suffix)

    def _record(self, identity):
        self._path(identity, ".log")
        if identity not in self.records:
            raise StoreError("Unknown recording", 404)
        return self.records[identity]

    def _save(self, record):
        atomic_json(self._path(record["id"], ".json"), record)

    def catalog(self):
        with self.lock:
            records = sorted(self.records.values(), key=lambda record: record["started"], reverse=True)
            active = self.records.get(self.current)
            return copy.deepcopy({"active": active if active and active["state"] in ACTIVE else None,
                                  "records": records, "connection": self.preferences, "bytes": self.total,
                                  "max_storage": self.max_storage, "max_recording": self.max_recording,
                                  "max_records": self.max_records})

    def start(self, host, port=3232):
        host, port = destination(host, port)
        with self.lock:
            if self.closed:
                raise StoreError("Klog service is stopping", 503)
            if self.worker is not None and self.worker.is_alive():
                raise StoreError("Stop the current connection first", 409)
            if self.total >= self.max_storage or len(self.records) >= self.max_records:
                raise StoreError("Recording storage is full. Delete old recordings first.", 409)
            identity = uuid.uuid4().hex
            record = {"id": identity, "host": host, "port": port, "started": time.time(),
                      "ended": None, "bytes": 0, "state": "connecting", "message": "Connecting"}
            descriptor = os.open(self._path(identity, ".log"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            try:
                self._save(record)
                atomic_json(self.root / "connection.json", {"host": host, "port": port})
            except OSError:
                self._path(identity, ".log").unlink(missing_ok=True)
                self._path(identity, ".json").unlink(missing_ok=True)
                raise
            self.preferences = {"host": host, "port": port}
            self.records[identity], self.current = record, identity
            self.cancel = threading.Event()
            self.worker = threading.Thread(target=self._receive, args=(identity, self.cancel), daemon=True)
            self.worker.start()
            return copy.deepcopy(record)

    def _receive(self, identity, cancel):
        state, message = "stopped", "Stopped"
        connection = None
        try:
            record = self.records[identity]
            connection = self.connector((record["host"], record["port"]), timeout=5)
            connection.settimeout(.5)
            with self.lock:
                self.connection = connection
                if not cancel.is_set():
                    record.update(state="connected", message="Connected · recording on HA")
                    self._save(record)
            saved_at = time.monotonic()
            with self._path(identity, ".log").open("ab", buffering=0) as output:
                while not cancel.is_set():
                    try:
                        data = connection.recv(16 * 1024)
                    except socket.timeout:
                        continue
                    if not data:
                        state, message = "disconnected", "PS5 disconnected"
                        break
                    with self.lock:
                        room = min(self.max_recording - record["bytes"], self.max_storage - self.total)
                        piece = data[:max(0, room)]
                        if piece:
                            # FileIO writes can be short; account only for committed bytes.
                            while piece:
                                written = output.write(piece)
                                if not written:
                                    raise OSError("Recording write failed")
                                record["bytes"] += written
                                self.total += written
                                piece = piece[written:]
                        if len(data) >= room:
                            state, message = "limit", "Recording limit reached. Save or delete recordings."
                            break
                        if time.monotonic() - saved_at >= 1:
                            self._save(record)
                            saved_at = time.monotonic()
        except OSError as error:
            if not cancel.is_set():
                state = "error"
                if connection is None:
                    message = "Could not connect. Start the PS5 klog payload and check its IP and port."
                else:
                    message = "Connection or recording interrupted: " + str(error)[:160]
        finally:
            if connection is not None:
                connection.close()
            with self.lock:
                self.connection = None
                record = self.records[identity]
                record.update(state="stopped" if cancel.is_set() else state,
                              message="Stopped" if cancel.is_set() else message, ended=time.time())
                try:
                    self._save(record)
                except OSError:
                    record.update(state="error", message="Storage error. Available bytes remain in this recording.")

    def stop(self):
        with self.lock:
            if self.worker is None or not self.worker.is_alive():
                return
            self.cancel.set()
            record = self.records[self.current]
            record.update(state="stopping", message="Stopping")
            if self.connection is not None:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def open_recording(self, identity):
        with self.lock:
            record = copy.deepcopy(self._record(identity))
            descriptor = os.open(self._path(identity, ".log"), os.O_RDONLY | os.O_NOFOLLOW)
            stream = os.fdopen(descriptor, "rb")
            length = os.fstat(descriptor).st_size
            return Snapshot(stream, length), length, record

    def read(self, identity, offset=0, limit=READ_SIZE):
        if type(offset) is not int or offset < 0:
            raise StoreError("Invalid log position")
        if type(limit) is not int or not 1 <= limit <= READ_SIZE:
            raise StoreError("Invalid log read size")
        with self.lock:
            record = copy.deepcopy(self._record(identity))
            descriptor = os.open(self._path(identity, ".log"), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                size = os.fstat(descriptor).st_size
                if offset > size:
                    raise StoreError("Log position is beyond the recording")
                stream.seek(offset)
                data = stream.read(limit)
            return {"record": record, "offset": offset, "next": offset + len(data), "bytes": size,
                    "data": base64.b64encode(data).decode("ascii")}

    def delete(self, identity):
        with self.lock:
            record = self._record(identity)
            if identity == self.current and self.worker is not None and self.worker.is_alive():
                raise StoreError("Stop the recording before deleting it", 409)
            self._path(identity, ".json").unlink(missing_ok=True)
            try:
                self._path(identity, ".log").unlink(missing_ok=True)
            except OSError:
                self._save(record)
                raise
            self.total -= record["bytes"]
            del self.records[identity]

    def close(self):
        with self.lock:
            self.closed = True
        self.stop()
        if self.worker is not None:
            self.worker.join(timeout=6)
