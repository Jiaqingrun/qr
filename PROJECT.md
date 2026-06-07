# 项目约定 · qr

> QR 本地知识库程序仓库。全局规范见 `qr standards` / `00-personal-standards.mdc`。

## 用途
纯本地个人知识库：行为采集、时间线、向量检索/RAG、规范与 Cursor 规则、Web 控制台（8765）。

## 技术栈与结构
- Python 3.10+（与 `pyproject.toml` 一致），包名 `qr`，入口 `qr/cli.py`
- 核心模块：`collectors/`、`indexer.py`、`query.py`、`web.py`、`governance.py`、`prompt_guides.py`
- 运行数据仅在 `~/.qr`；业务代码仅在 `~/QR/dev/qr`

## 开发约定
- 修改后：`pip install -e ~/QR/dev/qr` 与 `qr web --restart`
- 测试：`python3 -m unittest discover -s tests`
- 自检：`qr doctor`；勿恢复误删 `qr.db` 的沿革逻辑
- 索引默认仅 `~/QR`（见 `config.json` → `index_roots`）

## AI 协作（本项目）
- 先读 `README.md`、`docs/USE_CASES.md`、**`docs/WEB_UI_LAYOUT.md`**（Web 改版）
- 最小 diff；不提交除非用户要求
- 时间线 cursor 事件按 file 打开归档路径，不用弹窗
- **Web UI**：全标签 `view--fill` 全宽布局；见 `docs/WEB_UI_LAYOUT.md`

### 改动后必做（知识库程序变更）
1. **文档与规则**：自行判断并同步 `README.md`、`docs/USE_CASES.md`；新命令/行为/API/MCP/Web 页须写入；必要时更新 `PROJECT.md`、`.cursor/rules/10-project.mdc`、`docs/CODE_AUDIT_CHECKLIST.md`
2. **全量自检**（有问题必须修完再继续）：
   - `pip install -e ~/QR/dev/qr`
   - `python -m compileall qr tests`
   - `/opt/anaconda3/envs/qr/bin/python -m unittest discover -s tests -v`
   - `qr doctor`；涉及 Web 则 `qr web --restart`
3. **清理无效代码**：删除未使用函数/导入、重复逻辑、过时注释；不保留死代码
