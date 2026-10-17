"""Bounded, inert projections of actual workspace bytes; no generated code is imported."""
import csv
import hashlib
import io
from pathlib import PurePosixPath
import struct
import zlib

SCHEMA = "execution-artifact/v1"
TEXT_LIMIT = 128_000
IMAGE_LIMIT = 4 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 16_777_216
TABLE_ROWS = 100
TABLE_COLUMNS = 24
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def read_limit(path):
    if not isinstance(path, str):
        raise ValueError("文件路径无效")
    return (
        IMAGE_LIMIT
        if PurePosixPath(path).suffix.lower() in IMAGE_SUFFIXES
        else TEXT_LIMIT
    )


def dimensions(data, suffix):
    """Validate bounded static image structure and dimensions, not pixel content or meaning."""
    if suffix == ".png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG签名无效")
        position, width, height, found_data, ended = 8, None, None, False, False
        while position + 12 <= len(data):
            length = int.from_bytes(data[position : position + 4], "big")
            kind = data[position + 4 : position + 8]
            end = position + 12 + length
            if end > len(data):
                raise ValueError("PNG区块不完整")
            chunk = data[position + 8 : end - 4]
            crc = int.from_bytes(data[end - 4 : end], "big")
            if zlib.crc32(kind + chunk) & 0xFFFFFFFF != crc:
                raise ValueError("PNG区块校验失败")
            if position == 8:
                if kind != b"IHDR" or length != 13:
                    raise ValueError("PNG尺寸头无效")
                width, height = struct.unpack(">II", chunk[:8])
            elif kind == b"IHDR":
                raise ValueError("PNG尺寸头重复")
            if kind in {b"acTL", b"fcTL", b"fdAT"}:
                raise ValueError("当前仅预览静态PNG")
            found_data |= kind == b"IDAT"
            position = end
            if kind == b"IEND":
                if length or position != len(data):
                    raise ValueError("PNG结束区块无效")
                ended = True
                break
        if not ended or not found_data:
            raise ValueError("PNG图像不完整")
        mime = "image/png"
    else:
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise ValueError("JPEG签名或结束标记无效")
        position, width, height = 2, None, None
        while position < len(data) - 2:
            if data[position] != 0xFF:
                raise ValueError("JPEG区段无效")
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker in {0xD8, 0xD9, 0x00} or 0xD0 <= marker <= 0xD7:
                raise ValueError("JPEG区段顺序无效")
            if marker == 0x01:
                continue
            if position + 2 > len(data):
                raise ValueError("JPEG区段不完整")
            length = int.from_bytes(data[position : position + 2], "big")
            if length < 2 or position + length > len(data):
                raise ValueError("JPEG区段长度无效")
            if marker in {0xC0, 0xC1, 0xC2}:
                if length < 8 or width is not None:
                    raise ValueError("JPEG尺寸头无效")
                height, width = struct.unpack(">HH", data[position + 3 : position + 7])
            elif 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
                raise ValueError("当前仅预览常用基线或渐进JPEG")
            position += length
            if marker == 0xDA:
                break
        mime = "image/jpeg"
    if (
        not width
        or not height
        or max(width, height) > MAX_SIDE
        or width * height > MAX_PIXELS
    ):
        raise ValueError("图像尺寸无效或超过预览范围")
    return mime, width, height


def projection(path, data, revision):
    if len(data) > read_limit(path):
        raise ValueError("文件过大，请在本机工作目录查看")
    result = {
        "schema_version": SCHEMA,
        "path": path,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "revision": revision,
    }
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        mime, width, height = dimensions(data, suffix)
        return {
            **result,
            "kind": "image",
            "mime": mime,
            "width": width,
            "height": height,
        }
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("当前支持UTF-8文本、PNG和JPEG预览") from None
    result.update(kind="text", text=text)
    if suffix not in {".csv", ".tsv"}:
        return result
    rows, widths, count = [], set(), 0
    try:
        for row in csv.reader(
            io.StringIO(text.lstrip("\ufeff"), newline=""),
            delimiter="\t" if suffix == ".tsv" else ",",
            strict=True,
        ):
            if not row:
                continue
            count += 1
            widths.add(len(row))
            if len(rows) < TABLE_ROWS:
                rows.append(row[:TABLE_COLUMNS])
    except csv.Error:
        result["notice"] = "表格格式未能完整解析，显示原文。"
        return result
    if rows:
        result.update(
            kind="table",
            rows=rows,
            total_rows=count,
            columns=min(max(widths), TABLE_COLUMNS),
            rows_truncated=count > TABLE_ROWS,
            columns_truncated=max(widths) > TABLE_COLUMNS,
            ragged=len(widths) > 1,
        )
    return result
