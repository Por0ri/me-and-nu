const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const mod = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../components/pivot/home-ai-button.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText, { module: mod, exports: mod.exports, require: name => name.endsWith('.css') ? { default: { aiButton: 'aiButton' } } : require(name) });

test('Home AI renders an informational button with CSS positioning and the existing notice', () => {
  const messages = [];
  const button = mod.exports.HomeAIButton({ onNotice: message => messages.push(message) });
  assert.equal(button.type, 'button');
  assert.equal(button.props.type, 'button');
  assert.equal(button.props['aria-label'], 'AI 콘텐츠 탐색');
  assert.equal(button.props['aria-disabled'], 'true');
  assert.equal(button.props['data-home-ai'], '');
  assert.equal(button.props.style, undefined);
  assert.equal(button.props.children.props.src, '/pivot/home/chrome/ai.svg');
  button.props.onClick();
  assert.deepEqual(messages, ['AI 콘텐츠 탐색은 준비 중이에요.']);
});
