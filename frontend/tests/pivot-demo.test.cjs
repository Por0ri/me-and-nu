const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../lib/pivot/demo-access.ts'), 'utf8');
const output = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;
const moduleUnderTest = { exports: {} };
vm.runInNewContext(output, { module: moduleUnderTest, exports: moduleUnderTest.exports,
  require(name) { if (name !== 'server-only') throw new Error('Unexpected dependency'); return {}; },
});
const { canAccessPivotDemo } = moduleUnderTest.exports;
for (const [name, environment, expected] of [
  ['local development', { nodeEnv: 'development' }, true],
  ['local development environment', { nodeEnv: 'development', deploymentEnv: 'development' }, true],
  ['default production build', { nodeEnv: 'production' }, false],
  ['flag alone cannot open production', { nodeEnv: 'production', enabled: 'true' }, false],
  ['preview requires explicit opt-in', { nodeEnv: 'production', deploymentEnv: 'preview' }, false],
  ['approved preview', { nodeEnv: 'production', deploymentEnv: 'preview', enabled: 'true' }, true],
  ['production deployment rejects opt-in', { nodeEnv: 'production', deploymentEnv: 'production', enabled: 'true' }, false],
  ['production deployment also rejects dev runtime', { nodeEnv: 'development', deploymentEnv: 'production', enabled: 'true' }, false],
  ['preview flag must be exact', { nodeEnv: 'production', deploymentEnv: 'preview', enabled: 'TRUE' }, false],
  ['unknown environment fails closed', { nodeEnv: 'production', deploymentEnv: 'other', enabled: 'true' }, false],
  ['missing configuration fails closed', {}, false],
]) test(name, () => assert.equal(canAccessPivotDemo(environment), expected));
