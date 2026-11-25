const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
function tree(dir,{dependencies=false}={}) {
  const out={};
  function walk(folder,relative='') { for(const entry of fs.readdirSync(folder,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))) {
    if(!dependencies&&['node_modules','.next','.git'].includes(entry.name))continue;
    const file=path.join(folder,entry.name),name=relative+entry.name;
    if(entry.isSymbolicLink())throw Error('Redirected path is not supported: '+file);
    if(entry.isDirectory())walk(file,name+'/');else if(entry.isFile())out[name]=sha(fs.readFileSync(file));else throw Error('Unsupported file '+file);
  }}
  walk(dir);return out;
}
module.exports={sha,tree};
