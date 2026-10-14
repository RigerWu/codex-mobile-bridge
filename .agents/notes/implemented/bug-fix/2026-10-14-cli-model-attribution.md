# Agent Note: Attribute CLI replies to their recorded turn model

Status: implemented

## Problem

Native CLI `thread/read` exposes the current thread model but may omit per-turn model attribution. Historical replies could therefore show only `CODEX` after an explicit native connection. Assigning the current model to all turns would mislabel conversations where the model changed.

## Decision

The live-session view joins native turns by ID to its already loaded saved rollout `turn_context`. The saved context overrides only when it has an actual model. Native per-turn model fields, when provided, are retained. At the start of a new native turn, capture the then-current model for that turn; a subsequent settings change does not relabel it. Turns with no model evidence retain no model label. No added SSH or rollout read occurs in a native callback.

## Verification

A regression fixture with two recorded models and a third live turn passes. The current thread model is different from both historical models in the fixture, and changing it after the live turn starts does not relabel that turn.
