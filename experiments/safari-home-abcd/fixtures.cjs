const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const { root, packageRoot } = require('./config.cjs');
const width = 1308, height = 1632;
const sha = buffer => crypto.createHash('sha256').update(buffer).digest('hex');
const table = new Uint32Array(256);
for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; table[n] = c; }
function chunk(name, data) {
  const type = Buffer.from(name), combined = Buffer.concat([type, data]);
  let crc = 0xffffffff; for (const byte of combined) crc = table[(crc ^ byte) & 255] ^ (crc >>> 8);
  const length = Buffer.alloc(4), checksum = Buffer.alloc(4);
  length.writeUInt32BE(data.length); checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([length, combined, checksum]);
}
function png(page) {
  const pixels = Buffer.alloc((width * 3 + 1) * height);
  let seed = 0x20261125 ^ page;
  for (let y = 0; y < height; y++) {
    const line = y * (width * 3 + 1); pixels[line] = 0;
    for (let x = 0; x < width; x++) {
      seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
      const grain = (seed >>> 0) % 12, i = line + 1 + x * 3;
      pixels[i] = ((x >> 3) + page * 23 + grain) & 255;
      pixels[i + 1] = ((y >> 3) + page * 41 + grain) & 255;
      pixels[i + 2] = (((x + y) >> 4) + page * 67 + grain) & 255;
    }
  }
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(pixels, { level: 6 })), chunk('IEND', Buffer.alloc(0))]);
}
const contents = Array.from({ length: 123 }, (_, i) => ({
  id: 1000 - i, title: '합성 카드 ' + (i + 1), summary: 'Windows 내부 A/B 검증용 합성 요약 ' + (i + 1),
  body: '운영 데이터가 없는 합성 본문입니다.\n\n카드 순서와 페이지네이션, 화면 이동을 확인합니다.',
}));
function generate() {
  const folder = path.join(root, 'fixtures');
  fs.mkdirSync(folder,{recursive:true});
  if (fs.existsSync(path.join(folder, 'manifest.json'))) throw new Error('Fixtures already exist; refusing overwrite');
  const json = JSON.stringify(contents, null, 2);
  fs.writeFileSync(path.join(folder, 'contents.json'), json);
  const images = [];
  for (let page = 1; page <= 10; page++) {
    const image = png(page), name = 'slide-' + page + '.png';
    fs.writeFileSync(path.join(folder, name), image);
    images.push({ page, name, width, height, bytes: image.length, sha256: sha(image) });
  }
  const manifest = { seed: '20261125', cards: 123, availableSlideCounts: [2, 5, 10], contentSha256: sha(json),
    imageReuse: 'One deterministic PNG per slide position, shared across cards; unique protected content/page URLs.',
    sharedBy: ['A', 'B', 'C', 'D'], images };
  fs.writeFileSync(path.join(folder, 'manifest.json'), JSON.stringify(manifest, null, 2));
  const expected=JSON.parse(fs.readFileSync(path.join(packageRoot,'fixture-manifest.json')));
  require('node:assert/strict').deepEqual(manifest,expected,'Fixture differs: use tested Node/zlib; do not compare different images.');
  console.log(JSON.stringify({ cards: 123, images: images.length, width, height, totalImageBytes: images.reduce((n, i) => n + i.bytes, 0), contentSha256: manifest.contentSha256 }));
}
if (require.main === module) generate();
module.exports = { contents };
