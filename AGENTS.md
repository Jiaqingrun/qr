# AGENTS.md

> 由 QR 知识库生成：`00-personal-standards.mdc`（全局）+ `10-project.mdc`（本项目）。

## 全局个人规范

# 个人开发 / 存储 / 行为规范

> 这是一份"活文档"。`QR本地知识库` 会用它来生成 Cursor 规则、对照你的实际行为给出偏差提醒。
> 随时编辑：`qr standards --edit`。

## 一、存储与目录规范
- 所有代码项目统一放在 **`~/QR/<分类>/<project-name>`**（分类如 `dev` / `mobile` / `experiments` / `tools` / `archive`），**不要**放在桌面 / 下载 / `~/Projects` 散落目录。
- 新建项目：`qr workspace new <名称> --category dev`。
- 项目命名用小写中划线：`my-project`，避免空格和大小写混用（本机文件系统大小写不敏感）。
- 临时文件放系统临时目录，不留在桌面。
- 大文件（ISO / 安装包 / 视频）不放桌面，归档到外部盘或 NAS。

### 知识库与本机数据（QR）
- **业务项目代码**：仅 `~/QR/<分类>/<项目>`；**知识库程序仓库**（本系统源码）：`~/QR/dev/qr`。
- **运行数据**（数据库 `qr.db`、配置、周期总结、笔记）：仅 **`~/.qr`**，不提交 Git、不放入业务项目目录。
- **新建 / 迁移 / 清理**：`qr workspace new`、`qr workspace migrate`、`qr workspace prune`；删除项目须用 `qr workspace delete`（二次确认）。
- **行为与笔记**：关键进展用 `qr log` 或 `~/.qr/notes/*.md`（由 `qr ingest` 同步）；每周 `qr summary --period week` 对照本规范复盘。
- **索引与问答**：项目纳入知识库后执行 `qr index`；问答与检索基于本地索引，不依赖把数据拷进项目树。
- **运维参考**（细节以 `~/QR/dev/qr/README.md` 为准）：自检 `qr doctor`，备份 `qr backup`，后台任务 `qr schedule status`。

## 二、Python 环境规范
- 系统 Python（`/usr/bin/python3`）保持纯净，不 pip 安装任何东西。
- 每个需要依赖的项目用独立 conda 环境：`conda create -n <name> python=3.x`。
- 统一用 `/opt/anaconda3` 作为唯一 conda 入口；环境名用小写。
- 每个项目根目录提供 `requirements.txt` 或 `environment.yml`，可复现。

## 三、Git 与开发规范
- 每个项目第一步 `git init` 并尽早提交，避免无版本控制的"裸代码"。
- 提交信息写清"为什么"，使用动词开头：add / update / fix / refactor。
- 每个项目有 `README.md` 说明用途、运行方式、依赖。
- 密钥、token 绝不写进代码或 dotfile 明文；放入 `~/.config/zsh/secrets.zsh`（权限 600）或项目 `.env`（加入 `.gitignore`）。

## 四、AI 协作规范
- 复杂/多文件任务先让 AI 出方案再动手。
- 项目里放 `AGENTS.md` 与 `.cursor/rules`，让 AI 自动遵守本规范（`qr rules` 生成）。
- 重要的 AI 对话结论，用 `qr log` 或 `~/.qr/notes/*.md` 沉淀，便于时间线与总结引用。

## 五、行为与复盘规范
- 每天用 `qr log` 记录关键进展或决定。
- 每周查看 `qr summary --period week` 的总结，对照本规范修正习惯。
- 阶段性把散落项目迁入 `~/QR` 或归档：`qr workspace migrate` / `qr workspace prune`。

## 六、界面与视觉规范（全局）
- **QR 知识库 Web**（`http://127.0.0.1:8765`）与各业务项目前台，在信息架构上保持一致：侧栏导航、卡片分区、时间线列表（首行标题 + 可点击路径）、标签色与来源色区分。
- 深色主题为默认参考；主色使用 CSS 变量（如 `--lime` / `--muted` / `--rose`），避免硬编码散落颜色。
- 交互：可点击项用 `.tl-link` / `btn` 体系；危险操作用 `--rose` 并二次确认；加载与空状态需有明确文案，避免空白页。
- 新页面或改版先对照本节的布局与组件习惯，再写项目内 UI；**项目特有**的视觉（品牌色、插画）写在各项目 `PROJECT.md`，不写入本节。

## 本项目约定

# 项目约定 · qr

> QR 本地知识库程序仓库。全局规范见 `qr standards` / `00-personal-standards.mdc`。

## 用途
纯本地个人知识库：行为采集、时间线、向量检索/RAG、规范与 Cursor 规则、Web 控制台（8765）。

## 技术栈与结构
- Python 3.12+，包名 `qr`，入口 `qr/cli.py`
- 核心模块：`collectors/`、`indexer.py`、`query.py`、`web.py`、`governance.py`、`prompt_guides.py`
- 运行数据仅在 `~/.qr`；业务代码仅在 `~/QR/dev/qr`

## 开发约定
- 修改后：`pip install -e ~/QR/dev/qr` 与 `qr web --restart`
- 测试：`python3 -m unittest discover -s tests`
- 自检：`qr doctor`；勿恢复误删 `qr.db` 的沿革逻辑
- 索引默认仅 `~/QR`（见 `config.json` → `index_roots`）

## AI 协作（本项目）
- 先读 `README.md`、`docs/USE_CASES.md`
- 最小 diff；不提交除非用户要求
- 时间线 cursor 事件按 file 打开归档路径，不用弹窗
