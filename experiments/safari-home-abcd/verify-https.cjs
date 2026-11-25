require('./net-guard.cjs');
const fs = require('node:fs');
const path = require('node:path');
const https = require('node:https');
const tls = require('node:tls');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { root, host, port, origin, variants } = require('./config.cjs');
const { request, ca } = require('./local-client.cjs');
const probes = [];
async function probe(name, task) { const details = await task(); probes.push({ name, status: 'pass', ...details }); }
(async () => {
  await probe('TLS chain, SAN, validity and peer authorization', () => new Promise((resolve, reject) => {
    const socket = tls.connect({ host, port, ca, rejectUnauthorized: true, minVersion: 'TLSv1.2' }, () => {
      try {
        const cert = socket.getPeerX509Certificate();
        assert.equal(socket.authorized, true); assert.equal(cert.checkIP(host), host);
        assert.ok(Date.now() >= Date.parse(cert.validFrom) && Date.now() < Date.parse(cert.validTo));
        assert.equal(cert.checkIP('192.0.2.1'), undefined);
        resolve({ authorized: true, protocol: socket.getProtocol(), subjectAltName: cert.subjectAltName, fingerprint256: cert.fingerprint256, unknownIpRejected: true });
      } catch (e) { reject(e); } finally { socket.end(); }
    }); socket.on('error', reject);
  }));
  await probe('Untrusted CA rejected without process-scoped trust', () => new Promise((resolve, reject) => {
    const req = https.get({ host, port, path: '/__lab/health', ca: [], rejectUnauthorized: true }, res => { res.resume(); reject(new Error('Unexpected implicit trust')); });
    req.on('error', error => { if (!['UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY'].includes(error.code)) reject(error); else resolve({ errorCode: error.code }); });
  }));
  await probe('Loopback listeners, same origin and CSP', async () => {
    const response = await request('/__lab/health'), health = JSON.parse(response.bytes);
    assert.equal(response.status, 200); assert.equal(health.mode, 'loopback');
    assert.ok(health.listeners.every(s => s.address === host)); assert.ok(health.next.every(s => s.host === host));
    assert.ok(response.headers['content-security-policy'].includes("connect-src 'self'"));
    assert.equal(response.headers['access-control-allow-origin'], undefined);
    return { origin, listeners: health.listeners, next: health.next, wildcardCors: false };
  });
  for (const variant of Object.keys(variants)) await probe('Synthetic HTTPS session and API restrictions ' + variant, async () => {
    const start = await request('/__lab/start/' + variant + '?slides=10');
    const setCookie = start.headers['set-cookie'][0];
    assert.equal(start.status, 302); assert.ok(setCookie.startsWith('__Host-lab4tls_session='));
    for (const flag of ['Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/']) assert.ok(setCookie.includes(flag));
    assert.ok(!/Domain=/i.test(setCookie));
    const cookie = setCookie.split(';')[0], headers = { Cookie: cookie };
    const session = JSON.parse((await request('/api/v1/auth/session', { headers })).bytes);
    assert.equal(session.authenticated, true); assert.equal(session.user.id, 12);
    const unknown = await request('/api/v1/not-defined?destination=https%3A%2F%2Fexternal-probe.invalid', { headers });
    assert.equal(unknown.status, 404); assert.equal(JSON.parse(unknown.bytes).code, 'MOCK_ROUTE_NOT_DEFINED');
    const oauth = await request('/api/v1/auth/login/kakao', { method: 'POST', headers }); assert.equal(oauth.status, 403);
    const csrf = await request('/api/v1/contents/1000/bookmark', { method: 'PUT', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ topicId: 7 }) }); assert.equal(csrf.status, 403);
    const image = await request('/api/v1/contents/1000/slides/1/image?topicId=7', { headers }); assert.equal(image.status, variant === 'B' ? 403 : 200);
    const outsideImage = await request('/_next/image?url=https%3A%2F%2Fexternal-probe.invalid%2Fimage.png&w=640&q=75', { headers }); assert.equal(outsideImage.status, 403);
    const crossOrigin = await request('/api/v1/auth/session', { headers: { ...headers, Origin: 'https://external-probe.invalid' } }); assert.equal(crossOrigin.status, 403);
    const wrongHost = await request('/__lab/health', { headers: { Host: '192.0.2.1:' + port } }); assert.equal(wrongHost.status, 403);
    const privateFile = await request('/private/server.key', { headers }); assert.equal(privateFile.status, 404);
    assert.equal((await request('/api/v1/contents/1000/slides/1/image?topicId=7')).status, 401);
    return { flags: { secure: true, httpOnly: true, sameSite: 'Lax', hostOnly: true, path: '/' },
      protectedImageStatus: image.status, imageSha256: variant === 'B' ? null : crypto.createHash('sha256').update(image.bytes).digest('hex'), unknownApi: unknown.status,
      oauth: oauth.status, missingCsrf: csrf.status, externalImageProxy: outsideImage.status,
      crossOrigin: crossOrigin.status, lanHostOnLoopback: wrongHost.status, privateKeyHttpAccess: privateFile.status, unauthenticatedImage: 401 };
  });
  fs.writeFileSync(path.join(root, 'results/https-verification.json'), JSON.stringify({ probes, passed: true, systemTrustChanged: false, lanConnectionAttempted: false }, null, 2));
  console.log(JSON.stringify({ passed: true, probes: probes.map(p => p.name) }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
