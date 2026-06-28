"""M8-3 · 知识库迁移包导出/导入（非实时同步）。"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from . import backup_ops, config

BUNDLE_VERSION = 1
_BUNDLE_FILES = ("qr.db", "config.json", "standards.md")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(qr_home: Path) -> list[Path]:
    out: list[Path] = []
    for name in _BUNDLE_FILES:
        p = qr_home / name
        if p.is_file():
            out.append(p)
    if not out:
        raise FileNotFoundError(f"在 {qr_home} 未找到可打包文件（至少需 qr.db）")
    return out


def export_bundle(dest: str = "") -> dict[str, Any]:
    """打包 qr.db + config + standards 为 zip（不含 ~/QR 源码）。"""
    config.ensure_dirs()
    qr_home = config.QR_HOME
    files = _collect_files(qr_home)
    if dest:
        out = Path(dest).expanduser().resolve()
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = qr_home / "bundles" / f"qr-bundle-{stamp}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest_files: list[dict[str, Any]] = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            rel = p.name
            zf.write(p, arcname=rel)
            manifest_files.append({
                "path": rel,
                "size": p.stat().st_size,
                "sha256": _sha256(p),
            })
        manifest = {
            "bundle_version": BUNDLE_VERSION,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "qr_home": str(qr_home),
            "files": manifest_files,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"ok": True, "path": str(out), "files": [f["path"] for f in manifest_files]}


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = zf.read("manifest.json")
    except KeyError as e:
        raise ValueError("zip 缺少 manifest.json") from e
    data = json.loads(raw.decode("utf-8"))
    if int(data.get("bundle_version", 0)) != BUNDLE_VERSION:
        raise ValueError(f"不支持的 bundle 版本: {data.get('bundle_version')}")
    return data


def import_bundle(
    path: str,
    *,
    dest_home: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """校验或解压迁移包到目标 ~/.qr（默认不覆盖，dry_run 仅校验）。"""
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        return {"ok": False, "error": "文件不存在"}
    target = Path(dest_home).expanduser().resolve() if dest_home else config.QR_HOME
    errors: list[str] = []
    verified: list[str] = []
    with zipfile.ZipFile(src, "r") as zf:
        manifest = _read_manifest(zf)
        for entry in manifest.get("files") or []:
            rel = entry.get("path") or ""
            if rel not in _BUNDLE_FILES:
                errors.append(f"未知条目: {rel}")
                continue
            try:
                data = zf.read(rel)
            except KeyError:
                errors.append(f"zip 缺少 {rel}")
                continue
            expect = entry.get("sha256") or ""
            got = hashlib.sha256(data).hexdigest()
            if expect and got != expect:
                errors.append(f"{rel} 校验和不匹配")
                continue
            if rel == "qr.db":
                check = backup_ops.verify_backup_from_bytes(data)
                if not check.get("ok"):
                    errors.append(f"qr.db 无效: {check.get('error')}")
                    continue
            verified.append(rel)
        if errors:
            return {"ok": False, "errors": errors, "verified": verified, "dry_run": dry_run}
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "verified": verified,
                "dest": str(target),
                "manifest": manifest,
            }
        target.mkdir(parents=True, exist_ok=True)
        for rel in verified:
            data = zf.read(rel)
            dest_file = target / rel
            if dest_file.exists():
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = target / "backups" / f"pre-import-{rel}-{stamp}"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest_file, backup)
            dest_file.write_bytes(data)
        return {
            "ok": True,
            "dry_run": False,
            "verified": verified,
            "dest": str(target),
        }
