// Allowlisted metrics only. Never exports cookies, CSRF tokens, private keys, machine paths or raw diagnostics.
const fs=require('node:fs'),path=require('node:path');
const {root}=require('./config.cjs');
const browser=JSON.parse(fs.readFileSync(path.join(root,'results/verification.json')));
const https=JSON.parse(fs.readFileSync(path.join(root,'results/https-verification.json')));
const egress=JSON.parse(fs.readFileSync(path.join(root,'results/egress-probes.json')));
const requests=fs.readFileSync(path.join(root,'results/browser-requests.jsonl'),'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
const urlCounts={};for(const r of requests){const u=new URL(r.url);u.searchParams.delete('_rsc');const key=r.method+' '+u.href;urlCounts[key]=(urlCounts[key]||0)+1;}
const result={kind:'Windows Chrome portable-harness validation; not iPhone Safari',passed:browser.passed&&https.passed&&egress.passed,
 node:process.version,zlib:process.versions.zlib,
 cases:browser.cases.map(c=>({variant:c.variant,width:c.width,status:c.status,initialCards:c.initial.cards,initialDom:c.initial.elements,initialApi:c.initialRequests.apiRequests,initialJson:c.initialRequests.apiJsonRequests,initialImages:c.initialRequests.imageRequests,flowApi:c.normalRequests.apiRequests,flowImages:c.normalRequests.imageRequests,imageNativeCalls:c.finalMetrics.native,cookieFlags:c.cookieFlags,csp:c.csp,pageSizes:c.feeds.map(f=>f.ids.length),finalCards:c.finalCards,retainedCards:c.retainedCards,fixtureSha256:c.fixtureSha256,checks:c.checks})),
 builds:JSON.parse(fs.readFileSync(path.join(root,'results/build-results.json'))).map(b=>({variant:b.variant,exitCode:b.code,typescriptPassed:/Finished TypeScript/.test(fs.readFileSync(path.join(root,'results/build-'+b.variant+'.log'),'utf8'))})),
 browserRequests:{count:requests.length,origins:[...new Set(requests.map(r=>new URL(r.url).origin))],urlCounts},
 httpsProbeCount:https.probes.length,egressProbeCount:egress.results.length,
 limitations:['No OS-wide packet capture','No physical iPhone execution or Safari memory measurement','Browser route isolation, CSP and Node network guards cover the tested application processes; they are not a host firewall']};
fs.writeFileSync(path.join(root,'results/shareable-results.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({passed:result.passed,cases:result.cases.length,requests:requests.length,origins:result.browserRequests.origins}));
