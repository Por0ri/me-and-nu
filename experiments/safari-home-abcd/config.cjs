const path=require('node:path');
const packageRoot=__dirname, root=path.join(packageRoot,'.runtime');
module.exports={packageRoot,root,source:path.resolve(packageRoot,'../..'),
 host:'127.0.0.1',port:8443,origin:'https://127.0.0.1:8443',
 variants:{A:{ref:'95c2c205f4ae82b881933fd7def9404c3ff9bd60',pageSize:100,port:3577,mode:'baseline'},B:{ref:'95c2c205f4ae82b881933fd7def9404c3ff9bd60',pageSize:100,port:3578,mode:'no-images'},C:{ref:'95c2c205f4ae82b881933fd7def9404c3ff9bd60',pageSize:100,port:3579,mode:'one-card'},D:{ref:'6b0ad9361b65a15c7d4b153b7b55e561f3e73e4a',pageSize:20,port:3580,mode:'page20'}},
 playwright:process.env.LAB_PLAYWRIGHT_MODULE||path.join(root,'tools/node_modules/playwright'),
 openssl:process.env.LAB_OPENSSL||(process.platform==='win32'?path.join(process.env.ProgramFiles||'C:/Program Files','Git/usr/bin/openssl.exe'):'openssl')};
