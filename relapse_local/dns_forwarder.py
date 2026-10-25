"""Loopback-only UDP/TCP adapter for nanoDNS's UDP listener. No other resolver."""

import socket
import socketserver
import struct
import threading
import time

MAX_PACKET = 4096  # nanoDNS 0.4's MAX_DNS_PACKET
TIMEOUT = 3


def failure(packet):
    if len(packet) < 12:
        return b""
    flags = struct.unpack_from("!H", packet, 2)[0]
    # Keep the question and optional EDNS section, but never claim valid data.
    return packet[:2] + struct.pack("!H", 0x8082 | (flags & 0x7910)) + packet[4:]


def exchange(packet, upstream):
    if len(packet) < 12:
        return b""
    if len(packet) > MAX_PACKET:
        return packet[:2] + struct.pack("!HHHHH", 0x8082, 0, 0, 0, 0)
    deadline = time.monotonic() + TIMEOUT
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(upstream)
            sock.send(packet)
            while time.monotonic() < deadline:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                response = sock.recv(MAX_PACKET + 1)
                if (12 <= len(response) <= MAX_PACKET and response[:2] == packet[:2]
                        and response[2] & 0x80
                        and (response[2] & 0x78) == (packet[2] & 0x78)):
                    return response
    except OSError:
        pass
    return failure(packet)


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise EOFError
        data.extend(part)
    return bytes(data)


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        response = exchange(packet, self.server.upstream)
        if response:
            sock.sendto(response, self.client_address)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(TIMEOUT)
        try:
            for _ in range(64):
                size = struct.unpack("!H", read_exact(self.request, 2))[0]
                if not 12 <= size <= MAX_PACKET:
                    return
                response = exchange(read_exact(self.request, size), self.server.upstream)
                self.request.sendall(struct.pack("!H", len(response)) + response)
        except (OSError, EOFError):
            return


class BoundedThreads(socketserver.ThreadingMixIn):
    daemon_threads = True

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class UDPServer(BoundedThreads, socketserver.UDPServer):
    max_packet_size = MAX_PACKET + 1


class TCPServer(BoundedThreads, socketserver.TCPServer):
    allow_reuse_address = True
    request_queue_size = 16


class NanoDNSRelay:
    def __init__(self, address, port=53):
        self.servers, self.threads = [], []
        try:
            udp = UDPServer(("127.0.0.1", 0), UDPHandler)
            self.servers.append(udp)
            self.port = udp.server_address[1]
            self.servers.append(TCPServer(("127.0.0.1", self.port), TCPHandler))
            for server in self.servers:
                server.upstream = (address, port)
                server.slots = threading.BoundedSemaphore(32)
        except BaseException:
            self.close()
            raise

    def start(self):
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            self.threads.append(thread)

    def healthy(self):
        return len(self.threads) == 2 and all(thread.is_alive() for thread in self.threads)

    def close(self):
        for server, thread in zip(self.servers, self.threads):
            if thread.is_alive():
                server.shutdown()
            thread.join(timeout=2)
        for server in self.servers:
            server.server_close()
