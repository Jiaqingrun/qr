#!/usr/bin/env python3
"""MCP stdio server exposing QR本地知识库 query/ask to Cursor."""
from __future__ import annotations

import json
import sys

from qr import db, facts, project_panel, query, retrieval_meta


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _fmt_hit(h: dict, i: int) -> str:
    sc = h.get("scores") or {}
    parts = [
        f"[{i+1}] {h['path']}",
        f"type={h.get('source_type', retrieval_meta.classify_source_type(h.get('path', '')))}",
        f"final={h.get('score', 0):.3f}",
    ]
    if sc:
        parts.append(
            f"vec={sc.get('vector', 0):.3f} fts={sc.get('fts', 0):.3f} "
            f"rrf={sc.get('rrf', 0):.3f} boost={sc.get('path_boost', 0):.3f}"
        )
    return " · ".join(parts) + f"\n{h['text'][:800]}"


def _tools_list():
    return {
        "tools": [
            {
                "name": "qr_search",
                "description": "在 QR本地知识库 中语义+FTS 混合检索，返回可解释分数",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "k": {"type": "integer", "default": 6},
                        "project": {"type": "string"},
                    },
                    "required": ["question"],
                },
            },
            {
                "name": "qr_ask",
                "description": "基于 QR本地知识库 RAG 问答（含稳定事实记忆）",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "k": {"type": "integer", "default": 6},
                        "project": {"type": "string"},
                        "deep": {"type": "boolean", "default": False},
                        "model": {"type": "string", "description": "Ollama 模型 id，见 ask_models"},
                    },
                    "required": ["question"],
                },
            },
            {
                "name": "qr_project",
                "description": "QR本地知识库 · 单项目面板：Git、Cursor、合规、事实、样例检索",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project": {"type": "string"},
                        "days": {"type": "integer", "default": 14},
                    },
                    "required": ["project"],
                },
            },
            {
                "name": "qr_facts",
                "description": "读取 QR本地知识库 稳定事实记忆（长期配置与约定）",
                "inputSchema": {
                    "type": "object",
                    "properties": {"project": {"type": "string"}},
                },
            },
            {
                "name": "qr_log_decision",
                "description": "写入一条决策日志到 QR本地知识库 时间线",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        ],
    }


def _call_tool(name: str, arguments: dict) -> dict:
    db.init_db()
    if name == "qr_search":
        hits = query.search(
            arguments["question"],
            int(arguments.get("k", 6)),
            project=arguments.get("project"),
        )
        text = "\n\n".join(_fmt_hit(h, i) for i, h in enumerate(hits)) or "无命中"
        return {"content": [{"type": "text", "text": text}]}
    if name == "qr_ask":
        from qr import models as qr_models

        try:
            model = qr_models.resolve_ask_model(
                arguments.get("model"),
                deep_legacy=bool(arguments.get("deep")),
            )
        except ValueError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        answer, hits, web = query.ask(
            arguments["question"],
            int(arguments.get("k", 6)),
            model=model,
            project=arguments.get("project"),
        )
        refs = "\n".join(_fmt_hit(h, i) for i, h in enumerate(hits[:3]))
        return {"content": [{"type": "text", "text": f"{answer}\n\n---\n{refs}"}]}
    if name == "qr_project":
        data = project_panel.panel(
            arguments["project"],
            int(arguments.get("days", 14)),
        )
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}]}
    if name == "qr_facts":
        block = facts.prompt_block(arguments.get("project")) or "（暂无稳定事实）"
        return {"content": [{"type": "text", "text": block}]}
    if name == "qr_log_decision":
        from qr.collectors import notes

        with db.session() as conn:
            ok = notes.add_note(conn, arguments["text"], kind="decision")
        return {"content": [{"type": "text", "text": "已记录决策" if ok else "记录失败"}]}
    return {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True}


def main():
    db.init_db()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method", "")
        if method == "initialize":
            _send({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "qr", "version": "0.3.0"},
                },
            })
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": _tools_list()})
        elif method == "tools/call":
            params = req.get("params", {})
            result = _call_tool(params.get("name", ""), params.get("arguments") or {})
            _send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "notifications/initialized":
            pass
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})


if __name__ == "__main__":
    main()
