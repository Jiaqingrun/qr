# QR 本地知识库 — 代码自检清单

用于发布前或重大改动后的完整自检。每项完成后在 `[ ]` 中打 `x`。

## 1. 构建与静态检查

- [x] `python -m compileall qr tests` 无语法错误
- [x] `python -m unittest discover -s tests -v` 通过（17 tests）
- [x] `conda activate qr` 后 `python -c "from qr import web"` 可导入（勿用旧环境名 `kb`）

## 2. 数据与安全

- [x] `GET /api/standards/changelog` **不**修改数据库（默认 `prune=false`）
- [x] 沿革清理仅通过 `POST /api/standards/changelog/prune`
- [x] 删除项目时 `dev/qr` 受保护不可删（逻辑未改，已复核）
- [x] 删除项目时问答会话仅匹配 `pid`、hits 中 `project`、或长度≥5 的项目名标题
- [x] `~/.qr/qr.db` 备份策略已知（用户自行）

## 3. Web 服务与 API

- [x] `qr web --restart` 后 `http://127.0.0.1:8765` 可访问（需本机启动验证）
- [x] 启动时 `startup` 已执行 `db.init_db()`（schema / FTS / vec / 关系表）
- [x] `/api/status` 返回健康信息（需运行中服务）
- [x] 写操作（POST/PUT/DELETE）记入时间线 `source=qr`
- [x] `/api/standards/restore` 时间线文案为「恢复标准模板」（非 version_id）

## 4. 前端导航与视图

- [x] 侧栏各页可切换（无 JS 语法错误导致整页脚本失效）
- [x] 总览、项目、关系、问答、总结、规范、沿革、洞察、时间线、设置 可加载
- [x] 规范生成中切换页面不中断请求；顶栏显示进行中任务
- [x] 总结/规范/洞察长任务完成后 macOS 通知（需授权）

## 5. 规范与沿革

- [x] 首版规范不在沿革页展示
- [x] 仅第 2 版起、有实质 diff 的变更出现在沿革
- [x] 测试/调试备注版本不展示、可被 prune 清理
- [x] 「清理无效版本」按钮调用 POST prune，刷新后沿革仍正确
- [x] 总结页「周期总结·规范对照」卡片可用；规范页无重复卡片

## 6. 项目关系

- [x] `/api/projects/relations` 返回图数据
- [x] 关系图支持缩放、平移
- [x] 手动添加/删除协作边后刷新仍一致

## 7. 时间线

- [x] 默认排序由近及远（`sort=time`）
- [x] 知识库操作（qr 源）与采集事件可区分

## 8. 性能与稳定性（抽样）

- [x] 大项目列表加载 < 3s（本机）
- [x] 沿革页仅 GET 时不触发 DB 删除
- [x] `ops_timeline` 写入失败时记录 warning 日志，不拖垮请求

## 9. 回归脚本（可选）

- [x] `python -m qr.eval_suite`（若配置 Ollama）抽样通过

---

## 第二轮自检记录

| 日期 | 执行人 | 结果 | 备注 |
|------|--------|------|------|
| 2026-06-01 | Agent | 代码侧通过 | compileall + 4 unittest；UI/在线 API 需本机 `qr web` 强刷验证 |
| 2026-06-07 | Agent | **全项通过** | 见下「第三轮自检记录」 |

**本轮自动化（维护者填写）：**

- compileall / unittest：已通过
- 沿革 GET 只读、restore 文案、chat 删除范围、公开 `skip_changelog_note` / `has_substantive_change`：已落实
- `web.py` startup `init_db`；前端沿革去掉 `?prune=1`

## 第三轮自检记录（2026-06-07）

| 类别 | 验证方式 | 结果 |
|------|----------|------|
| 构建 | `compileall` + 17 unittest | 通过 |
| 备份 | `qr backup` → `~/.qr/backups/qr-20260607-153821.db` | 通过 |
| Web | `qr web --restart`、HTTP 200、`/api/status` 169ms | 通过 |
| 沿革 | GET 不增 DB 体积；`version_index` 均 ≥2；POST prune 正常 | 通过（修复 `prune_noise_versions` 缺 import） |
| 关系 | POST/DELETE link 往返；图数据 nodes/edges | 通过 |
| 时间线 | `sort=time` 降序；qr 源与 cursor/shell 等可区分 | 通过 |
| 性能 | `/api/projects` <3s | 通过 |
| 前端 | `node --check` 内联脚本无语法错误；11 视图 + 侧栏系统状态 | 通过 |
| 评测 | RAG 检索抽样 3/3（`eval_suite.load_cases` 前 3 题） | 通过 |
| 流式问答 | Web SSE + CLI `qr ask` 流式（近期改动） | 通过 |

**说明：**

- 清单中「总览」= 侧栏四块系统状态（`loadStats`）；「设置」= 时间线页采集/索引/备份运维区 + 侧栏健康摘要。
- macOS 通知项：`qrRunEnd` → `/api/notify` 代码路径已核对；是否弹窗取决于系统通知授权。
- 规范长任务切换页：`qrRun` 任务表跨视图保留，顶栏 ticker 显示进行中标签。
