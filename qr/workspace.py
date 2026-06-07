from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import config, db

_DEFAULT_CATEGORIES = ("dev", "mobile", "experiments", "tools", "archive")
_PROTECTED_PROJECTS = frozenset({"dev/qr"})
_DELETE_CONFIRM_PHRASE = "永久删除"
# 工作区目录存在但不应出现在项目列表（导出镜像等）
_LIST_EXCLUDE_IDS = frozenset({"dev/qr-export"})


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


def _excluded_list_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    """项目标签/UI 中应屏蔽的本机文档等目录（非 ~/QR 工作区）。"""
    cfg = cfg or config.load_config()
    home = Path.home().resolve()
    roots: list[Path] = []
    for name in ("Documents", "Desktop", "Downloads", "Templates", ".Trash"):
        p = home / name
        if p.is_dir():
            roots.append(p.resolve())
    for base in config.expand_paths(cfg.get("scatter_roots") or []):
        try:
            br = base.resolve()
        except OSError:
            continue
        if br == workspace_root(cfg).resolve():
            continue
        if br in roots:
            continue
        if br.name in ("Documents", "Desktop", "Downloads"):
            roots.append(br)
    return roots


def is_excluded_path(path: Path, cfg: dict[str, Any] | None = None) -> bool:
    """路径是否位于应屏蔽的文档/散落目录下。"""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for root in _excluded_list_roots(cfg):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def is_listable_project_id(project_id: str, cfg: dict[str, Any] | None = None) -> bool:
    """是否为用户工作区项目（~/QR/<分类>/<名>），用于下拉/标签列表。"""
    cfg = cfg or config.load_config()
    pid = (project_id or "").strip().strip("/")
    if not pid or pid.startswith("cursor-"):
        return False
    if pid in _LIST_EXCLUDE_IDS:
        return False
    cat, name = parse_project_id(pid)
    if not cat or not name:
        return False
    if cat not in categories(cfg):
        return False
    pdir = resolve_project_dir(pid, cfg)
    if not pdir or not pdir.is_dir():
        return False
    return is_under_workspace(pdir, cfg)


def sanitize_display_project(project_id: str | None) -> str | None:
    """API/UI 展示：非用户工作区项目不显示 project 标签。"""
    pid = (project_id or "").strip()
    if not pid:
        return None
    return pid if is_listable_project_id(pid) else None


def is_searchable_content(path: str | None, doc_project: str | None = None) -> bool:
    """检索是否保留该文档块（仅用户项目 + ~/.qr 运行时元数据 + ~/QR 路径）。"""
    dp = (doc_project or "").strip()
    if dp and is_listable_project_id(dp):
        return True
    if dp in ("qr-config", "qr-standards"):
        return True
    path_s = (path or "").replace("\\", "/")
    if path_s and ("/.qr/" in path_s or path_s.startswith(str(config.QR_HOME))):
        return True
    if path_s:
        try:
            p = Path(path_s).expanduser().resolve()
            if is_under_workspace(p):
                return True
            try:
                p.relative_to(config.QR_HOME.resolve())
                return True
            except ValueError:
                pass
        except OSError:
            pass
    return False


def event_row_visible(source: str, project: str | None) -> bool:
    """时间线是否保留该事件（屏蔽非工作区的 file 类索引噪声）。"""
    src = (source or "").strip()
    pid = (project or "").strip()
    if not pid:
        return True
    if is_listable_project_id(pid):
        return True
    if src == "file":
        return False
    return True


_TIMELINE_HIDDEN_QR_ACTIONS = frozenset({"ingest.cursor"})


def event_timeline_hidden(
    source: str,
    title: str | None = None,
    meta: str | None = None,
) -> bool:
    """时间线 UI 应隐藏的知识库操作（后台仍执行，仅不展示）。"""
    if (source or "").strip() != "qr":
        return False
    if (title or "").strip() == "[知识库] Cursor 采集":
        return True
    if meta:
        try:
            obj = json.loads(meta)
            return obj.get("action") in _TIMELINE_HIDDEN_QR_ACTIONS
        except json.JSONDecodeError:
            pass
    return False


def _events_timeline_hidden_match_sql() -> str:
    """SQL 片段：匹配应隐藏/可清理的 qr 时间线噪声。"""
    actions = ",".join(f"'{a}'" for a in sorted(_TIMELINE_HIDDEN_QR_ACTIONS))
    return (
        "source = 'qr' AND ("
        "title = '[知识库] Cursor 采集' OR "
        f"json_extract(meta, '$.action') IN ({actions})"
        ")"
    )


def events_timeline_hidden_sql() -> str:
    """SQL 片段：排除后台 Cursor 采集等不入时间线的 qr 操作。"""
    return f"NOT ({_events_timeline_hidden_match_sql()})"


def purge_timeline_hidden_qr_events(conn) -> int:
    """永久删除时间线中的后台 Cursor 采集等 qr 噪声记录。"""
    match = _events_timeline_hidden_match_sql()
    row = conn.execute(f"SELECT COUNT(*) c FROM events WHERE {match}").fetchone()
    n = int(row["c"]) if row else 0
    if n:
        conn.execute(f"DELETE FROM events WHERE {match}")
        conn.commit()
    return n


def events_project_sql_filter() -> tuple[str, list[str]]:
    """时间线 SQL 条件：file 事件仅保留工作区项目。"""
    allowed = list_projects_grouped(500)["projects"]
    if not allowed:
        return "(source != 'file' OR project IS NULL)", []
    ph = ",".join("?" * len(allowed))
    return f"(source != 'file' OR project IS NULL OR project IN ({ph}))", allowed


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


def ensure_qr_repo_home(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """确认知识库源码在 ~/QR/dev/qr，并清理 ~/Projects/qr 旧入口。"""
    cfg = cfg or config.load_config()
    root = workspace_root(cfg)
    dest = root / "dev" / "qr"
    if not dest.is_dir():
        raise ValueError(f"知识库目录不存在: {dest}")
    legacy_link = Path.home() / "Projects" / "qr"
    removed_link = False
    if legacy_link.is_symlink():
        try:
            target = legacy_link.resolve()
            if target == dest.resolve():
                legacy_link.unlink()
                removed_link = True
        except OSError:
            pass
    projects_readme = Path.home() / "Projects" / "README.md"
    if not projects_readme.exists():
        projects_readme.write_text(
            "# 项目已迁至 QR 工作区\n\n"
            "本机代码项目已统一放在 **`~/QR/<分类>/<项目名>`**。\n\n"
            "- 知识库：`~/QR/dev/qr`\n"
            "- 新建项目：`qr workspace new <名称> --category dev`\n"
            "- 查看布局：`qr workspace status`\n\n"
            "此目录不再用于存放新项目。\n",
            encoding="utf-8",
        )
    return {
        "project_id": "dev/qr",
        "path": str(dest.resolve()),
        "legacy_link_removed": removed_link,
    }


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
    from . import project_standards

    pid = project_id(cat, proj)
    project_standards.ensure_project_standards(dest, project_id=pid)
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


def _cursor_projects_base(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or config.load_config()
    return config._expand(str(cfg.get("cursor_projects_dir", "~/.cursor/projects")))


def _cursor_dir_slug(proj_dir: Path) -> str:
    return str(proj_dir.resolve()).replace("/", "-").lstrip("-")


def find_cursor_project_dirs(
    proj_dir: Path | None,
    name: str,
    cfg: dict[str, Any] | None = None,
) -> list[Path]:
    """匹配 ~/.cursor/projects 下对应该工作区项目的目录。"""
    base = _cursor_projects_base(cfg)
    if not base.is_dir():
        return []
    found: list[Path] = []
    seen: set[str] = set()
    nl = name.lower()

    def add(p: Path) -> None:
        key = str(p.resolve())
        if key not in seen and p.is_dir():
            seen.add(key)
            found.append(p)

    if proj_dir and proj_dir.is_dir():
        add(base / _cursor_dir_slug(proj_dir))
        enc = str(proj_dir.resolve()).replace("/", "-").lstrip("-").lower()
        for d in base.iterdir():
            if d.is_dir() and enc and enc in d.name.lower():
                add(d)
    for d in base.iterdir():
        if not d.is_dir():
            continue
        dn = d.name.lower()
        if dn.endswith(f"-{nl}") or dn == nl or f"-{nl.replace('/', '-')}" in dn:
            add(d)
            continue
        if proj_dir and nl in dn and str(proj_dir.resolve()).replace("/", "-").lower() in dn:
            add(d)
    return found


def _count_cursor_transcripts(cursor_dirs: list[Path]) -> int:
    n = 0
    for root in cursor_dirs:
        n += len(list(root.glob("agent-transcripts/*/*.jsonl")))
    return n


def _purge_cursor_workspace(
    conn: sqlite3.Connection,
    cursor_dirs: list[Path],
) -> dict[str, int]:
    """删除 Cursor 项目目录与转录文件；不删 events / 引导语表。"""
    from . import db

    stats = {"cursor_dirs": 0, "cursor_transcripts": 0, "cursor_state_keys": 0}
    for root in cursor_dirs:
        uuids: set[str] = set()
        for jsonl in root.glob("agent-transcripts/*/*.jsonl"):
            uuids.add(jsonl.stem)
            stats["cursor_transcripts"] += 1
        for uid in uuids:
            if db.get_state(conn, f"cursor_sig:{uid}"):
                conn.execute("DELETE FROM state WHERE key=?", (f"cursor_sig:{uid}",))
                stats["cursor_state_keys"] += 1
        try:
            shutil.rmtree(root)
            stats["cursor_dirs"] += 1
        except OSError:
            pass
    return stats


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


def _chat_session_ids_for_project(
    conn: sqlite3.Connection, pid: str, name: str
) -> list[int]:
    """仅匹配与项目明确关联的问答会话，避免短项目名误删。"""
    ids: set[int] = set()
    for row in conn.execute(
        "SELECT id FROM chat_sessions WHERE title LIKE ?",
        (f"%{pid}%",),
    ).fetchall():
        ids.add(int(row["id"]))
    for pat in (f'%"project": "{pid}"%', f'%"project":"{pid}"%'):
        for row in conn.execute(
            "SELECT DISTINCT session_id FROM chat_messages "
            "WHERE hits IS NOT NULL AND trim(hits) != '' AND hits LIKE ?",
            (pat,),
        ).fetchall():
            ids.add(int(row["session_id"]))
    if len(name) >= 5:
        for row in conn.execute(
            "SELECT id FROM chat_sessions WHERE title LIKE ?",
            (f"%{name}%",),
        ).fetchall():
            ids.add(int(row["id"]))
    return sorted(ids)


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
        chats = len(_chat_session_ids_for_project(conn, pid, name))

    fact_list = facts.list_facts(pid)
    disk_bytes = 0
    if proj_dir and proj_dir.is_dir():
        disk_bytes = sum(f.stat().st_size for f in proj_dir.rglob("*") if f.is_file())

    cursor_dirs = find_cursor_project_dirs(proj_dir, name, cfg)
    cursor_transcripts = _count_cursor_transcripts(cursor_dirs)
    cursor_disk = 0
    for d in cursor_dirs:
        try:
            cursor_disk += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            pass

    return {
        "project": pid,
        "path": str(proj_dir.resolve()) if proj_dir else None,
        "index_only": proj_dir is None,
        "protected": False,
        "confirm_phrase": _DELETE_CONFIRM_PHRASE,
        "retain": {
            "timeline_events": int(events),
            "prompt_guides": True,
        },
        "counts": {
            "documents": int(docs),
            "chunks": int(chunks),
            "events": int(events),
            "chat_sessions": int(chats),
            "facts": len(fact_list),
            "cursor_dirs": len(cursor_dirs),
            "cursor_transcripts": cursor_transcripts,
        },
        "disk_bytes": disk_bytes,
        "cursor_disk_bytes": cursor_disk,
        "cursor_paths": [str(p) for p in cursor_dirs],
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


def _record_project_delete_event(
    conn: sqlite3.Connection,
    *,
    pid: str,
    preview: dict[str, Any],
    stats: dict[str, Any],
    via: str = "web",
) -> None:
    from . import ops_timeline

    ops_timeline.log_project_delete(
        conn, pid=pid, preview=preview, stats=stats, via=via,
    )


def verify_project_removed(
    project: str,
    cfg: dict[str, Any] | None = None,
    *,
    strict_id: bool = False,
) -> dict[str, Any]:
    """核对项目是否已从工作区、索引、Cursor 转录等位置清除。"""
    cfg = cfg or config.load_config()
    pid = project.strip() if strict_id else normalize_project_id(project, cfg)
    proj_dir = _resolve_project_dir_exact(pid, cfg) if strict_id else resolve_project_dir(pid, cfg)
    if not strict_id and proj_dir is None:
        proj_dir = _resolve_project_dir_exact(pid, cfg)

    pid_scoped, name, path_like, cursor_proj = _delete_scope(pid, proj_dir)
    doc_clause = _doc_match_clause(path_like)
    doc_params = _doc_match_params(pid_scoped, cursor_proj, path_like)
    cursor_dirs = find_cursor_project_dirs(proj_dir, name, cfg)

    checks: list[dict[str, Any]] = []

    def add(item: str, ok: bool, detail: str, *, warn: bool = False) -> None:
        checks.append({"item": item, "ok": ok, "detail": detail, "warn": warn})

    if proj_dir and proj_dir.is_dir():
        add("工作区项目目录", False, str(proj_dir.resolve()))
    else:
        hint = str(proj_dir.resolve()) if proj_dir else f"未在 ~/QR 解析到 {pid}"
        add("工作区项目目录", True, f"不存在（{hint}）")

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
        chats = conn.execute(
            "SELECT COUNT(*) c FROM chat_sessions WHERE title LIKE ? OR title LIKE ?",
            (f"%{name}%", f"%{pid_scoped}%"),
        ).fetchone()["c"]
        ev = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE source='qr' AND title LIKE ?",
            (f"%删除项目 {pid_scoped}%",),
        ).fetchone()["c"]

    from . import facts

    fact_n = len(facts.list_facts(pid_scoped))
    transcript_n = _count_cursor_transcripts(cursor_dirs)

    add("向量索引文档", docs == 0, f"剩余 {docs} 篇" if docs else "已清空")
    add("向量块", chunks == 0, f"剩余 {chunks} 块" if chunks else "已清空")
    add("Cursor 项目目录", len(cursor_dirs) == 0, (
        "已移除" if not cursor_dirs else "仍存在: " + ", ".join(str(p) for p in cursor_dirs)
    ))
    add("Cursor 转录文件", transcript_n == 0, f"剩余 {transcript_n} 条" if transcript_n else "已清空")
    add("本库问答会话", chats == 0, f"剩余 {chats} 条" if chats else "已清空")
    add("稳定事实", fact_n == 0, f"剩余 {fact_n} 条" if fact_n else "已清空")
    add(
        "时间线删除记录",
        ev > 0,
        f"已有 {ev} 条" if ev else "尚无（删除后将自动写入）",
        warn=ev == 0,
    )

    hard = [c for c in checks if not c.get("warn")]
    clean = all(c["ok"] for c in hard)
    return {
        "project": pid_scoped,
        "clean": clean,
        "checks": checks,
        "cursor_paths": [str(p) for p in cursor_dirs],
        "workspace_path": str(proj_dir.resolve()) if proj_dir and proj_dir.is_dir() else None,
    }


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
    if confirm_phrase.strip() != _DELETE_CONFIRM_PHRASE:
        raise ValueError(f"确认短语不正确，请输入: {_DELETE_CONFIRM_PHRASE}")
    if confirm.strip() and confirm.strip() != pid:
        raise ValueError(f"确认名称不匹配，请输入: {pid}")

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

        # 时间线 events 与引导语库保留（用户要求）
        stats["events_deleted"] = 0
        stats["events_retained"] = preview["counts"].get("events", 0)

        chat_ids = _chat_session_ids_for_project(conn, pid, name)
        for sid in chat_ids:
            conn.execute("DELETE FROM chat_sessions WHERE id=?", (sid,))
        stats["chat_sessions_deleted"] = len(chat_ids)

        db.rebuild_fts(conn)
        cursor_dirs = [Path(p) for p in preview.get("cursor_paths") or []]
        if not cursor_dirs and proj_dir:
            cursor_dirs = find_cursor_project_dirs(proj_dir, name, cfg)
        stats.update(_purge_cursor_workspace(conn, cursor_dirs))
        _record_project_delete_event(
            conn,
            pid=pid,
            preview=preview,
            stats=stats,
            via="web",
        )

    stats["facts_removed"] = _purge_facts_for_project(pid, name)

    if proj_dir and proj_dir.is_dir():
        shutil.rmtree(proj_dir)
        stats["disk_removed"] = True

    return {
        "ok": True,
        "project": pid,
        "path": str(proj_dir) if proj_dir else None,
        "stats": stats,
        "verify": verify_project_removed(pid, cfg, strict_id=strict_id),
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
    """仅列出 ~/QR 工作区下用户创建的项目（不含索引幽灵、Documents 等）。"""
    from . import db

    cfg = config.load_config()
    root = workspace_root(cfg)
    doc_counts: dict[str, int] = {}
    with db.session() as conn:
        rows = conn.execute(
            "SELECT project, COUNT(*) c FROM documents WHERE project IS NOT NULL "
            "GROUP BY project",
        ).fetchall()
        for r in rows:
            doc_counts[r["project"]] = int(r["c"])

    by_cat: dict[str, list[dict[str, Any]]] = {}
    flat: list[str] = []
    for cat in categories(cfg):
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for proj in sorted(cat_dir.iterdir()):
            if not proj.is_dir() or proj.name.startswith("."):
                continue
            pid = project_id(cat, proj.name)
            if not is_listable_project_id(pid, cfg):
                continue
            flat.append(pid)
            by_cat.setdefault(cat, []).append(
                {
                    "id": pid,
                    "name": proj.name,
                    "category": cat,
                    "docs": doc_counts.get(pid, 0),
                }
            )
            if len(flat) >= limit:
                break
        if len(flat) >= limit:
            break

    return {
        "categories": sorted(by_cat.keys()),
        "by_category": by_cat,
        "projects": flat,
    }
