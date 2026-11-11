const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const mod = { exports: {} };
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../components/pivot/account-restoration-screen.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
vm.runInNewContext(code, { module: mod, exports: mod.exports, require(name) {
  if (name === 'react' || name === 'react/jsx-runtime') return require(name);
  if (name === '@/lib/api') return { ApiError: class extends Error {} };
  if (name === './pivot-ui') return { PivotBrand: () => null, PivotFrame: 'main', PrimaryButton: 'button' };
  if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
  throw new Error(name);
} });
const render = scheduledDeleteAt => renderToStaticMarkup(React.createElement(mod.exports.AccountRestorationScreen, {
  scheduledDeleteAt, restore: async () => {}, returnToLogin() {},
}));

test('Restoration uses the Korean calendar day and explicit exclusive deadline, including UTC rollover', () => {
  for (const [timestamp, date, time] of [
    ['2027-02-23T15:00:00Z', '2027년 2월 24일', '00:00:00'],
    ['2027-02-24T08:14:35.123456Z', '2027년 2월 24일', '17:14:35'],
    ['2027-12-31T20:30:00-05:00', '2028년 1월 1일', '10:30:00'],
  ]) {
    const html = render(timestamp);
    assert.ok(html.includes(`<time dateTime="${timestamp}">${date}까지</time>`));
    assert.ok(html.includes(`한국 시간 ${time} 전까지 복구할 수 있어요.`));
    assert.match(html, /탈퇴 유예 중인 계정이에요\.<br\/>유예 기간 안에 복구하면<br\/>서비스를 다시 이용할 수 있어요\./);
    assert.match(html, /삭제예정일/);
    assert.match(html, /계정 복구하기/);
    assert.match(html, /로그인 화면으로/);
  }
});

test('Missing, invalid and timezone-free deadlines retain safe recovery guidance', () => {
  for (const timestamp of [null, undefined, '', 'bad-date', '2027-02-30T00:00:00Z', '2027-13-01T00:00:00Z', '2027-02-24', '2027-02-24T15:00:00']) {
    const html = render(timestamp);
    assert.match(html, /삭제 예정일을 확인할 수 없어요\./);
    assert.doesNotMatch(html, /<time|Invalid Date|한국 시간/);
    assert.match(html, /계정 복구하기/);
  }
});
