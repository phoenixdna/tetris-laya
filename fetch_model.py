# -*- coding: utf-8 -*-
"""绕过 huggingface_hub（本机上它在 CDN 环节失败），用 requests 直连下载权重到本地。"""
import sys
from pathlib import Path

import requests

REPO = "convaiinnovations/laya-typed-decisions"
OUT = Path(__file__).resolve().parent / "models" / "laya-typed-decisions"

FILES = [".gitattributes", "README.md", "encoder/config.json",
         "model.safetensors", "rl_agent_config.json",
         "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json"]

s = requests.Session()
for f in FILES:
    dest = OUT / f
    url = f"https://huggingface.co/{REPO}/resolve/main/{f}"
    # 先拿总长度，完整了就跳过（避免把残缺文件当完成）
    try:
        total = int(s.head(url, timeout=30, allow_redirects=True).headers.get("content-length", 0))
    except Exception:
        total = 0
    if dest.exists() and total and dest.stat().st_size >= total:
        print(f"skip {f} ({dest.stat().st_size / 1e6:.0f}MB complete)")
        continue
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{REPO}/resolve/main/{f}"
    have = dest.stat().st_size if dest.exists() else 0
    # 续传：用 Range 从已下载处继续
    headers = {"Range": f"bytes={have}-"} if have else {}
    mode = "ab" if have else "wb"
    print(f"downloading {f} (resume from {have / 1e6:.0f}MB) ...")
    with s.get(url, stream=True, timeout=60, headers=headers) as r:
        if r.status_code == 416:
            print(f"  {f} already complete ({have}B), skip")
            continue
        r.raise_for_status()
        total = int(r.headers.get("content-range", "bytes 0-0/0").split("/")[-1]
                    or r.headers.get("content-length", 0))
        done = have
        with open(dest, mode) as fh:
            for chunk in r.iter_content(1024 * 512):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done / 1e6:.0f}/{total / 1e6:.0f}MB "
                          f"({100 * done / total:.0f}%)", end="")
        print()
print("ALL DONE ->", OUT)
