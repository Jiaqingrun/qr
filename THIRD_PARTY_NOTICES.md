# 第三方开源组件声明

本文件列出 **QR 本地知识库**（`pip install` / `pyproject.toml` 声明依赖）所使用的主要第三方组件及其许可协议。

> **范围说明**：仅包含本项目的 Python 依赖树，不包含 Ollama 与大模型权重（见 [README · 许可与外部组件](README.md#许可与外部组件)）。

生成参考：`pipdeptree -p qr` + `pip-licenses`（2026-06）。

---

## 直接依赖


| 组件                      | 版本（参考）  | 许可                             | 项目地址                                                                                 |
| ----------------------- | ------- | ------------------------------ | ------------------------------------------------------------------------------------ |
| typer                   | 0.26.x  | MIT                            | [https://github.com/fastapi/typer](https://github.com/fastapi/typer)                 |
| rich                    | 15.x    | MIT                            | [https://github.com/Textualize/rich](https://github.com/Textualize/rich)             |
| httpx                   | 0.28.x  | BSD-3-Clause                   | [https://github.com/encode/httpx](https://github.com/encode/httpx)                   |
| numpy                   | 1.26.x  | BSD-3-Clause                   | [https://numpy.org](https://numpy.org)                                               |
| sqlite-vec              | 0.1.x   | **MIT 或 Apache-2.0（双许可，任选其一）** | [https://github.com/asg017/sqlite-vec](https://github.com/asg017/sqlite-vec)         |
| fastapi                 | 0.136.x | MIT                            | [https://github.com/fastapi/fastapi](https://github.com/fastapi/fastapi)             |
| uvicorn                 | 0.48.x  | BSD-3-Clause                   | [https://github.com/encode/uvicorn](https://github.com/encode/uvicorn)               |
| pyobjc-framework-Cocoa  | 12.x    | MIT                            | [https://github.com/ronaldoussoren/pyobjc](https://github.com/ronaldoussoren/pyobjc) |
| pyobjc-framework-Quartz | 12.x    | MIT                            | [https://github.com/ronaldoussoren/pyobjc](https://github.com/ronaldoussoren/pyobjc) |
| pywebview               | 6.x     | BSD-3-Clause                   | [https://pywebview.flowrl.com/](https://pywebview.flowrl.com/)                       |


`pyobjc` 与 `pywebview` 仅随 **macOS** 可选依赖安装。

---

## 主要传递依赖


| 组件                                        | 许可                                | 项目地址                                                                                     |
| ----------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------- |
| starlette                                 | BSD-3-Clause                      | [https://github.com/encode/starlette](https://github.com/encode/starlette)               |
| pydantic / pydantic_core                  | MIT                               | [https://github.com/pydantic/pydantic](https://github.com/pydantic/pydantic)             |
| anyio                                     | MIT                               | [https://github.com/agronholm/anyio](https://github.com/agronholm/anyio)                 |
| click                                     | BSD-3-Clause                      | [https://github.com/pallets/click](https://github.com/pallets/click)                     |
| h11 / httptools / websockets / watchfiles | MIT / BSD                         | 见 PyPI                                                                                   |
| uvloop                                    | Apache-2.0 **或** MIT（双许可）         | [https://github.com/MagicStack/uvloop](https://github.com/MagicStack/uvloop)             |
| PyYAML                                    | MIT                               | [https://pyyaml.org/](https://pyyaml.org/)                                               |
| python-dotenv                             | BSD-3-Clause                      | [https://github.com/theskumar/python-dotenv](https://github.com/theskumar/python-dotenv) |
| certifi                                   | **MPL-2.0**（仅 certifi 包内 CA 证书文件） | [https://github.com/certifi/python-certifi](https://github.com/certifi/python-certifi)   |
| bottle / proxy_tools                      | MIT                               | pywebview 传递依赖                                                                           |
| pyobjc-core / pyobjc-framework-WebKit 等   | MIT                               | [https://github.com/ronaldoussoren/pyobjc](https://github.com/ronaldoussoren/pyobjc)     |
| markdown-it-py / Pygments / shellingham 等 | MIT / BSD / ISC                   | 见 PyPI                                                                                   |


完整依赖树：`pipdeptree -p qr`

---

## 特别说明

### sqlite-vec

- Python 包内包含原生扩展 `vec0.dylib`（按平台分发）。
- 上游仓库 [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) 采用 **MIT + Apache-2.0** 双许可；再分发时请保留对应许可全文（见上游 `LICENSE-MIT` / `LICENSE-APACHE`）。
- PyPI 元数据中的 `License` 字段可能不完整，**以上游 GitHub 为准**。

### PyObjC / libffi

- PyObjC 为 MIT，但文档注明捆绑 **libffi**（另有独立许可）。若打包分发 macOS 应用，请查阅 [pyobjc 许可说明](https://github.com/ronaldoussoren/pyobjc)。

### pywebview（macOS）

- 通过系统 **WebKit** 渲染界面，不捆绑 Chromium。
- 分发 `.app` 时另受 **Apple 平台与开发者协议**约束。

### 前端静态资源

- `qr/static/` 下 HTML / CSS / JavaScript 为本项目原创，**无第三方 CDN 库**。

---

## Ollama 与模型（非本仓库依赖）


| 组件                           | 说明                                               |
| ---------------------------- | ------------------------------------------------ |
| [Ollama](https://ollama.com) | 独立安装，MIT；**不随本仓库分发**                             |
| 各 LLM / Embedding 模型         | 用户自行 `ollama pull`；许可见各模型官方页面（如 Qwen、DeepSeek 等） |
| 百度千帆 API（可选）                 | 商业 API 服务条款，非开源组件                                |


---

## 更新本文件

依赖变更后重新生成：

```bash
pip install pipdeptree pip-licenses
pipdeptree -p qr
pip-licenses --packages $(pipdeptree -p qr -e dot | cut -d= -f1 | sort -u | tr '\n' ' ')
```

---

*QR 本地知识库 · 第三方声明 · 2026-06*