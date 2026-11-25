// Validate generated source and fixture before build/start; never contacts a service.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {root,packageRoot,variants}=require('./config.cjs'),{tree,sha}=require('./utils.cjs');
const expected=JSON.parse(fs.readFileSync(path.join(packageRoot,'fixture-manifest.json')));
const actual=JSON.parse(fs.readFileSync(path.join(root,'fixtures/manifest.json')));
assert.deepEqual(actual,expected);
assert.equal(sha(fs.readFileSync(path.join(root,'fixtures/contents.json'))),expected.contentSha256);
for(const image of expected.images)assert.equal(sha(fs.readFileSync(path.join(root,'fixtures',image.name))),image.sha256);
const prepared=JSON.parse(fs.readFileSync(path.join(root,'results/source-prepared.json')));
for(const name of Object.keys(variants)){
  const current=tree(path.join(root,name,'frontend'));
  assert.ok(!Object.keys(current).some(f=>/(^|\/)\.env(?:$|\.(?!example$))/.test(f)),name+': unexpected env file');
  // Next may write generated types/config during build; compare application source and dependencies' manifests.
  for(const [file,hash]of Object.entries(prepared[name]))if(!['next-env.d.ts','tsconfig.tsbuildinfo'].includes(file))assert.equal(current[file],hash,name+': '+file);
}
console.log('PASS all four prepared sources and 123-card/10-PNG fixture hashes');
