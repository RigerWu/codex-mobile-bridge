# Agent Note: Continue saved CLI chats after terminal exit

Status: implemented

The model, Skill, plan and local Goal restrictions are superseded by [CLI session controls](2026-10-14-cli-session-controls.md); permission restrictions are superseded by [CLI permission switching](2026-10-14-cli-permission-switching.md). Editing/forking, rename and side-chat restrictions remain.

The source-based Desktop exclusion below is partially superseded by [existing-owner routing](../bug-fix/2026-10-14-cli-existing-owner.md): CLI origin is provenance, and an already active Desktop owner is reused without creating a replacement runtime.

## Problem

The loaded-only policy in [CLI access](2026-10-14-codex-cli-access.md) leaves a visible chat unusable after its terminal exits and the native runtime unloads it. The user explicitly requires continuing from the phone without first reopening a computer terminal. The old error was reproduced against an actual saved CLI 0.162.1 thread, without changing its origin metadata.

## Decision

Explicit Continue chat connects to the original native daemon and resumes the same thread ID. If the daemon is stopped, the gateway invokes its already installed selected managed binary with `app-server daemon start`, then reconnects through the official proxy. Native lifecycle locking handles concurrent starts; the gateway serializes same-thread attachment. Local and SSH package selection follows the inspected 0.162.1 lifecycle. SSH proxy can use the managed binary when PATH and the conventional local-bin path have no CLI.

The local proxy prefers the selected managed package over a different CLI on PATH, so the proxy and daemon use the same installation when that package exists. The account-change guard asks the native `app-server daemon version` command for read-only status under the selected `CODEX_HOME`; missing socket and PID evidence means stopped, while any probe failure or unrecognized response stays unknown and blocks the change. Windows runs the proxy and lifecycle probe without opening a console window. The native control endpoint remains the upstream AF_UNIX socket on Linux, macOS and Windows; no named-pipe protocol is introduced.

Passive history/list reads never start the daemon or resume threads. Cold resume verifies saved CLI origin, provider and cwd against the target runtime, then rechecks the resume result. No model, provider, authentication, cwd, trust or permission overrides are supplied. The gateway does not install, bootstrap, update or restart a runtime; the native daemon retains its existing configuration, including its own updater preferences. Closing the viewer/gateway only closes the proxy. Unknown writes are not replayed. Configurations without an installed compatible daemon remain readable and explain the prerequisite.

SSH failure dismissal is scoped to the current gateway and exact host fault until recovery, a changed fault, or full page reload. Connection diagnostics lives in settings with readable aggregate status and copyable summary. Native export is capability-gated and permits only the authenticated same-gateway diagnostics endpoint (and optional UUID query); relay applies the same narrow GET rule. Dynamic reports cannot resume partial downloads.

## Alternatives considered

Keeping loaded-only access offers a narrower lifecycle but fails the requested phone workflow and produced the reported error. Launching an independent `app-server --listen` or using Desktop as a fallback could execute the chat but would not participate in the native shared daemon lifecycle. Invoking native bootstrap could satisfy a missing package but silently installs software; the implementation instead requires an existing managed package.

## Existing-note audit

The prior CLI access note is partially superseded and mutually linked; source discovery and unknown-write isolation remain applicable. The PDF/HTML and notification settings notes are unrelated to CLI lifecycle. The file-transfer settings read note governs a separate settings panel and is unchanged. No proposed or rejected note directories existed at inspection.

## Consequences

The real Web acceptance also exposed completion events omitting user items that remain in authoritative thread/read. Completion projection preserves only those previously known user messages, deduplicated by ID; other items still follow the completion payload. A regression reproduced the disappearing bubble before this correction.

Original-terminal and phone continuation share the same saved history and native daemon. Real macOS CLI 0.162.1 tests cover an exited terminal with an unloaded thread, a fully stopped daemon, send/stream/idempotence, and reopening the TUI to read both replies with the same daemon PID. Trust and credentials used for these tests are isolated under project .tmp; daily trust is unchanged. Provider/cwd checks are preflight and postflight, not an atomic upstream identity guarantee. The [upstream daemon contract](https://github.com/openai/codex/blob/main/codex-rs/app-server-daemon/README.md) documents Linux, macOS and Windows support, JSON lifecycle output, and a Windows canonical AF_UNIX address limit of 108 bytes. Local adapter tests cover the status guard and selected-package preference; Windows execution and arbitrary remote hosts still require native acceptance. See [verification](../../../../VERIFICATION.md) for build and UI scope.

Linux CLI 0.160.0 was exercised on an SSH host with an isolated `CODEX_HOME`, a symlink to that host's already installed executable, updater disabled, synthetic credentials and a loopback Responses fixture. The local `CliSessions` SSH path passively left a saved chat alone, explicitly joined it after its original native client closed, changed auto-review permissions with an effective reviewer update, completed the next turn, created a CLI chat, set and cleared a Goal, then restarted the isolated daemon and cold-resumed the same thread. The actual `RemoteStore` source reader found the saved CLI origin/provider/cwd in that isolated home. Across fixture runs, all 10 synthetic model POSTs reached the loopback endpoint; 28 unrelated external CONNECT attempts were denied. The daemon and mock listener were stopped after validation. The fixture and result are in [Linux native acceptance](../../../../tests/acceptance/codex_cli_linux_native.py) and its [loopback server](../../../../tests/acceptance/codex_cli_linux_mock_server.py). The test injects `CODEX_HOME` into SSH commands because changing the user's login profile was outside scope; this proves the transport and lifecycle against one host, not arbitrary login-shell configurations or a real terminal UI. CLI 0.160.0 persisted bridge-created threads with `codex-tui` originator and `vscode` source, which the existing CLI-origin classifier accepts.

On the tested native CLI, `daemon version` returns JSON with `status=running` when live and a nonzero error when stopped. A remaining socket or PID file with a failed probe is unknown and blocks account switching; only absence of both is treated as stopped. Recovery requires checking the selected daemon's state and removing stale artifacts only after confirming no matching process. Windows native continuation remains unverified.
