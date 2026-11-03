const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const mod = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../components/pivot/home-ai-button.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText, { module: mod, exports: mod.exports, require: name => name.endsWith('.css') ? {} : require(name) });
const choose = (...args) => JSON.parse(JSON.stringify(mod.exports.chooseHomeAIPosition(...args)));
const viewport = { left: 0, top: 0, right: 375, bottom: 812 };
const base = { left: 301, top: 752, right: 341, bottom: 792 };
const home = { x: 0, y: 0 }, left = { x: -281, y: 0 };
const detail = { left: 261, top: 768, right: 349, bottom: 812 };
const nav = { left: 87, top: 752, right: 287, bottom: 792 };
test('Home AI keeps the Figma anchor for clear or offscreen controls', () => {
  assert.deepEqual(choose(base, 20, home, [nav], viewport, true), home);
  assert.deepEqual(choose(base, 20, home, [nav, { ...detail, top: 820, bottom: 864 }], viewport, true), home);
});
test('Home AI moves only on actual overlap and clears both card controls and navigation', () => {
  assert.deepEqual(choose(base, 20, home, [nav, detail], viewport, true), left);
  assert.deepEqual(choose(base, 20, home, [nav, { ...detail, top: 809, bottom: 853 }], viewport, true), home);
});
test('Home AI retains a safe temporary position during scroll instead of toggling at the boundary', () => {
  assert.deepEqual(choose(base, 20, left, [nav], viewport, false), left);
  assert.deepEqual(choose(base, 20, left, [nav, { ...detail, top: 810, bottom: 854 }], viewport, true), left);
  assert.deepEqual(choose(base, 20, left, [nav], viewport, true), home);
});
test('Home AI finds another safe position when the preferred lane contains a visible control', () => {
  const next = choose(base, 20, home, [nav, detail, { left: 24, top: 740, right: 68, bottom: 790 }], viewport, false);
  assert.ok(next); assert.ok(next.y < 0);
  assert.ok(base.top + next.y >= 0);
});
test('Home AI never fabricates a safe position outside a fully blocked viewport', () => {
  assert.equal(choose(base, 20, home, [viewport], viewport, true), null);
});
