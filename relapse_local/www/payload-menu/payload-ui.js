// UI and sequencing only. The exploit and native sender are supplied by the page.
window.createPayloadUI = function (env) {
    const doc = env.document;
    const get = id => doc.getElementById(id);
    let catalog, session, busy = false, cancelled = false, primaryReady = false, rows = [];
    const title = entry => entry.active.label || entry.active.filename;
    const setText = (element, text) => {
        if (element.textContent !== text) element.textContent = text;
    };
    const status = message => { get("payload-status").textContent = message; };
    async function api(path, data) {
        const response = await env.fetch("/api/" + path, data === undefined ? {cache: "no-store"} : {
            method: "POST", headers: {"Content-Type": "application/json", "X-Relapse-Request": "1"},
            body: JSON.stringify(data)
        });
        const value = await response.json();
        if (!response.ok) throw new Error(value.error || "Host request failed");
        return value;
    }
    function attempted(entry) {
        return session && (session.attempts[entry.id] || Object.keys(session.attempts).some(
            id => session.attempts[id].sha256 === entry.active.sha256));
    }
    function showPanel(additional) {
        get("payload-home").hidden = additional;
        get("payload-additional").hidden = !additional;
        layoutRows();
        (additional ? get("payload-back") : get("primary-button")).focus();
        reportDisplay();
    }
    function layoutRows() {
        // Unlike display:none, the staged panel has a render tree even while
        // hidden. Lay out every text element before the exploit, as upstream does.
        void get("payload-additional").offsetHeight;
        for (let i = 0; i < rows.length; i++) {
            const element = rows[i].element;
            void element.offsetHeight;
            for (let j = 0; j < element.children.length; j++) void element.children[j].offsetHeight;
        }
    }
    function reportDisplay() {
        if (get("payload-additional").hidden || !env.reportDisplay) return;
        try { env.reportDisplay({expected: catalog.additional.length, built: rows.length}); }
        catch (_) { /* Display diagnostics must never interrupt payload loading. */ }
    }
    function controls() {
        get("primary-button").disabled = busy || !session || !catalog.primary || !!attempted(catalog.primary);
        get("primary-ready").hidden = !(session && session.attempts.primary) || primaryReady;
        get("primary-ready").disabled = busy;
        get("payload-refresh").disabled = busy;
        get("restore-button").disabled = busy || !primaryReady || !catalog.additional.some(e => e.restore && !attempted(e));
        get("cancel-restore").hidden = !busy;
        for (let i = 0; i < rows.length; i++) {
            const row = rows[i], entry = row.entry;
            row.button.disabled = busy || !primaryReady || !!attempted(entry);
            row.element.className = "payload-row" + (row.button.disabled ? " unavailable" : "");
            const record = session && session.attempts[entry.id];
            setText(row.state, record ? record.status : attempted(entry) ? "Already attempted" : "Load");
        }
    }
    function render() {
        setText(get("primary-name"), "Primary payload");
        setText(get("primary-file"), catalog.primary ? title(catalog.primary) : "Upload in Home Assistant");
        const list = get("payload-list");
        const previous = rows;
        rows = [];
        for (let i = 0; i < catalog.additional.length; i++) {
            const entry = catalog.additional[i];
            const signature = JSON.stringify([entry.active.filename, entry.active.label, entry.restore]);
            let row = previous.find(r => r.entry.id === entry.id && r.signature === signature);
            if (row) {
                // Refresh must not discard the text tree prepared before Relapse.
                row.entry = entry;
            } else {
                const element = doc.createElement("div");
                element.className = "payload-row";
                const button = doc.createElement("button");
                button.type = "button";
                button.className = "payload-load";
                button.dataset.payload = entry.id;
                // Keep native buttons text-only; render metadata in normal elements.
                button.textContent = title(entry);
                element.appendChild(button);
                if (entry.active.label) {
                    const detail = doc.createElement("small");
                    detail.textContent = entry.active.filename;
                    element.appendChild(detail);
                }
                if (entry.restore) {
                    const restore = doc.createElement("span");
                    restore.className = "restore-badge";
                    restore.textContent = "Restore";
                    element.appendChild(restore);
                }
                const state = doc.createElement("span"); state.className = "transfer-state";
                element.appendChild(state);
                row = {entry: entry, signature: signature, element: element, button: button, state: state};
                element.addEventListener("click", () => {
                    if (!button.disabled) return execute([row.entry], false);
                });
            }
            rows.push(row);
            if (list.children[i] !== row.element) list.insertBefore(row.element, list.children[i] || null);
        }
        while (list.children.length > rows.length) list.removeChild(list.lastChild);
        get("payload-empty").hidden = !!catalog.additional.length;
        get("payload-count").hidden = !catalog.additional.length;
        setText(get("payload-count"), catalog.additional.length + (catalog.additional.length === 1 ? " payload" : " payloads"));
        controls();
        layoutRows();
    }
    async function execute(entries, restoring) {
        if (busy || !session) return;
        if (entries.some(e => e.id !== "primary") && !primaryReady) {
            status("Confirm primary startup first."); return;
        }
        busy = true; cancelled = false; controls();
        let sent = 0;
        try {
            for (let i = 0; i < entries.length; i++) {
                const entry = entries[i];
                if (cancelled) break;
                if (attempted(entry)) continue;
                status((restoring ? "Restoring " + (i + 1) + "/" + entries.length + ": " : "Sending ") + title(entry));
                await env.send(entry);
                session.attempts[entry.id] = {sha256: entry.active.sha256, status: "sent"};
                sent++;
                if (restoring && i < entries.length - 1 && entry.wait_seconds) {
                    for (let seconds = entry.wait_seconds; seconds > 0 && !cancelled; seconds--) {
                        status(title(entry) + ": sent. Next payload in " + seconds + "s…");
                        await env.sleep(1000);
                    }
                }
            }
            status(cancelled ? "Restore stopped." :
                entries[0] && entries[0].id === "primary" ? "Sent. Wait for the PS5 startup notification, then confirm below." :
                sent + (sent === 1 ? " payload file sent." : " payload files sent."));
        } catch (error) {
            status("Stopped: " + error.message);
        } finally {
            busy = false;
            controls();
            reportDisplay();
        }
    }
    get("primary-button").addEventListener("click", () => execute(catalog.primary ? [catalog.primary] : [], false));
    get("additional-button").addEventListener("click", () => showPanel(true));
    get("payload-back").addEventListener("click", () => showPanel(false));
    get("primary-ready").addEventListener("click", () => {
        primaryReady = true; controls(); status("Primary ready.");
    });
    get("restore-button").addEventListener("click", () => execute(catalog.additional.filter(e => e.restore), true));
    get("cancel-restore").addEventListener("click", () => { cancelled = true; status("Stopping after the current transfer…"); });
    get("payload-refresh").addEventListener("click", async () => {
        if (busy) return;
        try { catalog = await api("catalog"); render(); status("Payload list refreshed."); reportDisplay(); }
        catch (error) { status(error.message); }
    });
    return {
        async prepare() {
            catalog = await api("catalog");
            render();
            if (!catalog.primary) throw new Error("No primary payload uploaded. Upload one in Home Assistant.");
            void get("relapse-payload-menu").offsetHeight;
        },
        async ready(fresh) {
            session = await api("session", {fresh: fresh});
            primaryReady = false;
            controls();
            get("console").style.display = "none";
            get("relapse-payload-menu").classList.add("ready");
            status(session.attempts.primary ? "Primary previously attempted. Confirm it is running." : "Ready. Load primary payload.");
            get("primary-button").focus();
        },
        async claim(entry) {
            await api("claim", {token: session.token, id: entry.id, sha256: entry.active.sha256});
            session.attempts[entry.id] = {sha256: entry.active.sha256, status: "attempted"};
        },
        async result(entry, result) {
            session.attempts[entry.id].status = result;
            await api("result", {token: session.token, id: entry.id, status: result});
        }
    };
};
