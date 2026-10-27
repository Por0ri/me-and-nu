const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

// Exercise the real login component's event handlers and effect cleanup with stable hook state.
function loginHarness() {
  const slots = [];
  const effects = [];
  const cleanups = [];
  const listeners = new Map();
  const starts = [];
  let cursor = 0;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!(index in slots) || dependencies.some((value, i) => value !== slots[index][i])) {
        slots[index] = dependencies;
        effects.push(() => { cleanups[index]?.(); cleanups[index] = effect(); });
      }
    },
  };
  const dependencies = {
    react,
    'react/jsx-runtime': require('react/jsx-runtime'),
    '@/lib/pivot/services': {
      oauthErrorMessage: () => '',
      async startSnsLogin(provider) { starts.push(provider); },
    },
    './pivot-session': {usePivotSession: () => ({state:{status:'ready',session:null},refresh(){}})},
    './pivot-ui': {PivotAsset:'img', PivotBrand:'div', PivotFrame:'main', PrimaryButton:'button'},
    './pivot.module.css': {default:{kakao:'kakao',naver:'naver'},__esModule:true},
  };
  const mod = {exports:{}};
  const source = fs.readFileSync(path.join(__dirname, '../components/pivot/login-screen.tsx'), 'utf8');
  const code = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  vm.runInNewContext(code, {
    module:mod,exports:mod.exports,URLSearchParams,
    require(name) { if (!(name in dependencies)) throw Error('Unexpected login dependency: '+name); return dependencies[name]; },
    window:{location:{search:''},
      addEventListener(name, callback) {if(!listeners.has(name)) listeners.set(name,new Set()); listeners.get(name).add(callback);},
      removeEventListener(name, callback) {listeners.get(name)?.delete(callback);}},
  });
  function render(props = {}) {
    cursor = 0;
    const view = mod.exports.LoginScreen(props);
    effects.splice(0).forEach(effect => effect());
    return view;
  }
  function button(view, provider) {
    if (!view || typeof view !== 'object') return null;
    if (view.type === 'button' && view.props.className === provider) return view;
    for (const child of [view.props?.children].flat(Infinity)) {
      const match = button(child, provider);
      if (match) return match;
    }
    return null;
  }
  return {render,button,starts,
    pageshow(persisted) {listeners.get('pageshow')?.forEach(callback => callback({persisted}));},
    unmount() {cleanups.forEach(cleanup => cleanup?.());},
    listenerCount() {return listeners.get('pageshow')?.size ?? 0;},
  };
}

test('Back from the provider restores both buttons and permits another OAuth attempt', async () => {
  const app = loginHarness();
  let view = app.render();
  await app.button(view, 'kakao').props.onClick();
  view = app.render();
  assert.equal(app.button(view, 'kakao').props.disabled, true);
  assert.equal(app.button(view, 'naver').props.disabled, true);
  app.pageshow(true);
  view = app.render();
  assert.equal(app.button(view, 'kakao').props.disabled, false);
  assert.equal(app.button(view, 'naver').props.disabled, false);
  await app.button(view, 'naver').props.onClick();
  assert.deepEqual(app.starts, ['kakao','naver']);
  app.unmount();
  assert.equal(app.listenerCount(), 0);
});

test('An ordinary pageshow does not release a pending login lock', async () => {
  const app = loginHarness();
  const before = app.render();
  await app.button(before, 'kakao').props.onClick();
  app.pageshow(false);
  const pending = app.render();
  assert.equal(app.button(pending, 'naver').props.disabled, true);
  await app.button(pending, 'naver').props.onClick();
  assert.deepEqual(app.starts, ['kakao']);
  app.unmount();
});
