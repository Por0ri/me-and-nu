const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

test('HOME reads and restores the active scroll owner without moving the other surface', () => {
  const calls = [];
  const screen = { scrollTop: 237, scrollTo: options => calls.push(['home', options.top]) };
  const window = { scrollY: 91, scrollTo: options => calls.push(['page', options.top]) };
  let overflowY = 'auto', mounted = true;
  const mod = { exports: {} };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../components/pivot/home-scroll.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { module: mod, exports: mod.exports, window,
    document: { querySelector: () => mounted ? screen : null }, getComputedStyle: () => ({ overflowY }) });
  assert.equal(mod.exports.homeScrollY(), 237);
  mod.exports.scrollHomeTo(237);
  overflowY = 'visible';
  assert.equal(mod.exports.homeScrollY(), 91);
  mod.exports.scrollHomeTo(91);
  mounted = false;
  mod.exports.scrollHomeTo(0);
  assert.deepEqual(calls, [['home', 237], ['page', 91], ['page', 0]]);
});
