const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { root, packageRoot, variants, origin } = require('./config.cjs');
function environment() {
  const keys = new Set(["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"]);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => keys.has(key.toUpperCase())));
  // No inherited deployment, API, DB or proxy configuration enters the lab.
  for (const key of Object.keys(env)) {
    if (/^(NEXT_PUBLIC_|DATABASE_|DB_|PG|REDIS_|VERCEL_|PIVOT_|HTTP_PROXY$|HTTPS_PROXY$|ALL_PROXY$|NODE_OPTIONS$)/i.test(key)) delete env[key];
  }
  return { ...env, NODE_ENV: 'production', NEXT_TELEMETRY_DISABLED: '1',
    NEXT_PUBLIC_API_BASE_URL: origin, NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'true',
    NODE_OPTIONS: '--require=' + JSON.stringify(path.join(packageRoot, 'net-guard.cjs')),
    LAB_EGRESS_LOG: path.join(root, 'results/node-egress.jsonl') };
}
async function build(name) {
  const cwd = path.join(root, name, 'frontend');
  const log = fs.openSync(path.join(root, 'results/build-' + name + '.log'), 'w');
  const processBuild = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'build', '--webpack'], {
    cwd, env: environment(), windowsHide: true, stdio: ['ignore', log, log],
  });
  const code = await new Promise((resolve, reject) => { processBuild.once('error', reject); processBuild.once('exit', resolve); });
  fs.closeSync(log);
  if (code !== 0) throw new Error('Build ' + name + ' failed: ' + code + '; inspect build-' + name + '.log');
  console.log('PASS production Webpack build ' + name);
  return { variant: name, code, buildId: fs.readFileSync(path.join(cwd, '.next/BUILD_ID'), 'utf8').trim() };
}
if (require.main === module) (async () => {
  const selected = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(variants);
  if (selected.some(name=>!variants[name])) throw new Error('Unknown variant');
  const target = path.join(root, 'results/build-results.json');
  const results = fs.existsSync(target) ? JSON.parse(fs.readFileSync(target)) : [];
  for (const name of selected) { const result=await build(name); const old=results.findIndex(r=>r.variant===name); if(old>=0)results.splice(old,1); results.push(result); fs.writeFileSync(target,JSON.stringify(results,null,2)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { environment };
