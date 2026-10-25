#!/usr/bin/env python3
"""Read-only release audit: integrity, local dependencies and an empty user library."""
import hashlib
import json
from pathlib import Path
import posixpath
import re
import sys
import zipfile

root = Path(__file__).resolve().parent
source_version = re.search(r'^version: "([^"]+)"$', (root / 'relapse_local/config.yaml').read_text(), re.M)[1]
archive = Path(sys.argv[1]) if len(sys.argv) > 1 else root / 'dist' / ('relapse-local-ha-' + source_version + '.zip')
with zipfile.ZipFile(archive) as bundle:
    names = set(bundle.namelist())
    assert bundle.testzip() is None
    assert bundle.read('README.md') == bundle.read('relapse_local/DOCS.md'), 'Release instructions differ'
    assert not any('/data/' in name or 'legacy-reference' in name or '__pycache__' in name for name in names)
    checked = 0
    for manifest, directory in (('UPSTREAM.json','relapse'),('PAYLOAD_MENU.json','payload-menu')):
        meta = json.loads(bundle.read('relapse_local/'+manifest))
        prefix='relapse_local/www/'+directory+'/'
        assert {name[len(prefix):] for name in names if name.startswith(prefix)} == set(meta['sha256'])
        for filename, expected in meta['sha256'].items():
            assert hashlib.sha256(bundle.read(prefix+filename)).hexdigest() == expected, filename
            checked += 1
    required = {f'relapse_local/www/{d}/payloads/{f}' for d in ('relapse','payload-menu')
                for f in ('elfldr-ps5-1360.elf','kexp_2026_05_25.bin')}
    binaries = {name for name in names if name.endswith(('.elf','.bin','.zip','.gz','.tar'))}
    assert binaries == required, binaries-required
    menu = json.loads(bundle.read('relapse_local/PAYLOAD_MENU.json'))
    assert menu['payloads'] == {}
    edges = set()
    for filename in sorted(names):
        if not filename.endswith(('.html','.js')):
            continue
        source=bundle.read(filename).decode()
        directory=posixpath.dirname(filename)
        page_directory='relapse_local/www/payload-menu' if '/payload-menu/' in filename else 'relapse_local/www/relapse'
        imports=re.findall(r'''\b(?:from\s*|import\s*\(\s*)['"](\.{1,2}/[^'"\s]+\.js(?:\?[^'"\s]*)?)['"]''',source)
        scripts=re.findall(r'''<script[^>]*\ssrc=['"]([^'"]+)['"]''',source)
        styles=re.findall(r'''<link[^>]*\shref=['"]([^'"]+)['"]''',source)
        workers=re.findall(r'''new\s+Worker\(\s*['"]([^'"]+)['"]''',source)
        for url,base in [(u,directory) for u in imports+scripts+styles]+[(u,page_directory) for u in workers]:
            assert not url.startswith(('https:','http:')), (filename,'external dependency',url)
            target=posixpath.normpath(posixpath.join(base,url.split('?')[0]))
            assert target in names,(filename,url,target)
            edges.add((filename,url,target))
    for filename in ('Dockerfile','config.yaml','run.sh','host.py','http_api.py','payload_store.py','dns_forwarder.py','translations/en.yaml','examples/nanodns.ini.example','admin/index.html','admin/app.js','admin/style.css','DOCS.md'):
        assert 'relapse_local/'+filename in names
    for directory in ('relapse','payload-menu'):
        assert 'relapse_local/www/'+directory+'/offsets/13.60.js' in names
    page=bundle.read('relapse_local/www/payload-menu/relapse.html').decode()
    memory=bundle.read('relapse_local/www/payload-menu/mem.js').decode()
    core_url=lambda s: re.search(r'''from ["'](\./core\.js\?v=\d+)["']''',s)[1]
    assert core_url(page)==core_url(memory),'core module URL mismatch'
    assert 'payloadUI.prepare().then(run)' in page
    assert 'payloadUI.ready(!!window.__relapseFreshSession)' in page
    config=bundle.read('relapse_local/config.yaml').decode()
    version=re.search(r'^version: "([^"]+)"$', config, re.M)[1]
    for filename, marker in (('Dockerfile', 'ARG BUILD_VERSION='+version),
                             ('http_api.py', 'VERSION = "'+version+'"'),
                             ('admin/index.html', 'id="version">'+version+'<'),
                             ('www/index.html', '<small>'+version+' ·'),
                             ('DOCS.md', '# HA Relapse '+version)):
        assert marker in bundle.read('relapse_local/'+filename).decode(), filename+' version mismatch'
    assert 'ingress: true' in config and 'ingress_stream: true' in config
    assert '8099/tcp:' not in config, 'Management port must not be mapped publicly'
    assert 'slug: relapse_local' in config, 'Keep existing app identity for updates'
    print(json.dumps({'archive':str(archive.resolve()),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'zip_entries':len(names),'pinned_asset_hashes_checked':checked,'local_dependency_edges_checked':len(edges),
        'user_payloads_bundled':False,'only_required_engine_binaries':True,'firmware_13_60_assets_present':True,
        'matching_core_module_urls':True,'ingress_management_included':True,'exploit_or_payload_execution':False},indent=2))
