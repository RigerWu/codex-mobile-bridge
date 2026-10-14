# Agent Note: Keep a joined CLI writer after a transient read timeout

Status: implemented

## Problem

The native Goal completion event can arrive while a background `thread/read` refresh is pending. The refresh worker treated every app-server read failure as proof that the joined writer was gone. A timed-out read therefore cleared the CLI join and made the phone history-only even when the proxy and daemon connection remained live.

## Decision

Preserve the joined session on `AppServerUnavailable` from a background refresh. The proxy's actual disconnect callback still clears all joins; an explicit `notLoaded` or identity mismatch still invalidates the affected thread. A failed read does not replay any write. Later native events or a user refresh can update the snapshot.

## Evidence and limits

An isolated native Codex 0.162.1 loopback Goal turn and explicit `complete` transition stayed connected and editable. Completion alone did not reproduce the user's disconnect. A deterministic adapter test reproduced the reported history-only state when a single `thread/read` timed out after a completed Goal event; the change keeps the live writer and its completed Goal state. The user's production connection loss may also involve a real proxy disconnect or a Desktop writer takeover, which this change does not override.
