'use strict';

const zlib = require('node:zlib');

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, bytes) {
  const name = Buffer.from(type);
  const size = Buffer.alloc(4);
  size.writeUInt32BE(bytes.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([name, bytes])));
  return Buffer.concat([size, name, bytes, checksum]);
}

function trayIconPNG() {
  // Project-authored 24px cat silhouette. No upstream artwork is used here.
  const size = 24;
  const rows = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const face = ((x - 12) / 10) ** 2 + ((y - 14) / 8) ** 2 < 1;
      const ears = y >= 2 && y < 11 && ((x >= 3 && x < 3 + y) || (x <= 20 && x > 20 - y));
      const eye = (x === 8 || x === 16) && y >= 12 && y <= 14;
      const nose = x === 12 && y === 17;
      const index = y * (size * 4 + 1) + 1 + x * 4;
      if (face || ears) {
        const value = eye || nose ? 240 : 53;
        rows[index] = value; rows[index + 1] = value; rows[index + 2] = value;
        rows[index + 3] = 255;
      }
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', header), chunk('IDAT', zlib.deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}

module.exports = { trayIconPNG };
