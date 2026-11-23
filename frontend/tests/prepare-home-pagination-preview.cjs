// Same isolated preview pattern as prepare-admin-members-preview.cjs; no .env is copied.
const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve(__dirname, '..');
const target = path.resolve(source, '../.preview-pivot/home-pagination/frontend');
fs.mkdirSync(target, { recursive: true });
fs.cpSync(source, target, { recursive: true, filter: file => {
  const name = path.basename(file);
  return !['node_modules', '.next', 'dev'].includes(name) && !name.startsWith('.env') && !name.endsWith('.tsbuildinfo');
} });
if (!fs.existsSync(path.join(target, 'node_modules'))) fs.symlinkSync(path.join(source, 'node_modules'), path.join(target, 'node_modules'), 'junction');
fs.mkdirSync(path.join(target, 'app/home-pagination'), { recursive: true });
fs.writeFileSync(path.join(target, 'app/home-pagination/page.tsx'),
  `import { FixtureHomePaginationPage } from '@/tests/fixtures/home-pagination-page';\nexport default FixtureHomePaginationPage;\n`);
console.log(target);
