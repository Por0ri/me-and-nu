require('./net-guard.cjs');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { root, packageRoot, origin, host, port, variants } = require('./config.cjs');
const { environment } = require('./build.cjs');
const { createSession, dispatch, manifest } = require('./mock.cjs');
if (process.argv.length !== 2) throw new Error('This experiment supports loopback only; no LAN mode');
require('./check.cjs');
const browserRuntime = fs.readFileSync(path.join(packageRoot, 'browser-runtime.js'), 'utf8');
const sessions = new Map(), children = [], handles = [], records = [];
let ownsMarker = false;
const control = crypto.randomBytes(32).toString('hex');
const requestLog = path.join(root, 'results/server-requests.jsonl');
const privateFile = path.join(root, 'private/runtime.json');
const csp = "default-src 'self'; connect-src 'self'; img-src 'self' blob: data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; media-src 'self' blob:; worker-src 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; report-uri /__lab/csp";
const append = record => {
  const row = { at: new Date().toISOString(), ...record }; records.push(row);
  fs.appendFileSync(requestLog, JSON.stringify(row) + '\n');
};
const cookieName = '__Host-lab4tls_session';
const cookie = s => cookieName + '=' + s.token + '; Secure; HttpOnly; SameSite=Lax; Path=/';
const findSession = req => sessions.get((req.headers.cookie || '').split(';').map(s => s.trim()).find(s => s.startsWith(cookieName + '='))?.slice(cookieName.length + 1));
function send(res, status, body, headers = {}) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers });
  res.end(body === undefined ? undefined : JSON.stringify(body));
}
async function body(req) {
  const chunks = []; let size = 0;
  for await (const data of req) { size += data.length; if (size > 256 * 1024) throw new Error('BODY_TOO_LARGE'); chunks.push(data); }
  const raw = Buffer.concat(chunks).toString('utf8');
  return raw ? JSON.parse(raw) : null;
}
let server, stopping = false;
async function stop() {
  if (stopping) return; stopping = true;
  server?.close(); server?.closeAllConnections();
  for (const child of children) child.kill();
  for (const handle of handles) fs.closeSync(handle);
  if (ownsMarker && fs.existsSync(privateFile)) fs.unlinkSync(privateFile);
  fs.writeFileSync(path.join(root, 'results/server-summary.json'), JSON.stringify({
    bind: { host, port }, requests: records.length,
    byVariant: Object.fromEntries(Object.keys(variants).map(v => [v, records.filter(r => r.variant === v).length])),
    byDestination: Object.fromEntries([...new Set(records.map(r => r.url && new URL(r.url).origin).filter(Boolean))]
      .map(o => [o, records.filter(r => r.url && new URL(r.url).origin === o).length])),
    undefinedApiRequests: records.filter(r => r.code === 'MOCK_ROUTE_NOT_DEFINED').length,
    outboundFallback: false, credentialValuesLogged: false,
  }, null, 2));
  setTimeout(() => process.exit(process.exitCode || 0), 300);
}
async function waitForNext(child, nextPort) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error('Next process exited: ' + child.exitCode);
    const ready = await new Promise(resolve => {
      const request = http.get({ host, port: nextPort, path: '/api/health', timeout: 800 }, res => { res.resume(); resolve(res.statusCode === 200); });
      request.on('timeout', () => request.destroy()); request.on('error', () => resolve(false));
    });
    if (ready) return;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('Next readiness timed out');
}
async function start() {
  if (fs.existsSync(privateFile)) throw new Error('Runtime marker exists; inspect/stop that lab first');
  const leaf = new crypto.X509Certificate(fs.readFileSync(path.join(root, 'public/server.pem')));
  const ca = new crypto.X509Certificate(fs.readFileSync(path.join(root, 'public/lab-ca.pem')));
  if (!leaf.checkIP(host) || Date.now() < Date.parse(leaf.validFrom) || Date.now() >= Date.parse(leaf.validTo) || !leaf.verify(ca.publicKey)) throw new Error('Invalid/expired TLS certificate');
  const tls = {key: fs.readFileSync(path.join(root, 'private/server.key')), cert: fs.readFileSync(path.join(root, 'public/server.pem')), minVersion: 'TLSv1.2'};
  for (const [name, variant] of Object.entries(variants)) {
    const log = fs.openSync(path.join(root, 'results/runtime-' + name + '.log'), 'w'); handles.push(log);
    const child = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', host, '--port', String(variant.port)], {
      cwd: path.join(root, name, 'frontend'), env: environment(), windowsHide: true, stdio: ['ignore', log, log],
    });
    child.on('error', error => { console.error(error); void stop(); }); children.push(child);
    await waitForNext(child, variant.port);
  }
  server = https.createServer(tls, async (req, res) => {
    const peer = req.socket.remoteAddress;
    if (peer !== '127.0.0.1' || req.headers.host !== '127.0.0.1:' + port || !req.url.startsWith('/') || req.url.startsWith('//')) {
      send(res, 403, { code: 'LOOPBACK_ONLY' }); return;
    }
    const url = new URL(req.url, origin), session = findSession(req);
    const entry = { method: req.method, url: url.href, variant: session?.variant, run: session?.run, peer };
    const policy = session?.variant === "B" ? csp.replace("img-src 'self' blob: data:", "img-src 'none'") : csp;
    res.setHeader('Content-Security-Policy', policy);
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (req.headers.origin && req.headers.origin !== origin) {
      append({ ...entry, status: 403, kind: 'origin-rejected' }); send(res, 403, { code: 'LOCAL_ORIGIN_REQUIRED' }); return;
    }
    try {
      if (url.pathname === '/__lab/health' && req.method === 'GET') {
        append({ ...entry, status: 200, kind: 'lab' });
        send(res, 200, { ready: true, origin, mode: 'loopback', bind: server.address(), listeners: [server.address()],
          next: children.map((child, i) => ({ variant: Object.keys(variants)[i], pid: child.pid, host, port: Object.values(variants)[i].port })),
          fixture: { cards: manifest.cards, contentSha256: manifest.contentSha256 }, fallback: false }); return;
      }
      if (url.pathname === '/__lab/stop' && req.method === 'POST') {
        if (req.headers['x-lab-control'] !== control) { send(res, 403, { code: 'CONTROL_REQUIRED' }); return; }
        append({ ...entry, status: 200, kind: 'lab-stop' }); send(res, 200, { stopping: true }); void stop(); return;
      }
      if (url.pathname === '/__lab/csp' && req.method === 'POST') {
        const report = await body(req), violation = report?.['csp-report'];
        append({ ...entry, status: 204, kind: 'csp-block', blockedURI: violation?.['blocked-uri'], directive: violation?.['effective-directive'] });
        res.writeHead(204).end(); return;
      }
      const select = url.pathname.match(/^\/__lab\/start\/(A|B|C|D)$/);
      if (select && req.method === 'GET') {
        const slideCount = Number(url.searchParams.get('slides') || 10);
        if (![2, 5, 10].includes(slideCount)) { send(res, 400, { code: 'INVALID_SLIDE_COUNT' }); return; }
        const next = createSession(select[1], slideCount); sessions.set(next.token, next);
        append({ ...entry, variant: next.variant, run: next.run, status: 302, kind: 'start', slideCount });
        res.writeHead(302, { 'Set-Cookie': cookie(next), Location: '/pivot/home', 'Cache-Control': 'no-store' }).end(); return;
      }
      if (url.pathname === '/__lab' || url.pathname === '/') {
        append({ ...entry, status: 200, kind: 'index' });
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
        res.end('<!doctype html><html lang="ko"><meta name="viewport" content="width=device-width"><title>Windows HTTPS A/B/C/D</title><body style="font:18px sans-serif;margin:40px"><h1>Safari 원인 분리 A/B/C/D</h1><p>127.0.0.1 HTTPS 전용 · 합성 123건 · 외부 API 없음</p>' +
          Object.entries(variants).map(([name, v]) => '<p><a href="/__lab/start/' + name + '?slides=10">' + name + ' (' + v.mode + '): 최초 ' + v.pageSize + '건 / 10슬라이드</a> (' + v.ref.slice(0, 8) + ')</p>').join('') +
          '<p>같은 브라우저에서는 한 번에 한 버전만 사용하세요. 링크를 다시 열면 새 합성 세션입니다.</p><p>2·5슬라이드: URL의 slides 값을 바꾸세요. 실제 계정 로그인은 지원하지 않습니다.</p></body></html>'); return;
      }
      if (url.pathname.startsWith('/__lab/source/')) {
        append({ ...entry, status: 200, kind: 'synthetic-source' }); send(res, 200, { message: 'Local synthetic content. No external source.' }); return;
      }
      if (session?.variant === 'B' && /(?:\/slides\/\d+\/image|\/_next\/image|\.(?:png|jpe?g|gif|webp|avif|svg|ico|bmp)$)/i.test(url.pathname)) { append({ ...entry, status: 403, kind: 'B-image-denied' }); send(res, 403, { code: 'LAB_B_IMAGE_DISABLED' }); return; }
      if (url.pathname.startsWith('/api/')) {
        const data = ['GET', 'HEAD'].includes(req.method) ? null : await body(req);
        const reply = dispatch(url, req.method, req.headers, data, session);
        append({ ...entry, status: reply.status, kind: reply.bytes ? 'protected-image' : reply.audit?.ids ? 'feed' : 'api',
          code: reply.body?.code, ...reply.audit });
        if (reply.bytes) { res.writeHead(reply.status, { 'Content-Type': reply.contentType, 'Content-Length': reply.bytes.length, 'Cache-Control': 'no-store' }); res.end(reply.bytes); }
        else send(res, reply.status, reply.body);
        return;
      }
      if (!session) { res.writeHead(302, { Location: '/__lab', 'Cache-Control': 'no-store' }).end(); return; }
      if (!['GET', 'HEAD'].includes(req.method) || !['/pivot', '/_next/', '/local-content/', '/ui-onboarding/', '/fonts/', '/favicon.ico'].some(prefix => url.pathname.startsWith(prefix))) {
        append({ ...entry, status: 404, kind: 'route-rejected' }); send(res, 404, { code: 'LAB_ROUTE_NOT_DEFINED' }); return;
      }
      if (url.pathname === '/_next/image') {
        const imageUrl = new URL(url.searchParams.get('url') || '/', origin);
        if (imageUrl.origin !== origin) { append({ ...entry, status: 403, kind: 'image-proxy-rejected' }); send(res, 403, { code: 'EXTERNAL_IMAGE_DISABLED' }); return; }
      }
      const upstream = variants[session.variant].port;
      const proxy = http.request({ host, port: upstream, method: req.method, path: url.pathname + url.search,
        headers: { ...req.headers, host: '127.0.0.1:' + port, 'x-forwarded-host': '127.0.0.1:' + port, 'x-forwarded-proto': 'https', 'accept-encoding': 'identity' } }, response => {
        if (response.headers.location && new URL(response.headers.location, origin).origin !== origin) {
          response.resume(); append({ ...entry, status: 502, kind: 'redirect-rejected' }); send(res, 502, { code: 'EXTERNAL_REDIRECT_DISABLED' }); return;
        }
        append({ ...entry, status: response.statusCode, kind: 'frontend', upstream: 'http://' + host + ':' + upstream + url.pathname + url.search });
        const headers = { ...response.headers, 'Content-Security-Policy': policy, 'Cache-Control': 'no-store' };
        if (String(response.headers['content-type']).includes('text/html')) {
          const chunks = []; response.on('data', chunk => chunks.push(chunk));
          response.on('end', () => {
            let html = Buffer.concat(chunks).toString('utf8');
            if (session.variant === 'B') html = html.replace(/<link\b[^>]*(?:rel=["'](?:icon|apple-touch-icon)["']|as=["']image["'])[^>]*>/gi, '');
            html = html.replace(/<head([^>]*)>/i, '<head$1><script>window.__LAB_VARIANT__=' + JSON.stringify(session.variant) + ';' + browserRuntime + '</script>');
            delete headers['content-length']; delete headers['content-encoding']; delete headers.etag;
            res.writeHead(response.statusCode, headers); res.end(html);
          });
        } else { res.writeHead(response.statusCode, headers); response.pipe(res); }
      });
      proxy.on('error', () => { if (!res.headersSent) send(res, 502, { code: 'LOCAL_FE_UNAVAILABLE' }); else res.destroy(); });
      req.pipe(proxy);
    } catch (error) {
      append({ ...entry, status: 500, kind: 'lab-error', code: error.message });
      if (!res.headersSent) send(res, 500, { code: 'LAB_ERROR' }); else res.destroy();
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, host, resolve); });
  fs.writeFileSync(privateFile, JSON.stringify({ pid: process.pid, control, origin })); ownsMarker = true;
  console.log('READY ' + origin + '/__lab (A=100, B=100/no-images, C=100/one-card, D=20; loopback only)');
  fs.writeFileSync(path.join(root, 'results/listeners.json'), JSON.stringify({
    gateway: server.address(), next: Object.entries(variants).map(([variant, v]) => ({ variant, address: host, port: v.port })),
  }, null, 2));
}
process.on('SIGINT', () => void stop()); process.on('SIGTERM', () => void stop());
start().catch(error => { console.error(error); process.exitCode = 1; void stop(); });
