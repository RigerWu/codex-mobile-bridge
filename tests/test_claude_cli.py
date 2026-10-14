import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.clients.claude import model
from bridge.clients.claude.cli_session import ClaudeCli, ResumePreflightBlocked, _Owned
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.manager import DesktopSessions
from bridge.clients.uploads import DesktopUploads


class ClaudeCliContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root/'project'
        self.cwd.mkdir()
        self.config = self.root/'config'
        (self.config/'sessions').mkdir(parents=True)
        folder = self.config/'projects'/'fixture'
        folder.mkdir(parents=True)
        self.native = str(uuid.uuid4())
        self.sid = model.gateway_id('cli:fixture:account', self.native)
        self.path = folder/(self.native + '.jsonl')
        self.path.write_text('\n'.join(json.dumps(event) for event in [
            {'type': 'user', 'cwd': str(self.cwd), 'message': {'role': 'user', 'content': 'Fixture request'}},
            {'type': 'mode', 'mode': 'plan'},
            {'type': 'assistant', 'cwd': str(self.cwd), 'message': {'role': 'assistant', 'model': 'fixture-model',
                                                                  'content': [{'type': 'text', 'text': 'Fixture answer'}]}},
        ]) + '\n')
        self.profile = {'binary': '/fixture/claude', 'directory': self.config, 'origin': 'fixture:account'}
        self.profile_patch = patch('bridge.clients.claude.cli_session._profile', return_value=self.profile)
        self.auth_patch = patch('bridge.clients.claude.cli_session._auth_ready', return_value=True)
        self.profile_patch.start(); self.auth_patch.start()
        self.addCleanup(self.profile_patch.stop); self.addCleanup(self.auth_patch.stop)
        self.cli = ClaudeCli(self.root/'uploads')

    def test_saved_list_and_detail_are_passive_and_closed(self):
        with patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            listing = self.cli.listing()
            detail = self.cli.call('detail', self.sid, {})
        popen.assert_not_called()
        self.assertEqual([row['id'] for row in listing['sessions']], [self.sid])
        self.assertEqual(detail['session']['connectionState'], 'closed')
        self.assertEqual(detail['session']['cwd'], str(self.cwd))
        self.assertEqual(detail['session']['permissionMode'], 'plan')
        self.assertTrue(detail['capabilities']['resume'])
        self.assertFalse(detail['capabilities']['send'])

    def test_closed_adapter_status_never_queries_auth(self):
        self.cli.close()
        with patch('bridge.clients.claude.cli_session._auth_ready',
                   side_effect=AssertionError('closed adapter started auth')):
            self.assertEqual(self.cli.status(), {'cliAvailable': False, 'cliAuthReady': False})

    def test_tool_only_first_user_record_keeps_listing_readable(self):
        events = self.path.read_text().splitlines()
        first = json.loads(events[0])
        first['message']['content'] = [{'type': 'tool_result', 'content': 'fixture'}]
        events[0] = json.dumps(first)
        self.path.write_text('\n'.join(events) + '\n')
        self.assertEqual(self.cli.listing()['sessions'][0]['title'], 'Claude Code 会话')

    def test_resume_uses_original_config_cwd_model_permission_and_no_prompt(self):
        process = Mock()
        process.poll.return_value = None
        process.stdout = iter(())
        process.stdin = Mock()
        with patch('bridge.clients.claude.cli_session.subprocess.Popen', return_value=process) as popen, \
             patch('bridge.clients.claude.cli_session._Owned') as owner_type:
            owner = owner_type.return_value
            owner.origin = self.profile['origin']
            owner.process = process
            owner.catalog = {'current_permission_mode': 'plan'}
            owner.pending = {}
            owner.active = False
            owner.model = None
            owner.permission = 'plan'
            owner.error = None
            owner.events = []
            owner.control.return_value = {'current_permission_mode': 'plan'}
            self.assertEqual(self.cli.call('resume', self.sid, {})['status'], 'ready')
            self.assertEqual(self.cli.call('resume', self.sid, {})['status'], 'ready')
            owner.control.assert_called_once_with({'subtype': 'initialize', 'hooks': None}, timeout=30)
            args, kwargs = popen.call_args
            self.assertEqual(kwargs['cwd'], str(self.cwd.resolve()))
            self.assertEqual(kwargs['env']['CLAUDE_CONFIG_DIR'], str(self.config))
            self.assertEqual(args[0][args[0].index('--permission-mode') + 1], 'plan')
            self.assertEqual(args[0][args[0].index('--model') + 1], 'fixture-model')
            self.assertIn('--resume', args[0])
            scope = str((self.root/'uploads'/DesktopUploads.scope('claude', self.sid)).resolve())
            self.assertEqual(args[0][args[0].index('--add-dir') + 1], scope)
            self.assertNotIn('Fixture request', args[0])
            self.assertEqual(popen.call_count, 1)

    def test_external_writer_guard_blocks_before_spawning(self):
        with patch('bridge.clients.claude.cli_session.assert_session_closed',
                   side_effect=BridgeUnavailable('external writer')) as guard, \
             patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            with self.assertRaises(BridgeUnavailable):
                self.cli.call('resume', self.sid, {})
        guard.assert_called_once_with(self.config, self.native)
        popen.assert_not_called()

    def test_account_switch_blocks_resume_and_unknown_permission_does_not_guess(self):
        self.cli.listing()
        with patch('bridge.clients.claude.cli_session._profile', return_value={**self.profile,
                   'directory': self.root/'other-config', 'origin': 'fixture:other'}), \
             patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            self.assertFalse(self.cli.contains(self.sid))
            with self.assertRaises(ValueError):
                self.cli.call('resume', self.sid, {})
            popen.assert_not_called()
        self.path.write_text(self.path.read_text().replace('{"type": "mode", "mode": "plan"}\n', ''))
        with patch('bridge.clients.claude.cli_session.subprocess.Popen') as popen:
            detail = self.cli.call('detail', self.sid, {})
            self.assertFalse(detail['capabilities']['resume'])
            self.assertIn('权限模式', detail['notice'])
            with self.assertRaises(BridgeUnavailable):
                self.cli.call('resume', self.sid, {})
            popen.assert_not_called()

    def test_resume_receipt_is_at_most_once(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.return_value = {'status': 'ready'}
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4())}
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'ready')
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'ready')
        adapter.call.assert_called_once_with('resume', self.sid, body)

    def test_file_only_send_uses_receipt_and_does_not_replay(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.return_value = {'status': 'accepted'}
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4()), 'text': '',
                'resolvedFiles': [{'name': 'note.txt', 'path': str(self.root/'note.txt')}]}
        self.assertEqual(manager.call('claude', 'send', self.sid, body)['status'], 'accepted')
        self.assertEqual(manager.call('claude', 'send', self.sid, body)['status'], 'accepted')
        adapter.call.assert_called_once_with('send', self.sid, body)

    def test_preflight_block_can_retry_with_new_receipt_after_session_closes(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.side_effect = [ResumePreflightBlocked('external writer'), {'status': 'ready'}]
        manager.adapters['claude'] = adapter
        first = {'id': str(uuid.uuid4())}
        second = {'id': str(uuid.uuid4())}
        self.assertEqual(manager.call('claude', 'resume', self.sid, first)['status'], 'blocked')
        self.assertEqual(manager.call('claude', 'resume', self.sid, first)['status'], 'blocked')
        self.assertEqual(manager.call('claude', 'resume', self.sid, second)['status'], 'ready')
        self.assertEqual(adapter.call.call_count, 2)

    def test_uncertain_resume_is_not_replayed_with_same_receipt(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.call.side_effect = BridgeUnavailable('initialize result uncertain')
        manager.adapters['claude'] = adapter
        body = {'id': str(uuid.uuid4())}
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'unknown')
        self.assertEqual(manager.call('claude', 'resume', self.sid, body)['status'], 'unknown')
        adapter.call.assert_called_once()

    def test_desktop_quit_guard_sees_owned_cli_task(self):
        manager = DesktopSessions(self.root/'gateway')
        self.addCleanup(manager.close)
        adapter = Mock()
        adapter.cli.busy.return_value = True
        manager.adapters['claude'] = adapter
        self.assertEqual(manager._claude_quit_evidence(), {'busy': True, 'unknown': False})
        with self.assertRaisesRegex(ValueError, 'Claude Code 任务'):
            manager._assert_idle('claude', {'unknown': False, 'mainPids': []})

    def test_only_owned_process_receives_send_controls_and_approval(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=False,
                     model=None, permission='plan', events=[])
        owner.pending = {}
        owner.catalog = {'models': [{'value': 'fixture-model', 'displayName': 'Fixture'}]}
        owner.condition = threading.Condition()
        owner.error = None
        owner.control.return_value = {'mode': 'default'}
        self.cli.owned[self.native] = owner
        self.assertEqual(self.cli.call('send', self.sid, {'text': 'new fixture message'})['status'], 'accepted')
        self.assertTrue(self.cli.busy())
        self.assertFalse(self.cli.call('detail', self.sid, {})['capabilities']['send'])
        with self.assertRaises(BridgeUnavailable):
            self.cli.call('send', self.sid, {'text': 'overlapping fixture'})
        owner.write.assert_called_with({'type': 'user', 'message': {'role': 'user', 'content': 'new fixture message'}})
        self.cli.call('settings', self.sid, {'model': 'fixture-model'})
        owner.control.assert_called_with({'subtype': 'set_model', 'model': 'fixture-model'})
        self.cli.call('access', self.sid, {'mode': 'default'})
        owner.control.assert_called_with({'subtype': 'set_permission_mode', 'mode': 'default'})
        owner.pending['request-1'] = {'request': {'tool_name': 'Bash', 'input': {'command': 'fixture'}}}
        self.cli.call('respond', self.sid, {'requestId': 'request-1', 'decision': 'decline'})
        self.assertNotIn('request-1', owner.pending)
        owner.write.assert_called_with({'type': 'control_response', 'response': {'subtype': 'success',
                                       'request_id': 'request-1', 'response': {'behavior': 'deny',
                                                                             'message': '用户拒绝了本次操作'}}})
        self.cli.call('stop', self.sid, {})
        owner.control.assert_called_with({'subtype': 'interrupt'})

    def test_attachment_only_send_uses_native_image_block_and_local_file_reference(self):
        process = Mock(); process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=False,
                     model=None, permission='plan', events=[])
        owner.pending = {}; owner.condition = threading.Condition(); owner.error = None
        self.cli.owned[self.native] = owner
        self.assertTrue(self.cli.call('detail', self.sid, {})['capabilities']['attachments'])
        file = self.root/'private note.txt'; file.write_text('fixture bytes')
        self.cli.call('send', self.sid, {'text': '', 'resolvedImages': [
            {'name': 'photo.png', 'url': 'data:image/png;base64,aGVsbG8='}],
            'resolvedFiles': [{'name': file.name, 'path': str(file)}]})
        content = owner.write.call_args.args[0]['message']['content']
        self.assertEqual(content[1], {'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': 'aGVsbG8='}})
        self.assertIn(str(file), content[0]['text'])
        self.assertNotIn('fixture bytes', json.dumps(content))

    def test_question_approval_forwards_answers_to_owned_tool(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=True,
                     model=None, permission='default', events=[])
        owner.pending = {'question-1': {'request': {'tool_name': 'AskUserQuestion',
                         'input': {'questions': [{'question': 'Fixture choice?', 'options': []}]}}}}
        owner.condition = threading.Condition()
        owner.error = None
        self.cli.owned[self.native] = owner
        self.cli.call('respond', self.sid, {'requestId': 'question-1', 'decision': 'accept',
                                           'answers': {'Fixture choice?': {'answers': ['Option A']}}})
        response = owner.write.call_args.args[0]['response']['response']
        self.assertEqual(response['updatedInput']['answers'], {'Fixture choice?': 'Option A'})

    def test_ambiguous_approval_write_cannot_be_replayed(self):
        process = Mock()
        process.poll.return_value = None
        owner = Mock(origin=self.profile['origin'], process=process, active=True,
                     model=None, permission='default', events=[])
        owner.pending = {'request-1': {'request': {'tool_name': 'Bash', 'input': {}}}}
        owner.condition = threading.Condition()
        owner.error = None
        owner.write.side_effect = BridgeUnavailable('write uncertain')
        self.cli.owned[self.native] = owner
        with self.assertRaises(BridgeUnavailable):
            self.cli.call('respond', self.sid, {'requestId': 'request-1', 'decision': 'accept'})
        with self.assertRaisesRegex(ValueError, '已处理或过期'):
            self.cli.call('respond', self.sid, {'requestId': 'request-1', 'decision': 'accept'})
        owner.write.assert_called_once()

    def test_native_init_identity_mismatch_terminates_owned_process(self):
        process = Mock()
        process.stdout = iter([json.dumps({'type': 'system', 'subtype': 'init',
                                           'session_id': str(uuid.uuid4()), 'cwd': str(self.cwd)})])
        owned = _Owned(process, self.profile['origin'], self.native, str(self.cwd))
        with owned.condition:
            owned.condition.wait_for(lambda: owned.error is not None, timeout=1)
        self.assertIn('身份', owned.error)
        process.terminate.assert_called_once()

    def test_native_cancel_and_terminal_result_clear_pending_approval(self):
        for ending in ({'type': 'control_cancel_request', 'request_id': 'pending'},
                       {'type': 'result', 'subtype': 'error_during_execution'}):
            process = Mock()
            process.stdout = iter([json.dumps({'type': 'control_request', 'request_id': 'pending',
                         'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {}}}),
                         json.dumps(ending)])
            owned = _Owned(process, self.profile['origin'], self.native, str(self.cwd))
            with owned.condition:
                self.assertTrue(owned.condition.wait_for(lambda: owned.finished, timeout=1))
            self.assertFalse(owned.pending)
            self.assertFalse(owned.active)
            self.assertEqual([event['type'] for event in owned.events],
                             ['result'] if ending['type'] == 'result' else [])
