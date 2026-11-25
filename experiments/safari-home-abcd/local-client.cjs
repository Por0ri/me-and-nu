require('./net-guard.cjs');
const https=require('node:https'),fs=require('node:fs'),path=require('node:path');
const {root,host,port}=require('./config.cjs');
const ca=fs.readFileSync(path.join(root,'public/lab-ca.pem'));
function request(route,options={}){return new Promise((resolve,reject)=>{
 const req=https.request({host,port,path:route,ca,rejectUnauthorized:true,minVersion:'TLSv1.2',method:options.method||'GET',headers:options.headers||{}},res=>{
  const authorized=res.socket.authorized,protocol=res.socket.getProtocol(),chunks=[];res.on('data',data=>chunks.push(data));res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,bytes:Buffer.concat(chunks),authorized,protocol}));
 });req.setTimeout(15000,()=>req.destroy(new Error('Local HTTPS timeout')));req.on('error',reject);if(options.body)req.write(options.body);req.end();
});}
module.exports={request,ca};
