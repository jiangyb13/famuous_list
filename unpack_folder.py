#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unpack_folder.py
================
把 pack_folder.py 生成的打包目录完整还原成原始文件夹（逐字节一致）。

用法：
  python unpack_folder.py --src "...\\20261102_famous_packed"
  python unpack_folder.py --src "...\\20261102_famous_packed" --out "D:\\restore\\20261102_famous"
  python unpack_folder.py --src ... --out ... --force

还原时会用清单里的 SHA-256 校验每个文件，任何不一致都会报告。
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time

META_FORMAT = "folderpack"


def log(msg=""):
    print(msg, flush=True)


def winpath(path):
    path = os.path.abspath(path)
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def safe_join(base, rel):
    """防止清单里的路径穿越到目标目录之外。"""
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ValueError(f"非法路径: {rel!r}")
    return os.path.join(base, *parts)


def read_meta(pack_dir):
    meta_path = os.path.join(pack_dir, "_pack", "meta.json")
    if not os.path.isfile(meta_path):
        raise SystemExit(f"[错误] 找不到 {meta_path}\n       请确认 --src 指向 pack_folder.py 的输出目录")
    with open(winpath(meta_path), "rb") as f:
        meta = json.loads(f.read().decode("utf-8"))
    if meta.get("format") != META_FORMAT:
        raise SystemExit("[错误] meta.json 格式不匹配，不是本工具生成的打包目录")
    return meta


def read_manifest(pack_dir, meta):
    entries = []
    parts = meta.get("manifest_parts") or ["manifest.0001.jsonl"]
    for name in parts:
        path = os.path.join(pack_dir, "_pack", name)
        with open(winpath(path), "rb") as f:
            for line in f.read().decode("utf-8").splitlines():
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries


def unpack(src, out, force):
    src = os.path.abspath(src)
    out = os.path.abspath(out)
    if not os.path.isdir(src):
        raise SystemExit(f"[错误] 打包目录不存在: {src}")

    meta = read_meta(src)
    entries = read_manifest(src, meta)

    files = [e for e in entries if e.get("t") == "f"]
    dirs = [e for e in entries if e.get("t") == "d"]

    if os.path.isdir(out) and os.listdir(out) and not force:
        raise SystemExit(f"[错误] 目标目录非空: {out}\n       加 --force 覆盖，或换一个 --out")

    chunk_dir = os.path.join(src, "chunks")
    log(f"[信息] 打包目录: {src}")
    log(f"[信息] 还原到  : {out}")
    log(f"[信息] 清单记录: {len(files)} 个文件 / {len(dirs)} 个目录")
    log("")

    os.makedirs(winpath(out), exist_ok=True)

    # 先建目录（含空目录）
    for d in dirs:
        try:
            os.makedirs(winpath(safe_join(out, d["p"])), exist_ok=True)
        except (OSError, ValueError) as e:
            log(f"[警告] 目录创建失败 {d.get('p')}: {e}")

    total = 0
    bad = []
    t0 = time.time()

    for idx, e in enumerate(files, 1):
        rel = e["p"]
        try:
            dst = safe_join(out, rel)
        except ValueError as err:
            bad.append(f"{rel}: {err}")
            continue

        # 1) 拼接所有数据块
        buf = bytearray()
        missing = False
        for cname in e.get("c", []):
            cpath = os.path.join(chunk_dir, cname)
            if not os.path.isfile(cpath):
                bad.append(f"{rel}: 缺少数据块 {cname}")
                missing = True
                break
            with open(winpath(cpath), "rb") as f:
                buf += f.read()
        if missing:
            continue

        # 2) 解码
        try:
            if e.get("m") == "b64":
                data = base64.b64decode(bytes(buf))
            else:
                text = bytes(buf).decode("utf-8")
                data = text.encode(e.get("enc", "utf-8"))
        except Exception as err:
            bad.append(f"{rel}: 解码失败 {err}")
            continue

        # 3) 落盘
        try:
            os.makedirs(winpath(os.path.dirname(dst)), exist_ok=True)
            with open(winpath(dst), "wb") as f:
                f.write(data)
        except OSError as err:
            bad.append(f"{rel}: 写入失败 {err}")
            continue

        # 4) 校验
        if hashlib.sha256(data).hexdigest() != e.get("h"):
            bad.append(f"{rel}: SHA-256 校验不一致")
        total += len(data)

        if idx % 50 == 0 or idx == len(files):
            log(f"  已还原 {idx}/{len(files)} 个文件")

    dt = time.time() - t0
    log("")
    log("=" * 62)
    log(f"还原完成，用时 {dt:.1f}s")
    log(f"  文件数量   : {len(files) - len(bad)}/{len(files)}")
    log(f"  还原总大小 : {total/1024/1024:.2f} MB")
    log(f"  目标目录   : {out}")
    if bad:
        log("")
        log(f"[错误] {len(bad)} 处问题：")
        for b in bad[:30]:
            log("   " + b)
    else:
        log("  校验结果   : 全部文件 SHA-256 一致 ✔")
    log("=" * 62)
    return 0 if not bad else 1


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="还原 pack_folder.py 打包的文件夹")
    ap.add_argument("--src", required=True, help="打包目录（含 _pack 和 chunks 的那一层）")
    ap.add_argument("--out", default=None, help="还原目标目录（默认 <src>_restored）")
    ap.add_argument("--force", action="store_true", help="目标目录非空时允许覆盖")
    args = ap.parse_args()

    out = args.out or (os.path.abspath(args.src).rstrip("\\/") + "_restored")
    sys.exit(unpack(args.src, out, args.force))


if __name__ == "__main__":
    main()
