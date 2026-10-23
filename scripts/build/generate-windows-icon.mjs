// Generates desktop/icon.ico from desktop/icon.png.
//
// The packaged Windows executable previously fell back to the default
// Electron icon because the repo only shipped a PNG and the packager was
// never given an icon. ICO entries here are PNG-compressed (supported since
// Windows Vista), so no external image tooling is needed: this script
// decodes the source PNG, downscales it, re-encodes each size as PNG, and
// assembles the ICO. The output is validated (header, entry count, and
// every entry's PNG dimensions) before the script reports success.
//
// Usage: node scripts/build/generate-windows-icon.mjs [source.png] [target.ico]

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync, inflateSync } from "node:zlib";

export const ICON_SIZES = [16, 20, 24, 32, 48, 64, 128, 256];

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

const crcTable = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer)
    crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  chunk.write(type, 4, "ascii");
  data.copy(chunk, 8);
  chunk.writeUInt32BE(
    crc32(Buffer.concat([Buffer.from(type, "ascii"), data])),
    8 + data.length,
  );
  return chunk;
}

function paeth(left, up, upLeft) {
  const p = left + up - upLeft;
  const pa = Math.abs(p - left);
  const pb = Math.abs(p - up);
  const pc = Math.abs(p - upLeft);
  if (pa <= pb && pa <= pc) return left;
  if (pb <= pc) return up;
  return upLeft;
}

// Decodes an 8-bit, non-interlaced RGB or RGBA PNG into RGBA pixels.
export function decodePng(buffer) {
  if (!buffer.subarray(0, 8).equals(PNG_SIGNATURE))
    throw new Error("图标源文件不是 PNG");
  let offset = 8;
  let width = 0;
  let height = 0;
  let colorType = 0;
  const idat = [];
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    const data = buffer.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      if (data[8] !== 8 || data[12] !== 0)
        throw new Error("仅支持 8 位非隔行 PNG 图标源");
      colorType = data[9];
      if (colorType !== 2 && colorType !== 6)
        throw new Error(`不支持的 PNG 颜色类型 ${colorType}`);
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
    offset += 12 + length;
  }
  const channels = colorType === 6 ? 4 : 3;
  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const pixels = Buffer.alloc(width * height * 4);
  let prior = Buffer.alloc(stride);
  for (let y = 0; y < height; y++) {
    const rowStart = y * (stride + 1);
    const filter = raw[rowStart];
    const row = Buffer.from(raw.subarray(rowStart + 1, rowStart + 1 + stride));
    for (let i = 0; i < stride; i++) {
      const left = i >= channels ? row[i - channels] : 0;
      const up = prior[i];
      const upLeft = i >= channels ? prior[i - channels] : 0;
      let predictor = 0;
      if (filter === 1) predictor = left;
      else if (filter === 2) predictor = up;
      else if (filter === 3) predictor = Math.floor((left + up) / 2);
      else if (filter === 4) predictor = paeth(left, up, upLeft);
      else if (filter !== 0)
        throw new Error(`不支持的 PNG 行过滤类型 ${filter}`);
      row[i] = (row[i] + predictor) & 0xff;
    }
    for (let x = 0; x < width; x++) {
      const source = x * channels;
      const target = (y * width + x) * 4;
      pixels[target] = row[source];
      pixels[target + 1] = row[source + 1];
      pixels[target + 2] = row[source + 2];
      pixels[target + 3] = channels === 4 ? row[source + 3] : 255;
    }
    prior = row;
  }
  return { width, height, pixels };
}

// Area-average downscale of RGBA pixels to a square target size.
export function downscale({ width, height, pixels }, size) {
  if (width !== height) throw new Error("图标源必须是正方形");
  const output = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    const y0 = Math.floor((y * height) / size);
    const y1 = Math.max(y0 + 1, Math.floor(((y + 1) * height) / size));
    for (let x = 0; x < size; x++) {
      const x0 = Math.floor((x * width) / size);
      const x1 = Math.max(x0 + 1, Math.floor(((x + 1) * width) / size));
      const sums = [0, 0, 0, 0];
      let count = 0;
      for (let sy = y0; sy < y1; sy++) {
        for (let sx = x0; sx < x1; sx++) {
          const source = (sy * width + sx) * 4;
          for (let c = 0; c < 4; c++) sums[c] += pixels[source + c];
          count++;
        }
      }
      const target = (y * size + x) * 4;
      for (let c = 0; c < 4; c++)
        output[target + c] = Math.round(sums[c] / count);
    }
  }
  return output;
}

export function encodePng(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type RGBA
  const rows = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    rows[y * (size * 4 + 1)] = 0; // filter: none
    rgba.copy(rows, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  return Buffer.concat([
    PNG_SIGNATURE,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(rows, { level: 9 })),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

export function assembleIco(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(entries.length, 4);
  const directory = Buffer.alloc(16 * entries.length);
  let imageOffset = 6 + directory.length;
  entries.forEach((entry, index) => {
    const at = index * 16;
    directory[at] = entry.size >= 256 ? 0 : entry.size; // 0 means 256
    directory[at + 1] = entry.size >= 256 ? 0 : entry.size;
    directory[at + 2] = 0; // palette colors
    directory[at + 3] = 0; // reserved
    directory.writeUInt16LE(1, at + 4); // color planes
    directory.writeUInt16LE(32, at + 6); // bits per pixel
    directory.writeUInt32LE(entry.png.length, at + 8);
    directory.writeUInt32LE(imageOffset, at + 12);
    imageOffset += entry.png.length;
  });
  return Buffer.concat([
    header,
    directory,
    ...entries.map((entry) => entry.png),
  ]);
}

export function validateIco(buffer, expectedSizes = ICON_SIZES) {
  if (buffer.length < 6 || buffer.readUInt16LE(0) !== 0)
    throw new Error("ICO 文件头无效");
  if (buffer.readUInt16LE(2) !== 1) throw new Error("ICO 类型不是图标");
  const count = buffer.readUInt16LE(4);
  if (count !== expectedSizes.length)
    throw new Error(`ICO 条目数 ${count} 与预期 ${expectedSizes.length} 不符`);
  for (let index = 0; index < count; index++) {
    const at = 6 + index * 16;
    const size = buffer[at] === 0 ? 256 : buffer[at];
    if (size !== expectedSizes[index])
      throw new Error(`ICO 条目 ${index} 尺寸 ${size} 与预期不符`);
    if (buffer[at + 1] !== buffer[at])
      throw new Error(`ICO 条目 ${index} 宽高字节不一致`);
    if (buffer.readUInt16LE(at + 6) !== 32)
      throw new Error(`ICO 条目 ${index} 不是 32 位图标`);
    const length = buffer.readUInt32LE(at + 8);
    const offset = buffer.readUInt32LE(at + 12);
    const png = buffer.subarray(offset, offset + length);
    if (png.length !== length || !png.subarray(0, 8).equals(PNG_SIGNATURE))
      throw new Error(`ICO 条目 ${index} 的 PNG 数据无效`);
    // IHDR is the first chunk: width/height sit at bytes 16/20 of the PNG.
    if (png.readUInt32BE(16) !== size || png.readUInt32BE(20) !== size)
      throw new Error(`ICO 条目 ${index} 的 PNG 尺寸与目录声明不符`);
  }
  return count;
}

export function generateWindowsIcon(pngPath, icoPath, sizes = ICON_SIZES) {
  const source = decodePng(readFileSync(pngPath));
  const entries = sizes.map((size) => ({
    size,
    png: encodePng(size, downscale(source, size)),
  }));
  const ico = assembleIco(entries);
  validateIco(ico, sizes);
  writeFileSync(icoPath, ico);
  return { sizes, bytes: ico.length };
}

const invokedDirectly =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  const root = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "../..",
  );
  const source = process.argv[2] || path.join(root, "desktop", "icon.png");
  const target = process.argv[3] || path.join(root, "desktop", "icon.ico");
  const { sizes, bytes } = generateWindowsIcon(source, target);
  console.log(
    `已生成 ${target}（${bytes} 字节，尺寸 ${sizes.join("/")})，校验通过。`,
  );
}
