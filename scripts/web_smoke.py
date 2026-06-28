#!/usr/bin/env python3
"""M9-2 · Web API 冒烟测试：主要 GET 返回 200 且 JSON 结构可用。

用法（需 `qr web` 已启动）：
  python scripts/web_smoke.py
  python scripts/web_smoke.py --base http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

# (path, required_top_level_keys)
SMOKE_GETS: list[tuple[str, tuple[str, ...]]] = [
    ("/api/status", ("health_ok", "events")),
    ("/api/projects", ("projects",)),
    ("/api/categories", ("categories",)),
    ("/api/index/status", ()),
    ("/api/resume", ()),
    ("/api/standards", ()),
    ("/api/facts", ("facts",)),
    ("/api/prompts/stats", ()),
    ("/api/eval/cases", ()),
    ("/api/ship-check", ()),
    ("/api/ai-assess/snapshot", ()),
    ("/api/compliance", ()),
    ("/api/module-map", ("areas",)),
    ("/api/shell/stats", ("file_pct",)),
    ("/api/tracker/status", ()),
    ("/api/ops/overview", ("doctor",)),
    ("/api/project/focus", ()),
    ("/api/digest?days=1", ("content",)),
]


def _get(base: str, path: str) -> tuple[int, Any]:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return resp.status, json.loads(body.decode("utf-8"))


def run_smoke(base: str) -> list[str]:
    errors: list[str] = []
    for path, keys in SMOKE_GETS:
        try:
            status, data = _get(base, path)
        except urllib.error.HTTPError as e:
            errors.append(f"{path} HTTP {e.code}")
            continue
        except urllib.error.URLError as e:
            errors.append(f"{path} 连接失败: {e.reason}")
            continue
        except json.JSONDecodeError:
            errors.append(f"{path} 非 JSON 响应")
            continue
        if status != 200:
            errors.append(f"{path} status {status}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path} 根节点非 object")
            continue
        for key in keys:
            if key not in data:
                errors.append(f"{path} 缺少键 {key!r}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="QR Web API 冒烟测试")
    parser.add_argument("--base", default="", help="Web 根 URL，默认读 config.web_port")
    args = parser.parse_args()
    base = args.base.strip()
    if not base:
        try:
            from qr import config

            cfg = config.load_config()
            host = cfg.get("web_host", "127.0.0.1")
            port = int(cfg.get("web_port", 8765))
            base = f"http://{host}:{port}"
        except Exception:
            base = "http://127.0.0.1:8765"
    errors = run_smoke(base)
    total = len(SMOKE_GETS)
    if errors:
        print(f"FAIL {len(errors)}/{total} 项", file=sys.stderr)
        for line in errors:
            print(f"  · {line}", file=sys.stderr)
        return 1
    print(f"OK {total}/{total} GET 冒烟通过 · {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
