# Agent Note: PDF 阅读与隔离 HTML 预览及源码切换

Status: implemented

## Problem

工作台的文本预览最多读取 512 KiB，PDF 走普通二进制提示，HTML 只能看源码。用户需要在浏览器和手机工作台阅读 PDF，也需要运行自包含 HTML 中的内联交互，同时保留原文源码视图。PDF 不应一次性塞入文本预览响应；HTML 文件中的脚本不能获得网关页面权限。

## Decision

工作台按 `.pdf` 后缀先调用既有会话的 `workspace/download?info=1`，用服务端核验的文件大小和 `clickDownloadMiB` 预检。在线阅读另限 20 MiB，不改变 512 KiB 文本预览门槛；合格文件交给 `BridgePdfPreview.create({url, info})`，`load()` 使用同一带鉴权的下载 URL 与既有 Range 响应，并附 `preview=1`。网关对每次请求执行 `min(20 MiB, clickDownloadMiB)` 的实际文件限额，拒绝与 `explicit/archive` 混用；防止 `info` 后文件变大绕过预算，普通下载行为保持不变。PDF.js 固定为 5.6.205 的本地 legacy module 与同版本 worker、CMap、标准字体，Apache-2.0 许可及字体许可随资源分发。只渲染当前页，提供页码、翻页、50%–200% 缩放和 PDF 文本层；纯扫描页仍显示画布。画布限制为约四百万像素，切页取消旧 render/text layer，关闭标签或重置工作台销毁加载任务。`isEvalSupported:false`、`enableXfa:false`、`useWasm:false`，不装 PDF 脚本、交互表单或 annotation action 层。加密、格式损坏、限额或读取失败均给出消息，文件头部保留下载入口。

HTML 使用 `BridgeHtmlPreview.create({text}) -> {element, dispose()}`。工作台提供“预览／源代码”按钮；源码复用 `BridgeSourceView.create({name, text})`，复制从原始文本取值。切到源码或关闭标签即销毁 HTML iframe。首版只覆盖文本预览门槛内的自包含 HTML，不扩展多文件站点或外部依赖。

HTML 执行环境是鉴权后返回的固定 `/html-preview-shell`，与用户正文分离。壳的 CSP 使用 `sandbox allow-scripts`，允许内联脚本与样式，默认禁资源，并禁止连接、表单和嵌套框架；主页面 `frame-src` 仅指定该壳路径。iframe 只授 `allow-scripts`，不授同源；壳发 ready 消息后，父页面核验 source/origin，单次传入不超过 512 KiB 的 UTF-8 文本。握手脚本使用局部作用域，避免 `document.open/write` 后保留的全局词法变量与用户 HTML 同名。iOS/Android 原生导航仅允许预览子帧进入固定壳，后续导航和高权限对话框被拒。壳不读取磁盘路径或任意 URL。

`web/assets.json`、固定公开 URL 白名单和 `scripts/gateway-resources.json` 同步纳入 PDF.js、HTML 壳、脚本与许可；CMap/字体仅由限定文件名的静态路由公开。SSH 注入的工作区 Python 源模块保持独立，不依赖页面脚本。

## Existing-note audit

已检索本仓库活跃笔记的 PDF、HTML 和预览关键词。本项吸收已批准的同名本地提案；[只读源码视图](2026-10-13-source-code-view.md)持有高亮、行号和复制原文约定，本笔记只记录 HTML 对其复用。鹈鹕测试中不可信 HTML 产物隔离属于相邻场景，不扩展到本项的模型或智测历史。

## Alternatives considered

- 浏览器原生 PDF 嵌入依赖少，但 WebView 控件和文本选择体验不一致，关闭后的加载任务也难以统一控制；改用本地 PDF.js 页面渲染。
- 将 PDF 经现有 `preview` 响应转 base64 实现简单，但突破 512 KiB 门槛、复制整文件并放大内存成本；使用已有受限 Range 下载路径。
- 直接把 HTML 插入工作台 DOM 能原样执行交互，但它会继承网关页面权限；选择独立壳和受限 iframe。
- 只呈现静态 HTML 更容易隔离，但不能满足已确认的内联交互范围；允许壳内脚本并限制网络、导航和同源能力。

## Consequences

普通、带文本层或扫描 PDF 可在当前页阅读；文档超过下载策略或 20 MiB 时需要下载后打开，扫描件不承诺 OCR。PDF.js 的本地资源增加网关体积，但离线和 SSH 页面不依赖 CDN。HTML 内联交互仅在隔离壳内运行，相对文件、外部 CDN、后端 API、表单提交和弹窗不属于首版支持范围。

Node 定向测试覆盖大小预检、快速换页取消、关闭销毁、工作台 info 路由及 HTML 源码切换；资源布局测试检查所有白名单与清单路径。HTML 合成 HTTP/relay 鉴权和 CSP 检查由同批安全测试覆盖。主审在真实 Chromium 验证了中文 PDF 的渲染与文本选择、翻页与缩放、扫描图页以及损坏/超限提示；HTML 的内联交互和源码切换通过，cookie、storage、父页面与宿主桥访问被拒，网络、表单与自导航未到达合成服务器。合成回归覆盖 PDF 在 info 后增长及 SSH/relay 参数路径。Android 构建和签名验证通过；iOS 类型检查通过，原生运行时验收另行记录，不能把浏览器结果等同于实机结果。多个隐藏 PDF 标签保留文档状态，关闭或重置才释放；不承诺全文件标签的统一内存配额。官方依据：[PDF.js API](https://mozilla.github.io/pdf.js/api/draft/module-pdfjsLib.html)、[PDF.js Getting Started](https://mozilla.github.io/pdf.js/getting_started/)；Context7 月配额耗尽后使用官方文档。
