const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const {
  getRedirectUrl,
  unstable_getResponseFromNextConfig,
} = require('next/experimental/testing/server');

const configPath = path.resolve(__dirname, '../next.config.ts');
const compiled = ts.transpileModule(fs.readFileSync(configPath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const configModule = { exports: {} };
vm.runInNewContext(compiled, { module: configModule, exports: configModule.exports }, { filename: configPath });
const nextConfig = configModule.exports.default;

test('Main and Checking root requests enter pivot and preserve query parameters', async () => {
  for (const origin of ['https://www.me-nu.kr', 'https://checking.me-nu.kr']) {
    const response = await unstable_getResponseFromNextConfig({
      url: `${origin}/?from=beta`, nextConfig,
    });
    assert.equal(response.status, 307);
    assert.equal(getRedirectUrl(response), `${origin}/pivot?from=beta`);
  }
});

test('Main root redirect leaves other hosts and existing routes unchanged', async () => {
  for (const url of [
    // The apex-to-www canonical redirect is owned by the hosting configuration.
    'https://me-nu.kr/',
    'https://test.me-nu.kr/',
    'https://labs.me-nu.kr/',
    'https://preview.vercel.app/',
    'https://me-nu.kr.example.com/',
    'https://www.me-nu.kr.example.com/',
    'https://fakewww.me-nu.kr/',
    'https://checking.me-nu.kr.example.com/',
    'http://localhost:3000/',
    'https://www.me-nu.kr/pivot',
    'https://www.me-nu.kr/pivot/home',
    'https://www.me-nu.kr/api/health',
    'https://checking.me-nu.kr/pivot',
  ]) {
    const response = await unstable_getResponseFromNextConfig({ url, nextConfig });
    assert.equal(response.status, 200, url);
    assert.equal(getRedirectUrl(response), null, url);
  }
});
