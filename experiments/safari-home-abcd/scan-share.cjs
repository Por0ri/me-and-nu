// Scan the actual proposed Git files, not ignored runtime contents. Reports no matched secret value.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),assert=require('node:assert/strict');
const {packageRoot,root,source}=require('./config.cjs'),{sha}=require('./utils.cjs');
const prefix='experiments/safari-home-abcd/';
const git=args=>cp.execFileSync('git',args,{cwd:source,encoding:'utf8',windowsHide:true,maxBuffer:32*1024*1024});
const split=s=>s.split('\0').filter(Boolean);
const candidates=[...new Set([...split(git(['ls-files','-z','--',prefix])),...split(git(['ls-files','--others','--exclude-standard','-z','--',prefix]))])].sort();
assert.ok(candidates.length>20,'Incomplete package');
const unrelated=[...split(git(['diff','--name-only','-z','95c2c205f4ae82b881933fd7def9404c3ff9bd60'])),...split(git(['ls-files','--others','--exclude-standard','-z']))].filter(f=>!f.startsWith(prefix));
assert.deepEqual(unrelated,[],'Unexpected change outside the handoff directory');
const forbiddenPath=/(^|\/)(?:\.runtime|private|node_modules|\.next|\.env[^/]*)(\/|$)|\.(?:key|pem|cer|pfx|p12|csr|zip|tar|png|jpg|log|jsonl)$/i;
const patterns={privateKey:new RegExp('-----BEGIN '+'(?:[A-Z ]+ )?PRIVATE KEY-----'),githubToken:/\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b/,awsKey:/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/,jwt:/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/,slackToken:/\bxox[baprs]-[A-Za-z0-9-]{20,}\b/,personalPath:/[A-Z]:[\\/](?:Users|dev)[\\/]/i,privateLan:/\b192\.168\.\d+\.\d+\b/};
const findings=[],files=[];
for(const file of candidates){
  if(forbiddenPath.test(file))findings.push({file,reason:'Forbidden generated/secret/binary path'});
  const absolute=path.join(source,file),stat=fs.lstatSync(absolute);
  assert.ok(stat.isFile()&&!stat.isSymbolicLink(),file);
  const bytes=fs.readFileSync(absolute);if(bytes.length>200000)findings.push({file,reason:'Unexpected file over 200 KB'});
  for(const [name,pattern]of Object.entries(patterns))if(pattern.test(bytes.toString('utf8')))findings.push({file,reason:name});
  files.push({file,bytes:bytes.length,sha256:sha(bytes)});
}
assert.deepEqual(findings,[],'Review findings (values intentionally omitted)');
const excluded=git(['check-ignore','--no-index','--',prefix+'.runtime/private/server.key',prefix+'.runtime/private/runtime.json',prefix+'node_modules/example',prefix+'.next/BUILD_ID',prefix+'.env.local',prefix+'test.key']).trim().split('\n');
assert.equal(excluded.length,6);
const result={passed:true,scope:'Proposed handoff files only; existing repository history not audited',files,totalBytes:files.reduce((n,f)=>n+f.bytes,0),excludedChecks:excluded.length,findings};
if(fs.existsSync(path.join(root,'results')))fs.writeFileSync(path.join(root,'results/share-scan.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
