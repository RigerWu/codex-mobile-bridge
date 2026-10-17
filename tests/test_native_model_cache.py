import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bridge.app.service import Bridge
from bridge.clients.codex.catalog import ModelStore


class NativeModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.bridge = object.__new__(Bridge)
        self.bridge.native_model_store = ModelStore(self.temp.name)
        self.bridge.native_model_lock = threading.Lock()
        self.bridge.native_model_reads = {}
        self.bridge.native_model_timers = {}
        self.bridge.native_model_refreshing = set()
        self.bridge.closed = threading.Event()
        self.bridge.codex_home = Path(self.temp.name)
        self.bridge.host = 'local'
        self.bridge.cli_sessions = SimpleNamespace(alias=None, get_kind=Mock(return_value={
            'models': [{'id': 'gpt-fixture', 'efforts': ['high']}], 'modelSource': 'codex'}))
        self.bridge._native_model_identity = Mock(return_value='identity-a')
        self.addCleanup(lambda: [timer.cancel() for timer in self.bridge.native_model_timers.values()])

    def test_native_cli_model_list_is_reused_and_explicit_refresh_forces_read(self):
        first = self.bridge._native_models('thread', self.temp.name, 'openai')
        self.assertEqual(self.bridge._native_models('thread', self.temp.name, 'openai'), first)
        self.bridge.cli_sessions.get_kind.assert_called_once()
        self.bridge._native_models('thread', self.temp.name, 'openai', refresh=True)
        self.assertEqual(self.bridge.cli_sessions.get_kind.call_count, 2)

    def test_shared_native_catalog_never_reuses_another_threads_selection(self):
        self.bridge.cli_sessions.get_kind.return_value = {
            'models': [{'id': 'gpt-fixture'}], 'modelSource': 'codex',
            'currentModel': 'thread-a-model', 'currentEffort': 'high'}
        first = self.bridge._native_models('thread-a', self.temp.name, 'openai')
        self.assertEqual(first['currentModel'], 'thread-a-model')
        saved = self.bridge.native_model_store.read('identity-a')[1]
        self.assertNotIn('currentModel', saved)
        self.assertNotIn('currentEffort', saved)
        second = self.bridge._native_models('thread-b', self.temp.name, 'openai')
        self.assertNotIn('currentModel', second)
        self.assertNotIn('currentEffort', second)
        sessions = {
            thread: SimpleNamespace(origin='cli', runtime='cli', connected=True,
                condition=threading.Condition(), state={
                    'cwd': self.temp.name, 'latestModel': thread + '-model',
                    'latestReasoningEffort': effort}, saved_cwd=None,
                view=lambda: {'provider': 'openai'})
            for thread, effort in (('thread-a', 'high'), ('thread-b', 'low'))}
        self.bridge.session = Mock(side_effect=lambda thread, **kwargs: sessions[thread])
        current = self.bridge.catalog('thread-b', kind='models')
        self.assertEqual((current['currentModel'], current['currentEffort']),
                         ('thread-b-model', 'low'))
        self.bridge.cli_sessions.get_kind.assert_called_once()
        self.bridge.native_model_store.write('identity-a', {**saved,
            'currentModel': 'old-schema-model', 'currentEffort': 'max'})
        old = self.bridge._native_models('thread-b', self.temp.name, 'openai')
        self.assertNotIn('currentModel', old)
        self.assertNotIn('currentEffort', old)

    def test_account_identity_change_rejects_inflight_model_result(self):
        def changed(*args, **kwargs):
            self.bridge._native_model_identity.return_value = 'identity-b'
            return {'models': [{'id': 'old-account'}]}
        self.bridge.cli_sessions.get_kind.side_effect = changed
        with self.assertRaisesRegex(ValueError, '账号身份已变化'):
            self.bridge._native_models('thread', self.temp.name, 'openai')
        self.assertIsNone(self.bridge.native_model_store.read('identity-a'))

    def test_native_identity_uses_account_and_config_without_model_list(self):
        bridge = self.bridge
        bridge.cli_sessions.client = SimpleNamespace(request=Mock(side_effect=lambda method, params, **kwargs:
            {'config': {'model_provider': 'openai'}} if method == 'config/read' else
            {'account': {'type': 'chatgpt', 'email': 'one@example.test'}}))
        first = Bridge._native_model_identity(bridge, self.temp.name, 'openai')
        self.assertEqual(bridge.cli_sessions.client.request.call_count, 2)
        bridge.cli_sessions.client.request.side_effect = lambda method, params, **kwargs: (
            {'config': {'model_provider': 'openai'}} if method == 'config/read' else
            {'account': {'type': 'chatgpt', 'email': 'two@example.test'}})
        self.assertNotEqual(first, Bridge._native_model_identity(bridge, self.temp.name, 'openai'))
