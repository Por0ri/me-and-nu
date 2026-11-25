const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { root, openssl } = require('./config.cjs');
const priv = path.join(root, 'private'), pub = path.join(root, 'public');
const run = args => cp.execFileSync(openssl, args, { cwd: root, windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
assert.ok(fs.existsSync(priv) && fs.existsSync(pub));
for (const name of ['ca.key', 'server.key']) assert.equal(fs.existsSync(path.join(priv, name)), false, 'Refusing key overwrite');
if(process.platform==='win32') {
  const sid=cp.execFileSync('whoami',['/user','/fo','csv','/nh'],{encoding:'utf8',windowsHide:true}).match(/S-1-5-[\d-]+/)[0];
  const owner=cp.execFileSync('powershell.exe',['-NoProfile','-Command',
    '[System.IO.Directory]::GetAccessControl($env:LAB_PRIVATE).GetOwner([System.Security.Principal.SecurityIdentifier]).Value'],
    {env:{...process.env,LAB_PRIVATE:priv},encoding:'utf8',windowsHide:true}).trim();
  const identities=[...new Set([sid,owner,'S-1-5-18'])];
  cp.execFileSync('icacls',[priv,'/inheritance:r','/grant:r',...identities.map(value=>'*'+value+':(OI)(CI)F')],{windowsHide:true,stdio:'pipe'});
} else fs.chmodSync(priv,0o700);
const caKey = path.join(priv, 'ca.key'), serverKey = path.join(priv, 'server.key');
const caPem = path.join(pub, 'lab-ca.pem'), caDer = path.join(pub, 'lab-ca.cer'), leaf = path.join(pub, 'server.pem');
run(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', caKey]);
run(['req', '-new', '-x509', '-key', caKey, '-sha256', '-days', '7', '-out', caPem,
  '-subj', '/CN=Safari Synthetic Lab CA',
  '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0', '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
  '-addext', 'subjectKeyIdentifier=hash']);
run(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', serverKey]);
run(['req', '-new', '-key', serverKey, '-out', path.join(priv, 'server.csr'), '-subj', '/CN=Safari Synthetic Loopback']);
const extensions = 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n';
fs.writeFileSync(path.join(pub, 'server-extensions.cnf'), extensions);
run(['x509', '-req', '-in', path.join(priv, 'server.csr'), '-CA', caPem, '-CAkey', caKey,
  '-set_serial', '0x' + crypto.randomBytes(16).toString('hex'), '-days', '3', '-sha256',
  '-extfile', path.join(pub, 'server-extensions.cnf'), '-out', leaf]);
run(['x509', '-in', caPem, '-outform', 'DER', '-out', caDer]);
const localVerify = run(['verify', '-CAfile', caPem, '-purpose', 'sslserver', '-verify_ip', '127.0.0.1', leaf]);
const cert = new crypto.X509Certificate(fs.readFileSync(leaf));
const ca = new crypto.X509Certificate(fs.readFileSync(caPem));
const metadata = { ca: { subject: ca.subject, fingerprint256: ca.fingerprint256, validFrom: ca.validFrom, validTo: ca.validTo },
  server: { subject: cert.subject, fingerprint256: cert.fingerprint256, validFrom: cert.validFrom, validTo: cert.validTo,
    subjectAltName: cert.subjectAltName, spkiSha256: crypto.createHash('sha256').update(cert.publicKey.export({ type: 'spki', format: 'der' })).digest('base64') },
  checks: { loopback: localVerify.trim().endsWith(': OK'), chain: cert.verify(ca.publicKey) },
  privateAcl: 'Workspace owner, executing account and SYSTEM only; inherited broad access removed on new private directory',
  systemCertificateStoreChanged: false, iPhoneCertificateInstalled: false };
assert.ok(Object.values(metadata.checks).every(Boolean));
fs.writeFileSync(path.join(root, 'results/certificates.json'), JSON.stringify(metadata, null, 2));
console.log(JSON.stringify(metadata, null, 2));
