"""Current-turn selection in the installed gateway's loopback model fixture."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from packaged_cli_e2e import MockHandler


class FixtureTurnSelectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.handler = MockHandler.__new__(MockHandler)
        self.handler.server = SimpleNamespace(
            markers={'deny': Path('/tmp/deny'), 'allow': Path('/tmp/allow')},
            record=lambda **row: self.calls.append(row))

    def test_codex_uses_latest_user_not_seed_history(self):
        events = self.handler._codex_events({'model': 'fixture', 'input': [
            {'role': 'user', 'content': 'CODEX_SEED'},
            {'role': 'assistant', 'content': 'CODEX_SEED_REPLY'},
            {'role': 'user', 'content': 'CODEX_HTTP_SEND'},
        ]})
        self.assertIn('CODEX_HTTP_REPLY', str(events))
        self.assertNotIn('CODEX_SEED_REPLY', str(events))

    def test_claude_uses_latest_user_before_trailing_system(self):
        events = self.handler._claude_events({'model': 'fixture', 'messages': [
            {'role': 'user', 'content': 'CLAUDE_SEED'},
            {'role': 'assistant', 'content': 'CLAUDE_SEED_REPLY'},
            {'role': 'user', 'content': 'CLAUDE_HTTP_SEND'},
            {'role': 'system', 'content': 'Native context suffix'},
        ]})
        self.assertIn('CLAUDE_HTTP_REPLY', str(events))
        self.assertNotIn('CLAUDE_SEED_REPLY', str(events))

    def test_claude_approval_matches_latest_tool_result_id(self):
        events = self.handler._claude_events({'model': 'fixture', 'messages': [
            {'role': 'user', 'content': 'CLAUDE_SEED'},
            {'role': 'user', 'content': 'REQUEST_DENY_WRITE'},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'toolu_deny_fixture'}]},
            {'role': 'user', 'content': [{'type': 'tool_result',
                'tool_use_id': 'toolu_deny_fixture', 'is_error': True}]},
            {'role': 'system', 'content': 'Native context suffix'},
        ]})
        self.assertIn('CLAUDE_DENIED_REPLY', str(events))
        self.assertNotIn('toolu_allow_fixture', str(self.calls))
        self.assertEqual(self.calls[-1]['toolId'], 'toolu_deny_fixture')
        self.assertTrue(self.calls[-1]['denied'])

    def test_claude_rejects_unrelated_tool_result_id(self):
        with self.assertRaisesRegex(ValueError, 'Unknown tool result'):
            self.handler._claude_events({'model': 'fixture', 'messages': [
                {'role': 'user', 'content': 'CLAUDE_SEED'},
                {'role': 'user', 'content': [{'type': 'tool_result',
                    'tool_use_id': 'toolu_unrelated', 'is_error': True}]},
            ]})


if __name__ == '__main__':
    unittest.main()
