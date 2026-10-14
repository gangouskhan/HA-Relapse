// Transport adapter only: inert byte buffers, mock native I/O, no console.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const context = {window: {}, Uint8Array, Number, Set};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../relapse_local/www/payload-menu/payload-sender.js'), 'utf8'), context);
const create = context.window.createLocalPayloadSender;
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
function fixture(size = 200, marker = 1) { const data = new Uint8Array(size).fill(marker); data.set([0x7f,0x45,0x4c,0x46]); return data; }
function entry(data, id = 'primary') { return {id, active: {sha256: hash(data), bytes: data.length, filename: 'Original File Name.elf'}}; }
function response(data, digest = hash(data)) { return {ok: true, headers: {get: name => name === 'Content-Length' ? String(data.length) : digest}, arrayBuffer: async () => data.buffer.slice(data.byteOffset, data.byteOffset + data.length)}; }
function transport(overrides = {}) { return {buffer: new Uint8Array(128), fetch: async () => response(fixture()), connect: async () => 3, claim: async () => {}, result: async () => {}, write: async (_s,_o,n) => n, close: async () => {}, ...overrides}; }

test('a 36.7 MB inert payload transfers exactly through a small buffer with partial writes', async () => {
    const data = fixture(36746616), selected = entry(data), buffer = new Uint8Array(1024*1024);
    const received = crypto.createHash('sha256'); let bytes = 0, closed = 0, claimed = false, result;
    const send = create(transport({buffer, fetch: async url => { assert.equal(url, '/user-payload/primary/'+hash(data)+'.elf'); return response(data); },
        claim: async () => {claimed = true;}, result: async (_e, value) => {result=value;},
        write: async (_s, offset, size) => {assert.ok(claimed); const n=Math.min(size,31003); received.update(buffer.subarray(offset,offset+n));bytes+=n;return n;},
        close: async () => {closed++;}}));
    assert.equal(await send(selected), data.length); assert.equal(bytes,data.length); assert.equal(received.digest('hex'),hash(data));
    assert.equal(closed,1); assert.equal(result,'sent'); await assert.rejects(send(selected),/second copy/);
});
test('different payloads can follow; aliases of the same bytes cannot run twice', async () => {
    let data=fixture(); const first=entry(data), alias=entry(data,'a'.repeat(32));
    const send=create(transport({fetch:async()=>response(data)})); await send(first);
    await assert.rejects(send(alias),/second copy/); data=fixture(200,2); await send(entry(data,'b'.repeat(32)));
});
test('partial failure closes socket, records failed result and prevents retry', async () => {
    let writes=0,closed=0,result; const selected=entry(fixture());
    const send=create(transport({write:async()=>++writes===1?7:-1,close:async()=>{closed++;},result:async(_e,v)=>{result=v;}}));
    await assert.rejects(send(selected),/after 7 bytes/); assert.equal(result,'failed'); assert.equal(closed,1);
    await assert.rejects(send(selected),/second copy/);
});
test('concurrent sends cannot interleave the shared buffer', async () => {
    let release; const wait=new Promise(r=>{release=r;}); const send=create(transport({fetch:async()=>{await wait;return response(fixture());}}));
    const first=send(entry(fixture())); await assert.rejects(send(entry(fixture(),'a'.repeat(32))),/already being sent/);release();await first;
});
test('download failure, mismatched revision and incomplete file never connect', async () => {
    let mode=0,connects=0; const data=fixture();
    const send=create(transport({connect:async()=>{connects++;return 3;},fetch:async()=>{
        if(mode===0)return {ok:false,status:409}; if(mode===1)return response(data,'0'.repeat(64));if(mode===2)return response(data.slice(0,180),hash(data));return response(data);
    }}));
    await assert.rejects(send(entry(data)),/HTTP 409/);mode++;await assert.rejects(send(entry(data)),/revision/);mode++;await assert.rejects(send(entry(data)),/incomplete/);
    assert.equal(connects,0);mode++;await send(entry(data));assert.equal(connects,1);
});
test('claim rejection writes no bytes and closes connection', async () => {
    let closed=0; const send=create(transport({claim:async()=>{throw new Error('already attempted');},write:async()=>assert.fail('must not send'),close:async()=>{closed++;}}));
    await assert.rejects(send(entry(fixture())),/already attempted/);assert.equal(closed,1);
});
test('oversized response is rejected before body allocation', async () => {
    const send=create(transport({fetch:async()=>({ok:true,headers:{get:()=>String(65*1024*1024)},arrayBuffer:async()=>assert.fail('no allocation')}),connect:async()=>assert.fail('no socket')}));
    await assert.rejects(send(entry(fixture())),/64 MiB/);
});
