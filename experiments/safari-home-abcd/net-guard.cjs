// Loaded into build/runtime processes and inherited by their Node children.
// No DNS or socket connection to a non-loopback host; no wildcard/LAN listener.
const fs = require('node:fs');
const net = require('node:net');
const dns = require('node:dns');
const path = require('node:path');
const allowed = new Set(['127.0.0.1', '::1', 'localhost']);
const log = process.env.LAB_EGRESS_LOG || path.join(require('./config.cjs').root, 'results/node-egress.jsonl');
function block(kind, destination) {
  fs.appendFileSync(log, JSON.stringify({ at: new Date().toISOString(), pid: process.pid,
    kind, destination: String(destination), blocked: true }) + '\n');
  const error = new Error('LAB_EXTERNAL_BLOCKED: ' + destination);
  error.code = 'LAB_EXTERNAL_BLOCKED';
  throw error;
}
function check(host, kind) {
  if (host && !allowed.has(String(host))) block(kind, host);
}
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  let first = Array.isArray(args[0]) ? args[0][0] : args[0];
  if (typeof first === 'object' && first !== null) {
    if (!first.path) check(first.host || 'localhost', 'socket');
  } else if (typeof first === 'number') check(typeof args[1] === 'string' ? args[1] : 'localhost', 'socket');
  return connect.apply(this, args);
};
const listen = net.Server.prototype.listen;
net.Server.prototype.listen = function (...args) {
  const first = args[0];
  const ipc = typeof first === 'string' || (first && typeof first === 'object' && first.path);
  const host = typeof first === 'object' ? first?.host : args[1];
  if (!ipc && host !== '127.0.0.1') block('non-loopback-listen', host || 'wildcard');
  return listen.apply(this, args);
};
const lookup = dns.lookup;
dns.lookup = function (host, ...args) { check(host, 'dns'); return lookup.call(this, host, ...args); };
const promiseLookup = dns.promises.lookup;
dns.promises.lookup = async function (host, ...args) { check(host, 'dns'); return promiseLookup.call(this, host, ...args); };
const originalFetch = globalThis.fetch;
globalThis.fetch = function (input, ...args) {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (!allowed.has(url.hostname.replace(/^\[|\]$/g, ''))) return Promise.reject((() => {
    try { block('fetch', url.href); } catch (e) { return e; }
  })());
  return originalFetch.call(this, input, ...args);
};
