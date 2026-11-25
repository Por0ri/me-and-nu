require('./net-guard.cjs');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { root } = require('./config.cjs');
(async () => {
  const results = [];
  const attempts = {
    fetch: () => fetch('https://external-probe.invalid/never-connect'),
    dns: () => require('node:dns').promises.lookup('external-probe.invalid'),
    socket: () => require('node:net').connect({ host: '192.0.2.1', port: 443 }),
    wildcardListen: () => require('node:net').createServer().listen({ port: 0, host: '0.0.0.0' }),
    lanListen: () => require('node:net').createServer().listen({ port: 0, host: '192.0.2.1' }),
  };
  // Guards throw before native DNS/connect/listen. No LAN socket is created or opened.
  for (const [kind, attempt] of Object.entries(attempts)) {
    let code; try { await attempt(); } catch (error) { code = error.code; }
    assert.equal(code, 'LAB_EXTERNAL_BLOCKED', kind); results.push({ kind, blockedBeforeNativeOperation: true });
  }
  fs.writeFileSync(path.join(root, 'results/egress-probes.json'), JSON.stringify({ passed: true, results, externalPacketsAttemptedByProbes: false, lanListenerCreated: false }, null, 2));
  console.log('PASS 5 guard probes; all rejected before native operation');
})().catch(error => { console.error(error); process.exitCode = 1; });
