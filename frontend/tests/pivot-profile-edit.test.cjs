// My Page 프로필 수정 (2026-11 design): the onboarding nickname rule and check, and the calls it makes.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');

function loader(overrides = {}) {
  const modules = new Map();
  function load(file) {
    if (modules.has(file)) return modules.get(file).exports;
    const mod = { exports: {} }; modules.set(file, mod);
    const output = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInNewContext(output, { module: mod, exports: mod.exports, AbortController, URL, setTimeout, clearTimeout, process: { env: {} },
      require(name) {
        if (name in overrides) return overrides[name];
        if (name === 'react' || name === 'react/jsx-runtime') return require(name);
        if (name.endsWith('.module.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
        const relative = name.startsWith('@/') ? name.slice(2) : path.join(path.dirname(file), name);
        return load(relative + (fs.existsSync(path.join(root, relative + '.tsx')) ? '.tsx' : '.ts'));
      },
    });
    return mod.exports;
  }
  return load;
}

test('the screen shows the member, keeps 저장하기 off for an unchanged nickname and uses the onboarding box', () => {
  const { ProfileEditScreen } = loader()('components/pivot/profile-edit-screen.tsx');
  const html = renderToStaticMarkup(React.createElement(ProfileEditScreen, {
    profile: { nickname: '미뉴', maskedEmail: 'minu***@gmail.com' }, onBack() {}, onSave() {},
  }));
  assert.match(html, /프로필에 표시되는 닉네임을 변경할 수 있어요/);
  assert.match(html, />미뉴<\/p><p class="email">minu\*\*\*@gmail\.com</);
  assert.match(html, /aria-label="뒤로가기"/);
  assert.match(html, /<input id="profile-nickname"[^>]*value="미뉴"/);
  assert.match(html, />2\/20</);
  assert.match(html, />2~20자<\/span>, <span>공백·특수문자 제외</);
  assert.match(html, /<button[^>]*disabled=""[^>]*>저장하기<\/button>/);
  // Onboarding renders the same box, so the two never drift apart.
  const { OnboardingProfileScreen } = loader()('components/pivot/onboarding-profile.tsx');
  const onboarding = renderToStaticMarkup(React.createElement(OnboardingProfileScreen, { profile: { nickname: '' }, onChange() {}, onContinue() {} }));
  assert.match(onboarding, /<input id="onboarding-nickname"/);
  assert.match(onboarding, />2~20자<\/span>, <span>공백·특수문자 제외</);
  assert.match(onboarding, />시작하기</);
});

test('the member check and save go to /users/me with the onboarding rule; the email is masked or explained', async () => {
  const calls = [];
  const api = {
    async apiRequest(url, init) { calls.push([url, init]); return { nickname: '미뉴덕후', available: true, reason: null }; },
    async getProfile() { return { nickname: '미뉴', email: null }; },
    async updateProfile(input) { calls.push(['PATCH /api/v1/users/me', input]); return { nickname: input.nickname }; },
  };
  const load = loader({ '@/lib/api': api, './services': { requirePivotApi() {} } });
  const { checkMyNickname, saveMyNickname, loadEditableProfile } = load('lib/pivot/profile-edit.ts');
  const signal = new AbortController().signal;
  await checkMyNickname('미뉴 덕후', signal, () => {});
  assert.equal(calls[0][0], '/api/v1/users/me/nickname?nickname=' + encodeURIComponent('미뉴 덕후'));
  assert.equal(calls[0][1].signal, signal);
  assert.equal(await saveMyNickname('미뉴덕후'), '미뉴덕후');
  // Objects made inside the vm have another realm's prototype, so compare their contents.
  assert.equal(JSON.stringify(calls[1]), JSON.stringify(['PATCH /api/v1/users/me', { nickname: '미뉴덕후' }]));
  assert.equal(JSON.stringify(await loadEditableProfile(signal)), JSON.stringify({ nickname: '미뉴', maskedEmail: '이메일 정보를 확인할 수 없어요' }));
  api.getProfile = async () => ({ nickname: ' ', email: 'minu.kim@gmail.com' });
  await assert.rejects(loadEditableProfile(signal));
});
