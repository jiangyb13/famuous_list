#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pack_folder.py
==============
把一个本地文件夹打包成「每个文件都小于 N 字节」的纯文本结构，
方便上传到有「单文件 < 100KB 且只允许纯文本」限制的 Git 仓库。

规则：
  * 文本文件      -> 按 UTF-8 文本切片，尽量在换行处断开（可在 Git 里直接阅读）
  * 图片/二进制   -> 转成 Base64 纯文本，再切片
  * 每个切片文件都 < --limit 字节（默认 90000，安全低于 100KB）

产物结构：
  <out>/
    _pack/
      meta.json              # 打包元信息（很小）
      manifest.0001.jsonl    # 文件清单，必要时自动分成多份，每份都 < limit
    chunks/
      000001.txt             # 数据块，每块都 < limit
      ...

用法：
  python pack_folder.py --src "C:\\Users\\xxx\\Desktop\\20261102_famous"
  python pack_folder.py --src "...\\20261102_famous" --out "...\\20261102_famous_packed"
  python pack_folder.py --src ... --out ... --limit 90000 --force

还原请用配套的 unpack_folder.py。
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time

# ---------------------------------------------------------------- 常量

META_FORMAT = "folderpack"
META_VERSION = 1

# 明确按二进制处理的扩展名（即使内容能解码成文本也不做文本切片）
BINARY_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico",
    ".heic", ".heif", ".avif", ".psd", ".raw", ".cr2", ".nef", ".arw",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma",
    ".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm",
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".tar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".class", ".jar",
    ".ttf", ".otf", ".woff", ".woff2", ".db", ".sqlite", ".pyc",
}

# 明确按文本处理的扩展名
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".html", ".htm", ".css", ".scss", ".less", ".js", ".mjs", ".cjs", ".jsx",
    ".ts", ".tsx", ".vue", ".svelte", ".py", ".java", ".kt", ".c", ".h", ".hpp",
    ".cc", ".cpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".sql", ".srt", ".vtt",
    ".tex", ".r", ".lua", ".pl", ".gradle", ".dockerfile", ".gitignore",
    ".gitattributes", ".editorconfig", ".env", ".sln", ".vcxproj", ".pro",
}

BOM_UTF8 = b"\xef\xbb\xbf"


# ---------------------------------------------------------------- 工具

def log(msg=""):
    print(msg, flush=True)


def winpath(path):
    """Windows 长路径安全处理（>260 字符也不会炸）。"""
    path = os.path.abspath(path)
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def detect_text(data):
    """
    尝试把 bytes 当文本解码。
    返回 (text, encoding) ；不是文本返回 None。
    encoding 为还原时使用的 Python 编码名。
    """
    if not data:
        return ("", "utf-8")
    if data.startswith(BOM_UTF8):
        try:
            return (data[len(BOM_UTF8):].decode("utf-8"), "utf-8-sig")
        except UnicodeDecodeError:
            return None
    for enc in ("utf-8", "gbk", "gb18030", "big5", "utf-16"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # 排除把二进制误判成文本的情况：不能有 NUL 等控制字符
        if any((ord(ch) < 32 and ch not in "\t\r\n\f\b") for ch in text[:4096]):
            return None
        return (text, enc)
    return None


def split_text_bytes(data, limit):
    """按 UTF-8 字节切分，不切断多字节字符，尽量在换行处断开。"""
    chunks = []
    i, n = 0, len(data)
    while i < n:
        end = min(i + limit, n)
        if end < n:
            # 1) 回退到 UTF-8 字符边界
            back = end
            while back > i and (data[back] & 0xC0) == 0x80:
                back -= 1
            if back > i:
                end = back
            # 2) 尽量在换行处断开（至少保留一半容量，避免碎片过多）
            nl = data.rfind(b"\n", i, end)
            if nl != -1 and (nl + 1 - i) >= limit // 2:
                end = nl + 1
        if end <= i:            # 兜底，理论上不会发生
            end = min(i + limit, n)
        chunks.append(data[i:end])
        i = end
    return chunks


def split_fixed(data, limit):
    """定长切分（Base64 用，长度取 4 的倍数更整齐）。"""
    step = max(4, (limit // 4) * 4)
    return [data[i:i + step] for i in range(0, len(data), step)] or [b""]


# ---------------------------------------------------------------- 主流程

def iter_tree(src):
    """稳定顺序遍历：先目录（相对路径排序），再文件。"""
    dirs, files = [], []
    for root, dirnames, filenames in os.walk(src):
        dirnames.sort()
        rel_root = os.path.relpath(root, src)
        if rel_root != ".":
            dirs.append(rel_root.replace("\\", "/"))
        for name in sorted(filenames):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src).replace("\\", "/")
            files.append((rel, full))
    dirs.sort()
    files.sort(key=lambda x: x[0])
    return dirs, files


def pack(src, out, limit, force):
    src = os.path.abspath(src)
    out = os.path.abspath(out)
    if not os.path.isdir(src):
        raise SystemExit(f"[错误] 源目录不存在: {src}")
    if out == src or src.startswith(out + os.sep):
        raise SystemExit("[错误] 输出目录不能是源目录或源目录的上级目录")
    if os.path.isdir(out) and os.listdir(out):
        if not force:
            raise SystemExit(f"[错误] 输出目录非空: {out}\n       加 --force 覆盖，或换一个 --out")
        log(f"[提示] 输出目录非空，--force 已开启，将覆盖同名文件")

    chunk_dir = os.path.join(out, "chunks")
    pack_dir = os.path.join(out, "_pack")
    os.makedirs(chunk_dir, exist_ok=True)
    os.makedirs(pack_dir, exist_ok=True)

    dirs, files = iter_tree(src)
    log(f"[信息] 源目录: {src}")
    log(f"[信息] 子目录 {len(dirs)} 个，文件 {len(files)} 个")
    log(f"[信息] 单块上限: {limit} 字节 (~{limit/1024:.1f} KB)")
    log("")

    manifest_lines = []
    chunk_index = 0
    total_bytes = 0
    text_files = b64_files = 0
    max_chunk = 0
    errors = []

    t0 = time.time()
    for idx, (rel, full) in enumerate(files, 1):
        try:
            with open(winpath(full), "rb") as f:
                data = f.read()
        except OSError as e:
            errors.append(f"{rel}: 读取失败 {e}")
            continue

        size = len(data)
        total_bytes += size
        digest = sha256_bytes(data)
        ext = os.path.splitext(rel)[1].lower()

        mode, enc, payload = "b64", None, b""
        if ext in BINARY_EXTS:
            mode = "b64"
        else:
            decoded = detect_text(data)
            if decoded is not None and (ext in TEXT_EXTS or ext == "" or ext not in BINARY_EXTS):
                text, encoding = decoded
                # 关键：验证「文本 -> 还原编码」能 100% 还原原始字节，否则退回 Base64
                try:
                    roundtrip = text.encode(encoding)
                except (UnicodeEncodeError, LookupError):
                    roundtrip = None
                if roundtrip == data:
                    mode, enc, payload = "txt", encoding, text.encode("utf-8")
                else:
                    mode = "b64"

        if mode == "b64":
            payload = base64.b64encode(data)
            parts = split_fixed(payload, limit) if payload else [b""]
            b64_files += 1
        else:
            parts = split_text_bytes(payload, limit) if payload else [b""]
            text_files += 1

        chunk_names = []
        for part in parts:
            chunk_index += 1
            name = f"{chunk_index:06d}.txt"
            with open(winpath(os.path.join(chunk_dir, name)), "wb") as f:
                f.write(part)
            max_chunk = max(max_chunk, len(part))
            chunk_names.append(name)

        entry = {
            "t": "f",
            "p": rel,
            "m": mode,
            "s": size,
            "h": digest,
            "c": chunk_names,
        }
        if mode == "txt":
            entry["enc"] = enc
        manifest_lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))

        if idx % 50 == 0 or idx == len(files):
            log(f"  已处理 {idx}/{len(files)} 个文件，已生成 {chunk_index} 个数据块")

    for d in dirs:
        manifest_lines.append(json.dumps({"t": "d", "p": d}, ensure_ascii=False, separators=(",", ":")))

    # ---- 写清单（必要时分卷，保证每卷 < limit）----
    manifest_parts = []
    buf = []
    buf_len = 0
    part_no = 0

    def flush_part():
        nonlocal buf, buf_len, part_no
        if not buf:
            return
        part_no += 1
        name = f"manifest.{part_no:04d}.jsonl"
        with open(winpath(os.path.join(pack_dir, name)), "wb") as f:
            f.write("".join(buf).encode("utf-8"))
        manifest_parts.append(name)
        buf, buf_len = [], 0

    for line in manifest_lines:
        raw = line + "\n"
        blen = len(raw.encode("utf-8"))
        if buf and buf_len + blen > limit:
            flush_part()
        buf.append(raw)
        buf_len += blen
    flush_part()

    meta = {
        "format": META_FORMAT,
        "version": META_VERSION,
        "chunk_bytes": limit,
        "source_name": os.path.basename(src.rstrip("\\/")),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "dir_count": len(dirs),
        "total_bytes": total_bytes,
        "chunk_count": chunk_index,
        "manifest_parts": manifest_parts,
    }
    with open(winpath(os.path.join(pack_dir, "meta.json")), "wb") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    # ---- 汇总 ----
    dt = time.time() - t0
    log("")
    log("=" * 62)
    log(f"打包完成，用时 {dt:.1f}s")
    log(f"  文件总数      : {len(files)}  (文本 {text_files} / Base64 {b64_files})")
    log(f"  原始总大小    : {total_bytes/1024/1024:.2f} MB")
    log(f"  数据块数量    : {chunk_index}  (清单 {len(manifest_parts)} 份)")
    log(f"  最大单文件    : {max_chunk} 字节  ({max_chunk/1024:.1f} KB)  {'OK' if max_chunk <= limit else '超限!'}")
    log(f"  输出目录      : {out}")
    if errors:
        log("")
        log(f"[警告] {len(errors)} 个文件处理失败：")
        for e in errors[:20]:
            log("   " + e)
    log("=" * 62)
    log("下一步：把输出目录整个 git add / commit / push 即可。")
    return 0 if not errors else 1


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="把文件夹打包成每个文件都小于 N 字节的纯文本结构")
    ap.add_argument("--src", required=True, help="源文件夹，例如 C:\\Users\\xxx\\Desktop\\20261102_famous")
    ap.add_argument("--out", default=None, help="输出文件夹（默认 <src>_packed）")
    ap.add_argument("--limit", type=int, default=90000,
                    help="单个文件最大字节数，默认 90000（安全低于 100KB）")
    ap.add_argument("--force", action="store_true", help="输出目录非空时允许覆盖")
    args = ap.parse_args()

    out = args.out or (os.path.abspath(args.src).rstrip("\\/") + "_packed")
    if args.limit < 1024:
        raise SystemExit("[错误] --limit 太小")
    sys.exit(pack(args.src, out, args.limit, args.force))


if __name__ == "__main__":
    main()
