const fs=require('node:fs'),path=require('node:path');
const {root,origin}=require('./config.cjs'),{request}=require('./local-client.cjs');
const state=JSON.parse(fs.readFileSync(path.join(root,'private/runtime.json')));
if(state.origin!==origin)throw new Error('Unexpected origin');
request('/__lab/stop',{method:'POST',headers:{'X-Lab-Control':state.control}}).then(r=>{if(r.status!==200)throw new Error('Stop rejected');console.log(r.bytes.toString());}).catch(e=>{console.error(e);process.exitCode=1;});
