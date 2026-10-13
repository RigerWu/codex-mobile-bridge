# Agent Note: Continue saved CLI chats after terminal exit

Status: implemented

The source-based Desktop exclusion below is partially superseded by [existing-owner routing](../bug-fix/2026-10-14-cli-existing-owner.md): CLI origin is provenance, and an already active Desktop owner is reused without creating a replacement runtime.

## Problem

The loaded-only policy in [CLI access](2026-10-14-codex-cli-access.md) leaves a visible chat unusable after its terminal exits and the native runtime unloads it. The user explicitly requires continuing from the phone without first reopening a computer terminal. The old error was reproduced against an actual saved CLI 0.162.1 thread, without changing its origin metadata.

## Decision

Explicit Continue chat connects to the original native daemon and resumes the same thread ID. If the daemon is stopped, the gateway invokes its already installed selected managed binary with `app-server daemon start`, then reconnects through the official proxy. Native lifecycle locking handles concurrent starts; the gateway serializes same-thread attachment. Local and SSH package selection follows the inspected 0.162.1 lifecycle. SSH proxy can use the managed binary when PATH and the conventional local-bin path have no CLI.

Passive history/list reads never start the daemon or resume threads. Cold resume verifies saved CLI origin, provider and cwd against the target runtime, then rechecks the resume result. No model, provider, authentication, cwd, trust or permission overrides are supplied. The gateway does not install, bootstrap, update or restart a runtime; the native daemon retains its existing configuration, including its own updater preferences. Closing the viewer/gateway only closes the proxy. Unknown writes are not replayed. Configurations without an installed compatible daemon remain readable and explain the prerequisite.

SSH failure dismissal is scoped to the current gateway and exact host fault until recovery, a changed fault, or full page reload. Connection diagnostics lives in settings with readable aggregate status and copyable summary. Native export is capability-gated and permits only the authenticated same-gateway diagnostics endpoint (and optional UUID query); relay applies the same narrow GET rule. Dynamic reports cannot resume partial downloads.

## Alternatives considered

Keeping loaded-only access offers a narrower lifecycle but fails the requested phone workflow and produced the reported error. Launching an independent `app-server --listen` or using Desktop as a fallback could execute the chat but would not participate in the native shared daemon lifecycle. Invoking native bootstrap could satisfy a missing package but silently installs software; the implementation instead requires an existing managed package.

## Existing-note audit

The prior CLI access note is partially superseded and mutually linked; source discovery and unknown-write isolation remain applicable. The PDF/HTML and notification settings notes are unrelated to CLI lifecycle. The file-transfer settings read note governs a separate settings panel and is unchanged. No proposed or rejected note directories existed at inspection.

## Consequences

The real Web acceptance also exposed completion events omitting user items that remain in authoritative thread/read. Completion projection preserves only those previously known user messages, deduplicated by ID; other items still follow the completion payload. A regression reproduced the disappearing bubble before this correction.

Original-terminal and phone continuation share the same saved history and native daemon. Real macOS CLI 0.162.1 tests cover an exited terminal with an unloaded thread, a fully stopped daemon, send/stream/idempotence, and reopening the TUI to read both replies with the same daemon PID. Trust and credentials used for these tests are isolated under project .tmp; daily trust is unchanged. Provider/cwd checks are preflight and postflight, not an atomic upstream identity guarantee. Other platforms and arbitrary SSH hosts require native acceptance; shell fixtures do not establish that real remote environments work. See [verification](../../../../VERIFICATION.md) for build and UI scope.
