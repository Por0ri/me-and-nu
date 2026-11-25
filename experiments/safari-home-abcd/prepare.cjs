// Only extracts committed frontend trees into this package's ignored runtime.
// No fetch, package installation, original-source mutation or shared junctions.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),assert=require('node:assert/strict');
const {root,source,variants}=require('./config.cjs'),{tree,sha}=require('./utils.cjs');
const run=(program,args,cwd=source)=>cp.execFileSync(program,args,{cwd,env:{...process.env,GIT_LFS_SKIP_SMUDGE:'1'},encoding:'utf8',windowsHide:true,maxBuffer:32*1024*1024});
const args=process.argv.slice(2);
if(args.length&&!(args.length===2&&args[0]==='--deps-from'))throw Error('Usage: node prepare.cjs [--deps-from /absolute/frontend/node_modules]');
const deps=args.length?path.resolve(args[1]):null;
if(fs.existsSync(root))throw Error('Runtime exists. Preserve/rename it before preparing a new run.');
// Check all refs before creating any runtime. Missing refs must be fetched separately by the operator.
for(const v of Object.values(variants))run('git',['cat-file','-e',v.ref+'^{commit}']);
let dependencyHashes;
if(deps){
  assert.ok(fs.lstatSync(deps).isDirectory()&&!fs.lstatSync(deps).isSymbolicLink(),'Use a physical dependency directory');
  dependencyHashes=tree(deps,{dependencies:true});
  for(const v of Object.values(variants))assert.deepEqual(JSON.parse(fs.readFileSync(path.join(deps,'../package-lock.json'))),JSON.parse(run('git',['show',v.ref+':frontend/package-lock.json'])),'Dependency source lockfile differs');
}
for(const name of ['results','fixtures','private','public'])fs.mkdirSync(path.join(root,name),{recursive:true});
const snapshots={};
for(const [name,v]of Object.entries(variants)){
  const target=path.join(root,name);fs.mkdirSync(target);
  const archive=path.join(root,name+'.tar');run('git',['archive','--format=tar','--output='+archive,v.ref,'frontend']);run('tar',['-xf',archive,'-C',target]);
  const frontend=path.join(target,'frontend');snapshots[name]=tree(frontend);
  assert.ok(!Object.keys(snapshots[name]).some(f=>/(^|\/)\.env(?:$|\.(?!example$))/.test(f)),'Unexpected committed env file');
  if(deps){fs.cpSync(deps,path.join(frontend,'node_modules'),{recursive:true,force:false,errorOnExist:true});assert.deepEqual(tree(path.join(frontend,'node_modules'),{dependencies:true}),dependencyHashes);}
  console.log('Prepared '+name+' from '+v.ref+(deps?' with independent dependency copy':''));
}
fs.writeFileSync(path.join(root,'results/source-original.json'),JSON.stringify(snapshots));
fs.writeFileSync(path.join(root,'results/preparation.json'),JSON.stringify({variants,node:process.version,zlib:process.versions.zlib,dependencyCopy:!!deps,packageInstall:false,networkFetch:false,originalFilesModified:false},null,2));
if(deps)fs.writeFileSync(path.join(root,'results/dependency-hashes.json'),JSON.stringify(dependencyHashes));
