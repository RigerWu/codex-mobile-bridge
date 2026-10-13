# Codex CLI 会话接入（v2.0.4 待发布）

聊天列表纳入现有 Codex CLI 会话，并以 `CLI` 标记来源。打开聊天先读已保存历史，并只读查找已有 Codex App 执行连接；不启动 CLI、不向桌面 App 跳转，也不发送模型请求。Codex App 已配置的有效 SSH 连接会参与发现；远端通过用户登录 shell 读取其 `CODEX_HOME`，未设置时使用 `~/.codex`。

## 继续 CLI 中的工作

1. 在手机或 Web 中打开带 `CLI` 标记的聊天，先阅读已保存历史。
2. 如果这条聊天已由 Codex App 接续，网关会核验并复用该执行连接。否则点击“继续会话”，连接原 CLI daemon；原终端可以已经关闭，必要时显式启动该 `CODEX_HOME` 中已安装的原生托管 daemon，再恢复同一条会话。
3. 连接成功后可发送、排队、补充任务、停止，以及处理支持的审批和问题卡片；也可选择模型与推理强度、添加 Skill、切换普通/计划模式。兼容的本机和 SSH 原生 CLI 运行时支持 Goal 的创建、暂停、修改、恢复与关闭。任务继续使用原会话的提供商、认证与工作目录。

只有点击“继续会话”才会启动托管 daemon 或恢复会话；打开聊天、读取历史和列表刷新都不会触发。网关不会安装 CLI、接受新的 SSH 主机密钥、强行夺取其他进程的写入权，或覆盖原会话身份。原生 daemon 未安装、版本不兼容或身份核验失败时，历史仍可阅读，并显示连接失败原因。

macOS 的 Codex CLI **0.162.1** 已分别验证 daemon 正在运行时接入，以及原终端关闭后的冷恢复；两种情况下发送与流式回复均通过。SSH 使用已有主机配置和密钥校验，不自动接受新主机密钥或安装远端程序。Windows、Linux 与不同远端配置的原生交互仍需各自验收，不能根据 macOS 结果认定全部兼容。

由 Codex App 接续的 CLI 聊天复用现有 App 的模型、Skill 和模式操作；由原生 CLI 执行的聊天从同一个 daemon 读取目录并提交设置、Skill 和计划请求。自定义提供商可手动填写模型 ID，不切换到其他提供商的模型目录。兼容的本机和 SSH 原生 CLI 通过同一 daemon 操作 Goal；SSH 上由 Codex App 接管的聊天尚不支持 Goal，网关不会另起执行方。连接成功后目录自动加载，短暂断线保留已选 Skill 与输入草稿。

CLI 权限可从聊天中的权限按钮切换；原生 CLI 按执行主机的运行时与策略展示可用选项，App 接续的聊天通过原 owner 更新。保存后从后续回合生效，界面以原生确认结果为准；聊天按钮将完全访问权限简写为“完全访问”，确认说明保持完整。消息编辑/分支、重命名和临时侧边聊天暂不开放。网关关闭或手机页面退出只断开网关连接，不停止 CLI 任务。连接中断或写入结果不确定时不自动重发；先查看原会话历史与运行状态，再决定后续操作。

## 会话缺失时

从“设置”中的“连接诊断”查看本机和远端的读取阶段、状态与会话来源计数，可复制摘要或导出 `codex-session-diagnostics.json`。旧版手机 App 可查看和复制；文件导出需更新 App。登录后才能读取诊断。摘要和导出不包含聊天正文、标题、项目路径、SSH 别名或凭据；远端以 `remote-1` 等编号表示。顶部 SSH 错误可关闭：同一故障在自动刷新时不再出现，恢复或错误变化后可重新提示；页面重新加载会重置关闭状态。

调查某一条会话时，可在已登录的网关地址后访问 `/api/sessions/diagnostics?thread=<会话 UUID>`。额外结果区分数据库中不存在、来源不支持、已归档、内部子任务以及历史文件缺失/不可读等情况；导出不回显该 UUID。其他用户电脑不可达时，需要对方提供此诊断，才能区分 SSH、数据目录和会话过滤问题。

## 原生协议边界

当前官方 app-server 接口仍可能变化。显式接入前会核验运行环境与会话身份；冷恢复还会比较已保存的来源、提供商和工作目录。0.162.1 的 `thread/resume` 没有原子化“仅恢复经核验会话”选项：若会话恰在核验与恢复之间变化，不能宣称具有绝对无竞态的 owner 保证。被动阅读不会执行此操作。

## English

CLI chats appear with a **CLI** badge. Opening one reads saved history and passively checks for an existing Codex App owner. If App already owns the chat, Bridge verifies and follows that owner. Otherwise, select **Continue chat** to connect to the native CLI daemon or explicitly start its already installed, managed daemon and resume the same chat, even if the terminal has closed. It does not install CLI, bypass SSH host-key checks, change the chat's identity, or seize another runtime's writer lock. Supported actions include send, queue, steer, interrupt, approvals and input requests, plus model/effort selection, Skills and default/plan modes. Compatible joined native CLI runtimes, including SSH hosts, support creating, pausing, editing, resuming and clearing Goals through the same daemon. SSH chats owned by the desktop App do not expose Goal controls.

Native CLI controls use the same daemon for catalogs, settings, Skills, modes and Goals. Custom providers accept manually entered model IDs without switching providers. Catalogs load after connection; transient disconnects retain draft text and selected Skills. Permission controls follow the execution runtime and its policy on local and SSH CLI chats; App-owned chats use their existing owner. Changes apply to subsequent turns and require effective runtime confirmation. Editing/forking, rename and side chats remain unavailable for CLI-origin chats.

Warm join and cold resume, including send and streaming replies, passed on macOS with CLI 0.162.1; this does not establish Windows, Linux or every SSH environment as verified. Open **Connection diagnostics** in Settings to read local and remote status and counts, copy the summary, or export a file. Older native apps can read and copy but need an update to export. Diagnostics require login and omit chat text, titles, paths, aliases and credentials. Dismissing an SSH error suppresses the same fault across automatic refreshes until recovery or a changed fault; reloading the page resets that dismissal. Unknown writes are not automatically replayed. The upstream resume API has no atomic identity guard across preflight and resume.
