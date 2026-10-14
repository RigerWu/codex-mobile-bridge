# Agent Note: Unified Claude chat list

Status: implemented

## Problem

Separate Code, Cowork and CLI groups interrupt chronological scanning and add a filter absent from the Codex list. Users want to identify each chat's type without changing views.

## Decision

Show all Claude chat types in the same recent list. Put a compact type label beside the title, above the existing date. Keep search, archive visibility, project grouping, activity indicators and selection behavior. The create dialog owns its own chat-type selection.

## Alternatives considered

Keeping the type selector or collapsible type groups preserves the old navigation but does not meet the requested single-list behavior. Type remains row metadata instead. Project grouping remains an explicit display choice and groups by project across types.

## Consequences

Saved type filters no longer hide chats. Existing project collapse preferences still apply to project groups. DOM tests cover chronological mixing, row metadata placement, archive/search, project grouping and provider switching; responsive appearance is verified during integration.
