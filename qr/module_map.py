"""M9-1 · 模块地图：设计者不改代码也能定位主文件。"""
from __future__ import annotations

from typing import Any

from . import config

_REPO = config.REPO_ROOT


def _f(rel: str) -> str:
    return str((_REPO / rel).resolve())


def module_map() -> dict[str, Any]:
    """采集 / 索引 / 问答 / Web / MCP 各模块说明与主文件。"""
    areas = [
        {
            "id": "collectors",
            "title": "采集",
            "icon": "capture",
            "lines": [
                "Cursor 对话、Git、笔记、屏幕采样写入时间线与库。",
                "非 Cursor 投入用 `qr log --type activity`；决策用 `--type decision`。",
                "暂停屏幕采样：`qr track --pause` 或运维页开关。",
            ],
            "files": [
                {"label": "笔记 / 活动", "path": _f("qr/collectors/notes.py")},
                {"label": "Cursor 归档", "path": _f("qr/collectors/cursor.py")},
                {"label": "屏幕采样", "path": _f("qr/tracker.py")},
                {"label": "Git 事件", "path": _f("qr/collectors/gitlog.py")},
            ],
            "docs": [
                {"label": "用例", "path": _f("docs/USE_CASES.md")},
                {"label": "短板 M5", "path": _f("docs/短板修复.md")},
            ],
        },
        {
            "id": "index",
            "title": "索引",
            "icon": "index",
            "lines": [
                "扫描 `index_roots` 切块、向量化；`qr index` / ingest 后增量。",
                "健康与孤儿清理：`qr index-health`、运维页「索引健康」。",
                "检索升级路线见 RETRIEVAL_UPGRADE_PLAN。",
            ],
            "files": [
                {"label": "索引器", "path": _f("qr/indexer.py")},
                {"label": "索引健康", "path": _f("qr/index_health.py")},
                {"label": "配置 index_roots", "path": _f("qr/config.py")},
            ],
            "docs": [
                {"label": "检索升级计划", "path": _f("docs/RETRIEVAL_UPGRADE_PLAN.md")},
                {"label": "扩展评测题", "path": _f("docs/EVAL_EXTENDED_CASES.md")},
            ],
        },
        {
            "id": "query",
            "title": "问答 / 检索",
            "icon": "dialog",
            "lines": [
                "混合检索 + 可选 rerank；`qr ask` / Web 问答。",
                "仅出处模式：`qr ask --citations-only`（不调用 chat 模型）。",
                "RAG 门禁：`qr eval rag`（core 9 题）；扩展题 `--extended`。",
            ],
            "files": [
                {"label": "检索主逻辑", "path": _f("qr/query.py")},
                {"label": "评测题集", "path": _f("qr/eval_suite.py")},
                {"label": "月报评测", "path": _f("qr/monthly_eval.py")},
            ],
            "docs": [
                {"label": "AI 评测量表", "path": _f("docs/AI_SKILL_ASSESSMENT.md")},
            ],
        },
        {
            "id": "web",
            "title": "Web / CLI",
            "icon": "ops",
            "lines": [
                "控制台 `qr web`（默认 8765）；API 在 `qr/web.py`。",
                "运维：自检、备份、定时任务、模块地图（本页）。",
                "设计者验收：`qr ship-check`、合规 `qr compliance --ship`。",
            ],
            "files": [
                {"label": "Web API", "path": _f("qr/web.py")},
                {"label": "CLI 入口", "path": _f("qr/cli.py")},
                {"label": "运维面板", "path": _f("qr/ops_panel.py")},
                {"label": "设计者验收", "path": _f("qr/ship_check.py")},
            ],
            "docs": [
                {"label": "进化计划", "path": _f("docs/EVOLUTION_PLAN.md")},
                {"label": "短板修复总表", "path": _f("docs/短板修复.md")},
            ],
        },
        {
            "id": "governance",
            "title": "治理 / MCP",
            "icon": "dialog",
            "lines": [
                "规范 `qr standards`；修订预览 `qr standards-revise`。",
                "引导语与片段：`qr/prompt_guides.py`；facts 与 PROJECT 分层。",
                "MCP 知识库服务配置见 Cursor MCP（user-qr-knowledge）。",
            ],
            "files": [
                {"label": "规范治理", "path": _f("qr/governance.py")},
                {"label": "规范修订队列", "path": _f("qr/standards_revision.py")},
                {"label": "引导语", "path": _f("qr/prompt_guides.py")},
                {"label": "工作区 / Cursor 根", "path": _f("qr/workspace.py")},
            ],
            "docs": [
                {"label": "全局规范", "path": _f("standards/STANDARDS.md")},
            ],
        },
    ]
    return {
        "repo_root": str(_REPO),
        "areas": areas,
        "shortfall_doc": _f("docs/短板修复.md"),
        "retrieval_doc": _f("docs/RETRIEVAL_UPGRADE_PLAN.md"),
    }
