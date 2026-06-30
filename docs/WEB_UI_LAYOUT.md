# QR Web 界面布局规范

> 适用于 `qr/static/index.html`（`http://127.0.0.1:8765`）。改版或新增标签页时须遵守。

## 总体原则

1. **全宽自适应**：`.content` 横向铺满主区域（侧栏右侧至窗口边缘），仅保留 `--split-pad`（20px）外边距；**禁止** `max-width` 居中窄栏。
2. **纵向填满**：每个标签页使用 `.view.view--fill`，在顶栏下方占满剩余视口高度；**禁止**对 `.page-body` 或 split shell 使用 `max-height: calc(100vh - …)` 硬编码截断（应依赖 flex 链 `flex:1; min-height:0`）。
3. **区域间距**：统一使用 CSS 变量 `--split-gap`（16px）分隔操作区、主内容区、分栏卡片。
4. **内部滚动**：滚动发生在列表/正文/面板内部，避免整页空白或双滚动条。
5. **关系页纵向分配**：`rel-stage` 用 `grid-template-rows` 让图谱与下方 dock（详情 + 组合）按比例撑满剩余高度，禁止固定 `vh` 上限导致底部留白。

## 页面骨架

```html
<section class="view view--fill" id="view-xxx">
  <div class="page-ops"><!-- 工具栏 / 筛选 / 输入 --></div>
  <div class="page-body [修饰符]"><!-- 主内容 --></div>
</section>
```

### `page-body` 修饰符

| 修饰符 | 适用场景 | 示例标签 |
|--------|----------|----------|
| （默认） | 单卡片 + 分页 | 时间线 |
| `page-body--scroll` | 结果列表纵向滚动 | 检索、应用、规范沿革 |
| `page-body--grid` | 多列网格撑满 | 项目、关系、洞察 |
| `page-body--split` | 左右分栏 shell | 引导语 |
| `split-shell` | 左列表 + 右详情 | 问答、总结 |

## 各标签布局

| 标签 | 结构 |
|------|------|
| **今日** | `page-ops`（快捷按钮）+ `page-body--grid`（接着干 / 待处理 / 摘要） |
| 问 | `split-ops`（模式 Seg + 高级折叠）+ `split-shell`（历史 \| 对话）；出处/符号模式用 `#askCiteOut` |
| 总结 | `split-ops` + `split-shell`（列表 \| 正文 + 规范对照） |
| 项目 / 洞察 | `page-ops` + `page-body--grid` → `insight-grid` |
| 关系 | `page-ops` + `rel-shell`（左编辑 \| 右图谱 + 详情/组合 dock，纵向 flex 填满，禁止 `max-height: calc(100vh - …)` 截断） |
| 提示库 | `page-ops` + `pg-shell`（收件箱 + 已保存引导语；点开引导语弹窗左问话右 Cursor 回复） |
| 设 | `page-ops`（常用 / 高级折叠）+ `page-body--grid`；组内 Tab：系统 / 规范 / 验收 |
| 时间线 / 规范正文 | `page-ops` + `page-body` → 卡片 + 分页 |

### 组导航（`daily` / `starter` 档）

- 侧栏 **记录** → 页内 `.page-tabs`：`时间线 | 总结 | 应用`
- 侧栏 **项目** → `概览 | 关系`（`daily` 档隐藏关系）
- 侧栏 **设** → `系统 | 规范 | 验收`
- 顶栏 **本周主攻** 下拉全局生效

### 体验档位

- `full`：13 项侧栏（向后兼容，config 无 `ui_tier` 时默认）
- `daily`：6 项 + 更多；landing 默认 **今日**
- 配置：`GET/POST /api/ui-tier` · `~/.qr/config.json` → `ui_tier` / `ui_onboarding_done` / `ui_landing_view` / `ui_profile`

### 侧栏档位条（AI 开关上方）

| 档位 | 显示 |
|------|------|
| `full` | 蓝色虚线按钮 **切换到日常界面** |
| `daily` / `starter` | 绿色条 **当前：日常档** · **设 → 验收** · **完整档** |

完整档运维页顶部另有 **界面体验** 卡片（入门 / 日常 / 完整 + 设计者验收）。

## 各标签布局（完整档对照）

| 标签 | 结构 |
|------|------|
| 问答 | `split-ops` + `split-shell`（历史 \| 对话） |
| 总结 | `split-ops` + `split-shell`（列表 \| 正文 + 规范对照） |
| 项目 / 洞察 | `page-ops` + `page-body--grid` → `insight-grid` |
| 关系 | `page-ops` + `rel-shell`（左编辑 \| 右图谱 + 详情/组合 dock，纵向 flex 填满，禁止 `max-height: calc(100vh - …)` 截断） |
| 引导语 | `page-ops` + `pg-shell`（收件箱 + 已保存引导语；点开引导语弹窗左问话右 Cursor 回复） |
| 时间线 / 规范 | `page-ops` + `page-body` → 卡片 + 分页 |
| 检索 / 应用 / 沿革 | `page-ops` + `page-body--scroll` |

## CSS 变量

```css
--split-gap: 16px;   /* 区域间距 */
--split-pad: 20px;   /* 内容区外边距 */
--page-chrome: 252px; /* 顶栏 + 操作区估算，用于 max-height */
```

## 禁止事项

- 不要在单页恢复 `max-width: 1060px` 居中（除非用户明确要求窄阅读模式）。
- 不要把「浏览/筛选」与「主内容」拆到视口上下远端而无 `page-ops` 分组。
- 新页禁止空白占位：列表页需 empty 态，详情页需 empty 态。

## 修改后

```bash
pip install -e ~/QR/dev/qr && qr web --restart
```

浏览器 **Cmd+Shift+R** 强刷。
