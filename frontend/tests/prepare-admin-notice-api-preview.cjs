// Isolated loopback preview using the real production route and API client.
// No hosted credentials, environment files, or fake route entry points are copied.
const fs = require('node:fs'), path = require('node:path');
if (process.env.VERCEL || process.env.VERCEL_ENV) throw new Error('Local preview only');
const source = path.resolve(__dirname, '..');
const target = path.resolve(source, '../.preview-pivot/admin-notices-api-20261123/frontend');
fs.mkdirSync(target, { recursive: true });
fs.cpSync(source, target, { recursive: true, filter: file => {
  const name = path.basename(file);
  return !['node_modules', '.next', 'dev'].includes(name) && !name.startsWith('.env') && !name.endsWith('.tsbuildinfo');
} });
if (!fs.existsSync(path.join(target, 'node_modules'))) fs.symlinkSync(fs.realpathSync(path.join(source, 'node_modules')), path.join(target, 'node_modules'), 'junction');
console.log(target);
