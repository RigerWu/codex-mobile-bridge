import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.clients.claude.session_registry import assert_session_closed
from bridge.clients.errors import BridgeUnavailable


class ClaudeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name)
        self.registry = self.config/'sessions'
        self.registry.mkdir()
        self.session_id = '11111111-1111-4111-8111-111111111111'
        self.start = 'Wed Oct 14 02:32:48 2026'

    def entry(self, session_id=None, **changes):
        value = {'sessionId': session_id or self.session_id, 'pid': 1234,
                 'pidDomain': 'darwin', 'procStart': self.start, 'cwd': '/fixture/project'}
        value.update(changes)
        (self.registry/'1234.json').write_text(json.dumps(value))

    def test_only_matching_live_session_blocks_resume(self):
        self.entry(session_id='22222222-2222-4222-8222-222222222222')
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill') as kill:
            assert_session_closed(self.config, self.session_id)
        kill.assert_not_called()
        self.entry()
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill'), \
             patch('bridge.clients.claude.session_registry.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=' '+self.start+'\n', stderr='')):
            with self.assertRaisesRegex(BridgeUnavailable, '仍在其他终端运行'):
                assert_session_closed(self.config, self.session_id)

    def test_closed_or_reused_pid_allows_resume(self):
        self.entry()
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill', side_effect=ProcessLookupError):
            assert_session_closed(self.config, self.session_id)
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill'), \
             patch('bridge.clients.claude.session_registry.subprocess.run',
                   return_value=SimpleNamespace(returncode=0,
                                                stdout='Wed Oct 14 01:00:00 2026\n', stderr='')):
            assert_session_closed(self.config, self.session_id)

    def test_unknown_registry_or_process_is_not_assumed_closed(self):
        self.entry()
        with patch('bridge.clients.claude.session_registry.sys.platform', 'linux'):
            with self.assertRaises(BridgeUnavailable):
                assert_session_closed(self.config, self.session_id)
        with patch('bridge.clients.claude.session_registry.sys.platform', 'darwin'), \
             patch('bridge.clients.claude.session_registry.os.kill', side_effect=PermissionError):
            with self.assertRaises(BridgeUnavailable):
                assert_session_closed(self.config, self.session_id)
        (self.registry/'1234.json').write_text('{bad json')
        with self.assertRaises(BridgeUnavailable):
            assert_session_closed(self.config, self.session_id)
        (self.registry/'1234.json').unlink()
        assert_session_closed(self.config, self.session_id)
        self.registry.rmdir()
        with self.assertRaises(BridgeUnavailable):
            assert_session_closed(self.config, self.session_id)
