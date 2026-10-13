# Codex CLI 会话接入（v2.0.4 待发布）

聊天列表纳入现有 Codex CLI 会话，并以 `CLI` 标记来源。打开聊天先读已保存历史，不启动 CLI、不向桌面 App 跳转，也不发送模型请求。Codex App 已配置的有效 SSH 连接会参与发现；远端通过用户登录 shell 读取其 `CODEX_HOME`，未设置时使用 `~/.codex`。

## 继续 CLI 中的工作

1. 在原电脑或原 SSH 主机的终端中打开所需会话，保留已有 CLI 运行环境。
2. 在手机或 Web 中打开这条带 `CLI` 标记的聊天，点击“连接 CLI”。
3. 连接成功后可发送、排队、补充任务、停止，以及处理支持的审批和问题卡片。任务继续使用该运行环境的提供商、认证与工作目录。

网关连接已有 Codex app-server daemon 的控制 socket，不替用户启动 daemon。CLI 版本过旧、没有控制 socket，或会话未在该运行环境中加载时，保留历史阅读并显示连接失败原因。请先在原终端恢复该会话，再点击连接；网关不会自动改用桌面会话执行。

本轮原生联调使用 macOS 的 Codex CLI **0.162.1**。SSH 使用已有主机配置和密钥校验，不自动接受新主机密钥或安装远端程序。Windows、Linux 与不同远端配置的原生交互仍需各自验收，不能根据本机结果认定全部兼容。

CLI 中的模型/权限设置、Skill 选择、计划/目标切换、消息编辑/分支、重命名和临时侧边聊天暂不开放；需要时回原 CLI 操作。网关关闭或手机页面退出只断开网关连接，不停止 CLI 任务。连接中断或写入结果不确定时不自动重发；先查看原终端结果，再决定后续操作。

## 会话缺失时

在 Codex 聊天列表点击“诊断”，下载 `codex-session-diagnostics.json`。该导出需要登录，包含本机/远端的读取阶段、会话来源计数和数据库兼容信息，不包含聊天正文、标题、项目路径、SSH 别名或凭据。远端以 `remote-1` 等编号表示。

调查某一条会话时，可在已登录的网关地址后访问 `/api/sessions/diagnostics?thread=<会话 UUID>`。额外结果区分数据库中不存在、来源不支持、已归档、内部子任务以及历史文件缺失/不可读等情况；导出不回显该 UUID。其他用户电脑不可达时，需要对方提供此诊断，才能区分 SSH、数据目录和会话过滤问题。

## 原生协议边界

当前官方 app-server 接口仍可能变化。显式接入前会核验运行环境、会话已加载状态和身份；操作前复核提供商与工作目录。0.162.1 的 `thread/resume` 没有原子化“仅接入已加载会话”选项：若会话恰在核验与接入之间卸载，原 daemon 仍可能从历史重新加载同一会话。被动阅读不会执行此操作，但不能宣称显式连接具有绝对无竞态的 owner 保证。

## English

CLI chats appear with a **CLI** badge. Opening them reads saved history only. To interact, open the same chat in its original terminal and select **Connect CLI** in Bridge. The gateway connects to the existing daemon, keeps its provider/authentication/cwd, and does not start a replacement daemon or fall back to Desktop execution. Supported actions include send, queue, steer, interrupt, approvals and input requests. Configuration, modes, goals, Skills, editing/forking, rename and side chats remain in the original CLI.

The native test environment is macOS with CLI 0.162.1; this does not establish Windows, Linux or every SSH environment as verified. Diagnostics require login and omit chat text, titles, paths, aliases and credentials. Unknown writes are not automatically replayed. The upstream resume API has no atomic loaded-only guard, so an unload during an explicit join can still cause the existing daemon to reload the same chat.
