#!/usr/bin/env python3
"""Package HA Relapse with an empty user library. Never imports user payloads."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
ADDON = ROOT / "relapse_local"
DIST = ROOT / "dist"
ENGINE_FILES = {"elfldr-ps5-1360.elf", "kexp_2026_05_25.bin"}


def package(addon=ADDON, output=DIST):
    if addon == ADDON and (ROOT / "README.md").read_bytes() != (addon / "DOCS.md").read_bytes():
        raise ValueError("Keep README.md and relapse_local/DOCS.md in sync before packaging")
    for manifest_name, directory in (("UPSTREAM.json", "relapse"), ("PAYLOAD_MENU.json", "payload-menu")):
        webroot = addon / "www" / directory
        manifest = json.loads((addon / manifest_name).read_text())
        actual = {str(p.relative_to(webroot)) for p in webroot.rglob("*") if p.is_file()}
        if actual != set(manifest["sha256"]):
            raise ValueError("Unexpected or missing static assets: " + directory)
        for name, expected in manifest["sha256"].items():
            if hashlib.sha256((webroot / name).read_bytes()).hexdigest() != expected:
                raise ValueError("Asset checksum mismatch: " + name)
        if {p.name for p in (webroot / "payloads").iterdir()} != ENGINE_FILES:
            raise ValueError("Only the exploit's required loader files may be bundled")
    if json.loads((addon / "PAYLOAD_MENU.json").read_text())["payloads"]:
        raise ValueError("Release must not contain configured user payloads")
    for source in addon.rglob("*"):
        if source.is_symlink():
            raise ValueError("Symlinks are not allowed in the release")
        if source.suffix.lower() in (".elf", ".bin", ".zip", ".tar", ".gz") and not (
                source.parent.name == "payloads" and source.name in ENGINE_FILES):
            raise ValueError("Unexpected binary/archive in app: " + str(source))
    version = re.search(r'^version: "([^"]+)"$', (addon / "config.yaml").read_text(), re.M)[1]
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ("relapse-local-ha-" + version + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for source in sorted(addon.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                bundle.write(source, Path("relapse_local") / source.relative_to(addon))
        bundle.write(addon / "DOCS.md", "README.md")
    (output / "SHA256SUMS.txt").write_text("".join(
        hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
        for p in sorted(output.glob("relapse-local-ha-*.zip"))))
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(package())
