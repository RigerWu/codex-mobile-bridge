# Agent Note: Claude signed connection readiness

Status: implemented

## Problem

During explicit Claude Desktop initialization, a signed renderer receipt could make `status()` report connected while the native DevTools cleanup still held `reconnecting`. An authenticated list request in that interval failed with “Claude 正在重新连接”.

## Decision

After the current connector's signed receipt is accepted, and after the existing read probe succeeds when an earlier probe failed, mark the mailbox ready for normal calls before closing DevTools. Keep native cleanup running and report `consoleCleanupPending` until its result is known. A cleanup failure retains the existing manual-close message. The existing generation, owner, cancellation, whole-source injection, and unconfirmed-write checks stay in place.

## Consequences

The first list request can use the established file bridge while native cleanup is pending. A source fixture holds cleanup open on simulated Windows and macOS paths and verifies a signed list response in that interval. This fixture does not measure live startup time or replace platform acceptance and packaged validation.
