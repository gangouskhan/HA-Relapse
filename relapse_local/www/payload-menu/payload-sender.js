// Native transport adapter. No firmware/vendor policy; no payload execution here.
window.createLocalPayloadSender = function (io) {
    let busy = false;
    const attempted = new Set();
    const maximum = 64 * 1024 * 1024;
    return async function (entry) {
        if (busy) throw new Error("A payload is already being sent. Please wait.");
        if (!entry || !/^(primary|[a-f0-9]{32})$/.test(entry.id) ||
            !entry.active || !/^[a-f0-9]{64}$/.test(entry.active.sha256))
            throw new Error("Invalid payload selection");
        const digest = entry.active.sha256;
        if (attempted.has(entry.id) || attempted.has(digest))
            throw new Error("This payload was already sent or attempted. Reboot before loading a second copy.");
        busy = true;
        let socket = null, claimed = false, result = "failed";
        try {
            const response = await io.fetch("/user-payload/" + entry.id + "/" + digest + ".elf");
            if (!response.ok) throw new Error("Payload download failed: HTTP " + response.status + ". Refresh the list.");
            const declared = Number(response.headers.get("Content-Length"));
            if (declared > maximum) throw new Error("Payload exceeds the 64 MiB limit");
            if (response.headers.get("X-Payload-SHA256") !== digest)
                throw new Error("Payload revision changed; refresh the list");
            const data = new Uint8Array(await response.arrayBuffer());
            if (data.length < 64 || data.length > maximum || data.length !== entry.active.bytes || declared !== data.length)
                throw new Error("Payload download is incomplete or its size changed");
            if (data[0] !== 0x7f || data[1] !== 0x45 || data[2] !== 0x4c || data[3] !== 0x46)
                throw new Error("The downloaded file is not an ELF payload");
            const buffer = io.buffer;
            if (buffer.BYTES_PER_ELEMENT !== 1 || !buffer.length) throw new Error("Invalid transfer buffer");
            const chunkSize = Math.min(buffer.length, 1024 * 1024);
            socket = await io.connect();
            // Record before the first byte: a failed/partial transfer may still run.
            await io.claim(entry);
            claimed = true;
            attempted.add(entry.id);
            attempted.add(digest);
            let total = 0;
            while (total < data.length) {
                const size = Math.min(chunkSize, data.length - total);
                buffer.set(data.subarray(total, total + size), 0);
                let offset = 0;
                while (offset < size) {
                    const written = await io.write(socket, offset, size - offset);
                    if (!Number.isInteger(written) || written <= 0 || written > size - offset)
                        throw new Error("Payload write failed after " + (total + offset) + " bytes. Reboot before retrying.");
                    offset += written;
                }
                total += size;
            }
            result = "sent";
            return total;
        } finally {
            try {
                if (socket !== null) await io.close(socket);
            } finally {
                if (claimed) {
                    try { await io.result(entry, result); } catch (_) { /* Durable attempt still blocks a duplicate. */ }
                }
                busy = false;
            }
        }
    };
};
