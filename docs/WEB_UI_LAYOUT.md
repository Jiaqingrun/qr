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
