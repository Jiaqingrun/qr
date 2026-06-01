# QR本地知识库

一套**纯本地、离线**的个人系统：记录并分析你的行为，规范开发/存储/行为，
生成周期性总结，并能基于你的项目内容进行语义检索与问答。全程使用本机
[ollama](https://ollama.com)（`bge-m3` 做向量，`qwen2.5:32b` 日常问答、`deepseek-r1:32b` 深度推理），
数据不出本机。

## 能力
- **行为采集**：Shell 命令历史、各项目 Git 提交、项目文件变化、Cursor AI 对话、手动笔记。
- **语义检索/问答**：对 `~/QR/<分类>/<项目>` 工作区建立向量索引，支持按分类/项目筛选，`qr ask` 用本地大模型回答。
- **周期性总结**：按天/周/月生成 Markdown 行为总结，并对照个人规范指出偏差。
- **治理**：维护一份"个人规范"，并据此生成项目级 `.cursor/rules` 与 `AGENTS.md`。
- **自动化**：`launchd` 每周自动跑一次采集+索引+总结。

## 安装
```bash
conda activate qr
pip install -e .
qr init
```

## 常用命令
```bash
qr status                 # 查看状态
qr ingest                 # 采集所有行为数据（增量）
qr index                  # 索引 ~/Projects 项目内容
qr query "关键词"          # 语义检索片段
qr ask "我之前怎么实现X的？"  # 本地大模型基于项目内容回答
qr log "今天决定用方案A"     # 随手记笔记
qr summary --period week  # 生成本周行为总结
qr standards --edit       # 编辑个人规范
qr rules --target ~/Projects/some-proj  # 为某项目生成 Cursor 规则/AGENTS.md
qr schedule install       # 安装每周定时任务
qr web --install          # 安装 Web 界面后台服务
qr desktop --install      # 安装 macOS 桌面应用（QR本地知识库.app）
qr workspace migrate --yes  # 将散落项目迁入 ~/QR
qr workspace new my-app --category dev  # 新建项目
```

## 工作区（~/QR）

本机代码统一放在 `~/QR/<分类>/<项目名>`，默认分类：`dev` / `mobile` / `experiments` / `tools` / `archive`。

```bash
qr workspace status       # 查看待迁移项目
qr workspace migrate --yes
qr index --reindex        # 迁移后重建索引（project 变为 dev/qr 形式）
```

## 数据位置
- 工作区：`~/QR`
- 知识库代码：`~/QR/dev/qr`（迁移后）或 `~/Projects/qr`（迁移前）
- 数据：`~/.qr`（`qr.db` 数据库、`summaries/` 总结、`standards.md` 规范、`config.json` 配置）

首次运行 `qr init` 会自动将旧目录 `~/.kb` 中的数据迁移到 `~/.qr`。

## 从旧版迁移
1. 将仓库目录重命名：`mv ~/Projects/kb ~/Projects/qr`（若尚未重命名）
2. 将 conda 环境重命名为 `qr`（或新建环境后 `pip install -e ~/Projects/qr`）
3. 运行 `qr init` 完成 `~/.kb` → `~/.qr` 数据迁移
4. 重新安装定时任务：`qr schedule uninstall` 后 `qr schedule install`（launchd 标签已改为 `com.qr.*`）

## 配置
编辑 `~/.qr/config.json` 可调整索引目录、模型名、分块大小、排除目录等。
