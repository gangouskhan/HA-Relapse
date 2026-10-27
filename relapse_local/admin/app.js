"use strict";
(() => {
    const get = id => document.getElementById(id);
    let catalog, busy = false;
    const title = entry => entry.active.label || entry.active.filename;
    const size = bytes => (bytes / 1024 / 1024).toFixed(2) + " MiB";
    function el(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function notice(message, error = false) {
        get("notice").textContent = message;
        get("notice").className = "notice" + (error ? " error" : "");
        get("notice").hidden = !message;
    }
    function setBusy(value) {
        busy = value;
        for (const node of document.querySelectorAll("#primary-panel button,#primary-panel input,#additional-panel button,#additional-panel input")) {
            if (node.getAttribute("role") !== "tab") node.disabled = value || node.dataset.unavailable === "true";
        }
    }
    async function api(path, data) {
        const response = await fetch("api/" + path, data === undefined ? {cache: "no-store"} : {
            method: "POST", headers: {"Content-Type": "application/json", "X-Relapse-Request": "1"},
            body: JSON.stringify(data)
        });
        const value = await response.json();
        if (!response.ok) throw new Error(value.error || "Request failed");
        return value;
    }
    async function refresh() {
        catalog = await api("catalog");
        render();
    }
    async function action(work, message) {
        if (busy) return;
        setBusy(true); notice("");
        try { await work(); await refresh(); notice(message); }
        catch (error) {
            notice(error.message, true);
            try { await refresh(); } catch (_) { /* Preserve the original error. */ }
        } finally { setBusy(false); get("upload-progress").hidden = true; }
    }
    function upload(file, kind, label = "", id) {
        if (!file || file.size < 64 || file.size > 64 * 1024 * 1024)
            return Promise.reject(new Error("Choose an ELF payload between 64 bytes and 64 MiB."));
        const query = new URLSearchParams({kind, filename: file.name, label});
        if (id) query.set("id", id);
        get("upload-progress").hidden = false;
        get("progress-label").textContent = "Uploading " + file.name;
        get("progress").value = 0;
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "api/upload?" + query);
            xhr.timeout = 300000;
            xhr.setRequestHeader("Content-Type", "application/octet-stream");
            xhr.setRequestHeader("X-Relapse-Request", "1");
            xhr.upload.onprogress = event => {
                if (event.lengthComputable) get("progress").value = event.loaded / event.total * 100;
            };
            xhr.onload = () => {
                let result;
                try { result = JSON.parse(xhr.responseText); }
                catch (_) { reject(new Error("Upload response was unreadable. Refresh the library before retrying.")); return; }
                if (xhr.status >= 200 && xhr.status < 300) resolve(result);
                else reject(new Error(result.error || "Upload failed"));
            };
            xhr.onerror = xhr.ontimeout = () => reject(new Error("Upload connection interrupted. Refresh to check the active version."));
            xhr.send(file);
        });
    }
    function button(text, handler, unavailable = false, className = "") {
        const node = el("button", text, className);
        node.type = "button";
        node.disabled = unavailable;
        node.dataset.unavailable = String(unavailable);
        node.addEventListener("click", handler);
        return node;
    }
    function empty(title, description) {
        const node = el("div", undefined, "empty-state");
        node.append(el("div", "+", "empty-symbol"), el("h3", title), el("p", description));
        return node;
    }
    function payloadCard(entry, primary, index) {
        const card = el("article", undefined, "payload-card");
        const header = el("div", undefined, "card-head");
        const heading = el("div");
        heading.append(el("div", primary ? "CURRENT PRIMARY" : "PAYLOAD " + String(index + 1).padStart(2, "0"), "step"), el("h3", title(entry)));
        header.append(heading, el("span", "Active file", "badge"));
        const meta = el("div", undefined, "metadata");
        meta.append(el("span", size(entry.active.bytes)), el("span", new Date(entry.active.uploaded_at * 1000).toLocaleDateString()));
        card.append(header, el("div", entry.active.filename, "filename"), meta);
        const edit = el("form", undefined, "edit-form");
        const label = el("label", "Display name (optional)");
        const input = el("input"); input.type = "text"; input.maxLength = 120;
        input.value = entry.active.label; input.placeholder = entry.active.filename;
        label.appendChild(input);
        const save = el("button", "Save name"); save.type = "submit";
        edit.append(label, save);
        edit.addEventListener("submit", event => {event.preventDefault(); action(() => api("edit", {id: entry.id, changes: {label: input.value}}), "Name saved.");});
        card.append(edit);
        if (!primary) {
            const settings = el("div", undefined, "restore-settings");
            const checkLabel = el("label", "", "checkbox-label");
            const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.checked = entry.restore;
            checkbox.addEventListener("change", () => action(() => api("edit", {id: entry.id, changes: {restore: checkbox.checked}}), "Restore selection saved."));
            checkLabel.append(checkbox, el("span", "Include in restore"));
            const delayLabel = el("label", "Wait after sending", "delay-label");
            const delay = el("input"); delay.type = "number"; delay.min = "0"; delay.max = "60"; delay.value = entry.wait_seconds;
            delay.addEventListener("change", () => action(() => api("edit", {id: entry.id, changes: {wait_seconds: Number(delay.value)}}), "Restore wait time saved."));
            delayLabel.append(delay, el("span", "seconds"));
            settings.append(checkLabel, delayLabel); card.append(settings);
        }
        const actions = el("div", undefined, "actions");
        if (!primary) {
            const replacement = el("input"); replacement.type = "file"; replacement.hidden = true;
            replacement.setAttribute("aria-label", "Replace " + title(entry));
            replacement.addEventListener("change", () => {
                const file = replacement.files[0];
                if (file) action(() => upload(file, "additional", entry.active.label, entry.id), "File replaced.");
            });
            card.append(replacement);
            actions.append(button("Replace file", () => replacement.click()));
            for (const [text, shift] of [["Move up", -1], ["Move down", 1]]) {
                actions.append(button(text, () => action(() => {
                    const ids = catalog.additional.map(e => e.id);
                    [ids[index], ids[index + shift]] = [ids[index + shift], ids[index]];
                    return api("order", {ids});
                }, "Restore order saved."), index + shift < 0 || index + shift >= catalog.additional.length));
            }
        }
        actions.append(button("Roll back", () => action(() => api("rollback", {id: entry.id}), "Rolled back. Reboot the PS5 before reloading an active payload."), !entry.previous));
        actions.append(button("Remove", () => {
            if (confirm("Remove " + title(entry) + " and its previous version from this library?"))
                action(() => api("remove", {id: entry.id}), "Payload removed from the library.");
        }, false, "danger"));
        card.append(actions);
        const previous = el("div", undefined, "previous");
        previous.append(el("strong", "Previous version"), el("span", entry.previous ?
            (entry.previous.label || entry.previous.filename) + " · " + size(entry.previous.bytes) : "None"));
        card.append(previous);
        return card;
    }
    function render() {
        get("version").textContent = catalog.version;
        get("additional-count").textContent = catalog.additional.length;
        get("primary-upload-title").textContent = catalog.primary ? "Replace file" : "Upload";
        get("primary-current").textContent = "";
        get("primary-current").append(catalog.primary ? payloadCard(catalog.primary, true, 0) :
            empty("No primary payload", "Upload an ELF file."));
        get("additional-list").textContent = "";
        if (!catalog.additional.length) get("additional-list").append(empty("No additional payloads", "Upload ELF files."));
        catalog.additional.forEach((entry, index) => get("additional-list").append(payloadCard(entry, false, index)));
        setBusy(busy);
    }
    const tabs = ["primary", "additional", "klog"];
    function tab(name) {
        for (const value of tabs) {
            get("tab-" + value).setAttribute("aria-selected", String(value === name));
            get("tab-" + value).tabIndex = value === name ? 0 : -1;
            get(value + "-panel").hidden = value !== name;
        }
        document.dispatchEvent(new CustomEvent("relapse:tab", {detail:name}));
    }
    for (const name of tabs) {
        get("tab-" + name).addEventListener("click", () => tab(name));
        get("tab-" + name).addEventListener("keydown", event => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault(); const other = tabs[(tabs.indexOf(name) + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
                tab(other); get("tab-" + other).focus();
            }
        });
    }
    get("primary-upload").addEventListener("submit", event => {
        event.preventDefault();
        const file = get("primary-file").files[0], label = get("primary-label").value;
        action(async () => { await upload(file, "primary", label); get("primary-upload").reset(); }, "Primary payload uploaded.");
    });
    get("additional-upload").addEventListener("submit", event => {
        event.preventDefault(); const files = Array.from(get("additional-files").files);
        action(async () => {
            let completed = 0;
            for (const file of files) {
                try { await upload(file, "additional"); completed++; }
                catch (error) { throw new Error(completed + "/" + files.length + " uploaded. " + file.name + ": " + error.message); }
            }
            get("additional-upload").reset();
        }, "Payloads uploaded.");
    });
    get("refresh").addEventListener("click", () => action(async () => {}, "Library refreshed."));
    refresh().catch(error => notice("Could not read the library: " + error.message, true));
})();
