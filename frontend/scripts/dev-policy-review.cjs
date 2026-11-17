// Local-only opt-in. Bind loopback explicitly; never reuse hosted environment settings.
const { spawn } = require('node:child_process');
const path = require('node:path');
if (process.env.VERCEL_ENV || process.env.VERCEL) throw new Error('Policy review is local development only');
const child = spawn(process.execPath, [require.resolve('next/dist/bin/next'), 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', '3196'], {
  stdio: 'inherit', cwd: path.join(__dirname, '..'),
  env: { ...process.env, NODE_ENV: 'development', PIVOT_POLICY_REVIEW_ENABLED: 'true', NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API: 'false',
    PIVOT_POLICY_REVIEW_SOURCE: path.join(__dirname, '../dev/policy-review/team-policy-2026-10-23.md') },
});
child.on('exit', code => { process.exitCode = code ?? 1; });
process.on('SIGINT', () => child.kill('SIGINT'));
process.on('SIGTERM', () => child.kill('SIGTERM'));
