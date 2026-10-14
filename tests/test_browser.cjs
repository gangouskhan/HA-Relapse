// Actual management UI + shipping PS5 controller with an inert native adapter.
// All requests are restricted to loopback. No PS5/HA connection, exploit or ELF execution.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
function elf(marker,size=256){const b=Buffer.alloc(size);Buffer.from([0x7f,0x45,0x4c,0x46,2,1,1]).copy(b);b.writeUInt16LE(62,18);b.writeBigUInt64LE(64n,32);b.writeUInt16LE(56,54);b.writeUInt16LE(1,56);b[size-1]=marker;return b;}
function uploadFile(name,marker,size){return {name,mimeType:'application/octet-stream',buffer:elf(marker,size)};}
const version=fs.readFileSync(path.resolve(__dirname,'../relapse_local/config.yaml'),'utf8').match(/^version: "([^"]+)"$/m)[1];
const output=path.resolve(__dirname,'../verification-ui',version);fs.mkdirSync(output,{recursive:true});
(async()=>{
    const server=spawn('python3',[path.join(__dirname,'ui_server.py')],{stdio:['ignore','pipe','inherit']});
    let browser;
    try{
        const urls=await new Promise((resolve,reject)=>{let value='';server.stdout.on('data',chunk=>{value+=chunk;if(value.includes('\n'))resolve(JSON.parse(value.split('\n')[0]));});server.on('error',reject);server.on('exit',code=>reject(new Error('UI server exited '+code)));});
        browser=await chromium.launch({headless:true,args:['--no-sandbox']});
        const context=await browser.newContext({viewport:{width:1200,height:950},ignoreHTTPSErrors:true,
            userAgent:'Mozilla/5.0 (PlayStation 5/13.60) AppleWebKit/605.1.15'});
        const errors=[];
        context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));
        await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.hostname==='127.0.0.1')return route.continue();errors.push('Non-local request '+url.href);return route.abort();});
        const page=await context.newPage();
        await page.goto(urls.admin);
        await page.getByRole('heading',{name:'No primary payload',exact:true}).waitFor();
        await page.screenshot({path:path.join(output,'01-primary-empty.png'),fullPage:true});
        await page.locator('#primary-file').setInputFiles(uploadFile('Combined primary β v1.elf',1,36746616));
        await page.locator('#primary-label').fill('Primary v1');
        await page.locator('#primary-upload button[type=submit]').click();
        await page.getByText('Primary payload uploaded.',{exact:true}).waitFor();
        assert.equal(await page.locator('#primary-current h3').textContent(),'Primary v1');
        await page.locator('#primary-file').setInputFiles(uploadFile('Primary v2.elf',2));
        await page.locator('#primary-label').fill('Primary v2');
        await page.locator('#primary-upload button[type=submit]').click();
        await page.locator('#primary-current h3').filter({hasText:'Primary v2'}).waitFor();
        await page.getByRole('button',{name:'Roll back',exact:true}).click();
        await page.locator('#primary-current h3').filter({hasText:'Primary v1'}).waitFor();
        await page.screenshot({path:path.join(output,'02-primary-rollback.png'),fullPage:true});
        await page.locator('#primary-file').setInputFiles({name:'bad.elf',mimeType:'application/octet-stream',buffer:Buffer.alloc(256)});
        await page.locator('#primary-upload button[type=submit]').click();
        await page.locator('#notice.error').waitFor();
        assert.equal(await page.locator('#primary-current h3').textContent(),'Primary v1');
        await page.getByRole('tab',{name:/Additional payloads/}).click();
        await page.locator('#additional-files').setInputFiles([uploadFile('Mount tools.elf',3),uploadFile('FTP tools.elf',4),uploadFile('One time tool.elf',5)]);
        await page.locator('#additional-upload button[type=submit]').click();
        await page.getByText('Payloads uploaded.',{exact:true}).waitFor();
        const cards=page.locator('#additional-list .payload-card');
        assert.equal(await cards.count(),3);
        for(const name of ['Mount tools.elf','FTP tools.elf']){
            await cards.filter({has:page.getByRole('heading',{name,exact:true})}).getByLabel('Include in restore').check();
            await page.getByText('Restore selection saved.',{exact:true}).waitFor();
        }
        await cards.filter({has:page.getByRole('heading',{name:'FTP tools.elf',exact:true})}).getByRole('button',{name:'Move up',exact:true}).click();
        await page.getByText('Restore order saved.',{exact:true}).waitFor();
        assert.equal(await cards.first().locator('h3').textContent(),'FTP tools.elf');
        await page.screenshot({path:path.join(output,'03-additional.png'),fullPage:true});
        const library=await (await context.request.get(urls.public+'/api/catalog')).json();
        const expected=['primary',...library.additional.filter(e=>e.restore).map(e=>e.id)];
        const consolePage=await context.newPage();
        const consoleRequests=[];
        consolePage.on('request',request=>consoleRequests.push(request.url()));
        await consolePage.goto(urls.guide);
        assert.equal(consolePage.url(),urls.public+'/');
        await consolePage.getByRole('link',{name:'Relapse',exact:true}).click();
        assert.equal(consolePage.url(),urls.public+'/payload-menu/relapse.html');
        await consolePage.waitForFunction(()=>window.testReady===true);
        assert.deepEqual(await consolePage.evaluate(()=>sentIDs),[]);
        assert.equal(await consolePage.locator('#payload-home button:visible').count(),2);
        await consolePage.locator('#primary-button').click();
        await consolePage.waitForFunction(()=>sentIDs.length===1);
        await consolePage.locator('#additional-button').click();
        assert.equal(await consolePage.locator('#restore-button').isDisabled(),true);
        const selectedRow=consolePage.locator('#payload-list button[data-payload]').filter({hasText:'FTP tools.elf'});
        const manualRow=consolePage.locator('#payload-list button[data-payload]').filter({hasText:'One time tool.elf'});
        assert.equal(await consolePage.locator('.restore-badge:visible').count(),2);
        assert.equal(await selectedRow.locator('.restore-badge').textContent(),'Restore');
        assert.equal(await manualRow.locator('.restore-badge').isVisible(),false);
        assert.equal(await selectedRow.isDisabled(),true);
        await consolePage.screenshot({path:path.join(output,'04a-ps5-restore-selection.png'),fullPage:true});
        // Saved HA selections are reflected only on refresh, without sending a file.
        const restoreCheckbox=cards.filter({has:page.getByRole('heading',{name:'FTP tools.elf',exact:true})}).getByLabel('Include in restore');
        await Promise.all([
            page.waitForResponse(response=>response.url().endsWith('/api/edit')&&response.request().method()==='POST'&&response.ok()),
            restoreCheckbox.uncheck()
        ]);
        await page.getByText('Restore selection saved.',{exact:true}).waitFor();
        await consolePage.locator('#payload-refresh').click();
        await consolePage.getByText('Payload list refreshed.',{exact:true}).waitFor();
        assert.equal(await selectedRow.locator('.restore-badge').isVisible(),false);
        await Promise.all([
            page.waitForResponse(response=>response.url().endsWith('/api/edit')&&response.request().method()==='POST'&&response.ok()),
            restoreCheckbox.check()
        ]);
        await page.getByText('Restore selection saved.',{exact:true}).waitFor();
        await consolePage.locator('#payload-refresh').click();
        await selectedRow.locator('.restore-badge').waitFor();
        assert.deepEqual(await consolePage.evaluate(()=>sentIDs),['primary']);
        await consolePage.locator('#primary-ready').click();
        await consolePage.locator('#restore-button').click();
        await consolePage.waitForFunction(()=>sentIDs.length===3);
        assert.deepEqual(await consolePage.evaluate(()=>sentIDs),expected);
        assert.deepEqual(consoleRequests.filter(url=>url.startsWith('https:')),[urls.guide]);
        assert.ok(consoleRequests.some(url=>url.startsWith(urls.public+'/user-payload/primary/')));
        assert.equal(await consolePage.locator('#restore-button').isDisabled(),true);
        assert.equal(await consolePage.locator('.restore-badge:visible').count(),2);
        await consolePage.screenshot({path:path.join(output,'04-ps5-additional.png'),fullPage:true});
        await consolePage.locator('#payload-back').click();
        await consolePage.screenshot({path:path.join(output,'05-ps5-home.png'),fullPage:true});
        await consolePage.goto(urls.public+'/ui-test?existing=1');
        await consolePage.waitForFunction(()=>window.testReady===true);
        assert.equal(await consolePage.locator('#primary-button').isDisabled(),true);
        assert.deepEqual(await consolePage.evaluate(()=>sentIDs),[]);
        await page.setViewportSize({width:390,height:844});
        await page.getByRole('tab',{name:/Primary payload/}).click();
        await page.screenshot({path:path.join(output,'06-mobile.png'),fullPage:true});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        assert.deepEqual(errors,[]);
        fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({passed:true,version,ingress_streaming_proxy:true,checks:['HTTPS guide redirects to HTTP landing','Relapse link opens inert fixture at shipping path','all subsequent console requests use local HTTP','chunked upload and JSON requests through a proxy','empty library','nested HA ingress path','36.7 MB upload','filename preservation','primary replacement','rollback','invalid upload preservation','multiple additional uploads','restore selection and order','PS5 restore badges reflect HA selections on refresh without sending','restore badges remain visible before confirmation and after sending','two-button PS5 UI','startup confirmation gate','ordered restore','duplicate prevention on reentry','mobile layout','offline requests only'],screenshots:7,exploit_execution:false,console_connections:false},null,2)+'\n');
        console.log('Browser checks passed; screenshots saved to '+output);
    }finally{
        if(browser)await browser.close();
        server.kill('SIGTERM');
        await new Promise(resolve=>server.once('exit',resolve));
    }
})().catch(error=>{console.error(error);process.exitCode=1;});
