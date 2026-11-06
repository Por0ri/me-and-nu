const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const mod = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../components/pivot/home-ai-button.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText, { module: mod, exports: mod.exports, require: name => name.endsWith('.css') ? {} : require(name) });
const choose = (...args) => JSON.parse(JSON.stringify(mod.exports.chooseHomeAIPosition(...args)));
const viewport = { left: 0, top: 0, right: 375, bottom: 812 };
const base = { left: 293, top: 752, right: 341, bottom: 800 };
const home = { x: 0, y: 0 }, raised = { x: 0, y: -36 };
const detail = { left: 261, top: 768, right: 349, bottom: 812 };
const nav = { left: 87, top: 752, right: 287, bottom: 800 };
test('Home AI keeps the Figma anchor for clear or offscreen controls', () => {
  assert.deepEqual(choose(base, 20, home, [nav], viewport, true), home);
  assert.deepEqual(choose(base, 20, home, [nav, { ...detail, top: 820, bottom: 864 }], viewport, true), home);
});
test('Home AI moves only on actual overlap and clears both card controls and navigation', () => {
  assert.deepEqual(choose(base, 20, home, [nav, detail], viewport, true), raised);
  // The SVG's shadow extends to y816, but it must neither intercept nor avoid input.
  assert.deepEqual(choose(base, 20, home, [nav, { ...detail, top: 801, bottom: 845 }], viewport, true), home);
});
test('Home AI retains a safe temporary position during scroll instead of toggling at the boundary', () => {
  assert.deepEqual(choose(base, 20, raised, [nav], viewport, false), raised);
  assert.deepEqual(choose(base, 20, raised, [nav, { ...detail, top: 802, bottom: 846 }], viewport, true), raised);
  assert.deepEqual(choose(base, 20, raised, [nav], viewport, true), home);
});
test('Home AI does not cross the screen just because the left dock is available', () => {
  const next = choose(base, 20, home, [nav, detail, { left: 24, top: 740, right: 68, bottom: 790 }], viewport, false);
  assert.deepEqual(next, raised);
});

test('Home AI clears a one-pixel collision with only the necessary movement and clearance', () => {
  assert.deepEqual(choose(base, 20, home, [nav, { ...detail, top: 799 }], viewport, false), { x: 0, y: -5 });
});

test('Home AI chooses a short horizontal escape when it is closer than a vertical escape', () => {
  const obstacle = { left: 330, right: 350, top: 700, bottom: 812 };
  assert.deepEqual(choose(base, 20, home, [obstacle], viewport, false), { x: -15, y: 0 });
});

test('Home AI holds its safe offset through small scroll reversals', () => {
  for (const delta of [-2, 0, 2, -2, 1]) {
    assert.deepEqual(choose(base, 20, raised, [nav, { ...detail, top: detail.top + delta }], viewport, false), raised);
  }
});

test('Home AI uses the actual target size and keeps all mobile targets inside the viewport', () => {
  for (const [width, height, right] of [[320, 568, 16], [375, 812, 34], [390, 844, 34]]) {
    const bounds = { left: 0, top: 0, right: width, bottom: height };
    const anchor = { left: width - right - 48, right: width - right, top: height - 60, bottom: height - 12 };
    const obstacle = { left: anchor.left - 10, right: width, top: anchor.bottom - 1, bottom: height };
    assert.deepEqual(choose(anchor, 20, home, [obstacle], bounds, false), { x: 0, y: -5 });
  }
});
test('Home AI never fabricates a safe position outside a fully blocked viewport', () => {
  assert.equal(choose(base, 20, home, [viewport], viewport, true), null);
});
