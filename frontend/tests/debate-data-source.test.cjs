// 토론 화면 데이터 출처 (lib/debate/client.ts).
// 2026-11-23 사용자 결정: 베타는 데이터 없는 진짜 토론방으로 배포하고, 회원이 가짜 데이터 토론방을 보는 일은 없어야 한다.
// 그래서 배포용 빌드(NODE_ENV=production)는 설정과 상관없이 늘 진짜 API 를 쓰고, 가짜 데이터는 개발 서버에서 mock 을 직접 켤 때만 쓴다.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const test=require('node:test'),assert=require('node:assert/strict'),ts=require('typescript');
function load(env) {
 const mod={exports:{}};
 const output=ts.transpileModule(fs.readFileSync(path.join(__dirname,'..','lib/debate/client.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const api={listDebates:'api'},mock={listDebates:'mock'};
 vm.runInNewContext(output,{module:mod,exports:mod.exports,process:{env},
  require(name){if(name==='./api')return api;if(name==='./mock-store')return mock;throw Error(name);}});
 return mod.exports;
}
test('production builds always use the real debate API, even when fake data is requested',()=>{
 for(const source of [undefined,'mock','api','']){
  const client=load({NODE_ENV:'production',NEXT_PUBLIC_DEBATE_DATA_SOURCE:source});
  assert.equal(client.DEBATE_DATA_SOURCE,'api');assert.equal(client.listDebates,'api');
 }
});
test('the development server shows fake data only when mock is set explicitly',()=>{
 assert.equal(load({NODE_ENV:'development',NEXT_PUBLIC_DEBATE_DATA_SOURCE:'mock'}).listDebates,'mock');
 for(const source of [undefined,'api','MOCK','']) assert.equal(load({NODE_ENV:'development',NEXT_PUBLIC_DEBATE_DATA_SOURCE:source}).listDebates,'api');
});
