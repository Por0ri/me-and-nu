// Prepare an ignored copy with an in-memory fetch boundary. Never copy .env files.
const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve(__dirname, '..');
const target = path.resolve(source, '../.preview-pivot/request-timeout/frontend');
fs.mkdirSync(target, { recursive: true });
fs.cpSync(source, target, { recursive: true, filter: file => {
  const name = path.basename(file);
  return !['node_modules', '.next', 'dev'].includes(name) && !name.startsWith('.env') && !name.endsWith('.tsbuildinfo');
} });
if (!fs.existsSync(path.join(target, 'node_modules'))) fs.symlinkSync(path.join(source, 'node_modules'), path.join(target, 'node_modules'), 'junction');
const apiFile = path.join(target, 'lib/api.ts');
fs.writeFileSync(apiFile, `import { fixtureFetch as fetch } from '@/tests/fixtures/request-timeout-transport';\n` + fs.readFileSync(apiFile, 'utf8'));
const serviceFile = path.join(target, 'lib/pivot/services.ts');
const services = fs.readFileSync(serviceFile, 'utf8');
const guard = /export function canUsePivotApi\(\): boolean \{[\s\S]*?\r?\n\}/;
if (!guard.test(services)) throw Error('Fixture guard replacement no longer matches');
fs.writeFileSync(serviceFile, services.replace(guard, 'export function canUsePivotApi(): boolean { return true; } // In-memory preview only.'));
fs.mkdirSync(path.join(target, 'app/request-timeout'), { recursive: true });
fs.writeFileSync(path.join(target, 'app/request-timeout/page.tsx'),
  `import { FixtureRequestTimeoutPage } from '@/tests/fixtures/request-timeout-page';\nexport default FixtureRequestTimeoutPage;\n`);
console.log(target);
