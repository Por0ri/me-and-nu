const crypto = require('node:crypto');
const path = require('node:path');

// Source manifests record the Git LF form of textual SVGs. Windows autocrlf
// changes only their checkout line endings; binary images remain byte-exact.
function sourceAssetHash(file, bytes) {
  const source = path.extname(file).toLowerCase() === '.svg'
    ? Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'), 'utf8')
    : bytes;
  return crypto.createHash('sha256').update(source).digest('hex');
}

module.exports = { sourceAssetHash };
