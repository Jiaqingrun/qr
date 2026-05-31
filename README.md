# kb · 本地个人行为知识库与治理系统

一套**纯本地、离线**的个人系统：记录并分析你的行为，规范开发/存储/行为，
生成周期性总结，并能基于你的项目内容进行语义检索与问答。全程使用本机
[ollama](https://ollama.com)（`nomic-embed-text` 做向量，`deepseek-r1:32b` 做总结/问答），
数据不出本机。

## 能力
- **行为采集**：Shell 命令历史、各项目 Git 提交、项目文件变化、Cursor AI 对话、手动笔记。
- **语义检索/问答**：对 `~/Projects` 下的项目内容建立向量索引，`kb ask` 用本地大模型回答。
- **周期性总结**：按天/周/月生成 Markdown 行为总结，并对照个人规范指出偏差。
- **治理**：维护一份"个人规范"，并据此生成项目级 `.cursor/rules` 与 `AGENTS.md`。
- **自动化**：`launchd` 每周自动跑一次采集+索引+总结。

## 安装
```bash
conda activate kb
pip install -e .
kb init
```

## 常用命令
```bash
kb status                 # 查看状态
kb ingest                 # 采集所有行为数据（增量）
kb index                  # 索引 ~/Projects 项目内容
kb query "关键词"          # 语义检索片段
kb ask "我之前怎么实现X的？"  # 本地大模型基于项目内容回答
kb log "今天决定用方案A"     # 随手记笔记
kb summary --period week  # 生成本周行为总结
kb standards --edit       # 编辑个人规范
kb rules --target ~/Projects/some-proj  # 为某项目生成 Cursor 规则/AGENTS.md
kb schedule install       # 安装每周定时任务
```

## 数据位置
- 代码：`~/Projects/kb`
- 数据：`~/.kb`（`kb.db` 数据库、`summaries/` 总结、`standards.md` 规范、`config.json` 配置）

## 配置
编辑 `~/.kb/config.json` 可调整索引目录、模型名、分块大小、排除目录等。
