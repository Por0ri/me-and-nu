const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const jsx = require('react/jsx-runtime');
const { renderToStaticMarkup } = require('react-dom/server');
const { redirect } = require('next/navigation');

const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../app/page.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const consumerComponents = ['ContentBrowser', 'ApiHealthCheck', 'AuthIntegrationTest'];

function rootPage(initialHeaders = {}) {
  let requestHeaders = initialHeaders;
  const elements = [], rendered = [];
  const dependencies = {
    'next/headers': { async headers() { return new Headers(requestHeaders); } },
    'next/navigation': { redirect },
    'next/link': { default: ({ children, ...props }) => React.createElement('a', props, children) },
    'react/jsx-runtime': Object.fromEntries(['jsx', 'jsxs'].map(name => [name, (...args) => {
      elements.push(args[0]);
      return jsx[name](...args);
    }])),
  };
  for (const name of consumerComponents) {
    dependencies['@/components/' + name] = { default() {
      rendered.push(name);
      return React.createElement('section', { 'data-consumer': name });
    } };
  }
  const mod = { exports: {} };
  vm.runInNewContext(code, { module: mod, exports: mod.exports,
    require(name) {
      assert.ok(name in dependencies, 'Unexpected root dependency: ' + name);
      return dependencies[name];
    },
  });
  return { Home: mod.exports.default, elements, rendered,
    setHeaders(value) { requestHeaders = value; },
  };
}

for (const host of ['test.me-nu.kr', 'TEST.ME-NU.KR']) {
  test(host + ': redirects before creating any Consumer UI or mounting its effects', async () => {
    const page = rootPage({ host });
    await assert.rejects(() => page.Home(), error => {
      assert.equal(error.digest, 'NEXT_REDIRECT;replace;/pivot/login;307;');
      return true;
    });
    assert.deepEqual(page.elements, []);
    assert.deepEqual(page.rendered, []);
  });
}

for (const host of [
  undefined, '', 'localhost', 'localhost:3000', '127.0.0.1:3000', '[::1]:3000',
  'me-nu.kr', 'www.me-nu.kr', 'api.test.me-nu.kr', 'other.vercel.app',
  'sub.test.me-nu.kr', 'test.me-nu.kr.evil.test', 'evil-test.me-nu.kr',
  'test.me-nu.kr:444', 'test.me-nu.kr.', 'test.me-nu.kr, evil.test',
  'test.me-nu.kr@evil.test',
]) {
  test((host || '(missing/empty host)') + ': preserves the Consumer root and all three tools', async () => {
    const page = rootPage(host === undefined ? {} : { host });
    const html = renderToStaticMarkup(await page.Home());
    assert.match(html, /관심사에서 만나는 글/);
    assert.match(html, /id="api-test"/);
    assert.match(html, /개발용 API 점검/);
    assert.deepEqual(page.rendered, consumerComponents);
  });
}

test('Forwarded host cannot redirect another Consumer host or override the test host', async () => {
  for (const host of [undefined, 'localhost:3000', 'me-nu.kr']) {
    const page = rootPage({ ...(host ? { host } : {}),
      'x-forwarded-host': 'test.me-nu.kr', forwarded: 'host=test.me-nu.kr;proto=https',
    });
    renderToStaticMarkup(await page.Home());
    assert.deepEqual(page.rendered, consumerComponents);
  }
  const page = rootPage({ host: 'test.me-nu.kr', 'x-forwarded-host': 'me-nu.kr' });
  await assert.rejects(() => page.Home(), { digest: 'NEXT_REDIRECT;replace;/pivot/login;307;' });
  assert.deepEqual(page.elements, []);
});

test('The same server module evaluates the host separately for every root request', async () => {
  const page = rootPage({ host: 'test.me-nu.kr' });
  await assert.rejects(() => page.Home(), { digest: 'NEXT_REDIRECT;replace;/pivot/login;307;' });
  page.setHeaders({ host: 'localhost:3000' });
  assert.match(renderToStaticMarkup(await page.Home()), /개발용 API 점검/);
  page.setHeaders({ host: 'test.me-nu.kr' });
  const previousElements = page.elements.length;
  await assert.rejects(() => page.Home(), { digest: 'NEXT_REDIRECT;replace;/pivot/login;307;' });
  assert.equal(page.elements.length, previousElements);
  assert.deepEqual(page.rendered, consumerComponents);
});
