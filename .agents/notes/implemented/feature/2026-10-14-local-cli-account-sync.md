# Agent Note: Optional local CLI account synchronization

Status: implemented

## Problem

Codex Desktop and the local Codex CLI share one configuration home. A Desktop switch changes the CLI files even when the user does not select CLI synchronization, while an already running CLI daemon may keep older settings. Claude Desktop official cookies are not Claude Code CLI credentials; API profiles can be copied only after verifying the saved Desktop profile.

## Decision

Desktop switch requests can explicitly select local CLI synchronization. Codex reports the shared configuration and, when selected, verifies the CLI account/provider/config separately after Desktop verification. A CLI failure leaves the verified Desktop switch in place and is reported as a partial failure. When CLI synchronization is not selected, the UI says the shared configuration changed but the CLI runtime is unverified.

A CLI-only Codex switch with Desktop stopped uses the existing account preparation and native credential snapshots, applies them to the shared home, verifies them through a fresh native account/config RPC, and restores the snapshot on failure. A running Desktop uses its established stop/apply/restart transaction. Joined CLI sessions, pending tasks and a live CLI control daemon block switching. The helper never terminates an external daemon or touches SSH configurations.

Claude Desktop synchronization accepts only the currently effective, fingerprint-matched saved API profile. It creates a managed Claude Code profile and selects it after Desktop succeeds. Native OAuth/cookie profiles remain separate and are selected read-only. Claude Code API settings use its documented base URL, authentication and model environment variables. No model request is used for verification.

## Verification

Targeted Python fixtures cover Codex rollback, live-daemon blocking, managed Claude API settings, Desktop API import, and separate Desktop/CLI outcomes. UI fixtures cover opt-in checkbox submission. An isolated native Codex acceptance fixture with a loopback model server confirmed that a fresh CLI turn uses its selected model and endpoint, with external connection attempts blocked. That fixture did not exercise an account switch. Real account changes, paid model calls, existing daemon reload, and SSH configuration changes were not run.
