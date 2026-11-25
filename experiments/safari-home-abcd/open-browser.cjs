require('./net-guard.cjs');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {root,origin,variants,playwright}=require('./config.cjs'),{request}=require('./local-client.cjs');
const {chromium}=require(playwright),certificate=JSON.parse(fs.readFileSync(path.join(root,'results/certificates.json')));
const variant=process.argv[2]||'A';if(!variants[variant])throw new Error('Use A, B, C or D');
(async()=>{
 const healthResponse=await request('/__lab/health');assert.equal(healthResponse.status,200);assert.equal(healthResponse.authorized,true);
 const health=JSON.parse(healthResponse.bytes);assert.equal(health.origin,origin);assert.deepEqual(health.next.map(n=>n.port),Object.values(variants).map(v=>v.port));
 const browser=await chromium.launch({channel:'chrome',headless:false,args:['--ignore-certificate-errors-spki-list='+certificate.server.spkiSha256,'--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--disable-default-apps','--disable-domain-reliability','--disable-client-side-phishing-detection','--no-pings','--disable-quic','--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=127.0.0.1']});
 const context=await browser.newContext({serviceWorkers:'block'});
 await context.route('**/*',route=>{const req=route.request(),allowed=new URL(req.url()).origin===origin;fs.appendFileSync(path.join(root,'results/manual-browser-requests.jsonl'),JSON.stringify({at:new Date().toISOString(),variant,url:req.url(),method:req.method(),allowed})+'\n');return allowed?route.continue():route.abort('blockedbyclient');});
 const page=await context.newPage();await page.goto(origin+'/__lab/start/'+variant+'?slides=10');
 console.log('Isolated local HTTPS '+variant+' opened. Close the browser window to end. No system certificate store changes.');
 page.on('close',()=>void browser.close());await new Promise(resolve=>browser.once('disconnected',resolve));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
