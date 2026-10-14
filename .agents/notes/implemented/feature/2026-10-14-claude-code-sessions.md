# Agent Note: Continue closed local Claude Code sessions

Status: implemented

## Problem

Claude Desktop discovery does not expose terminal Claude Code chats. Reusing Desktop initialization for those chats changes their runtime and does not preserve the CLI profile, project configuration or permission mode.

## Decision

Read saved chats from the explicit CLAUDE_CONFIG_DIR, the selected Cockpit CLI instance, or the default Claude profile. A gateway ID binds the native UUID to its profile and settings fingerprint. Passive list/detail never launches a session. Existing native settings sources remain user, project and local; credentials stay in the native profile/environment.

An explicit resume validates the saved cwd and known prior permission mode, refreshes authentication evidence, and checks the native sessions registry immediately before spawning the same native UUID. The initialize control request starts no model turn. Only the resulting owned process receives send, model, permission, approval and interrupt controls. Its first system/init frame must match the UUID and cwd; mismatch terminates that owned process. Native initialize alone has no UUID field, so it is not claimed as independent identity evidence.

Writes use the existing UUID receipt ledger. Known pre-spawn rejections return blocked and allow a later explicit request; unknown outcomes are never automatically replayed. Approval IDs are claimed before sending, cancellation removes pending requests, and native terminal results feed completion evidence. A second message cannot be sent during a running turn or pending approval. Claude CLI readiness is independent of Desktop connectivity.

## Alternatives considered

The native print-mode CLI accepts concurrent writers for one UUID, so relying on an upstream lock is insufficient. Opening an existing live terminal chat in another process is rejected. Registry evidence can verify a currently running writer, but cannot create an atomic lock against an independently started terminal.

Routing the user's credentials through a new SDK service adds a second configuration owner. Spawning the installed CLI with the original profile/cwd keeps native configuration and authentication behavior. SSH Claude Code is excluded until equivalent source/profile and process checks exist.

## Consequences

Closed local sessions can resume and expose native controls in the existing mobile/Web interface. Unknown original permissions, unavailable original configuration or uncertain writer evidence remain non-writable. On macOS, the registry PID start time is compared in UTC; other platforms do not assume an unverified process match is closed. A terminal opened between the final registry check and spawn remains a narrow upstream concurrency limitation.

An actual synthetic API request verified the selected existing authentication; an isolated capture verified project CLAUDE.md inheritance. Claude Code 2.1.287 with a loopback model service verified model selection, effective permission mode, allow/deny tool effects, interrupt of a running turn, and active-writer rejection followed by closed-session admission. Browser 390px English fixtures verified history-only, Continue, model/permissions, approvals and Stop presentation. These are macOS/native and browser checks, not Windows/Linux or physical-phone execution claims.

Decision search found [Codex CLI access](2026-10-14-codex-cli-access.md), which shares passive discovery and explicit ownership principles but concerns a different runtime. Its constraints remain valid; this note adds the local Claude implementation without superseding it.

## Explicit new CLI sessions

The new-chat picker offers Claude Code CLI beside Desktop Code and Cowork when the selected local or SSH profile is authenticated and a known project cwd exists. CLI creation requires a first message; the native CLI assigns the title. The selected project key binds its host, profile origin and cwd, and the adapter checks those inputs again before starting `claude -p --session-id` with stream-json controls. No bridge-written JSONL or empty placeholder chat is created. The first user frame must be acknowledged, then the native saved session must appear before the operation reports ready. An uncertain write stays unknown under the existing receipt ledger and is never replayed automatically.

An isolated Claude Code 2.1.287 mock API run created one local and one loopback SSH session. Each retained the selected profile's synthetic credential, cwd, model and plan mode, sent its first message once, and produced native saved history. The SSH check validates the transport and protocol on macOS; it is not a paid Linux model run.
