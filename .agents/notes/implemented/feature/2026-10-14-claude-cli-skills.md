# Agent Note: Installed Claude Code CLI Skills

Status: implemented

## Decision

The resumed CLI retains its original configuration directory, working directory and user/project/local setting sources. The Skill picker reads `SKILL.md` files from that same user profile, project ancestry and the profile's installed-plugin registry. Built-in slash commands have no installed Skill file and are excluded. Explicitly disabled plugins and skills marked `user-invocable: false` cannot be selected.

A selected CLI Skill is sent as its native `/name` invocation in the owned process. CLI messages allow one Skill selection, matching the native invocation form. The server rechecks installation and invocability immediately before sending. Closed history remains passive and offers no Skill selection.

## Verification

Fixture tests cover user, project and plugin Skill discovery, disabled invocation and native slash-command input. `python -B -m unittest tests.test_claude_cli` and `node --test tests/desktop-sessions-ui.test.cjs` pass. These are fixture checks; installed real-profile plugin layouts and a live model invocation were not exercised.
