const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.join(__dirname, '..');
const modules = new Map();
function load(relative) {
  if (modules.has(relative)) return modules.get(relative).exports;
  if (relative.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'));
  const mod = { exports: {} };
  modules.set(relative, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, setTimeout, clearTimeout, require(name) {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name);
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (name.startsWith('@/')) return load(name.slice(2) + (name.endsWith('.json') ? '' : '.ts'));
    if (name.startsWith('./')) {
      const base = path.join(path.dirname(relative), name);
      return load(base + (fs.existsSync(path.join(root, base + '.tsx')) ? '.tsx' : '.ts'));
    }
    throw new Error('Unexpected onboarding UI dependency: ' + name);
  } });
  return mod.exports;
}
const { OnboardingFlow } = load('components/pivot/onboarding-screens.tsx');
const { PivotFrame } = load('components/pivot/pivot-ui.tsx');
function render(step, mode, answers = {}) {
  return renderToStaticMarkup(React.createElement(OnboardingFlow, {
    state: { step, interest: 'animation', q1: 'ost', q2: 'events', ...answers },
    dispatch() {}, onExit() {}, mode,
  }));
}
for (const mode of ['demo', 'service']) {
  test(mode + ': interest/result omit ordinary Back; questions retain it', () => {
    for (const step of ['interest', 'result']) assert.doesNotMatch(render(step, mode), /aria-label="이전 단계"/);
    for (const step of ['q1', 'q2']) assert.equal((render(step, mode).match(/aria-label="이전 단계"/g) || []).length, 1);
    assert.match(render('result', mode), />다시 선택하기<\/button>/);
  });
  test(mode + ': unanswered Q1/Q2 block CTA and contain no stale result summary', () => {
    for (const step of ['q1', 'q2']) {
      const html = render(step, mode, { q1: null, q2: null });
      assert.match(html, /<button[^>]*disabled=""[^>]*>선택 완료<\/button>/);
      assert.doesNotMatch(html, /선택 답변 요약/);
    }
  });
}
test('Web frame omits simulated OS status indicators even in design preview', () => {
  const html = renderToStaticMarkup(React.createElement(PivotFrame, { chrome: true }, 'Screen'));
  assert.doesNotMatch(html, /9:41|imgWifi|imgOutline|imgIconMobileSignal|imgBatteryEnd/);
});

