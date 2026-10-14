# Agent Note: Skip duplicate failed CLI proxy connection

Status: implemented

## Problem

When an explicit native CLI connection failed, the adapter repeated the same proxy attempt under the daemon-start lock before starting the daemon. With a stalled proxy handshake this adds another full failed attempt, up to the existing 5-second connect timeout, before any recovery action. Warm native connects do not enter this path.

## Decision

Remember the daemon-start generation before the first connect. After taking the lock, retry the proxy only if another adapter call has successfully started the daemon meanwhile. Otherwise start it immediately and connect once. This keeps concurrent starts serialized and leaves the existing handshake timeout, identity checks, and no-write-replay rules intact.

## Verification and limit

A controlled unavailable-proxy fixture required two attempts instead of three. Its two 30 ms failure waits fell to one; this proves the removed wait under identical conditions, not a production speedup measurement. Isolated native 0.162.1 cold attach remained about 0.25 seconds before and after because that fixture has no stalled proxy. The reported multi-second delay in a Desktop-owned CLI-origin chat may lie in Desktop IPC owner discovery and was not reproduced by the native fixture.
