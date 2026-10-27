"use strict";
(() => {
    const get = id => document.getElementById("klog-" + id);
    const activeStates = new Set(["connecting", "connected", "stopping"]);
    const rows = new Map();
    let catalog, selected = "", live = "", offset = 0, text = "", decoder = new TextDecoder();
    let visible = false, busy = false, polling = false, timer, preferencesLoaded = false, needRead = false;
    let actionError = "", generation = 0;
    let following = true, scrollPosition = 0;
    let selectingText = false, displayed = get("output").textContent, viewState = "";
    const atBottom = () => get("output").scrollHeight - get("output").clientHeight - get("output").scrollTop <= 4;
    const size = bytes => bytes < 1024 ? bytes + " B" : bytes < 1024 * 1024 ? (bytes / 1024).toFixed(1) + " KiB" : (bytes / 1024 / 1024).toFixed(1) + " MiB";
    const date = record => new Date(record.started * 1000).toLocaleString();
    const download = id => "api/klog/download?id=" + encodeURIComponent(id);
    function node(tag, value, className) {
        const result = document.createElement(tag);
        if (value !== undefined) result.textContent = value;
        if (className) result.className = className;
        return result;
    }
    async function api(path, data) {
        const response = await fetch("api/klog/" + path, data === undefined ? {cache:"no-store"} : {
            method:"POST", headers:{"Content-Type":"application/json", "X-Relapse-Request":"1"}, body:JSON.stringify(data)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Klog request failed");
        return result;
    }
    function report(message, error = false) {
        get("status").textContent = message;
        get("status").classList.toggle("error", error);
    }
    function hasLogSelection() {
        const selection = window.getSelection();
        return selection && !selection.isCollapsed && selection.rangeCount > 0 &&
            selection.getRangeAt(0).intersectsNode(get("output"));
    }
    function paintLog() {
        if (!selected || selectingText || hasLogSelection()) return;
        const value = text || (activeStates.has(viewState) ? "Waiting for output…" : "No output received.");
        if (value === displayed) return;
        const output = get("output"), shown = output.clientHeight > 0;
        if (shown) {following = atBottom(); scrollPosition = output.scrollTop;}
        const current = output.firstChild;
        if (current && current.nodeType === 3 && output.childNodes.length === 1) {
            // Appending preserves existing ranges; replacing textContent destroys them.
            if (value.startsWith(displayed)) current.appendData(value.slice(displayed.length));
            else current.replaceData(0, current.length, value);
        } else {
            output.replaceChildren(document.createTextNode(value));
        }
        displayed = value;
        if (shown) {
            output.scrollTop = following ? output.scrollHeight : scrollPosition;
            scrollPosition = output.scrollTop;
        }
    }
    function select(id, followLive = false) {
        selected = id; live = followLive ? id : ""; offset = 0; text = ""; decoder = new TextDecoder(); needRead = true; generation++;
        following = true; scrollPosition = 0;
        selectingText = false; viewState = "connecting"; displayed = "Waiting for output…";
        get("output").textContent = displayed;
        get("position").textContent = "";
        get("more").hidden = true;
        render(); schedule(0);
    }
    function render() {
        if (!catalog) return;
        const active = catalog.active;
        if (!preferencesLoaded) {
            if (!get("host").value) get("host").value = catalog.connection.host;
            get("port").value = catalog.connection.port;
            preferencesLoaded = true;
        }
        get("start").disabled = busy || !!active;
        get("stop").disabled = busy || !active || active.state === "stopping";
        get("host").disabled = get("port").disabled = busy || !!active;
        get("refresh").disabled = busy;
        const record = catalog.records.find(item => item.id === selected);
        const status = active || record;
        report(actionError || (status ? status.message : "Not connected"), !!actionError || !!status && ["error", "limit", "interrupted"].includes(status.state));
        get("storage").textContent = size(catalog.bytes) + " / " + size(catalog.max_storage) + " · " + catalog.records.length + " / " + catalog.max_records + " recordings · " + size(catalog.max_recording) + " per recording";
        get("save").hidden = !record;
        if (record) {
            get("save").href = download(record.id);
            get("view-title").textContent = (activeStates.has(record.state) ? "Live · " : "Recording · ") + date(record);
        } else {
            get("view-title").textContent = "Live output";
            if (displayed !== "Connect to start a recording.") {
                displayed = "Connect to start a recording.";
                get("output").textContent = displayed;
            }
            get("position").textContent = "";
        }
        const ids = new Set(catalog.records.map(item => item.id));
        for (const [id, row] of rows) if (!ids.has(id)) {row.element.remove(); rows.delete(id);}
        get("recordings").querySelector(".empty-state")?.remove();
        if (!catalog.records.length) get("recordings").append(node("div", "No recordings", "empty-state"));
        catalog.records.forEach((record, index) => {
            let row = rows.get(record.id);
            if (!row) {
                row = {element:node("article", undefined, "klog-record"), title:node("strong"), details:node("span", "", "muted")};
                const heading = node("div", undefined, "klog-record-info"); heading.append(row.title, row.details);
                const actions = node("div", undefined, "actions");
                row.view = node("button", "View"); row.view.type = "button";
                row.view.addEventListener("click", () => select(record.id, record.id === catalog.active?.id));
                const save = node("a", "Save to device", "button-link"); save.href = download(record.id);
                row.remove = node("button", "Delete", "danger"); row.remove.type = "button";
                row.remove.addEventListener("click", () => {
                    if (confirm("Delete recording from " + date(record) + " from HA?")) action(async () => {
                        await api("delete", {id:record.id});
                        if (selected === record.id) {selected = ""; live = ""; text = ""; offset = 0; generation++;}
                    });
                });
                actions.append(row.view, save, row.remove); row.element.append(heading, actions); rows.set(record.id, row);
            }
            row.title.textContent = date(record);
            row.details.textContent = (record.host ? record.host + ":" + record.port + " · " : "") + size(record.bytes) + " · " + record.state;
            row.view.textContent = selected === record.id ? "Viewing" : "View";
            row.view.disabled = busy || selected === record.id;
            row.remove.disabled = busy || activeStates.has(record.state);
            row.element.classList.toggle("selected", selected === record.id);
            // Keep existing nodes and focus when only byte counts change.
            const position = get("recordings").children[index];
            if (position !== row.element) get("recordings").insertBefore(row.element, position || null);
        });
    }
    async function read() {
        if (!selected) return;
        const identity = selected, readingGeneration = generation;
        for (let batch = 0; batch < 4; batch++) {
            const result = await api("read?id=" + encodeURIComponent(identity) + "&offset=" + offset);
            if (selected !== identity || generation !== readingGeneration) return;
            const bytes = Uint8Array.from(atob(result.data), value => value.charCodeAt(0));
            text += decoder.decode(bytes, {stream:true}); offset = result.next;
            if (offset === result.bytes && !activeStates.has(result.record.state)) text += decoder.decode();
            const clipped = text.length > 200000;
            if (clipped) text = text.slice(-200000);
            viewState = result.record.state;
            paintLog();
            get("position").textContent = size(offset) + " / " + size(result.bytes) + " read · Viewer keeps recent text; Save downloads the full recording.";
            get("more").hidden = offset >= result.bytes;
            if (offset === result.bytes && !activeStates.has(result.record.state) && live === identity) live = "";
            if (offset >= result.bytes || live !== identity) break;
        }
    }
    function schedule(delay = 1000) {
        clearTimeout(timer);
        if (visible) timer = setTimeout(poll, delay);
    }
    async function poll() {
        if (!visible || polling || busy) {schedule(); return;}
        polling = true;
        try {
            catalog = await api("status");
            if (selected && !catalog.records.some(record => record.id === selected)) {
                selected = ""; live = ""; offset = 0; text = ""; generation++;
            }
            if (!selected && catalog.active) select(catalog.active.id, true);
            render();
            if (needRead || selected && selected === live) {needRead = false; await read();}
        } catch (error) {report(error.message, true);}
        finally {polling = false; schedule();}
    }
    async function action(work) {
        if (busy) return;
        busy = true; actionError = ""; render();
        try {await work();}
        catch (error) {actionError = error.message; report(actionError, true);}
        finally {busy = false; schedule(0);}
    }
    get("output").addEventListener("scroll", () => {
        if (!visible) return;
        following = atBottom(); scrollPosition = get("output").scrollTop;
    }, {passive:true});
    get("output").addEventListener("pointerdown", () => {selectingText = true;});
    const finishSelection = () => {selectingText = false; if (visible) paintLog();};
    document.addEventListener("pointerup", finishSelection);
    document.addEventListener("pointercancel", finishSelection);
    window.addEventListener("blur", finishSelection);
    document.addEventListener("selectionchange", () => {
        if (visible && !selectingText && !hasLogSelection()) paintLog();
    });
    document.addEventListener("relapse:tab", event => {
        visible = event.detail === "klog";
        if (visible) get("output").scrollTop = following ? get("output").scrollHeight : scrollPosition;
        schedule(0);
    });
    get("connect").addEventListener("submit", event => {
        event.preventDefault();
        const host = get("host").value.trim(), port = Number(get("port").value);
        action(async () => {const result = await api("connect", {host, port}); select(result.record.id, true);});
    });
    get("stop").addEventListener("click", () => action(() => api("stop", {})));
    get("refresh").addEventListener("click", () => schedule(0));
    get("more").addEventListener("click", () => {needRead = true; schedule(0);});
})();
