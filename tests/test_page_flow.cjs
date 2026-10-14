// Real controller with an inert DOM. No exploit, native memory, console or network.
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../relapse_local/www/payload-menu/payload-ui.js'),'utf8');
const clone=v=>JSON.parse(JSON.stringify(v));
const item=(id,restore=false)=>({id,active:{filename:id+'.elf',label:'',sha256:id==='primary'?'1'.repeat(64):id.repeat(64).slice(0,64),bytes:256},restore,wait_seconds:0});
function setup(library={primary:item('primary'),additional:[item('a',true),item('b',false),item('c',true)]},existing){
    class Element{
        constructor(){this.children=[];this.events={};this.style={};this.dataset={};this.hidden=false;this.disabled=false;this.classList={add:v=>{this.shown=v==='ready';}};}
        set textContent(value){this.text=value;this.children=[];}get textContent(){return this.text;}
        append(...nodes){this.children.push(...nodes);}appendChild(node){this.append(node);}focus(){}
        addEventListener(name,fn){this.events[name]=fn;}
        querySelectorAll(query){return this.children.filter(c=>query==='button[data-payload]'&&c.dataset.payload);}
        querySelector(query){return this.children.find(c=>c.className===query.slice(1));}
    }
    const ids=['payload-status','payload-home','payload-additional','payload-back','primary-button','primary-name','primary-file','primary-ready','payload-refresh','restore-button','cancel-restore','payload-list','payload-empty','additional-button','relapse-payload-menu','console'];
    const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
    const state=existing||{session:{token:'test',attempts:{}}};
    let failId, pendingSleep;
    const sent=[];
    const env={document:{getElementById:id=>elements[id],createElement:()=>new Element()},
        sleep:async()=>{if(pendingSleep)await pendingSleep();},
        fetch:async(url,options)=>{
            const data=options&&options.body?JSON.parse(options.body):{};
            if(url==='/api/catalog')return {ok:true,json:async()=>clone(library)};
            if(url==='/api/session'){if(data.fresh)state.session={token:'fresh',attempts:{}};return {ok:true,json:async()=>clone(state.session)};}
            if(url==='/api/claim'){
                if(state.session.attempts[data.id])return {ok:false,json:async()=>({error:'duplicate attempt'})};
                state.session.attempts[data.id]={status:'attempted',sha256:data.sha256};
            }
            if(url==='/api/result')state.session.attempts[data.id].status=data.status;
            return {ok:true,json:async()=>({ok:true})};
        },
        send:async entry=>{
            await ui.claim(entry);
            if(entry.id===failId){await ui.result(entry,'failed');throw new Error('simulated write failure');}
            sent.push(entry.id);await ui.result(entry,'sent');return 256;
        }
    };
    const context={window:{},Object,JSON};vm.runInNewContext(source,context);const ui=context.window.createPayloadUI(env);
    const click=id=>elements[id].events.click();
    return{ui,sent,elements,click,library,state,fail:id=>{failId=id;},sleep:fn=>{pendingSleep=fn;}};
}

test('empty upload-first setup stops before the exploit and injects nothing',async()=>{
    const p=setup({primary:null,additional:[]});await assert.rejects(p.ui.prepare(),/No primary payload/);assert.deepEqual(p.sent,[]);
});
test('menu reveals only on readiness and switching panels never sends anything',async()=>{
    const p=setup();await p.ui.prepare();assert.ok(!p.elements['relapse-payload-menu'].shown);await p.ui.ready(true);assert.ok(p.elements['relapse-payload-menu'].shown);
    p.click('additional-button');assert.equal(p.elements['payload-home'].hidden,true);p.click('payload-back');assert.equal(p.elements['payload-additional'].hidden,true);assert.deepEqual(p.sent,[]);
});
test('primary startup confirmation gates extras; restore sends selected entries in order',async()=>{
    const p=setup();await p.ui.prepare();await p.ui.ready(true);assert.equal(p.elements['restore-button'].disabled,true);
    await p.click('primary-button');assert.deepEqual(p.sent,['primary']);assert.equal(p.elements['restore-button'].disabled,true);
    p.click('primary-ready');await p.click('restore-button');assert.deepEqual(p.sent,['primary','a','c']);
    await p.click('restore-button');await p.click('primary-button');assert.deepEqual(p.sent,['primary','a','c']);
});
test('restore stops on failed transfer and retains ambiguous attempt guard',async()=>{
    const p=setup();await p.ui.prepare();await p.ui.ready(true);await p.click('primary-button');p.click('primary-ready');p.fail('a');
    await p.click('restore-button');assert.deepEqual(p.sent,['primary']);assert.match(p.elements['payload-status'].textContent,/Stopped/);assert.equal(p.state.session.attempts.a.status,'failed');
});
test('stop request takes effect between payloads',async()=>{
    const p=setup();p.library.additional[0].wait_seconds=1;p.sleep(async()=>p.click('cancel-restore'));
    await p.ui.prepare();await p.ui.ready(true);await p.click('primary-button');p.click('primary-ready');await p.click('restore-button');assert.deepEqual(p.sent,['primary','a']);
});
test('reentry retains send history; only a fresh loader session resets it',async()=>{
    const p=setup();await p.ui.prepare();await p.ui.ready(true);await p.click('primary-button');p.click('primary-ready');await p.click('restore-button');
    const q=setup(p.library,p.state);await q.ui.prepare();await q.ui.ready(false);assert.equal(q.elements['primary-button'].disabled,true);q.click('primary-ready');await q.click('restore-button');assert.deepEqual(q.sent,[]);
    await q.ui.ready(true);await q.click('primary-button');assert.deepEqual(q.sent,['primary']);
});
test('refresh adds uploaded payloads without hardcoded buttons',async()=>{
    const p=setup();await p.ui.prepare();await p.ui.ready(true);p.library.additional.push(item('d'));await p.click('payload-refresh');assert.equal(p.elements['payload-list'].children.length,4);
});
