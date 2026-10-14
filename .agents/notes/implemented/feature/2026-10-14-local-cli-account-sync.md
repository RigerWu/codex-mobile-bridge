# Agent Note: Optional local CLI account synchronization

Status: implemented

## Problem

Codex Desktop and the local Codex CLI share one configuration home. A Desktop switch changes the CLI files even when the user does not select CLI synchronization, while an already running CLI daemon may keep older settings. Claude Desktop official cookies are not Claude Code CLI credentials; API profiles can be copied only after verifying the saved Desktop profile.

## Decision

Desktop switch requests can explicitly select local CLI synchronization. Codex reports the shared configuration and, when selected, verifies the CLI account/provider/config separately after Desktop verification. A CLI failure leaves the verified Desktop switch in place and is reported as a partial failure. When CLI synchronization is not selected, the UI says the shared configuration changed but the CLI runtime is unverified.

A CLI-only Codex switch with Desktop stopped uses the existing account preparation and native credential snapshots, applies them to the shared home, verifies them through a fresh native account/config RPC, and restores the snapshot on failure. A running Desktop uses its established stop/apply/restart transaction. Joined CLI sessions, pending tasks and a live CLI control daemon block switching. The helper never terminates an external daemon or touches SSH configurations.

Claude Desktop synchronization accepts only the currently effective, fingerprint-matched saved API profile. It creates a managed Claude Code profile and selects it after Desktop succeeds. Native OAuth/cookie profiles remain separate and are selected read-only. Claude Code API settings use its documented base URL, authentication and model environment variables. No model request is used for verification.

The local CLI access controls are shown inside each provider's account management view in the web, mobile and gateway desktop interfaces. Codex and Claude Code have separate fixed-provider panels. A saved API access can explicitly query its upstream model-list endpoint or test that endpoint using its private saved credential without selecting the access or sending a conversation request. This test confirms model-list endpoint connectivity and authorization only; users can still enter a model ID manually when the endpoint is unavailable.

## Verification

Targeted Python fixtures cover Codex rollback, live-daemon blocking, managed Claude API settings, Desktop API import, and separate Desktop/CLI outcomes. UI fixtures cover opt-in checkbox submission. An isolated native Codex acceptance fixture with a loopback model server confirmed that a fresh CLI turn uses its selected model and endpoint, with external connection attempts blocked. That fixture did not exercise an account switch. Real account changes, paid model calls, existing daemon reload, and SSH configuration changes were not run.

## Cross-platform account guard follow-up

The CLI-only switch asks the Codex CLI session layer whether its native daemon may still be running. It blocks on uncertain status and no longer assumes a Unix socket in the account layer. Native verification checks both the selected API provider URL and the `openai_base_url` route used by existing Codex chats, plus the account type. Claude Code API verification uses the same managed-provider environment as its launch path and checks the configuration directory returned by `auth status --json` before selecting the profile. Imported Claude directories reject a symlink at the supplied path before resolution.

The cross-platform account fixtures cover daemon-status blocking, both effective Codex API routes, Claude's reported configuration directory, and symlinked import rejection. On macOS, the installed Claude Code 2.1.287 returned `authMethod=api_key` and `authMethod=oauth_token` respectively for isolated temporary profiles with placeholder keys; no model request or actual account change was made. The supported environment keys and provider-routing overrides were checked against the [official Claude Code environment variable reference](https://code.claude.com/docs/en/env-vars). Windows and Linux native CLI account switches, their process guards, credential resolution, and rollback still require real-device verification; macOS mock results do not establish those outcomes.
