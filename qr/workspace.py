from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import config

_DEFAULT_CATEGORIES = ("dev", "mobile", "experiments", "tools", "archive")
_PROTECTED_PROJECTS = frozenset({"dev/qr"})
_DELETE_CONFIRM_PHRASE = "永久删除"


def workspace_root(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or config.load_config()
    return config._expand(str(cfg.get("workspace_root", "~/QR")))


def categories(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or config.load_config()
    raw = cfg.get("project_categories") or list(_DEFAULT_CATEGORIES)
    return [str(c).strip() for c in raw if str(c).strip()]


def ensure_workspace_layout(cfg: dict[str, Any] | None = None) -> Path:
    root = workspace_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        cats = ", ".join(f"`{c}/`" for c in categories(cfg))
        readme.write_text(
            "# QR 工作区\n\n"
            "本机所有代码项目统一放在此目录下，按分类分子目录。\n\n"
            f"分类：{cats}\n\n"
            "- 新建项目：`qr workspace new <名称> --category dev`\n"
            "- 迁移散落项目：`qr workspace migrate`\n"
            "- 数据与知识库配置仍在 `~/.qr`\n",
            encoding="utf-8",
        )
    for cat in categories(cfg):
        (root / cat).mkdir(parents=True, exist_ok=True)
    return root


def parse_project_id(project: str) -> tuple[str | None, str]:
    """'dev/qr' -> ('dev', 'qr'); 'qr' -> (None, 'qr')"""
    p = (project or "").strip().strip("/")
    if not p:
        return None, ""
    if "/" in p:
        cat, name = p.split("/", 1)
        return cat, name
    return None, p


def project_id(category: str, name: str) -> str:
    return f"{category}/{name}"


def slug_name(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", name.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "project"


def project_from_path(path: Path, root: Path | None = None) -> str:
    """从绝对路径解析 project_id（category/name）。"""
    root = root or workspace_root()
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return retrieval_fallback_project(path)
    if not rel.parts:
        return root.name
    if len(rel.parts) >= 2:
        return project_id(rel.parts[0], rel.parts[1])
    return slug_name(rel.parts[0])


def retrieval_fallback_project(path: Path) -> str:
    """非工作区路径的兜底命名（兼容旧路径）。"""
    parts = [x for x in path.parts if x]
    if "QR" in parts:
        i = parts.index("QR")
        if i + 2 < len(parts):
            return project_id(parts[i + 1], parts[i + 2])
        if i + 1 < len(parts):
            return parts[i + 1]
    if "Projects" in parts:
        i = parts.index("Projects")
        if i + 1 < len(parts):
            return f"legacy/{parts[i + 1]}"
    return slug_name(path.parent.name if path.is_file() else path.name)


def resolve_project_dir(project: str, cfg: dict[str, Any] | None = None) -> Path | None:
    cat, name = parse_project_id(project)
    root = workspace_root(cfg)
    if cat and name:
        p = root / cat / name
        return p if p.is_dir() else None
    name = name or project
    for c in categories(cfg):
        p = root / c / name
        if p.is_dir():
            return p
    legacy = Path.home() / "Projects" / name
    return legacy if legacy.is_dir() else None


def is_under_workspace(path: Path, cfg: dict[str, Any] | None = None) -> bool:
    root = workspace_root(cfg)
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_project_dir(d: Path) -> bool:
    from .importer import MARKERS

    try:
        names = {p.name for p in d.iterdir()}
    except OSError:
        return False
    return bool(names & MARKERS) or (d / ".git").is_dir()


def discover_outside_workspace(cfg: dict[str, Any] | None = None) -> list[Path]:
    """发现尚未位于 ~/QR 下的项目目录。"""
    cfg = cfg or config.load_config()
    root = workspace_root(cfg).resolve()
    found: dict[str, Path] = {}

    def add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        if not rp.is_dir() or is_under_workspace(rp, cfg):
            return
        if _is_project_dir(rp):
            found[str(rp)] = rp

    for key in ("index_roots", "scatter_roots"):
        for base in config.expand_paths(cfg.get(key, [])):
            if not base.exists():
                continue
            if base.resolve() == root:
                continue
            if base.resolve() == Path.home().resolve():
                for c in base.iterdir():
                    if c.is_dir():
                        add(c)
                continue
            if base.name in ("Projects", "QR") and base.resolve() == Path.home().joinpath(base.name).resolve():
                try:
                    for c in base.iterdir():
                        if c.is_dir() and not c.is_symlink():
                            add(c)
                        elif c.is_symlink() and c.resolve().is_dir() and _is_project_dir(c.resolve()):
                            add(c.resolve())
                except OSError:
                    pass
                continue
            if _is_project_dir(base):
                add(base)
                continue
            try:
                for c in base.iterdir():
                    if c.is_dir():
                        add(c)
            except OSError:
                pass
    return sorted(found.values(), key=lambda p: str(p))


def infer_category(path: Path, cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or config.load_config()
    default = str(cfg.get("default_project_category", "dev"))
    p = str(path).lower()
    name = path.name.lower()
    if "android" in p or "kotlin" in p or "gradle" in p:
        return "mobile" if "mobile" in categories(cfg) else default
    if "desktop" in p:
        return "experiments" if "experiments" in categories(cfg) else default
    if name in ("qr", "kb") or "story-forge" in name:
        return "dev"
    if "pose" in name or "analysis" in name or "experiment" in name:
        return "experiments" if "experiments" in categories(cfg) else default
    if name in ("tools", "scripts", "dotfiles"):
        return "tools" if "tools" in categories(cfg) else default
    return default


def migrate_paths(
    paths: list[Path],
    *,
    category: str | None = None,
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    cfg = cfg or config.load_config()
    root = ensure_workspace_layout(cfg)
    results: list[dict[str, str]] = []
    used_names: set[str] = set()

    for src in paths:
        if is_under_workspace(src, cfg):
            results.append({"src": str(src), "dest": str(src), "status": "skipped_in_workspace"})
            continue
        cat = category or infer_category(src, cfg)
        if cat not in categories(cfg):
            cat = str(cfg.get("default_project_category", "dev"))
        dest_name = slug_name(src.name)
        dest = root / cat / dest_name
        n = 1
        key = project_id(cat, dest_name)
        while dest.exists() or key in used_names:
            dest_name = f"{slug_name(src.name)}-{n}"
            dest = root / cat / dest_name
            key = project_id(cat, dest_name)
            n += 1
        used_names.add(key)
        if dry_run:
            results.append({"src": str(src), "dest": str(dest), "status": "dry_run", "category": cat})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        results.append({"src": str(src), "dest": str(dest), "status": "moved", "category": cat})
    return results


def apply_workspace_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """将配置收敛为以 ~/QR 为唯一索引根。"""
    cfg = dict(cfg or config.load_config())
    root = ensure_workspace_layout(cfg)
    root_s = str(root)
    cfg["workspace_root"] = root_s.replace(str(Path.home()), "~")
    cfg["index_roots"] = [cfg["workspace_root"]]
    cfg["git_scan_roots"] = [cfg["workspace_root"]]
    cfg["scatter_roots"] = [
        str(Path.home()),
        str(Path.home() / "Desktop"),
        str(Path.home() / "Documents"),
    ]
    config.save_config(cfg)
    return cfg


def create_project(
    name: str,
    *,
    category: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> Path:
    cfg = cfg or config.load_config()
    root = ensure_workspace_layout(cfg)
    cat = category or str(cfg.get("default_project_category", "dev"))
    if cat not in categories(cfg):
        raise ValueError(f"未知分类 {cat}，可选: {', '.join(categories(cfg))}")
    proj = slug_name(name)
    dest = root / cat / proj
    if dest.exists():
        raise ValueError(f"项目已存在: {dest}")
    dest.mkdir(parents=True)
    readme = dest / "README.md"
    readme.write_text(
        f"# {proj}\n\n创建于 QR 工作区 `{cat}/{proj}`。\n",
        encoding="utf-8",
    )
    return dest


def normalize_project_id(project: str, cfg: dict[str, Any] | None = None) -> str:
    """解析并规范为 category/name；若仅给名称则在各分类中查找。"""
    cfg = cfg or config.load_config()
    cat, name = parse_project_id(project)
    if cat and name:
        return project_id(cat, slug_name(name))
    name = slug_name(name or project)
    found = resolve_project_dir(name, cfg)
    if found and is_under_workspace(found, cfg):
        return project_from_path(found, workspace_root(cfg))
    for c in categories(cfg):
        if (workspace_root(cfg) / c / name).is_dir():
            return project_id(c, name)
    return name


def is_protected_project(project: str, cfg: dict[str, Any] | None = None) -> bool:
    """仅精确匹配受保护 ID（如 dev/qr），不把 qr 别名误判为保护。"""
    pid = project.strip().lower().strip("/")
    if pid in _PROTECTED_PROJECTS:
        return True
    extra = {str(x).lower().strip("/") for x in (cfg or config.load_config()).get("protected_projects", [])}
    return pid in extra


def _resolve_project_dir_exact(project: str, cfg: dict[str, Any] | None = None) -> Path | None:
    """仅按 category/name 解析目录，避免把 legacy 单段名误解析到 dev/qr。"""
    cfg = cfg or config.load_config()
    cat, name = parse_project_id(project)
    if not cat or not name:
        return None
    p = workspace_root(cfg) / cat / name
    return p if p.is_dir() else None


def _delete_scope(project: str, proj_dir: Path | None) -> tuple[str, str, str | None, str]:
    """返回 pid, name, path_like, cursor_proj。"""
    pid = project.strip()
    cat, name = parse_project_id(pid)
    name = name or pid.split("/")[-1]
    cursor_proj = f"cursor-{name}"
    path_like = f"{proj_dir.resolve()}%" if proj_dir else None
    return pid, name, path_like, cursor_proj


def _doc_match_clause(path_like: str | None) -> str:
    if path_like:
        return (
            "lower(project)=lower(?) OR lower(project)=lower(?) OR path LIKE ?"
        )
    return "lower(project)=lower(?) OR lower(project)=lower(?)"


def _doc_match_params(pid: str, cursor_proj: str, path_like: str | None) -> tuple:
    if path_like:
        return (pid, cursor_proj, path_like)
    return (pid, cursor_proj)


def preview_project_delete(
    project: str,
    cfg: dict[str, Any] | None = None,
    *,
    strict_id: bool = False,
) -> dict[str, Any]:
    """统计将删除的知识库记录与本地目录（不执行删除）。"""
    from . import db, facts

    cfg = cfg or config.load_config()
    pid = project.strip() if strict_id else normalize_project_id(project, cfg)
    if is_protected_project(pid, cfg):
        raise ValueError(f"项目 {pid} 受保护，禁止删除（知识库本体）")

    proj_dir = _resolve_project_dir_exact(pid, cfg) if strict_id else resolve_project_dir(pid, cfg)
    if not strict_id and proj_dir is None:
        proj_dir = _resolve_project_dir_exact(pid, cfg)

    pid, name, path_like, cursor_proj = _delete_scope(pid, proj_dir)
    doc_clause = _doc_match_clause(path_like)
    doc_params = _doc_match_params(pid, cursor_proj, path_like)

    with db.session() as conn:
        docs = conn.execute(
            f"SELECT COUNT(*) c FROM documents WHERE {doc_clause}",
            doc_params,
        ).fetchone()["c"]
        chunks = conn.execute(
            f"SELECT COUNT(*) c FROM chunks WHERE doc_id IN ("
            f"SELECT id FROM documents WHERE {doc_clause})",
            doc_params,
        ).fetchone()["c"]
        if path_like:
            events = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE lower(project)=lower(?) "
                "OR lower(project)=lower(?) OR title LIKE ? OR content LIKE ?",
                (pid, cursor_proj, path_like, f"%{name}%"),
            ).fetchone()["c"]
        else:
            events = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE lower(project)=lower(?) "
                "OR lower(project)=lower(?)",
                (pid, cursor_proj),
            ).fetchone()["c"]
        chats = conn.execute(
            "SELECT COUNT(*) c FROM chat_sessions WHERE title LIKE ? OR title LIKE ?",
            (f"%{name}%", f"%{pid}%"),
        ).fetchone()["c"]

    fact_list = facts.list_facts(pid)
    disk_bytes = 0
    if proj_dir and proj_dir.is_dir():
        disk_bytes = sum(f.stat().st_size for f in proj_dir.rglob("*") if f.is_file())

    return {
        "project": pid,
        "path": str(proj_dir.resolve()) if proj_dir else None,
        "index_only": proj_dir is None,
        "protected": False,
        "confirm_phrase": _DELETE_CONFIRM_PHRASE,
        "counts": {
            "documents": int(docs),
            "chunks": int(chunks),
            "events": int(events),
            "chat_sessions": int(chats),
            "facts": len(fact_list),
        },
        "disk_bytes": disk_bytes,
    }


def _purge_facts_for_project(pid: str, name: str) -> int:
    from . import facts

    data = facts._load()
    before = len(data.get("facts", []))
    pl = pid.lower()
    nl = name.lower()
    data["facts"] = [
        f for f in data.get("facts", [])
        if not (
            (f.get("project") or "").lower() in (pl, nl)
            or nl in str(f.get("project", "")).lower()
            or pl in str(f.get("project", "")).lower()
        )
    ]
    removed = before - len(data["facts"])
    if removed:
        facts._save(data)
    return removed


def purge_project(
    project: str,
    *,
    confirm: str,
    confirm_phrase: str,
    cfg: dict[str, Any] | None = None,
    strict_id: bool = False,
) -> dict[str, Any]:
    """删除项目本地目录及知识库中的相关数据。须通过二次确认参数。"""
    from . import db

    cfg = cfg or config.load_config()
    pid = project.strip() if strict_id else normalize_project_id(project, cfg)
    if confirm.strip() != pid:
        raise ValueError(f"确认名称不匹配，请输入 exactly: {pid}")
    if confirm_phrase.strip() != _DELETE_CONFIRM_PHRASE:
        raise ValueError(f"确认短语不正确，请输入: {_DELETE_CONFIRM_PHRASE}")

    preview = preview_project_delete(pid, cfg, strict_id=strict_id)
    proj_dir = Path(preview["path"]) if preview.get("path") else None
    pid, name, path_like, cursor_proj = _delete_scope(pid, proj_dir)
    doc_clause = _doc_match_clause(path_like)
    doc_params = _doc_match_params(pid, cursor_proj, path_like)

    stats = dict(preview["counts"])
    stats["disk_removed"] = False
    stats["index_only"] = preview.get("index_only", False)

    with db.session() as conn:
        doc_rows = conn.execute(
            f"SELECT id FROM documents WHERE {doc_clause}",
            doc_params,
        ).fetchall()
        doc_ids = [int(r["id"]) for r in doc_rows]
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            chunk_rows = conn.execute(
                f"SELECT id FROM chunks WHERE doc_id IN ({placeholders})", doc_ids,
            ).fetchall()
            chunk_ids = [int(r["id"]) for r in chunk_rows]
            if chunk_ids and db.vec_available():
                for cid in chunk_ids:
                    try:
                        conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (cid,))
                    except sqlite3.OperationalError:
                        pass
            for did in doc_ids:
                db.fts_delete_doc(conn, did)
            conn.execute(
                f"DELETE FROM documents WHERE id IN ({placeholders})", doc_ids,
            )

        if path_like:
            ev_cur = conn.execute(
                "DELETE FROM events WHERE lower(project)=lower(?) "
                "OR lower(project)=lower(?) OR title LIKE ? OR content LIKE ?",
                (pid, cursor_proj, path_like, f"%{name}%"),
            )
        else:
            ev_cur = conn.execute(
                "DELETE FROM events WHERE lower(project)=lower(?) "
                "OR lower(project)=lower(?)",
                (pid, cursor_proj),
            )
        stats["events_deleted"] = ev_cur.rowcount

        chat_rows = conn.execute(
            "SELECT id FROM chat_sessions WHERE title LIKE ? OR title LIKE ?",
            (f"%{name}%", f"%{pid}%"),
        ).fetchall()
        for row in chat_rows:
            conn.execute("DELETE FROM chat_sessions WHERE id=?", (row["id"],))
        stats["chat_sessions_deleted"] = len(chat_rows)

        db.rebuild_fts(conn)

    stats["facts_removed"] = _purge_facts_for_project(pid, name)

    if proj_dir and proj_dir.is_dir():
        shutil.rmtree(proj_dir)
        stats["disk_removed"] = True

    return {
        "ok": True,
        "project": pid,
        "path": str(proj_dir) if proj_dir else None,
        "stats": stats,
    }


def audit_projects(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """区分工作区真实项目（~/QR 目录）vs 仅索引中的无效条目。"""
    cfg = cfg or config.load_config()
    grouped = list_projects_grouped(500)
    doc_counts = {p: 0 for p in grouped["projects"]}
    for cat in grouped["by_category"].values():
        for x in cat:
            doc_counts[x["id"]] = int(x.get("docs", 0))

    root = workspace_root(cfg)
    workspace_items: list[dict[str, Any]] = []
    fs_ids: set[str] = set()
    for cat in categories(cfg):
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for proj in cat_dir.iterdir():
            if not proj.is_dir() or proj.name.startswith("."):
                continue
            pid = project_id(cat, proj.name)
            fs_ids.add(pid)
            workspace_items.append({
                "id": pid,
                "path": str(proj),
                "docs": doc_counts.get(pid, 0),
                "protected": is_protected_project(pid, cfg),
            })

    indexed_only: list[dict[str, Any]] = []
    for pid, docs in doc_counts.items():
        if pid in fs_ids or pid.startswith("cursor-"):
            continue
        if is_protected_project(pid, cfg):
            continue
        indexed_only.append({"id": pid, "path": None, "docs": docs})

    workspace_items.sort(key=lambda x: x["id"])
    indexed_only.sort(key=lambda x: -x["docs"])
    return {
        "workspace_root": str(root),
        "workspace": workspace_items,
        "indexed_only": indexed_only,
    }


def list_junk_project_ids(cfg: dict[str, Any] | None = None) -> list[str]:
    """应清理的无效项目：索引幽灵 + 可弃用的导出镜像。"""
    audit = audit_projects(cfg)
    junk = [x["id"] for x in audit["indexed_only"]]
    for item in audit["workspace"]:
        if item["id"] == "dev/qr-export":
            junk.append(item["id"])
    return junk


def list_projects_grouped(limit: int = 200) -> dict[str, Any]:
    """从 documents 表汇总分类与项目（project 字段为 category/name）。"""
    from . import db

    cfg = config.load_config()
    with db.session() as conn:
        rows = conn.execute(
            "SELECT project, COUNT(*) c FROM documents WHERE project IS NOT NULL "
            "GROUP BY project ORDER BY c DESC LIMIT ?",
            (limit,),
        ).fetchall()
    by_cat: dict[str, list[dict[str, Any]]] = {}
    flat: list[str] = []
    for r in rows:
        pid = r["project"]
        if not pid or pid.startswith("cursor-"):
            continue
        flat.append(pid)
        cat, name = parse_project_id(pid)
        cat = cat or "legacy"
        by_cat.setdefault(cat, []).append(
            {"id": pid, "name": name, "category": cat, "docs": int(r["c"])}
        )
    root = workspace_root(cfg)
    for cat in categories(cfg):
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for proj in cat_dir.iterdir():
            if not proj.is_dir() or proj.name.startswith("."):
                continue
            pid = project_id(cat, proj.name)
            if pid in flat:
                continue
            flat.append(pid)
            by_cat.setdefault(cat, []).append(
                {"id": pid, "name": proj.name, "category": cat, "docs": 0}
            )

    return {
        "categories": sorted(by_cat.keys()),
        "by_category": by_cat,
        "projects": flat,
    }
