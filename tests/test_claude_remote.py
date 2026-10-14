import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.claude import remote, remote_worker
from bridge.clients.claude.remote import RemoteClaudeCli
from bridge.clients.uploads import DesktopUploads
from bridge.clients.errors import BridgeUnavailable
from bridge.resources import source_text


class RemoteWorkerContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root/'remote-profile'; self.config.mkdir()
        self.cwd = self.root/'remote-project'; self.cwd.mkdir()
        self.native = str(uuid.uuid4())
        folder = self.config/'projects'/'fixture'; folder.mkdir(parents=True)
        self.path = folder/(self.native+'.jsonl')
        self.path.write_text('\n'.join(json.dumps(event) for event in [
            {'type': 'user', 'cwd': str(self.cwd), 'permissionMode': 'plan',
             'message': {'role': 'user', 'content': 'remote fixture'}},
            {'type': 'assistant', 'cwd': str(self.cwd),
             'message': {'role': 'assistant', 'model': 'fixture-model',
                         'content': [{'type': 'text', 'text': 'fixture response'}]}}])+'\n')
        (self.config/'sessions').mkdir()
        bindir = self.root/'bin'; bindir.mkdir()
        binary = bindir/'claude'
        binary.write_text('#!/bin/sh\nif [ "$1" = auth ]; then printf \'{"loggedIn":true}\\n\'; fi\n')
        binary.chmod(0o700)
        self.env = {**os.environ, 'HOME': str(self.root), 'CLAUDE_CONFIG_DIR': str(self.config),
                    'PATH': str(bindir)+':'+os.environ.get('PATH', '')}
        self.binary = binary

    def run_worker(self, request):
        process = subprocess.run([sys.executable, '-u', '-c', source_text('clients/claude/remote_worker.py')],
                                 input=json.dumps(request)+'\n', text=True, capture_output=True,
                                 env=self.env, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_standalone_passive_history_and_host_profile(self):
        listed = self.run_worker({'operation': 'list'})['value']
        self.assertEqual(len(listed['sessions']), 1)
        profile = listed['profile']
        self.assertEqual(profile['binary'], str(self.binary.resolve()))
        self.assertIn(str(self.config), profile['origin'])
        self.assertNotIn('events', listed['sessions'][0])
        detail = self.run_worker({'operation': 'detail', 'origin': profile['origin'],
                                  'native': self.native, 'events': True})['value']
        self.assertEqual(len(detail['events']), 2)
        self.assertEqual(detail['cwd'], str(self.cwd))
        self.assertTrue(self.run_worker({'operation': 'auth', 'origin': profile['origin']})['value']['ready'])
        project_probe = self.run_worker({'operation': 'projects'})['value']
        self.assertEqual(project_probe['profile']['origin'], profile['origin'])
        self.assertTrue(project_probe['authReady'])
        self.assertEqual(len(project_probe['sessions']), 1)
        self.assertFalse((self.config/'mobile-bridge').exists())

    def test_upload_is_private_immutable_and_bound_to_remote_profile(self):
        profile = self.run_worker({'operation': 'list'})['value']['profile']
        scope, identifier = str(uuid.uuid4()), str(uuid.uuid4())
        data = b'remote upload bytes'
        request = {'operation': 'upload', 'origin': profile['origin'], 'scope': scope,
                   'id': identifier, 'name': 'note.txt', 'sha256': hashlib.sha256(data).hexdigest(),
                   'data': __import__('base64').b64encode(data).decode()}
        first = self.run_worker(request)['value']['path']
        self.assertEqual(Path(first).read_bytes(), data)
        self.assertEqual(self.run_worker(request)['value']['path'], first)
        self.assertEqual(Path(first).stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.run_worker({**request, 'origin': 'other'})['ok'])

    def test_linux_registry_pid_start_and_domain_are_checked(self):
        found = {'directory': str(self.config)}
        pid = 32123
        registry = self.config/'sessions'/(str(pid)+'.json')
        entry = {'sessionId': self.native, 'pid': pid, 'procStart': 'Wed Oct 14 10:00:00 2026',
                 'pidDomain': 'darwin:machine:pid:[123]'}
        registry.write_text(json.dumps(entry))
        with patch.object(remote_worker, 'pid_domain', return_value=entry['pidDomain']), \
             patch.object(remote_worker.os, 'kill', return_value=None), \
             patch.object(remote_worker.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=0, stdout='Wed Oct 14 10:00:00 2026\n')):
            with self.assertRaisesRegex(ValueError, '其他终端运行'):
                remote_worker.assert_session_closed(found, self.native)
        with patch.object(remote_worker, 'pid_domain', return_value=entry['pidDomain']), \
             patch.object(remote_worker.os, 'kill', return_value=None), \
             patch.object(remote_worker.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=0, stdout='Wed Oct 14 11:00:00 2026\n')):
            remote_worker.assert_session_closed(found, self.native)
        entry['pidDomain'] = 'darwin:other:pid:[456]'
        registry.write_text(json.dumps(entry))
        with patch.object(remote_worker, 'pid_domain', return_value='darwin:machine:pid:[123]'):
            with self.assertRaisesRegex(ValueError, '无法核验'):
                remote_worker.assert_session_closed(found, self.native)

    def test_linux_domain_uses_machine_and_pid_namespace(self):
        original = remote_worker.sys.platform
        with patch.object(remote_worker.sys, 'platform', 'linux'), \
             patch.object(remote_worker.Path, 'read_text', return_value='machine-id\n'), \
             patch.object(remote_worker.os, 'readlink', return_value='pid:[123]'):
            self.assertEqual(remote_worker.pid_domain(), 'darwin:machine-id:pid:[123]')
        self.assertEqual(remote_worker.sys.platform, original)

    def test_remote_origin_tracks_account_without_rotating_stable_identity(self):
        credentials = self.config/'.credentials.json'
        credentials.write_text('{"accessToken":"first"}')
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(remote_worker, 'auth_status', return_value={'loggedIn': True}):
            first = remote_worker.profile()['origin']
            credentials.write_text('{"accessToken":"second"}')
            self.assertNotEqual(remote_worker.profile()['origin'], first)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(remote_worker, 'auth_status', return_value={'loggedIn': True,
                                                                      'email': 'fixture@example.test'}):
            first = remote_worker.profile()['origin']
            credentials.write_text('{"accessToken":"rotated"}')
            self.assertEqual(remote_worker.profile()['origin'], first)
            os.environ['ANTHROPIC_API_KEY'] = 'different-account-key'
            self.assertNotEqual(remote_worker.profile()['origin'], first)


class RemoteAdapterContract(unittest.TestCase):
    def setUp(self):
        self.native = str(uuid.uuid4())
        self.hosts = Mock()
        self.hosts.hosts.return_value = {'ssh:test': {'alias': 'fixture-loopback', 'displayName': 'Test host'}}
        self.cli = RemoteClaudeCli(self.hosts)
        self.addCleanup(self.cli.close)
        self.row = {'nativeId': self.native, 'origin': 'default:/remote/.claude:abc',
                    'cwd': '/remote/project', 'model': 'fixture-model', 'permissionMode': 'plan',
                    'permissionKnown': True, 'updatedAt': 1, 'title': 'remote fixture'}

    def test_host_and_profile_scope_and_passive_listing(self):
        with patch.object(remote, '_remote', return_value={'sessions': [self.row]}) as request, \
             patch.object(remote.subprocess, 'Popen') as popen:
            self.cli._refresh(self.hosts.hosts())
            listing = self.cli.listing()
        popen.assert_not_called()
        sid = listing['sessions'][0]['id']
        self.assertEqual(listing['sessions'][0]['host'], 'ssh:test')
        self.assertEqual(request.call_args.args[0], 'fixture-loopback')
        self.assertNotEqual(sid, remote.model.gateway_id('cli:'+self.row['origin'], self.native))
        self.assertTrue(self.cli.contains(sid))
        self.hosts.hosts.return_value['ssh:test']['alias'] = 'different'
        with self.assertRaisesRegex(ValueError, '主机配置已变化'):
            self.cli._get(sid)

    def test_project_discovery_uses_one_bounded_host_probe(self):
        self.hosts.projects.return_value = [{'host': 'ssh:test', 'cwd': '/remote/project'}]
        with patch.object(remote, '_remote', return_value={
                'profile': {'origin': self.row['origin']}, 'authReady': True,
                'sessions': [self.row]}) as request:
            projects = self.cli.projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]['cwd'], '/remote/project')
        request.assert_called_once_with('fixture-loopback', {'operation': 'projects'}, timeout=12)

    def test_offline_host_refresh_does_not_block_cached_list_or_live_controls(self):
        row = self.cli._row('ssh:test', self.hosts.hosts()['ssh:test'], self.row)
        self.cli.index[row['id']] = row
        owned = SimpleNamespace(origin=row['origin'], process=Mock(), error=None,
                                condition=threading.Condition(), active=True, pending={},
                                model=None, permission=None, events=[], catalog={},
                                control=Mock(return_value={}), close=Mock())
        owned.process.poll.return_value = None
        self.cli.owned[row['id']] = owned
        self.cli.host_snapshot = self.hosts.hosts()
        self.cli.last_refresh = 0
        pause = threading.Event()
        entered = threading.Event()
        def offline(*_args, **_kwargs):
            entered.set()
            pause.wait(2)
            raise BridgeUnavailable('offline')
        try:
            with patch.object(remote, '_remote', side_effect=offline):
                start = __import__('time').monotonic()
                listed = self.cli.listing()
                self.assertLess(__import__('time').monotonic()-start, .25)
                self.assertEqual(listed['sessions'][0]['id'], row['id'])
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.cli.contains(row['id']))
                self.assertEqual(self.cli.call('stop', row['id'], {}), {'status': 'accepted'})
                owned.control.assert_called_once_with({'subtype': 'interrupt'})
        finally:
            pause.set()

    def test_remote_upload_replaces_gateway_path_before_native_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)/'local.txt'; local.write_bytes(b'ledger-verified bytes')
            self.row.update(id='session-id', alias='fixture-loopback')
            identifier = str(uuid.uuid4())
            with patch.object(remote, '_remote', return_value={'path': '/remote/private/original.txt'}) as transfer:
                result = self.cli._upload_files(self.row, [{'id': identifier, 'name': 'local.txt', 'path': str(local)}])
            self.assertEqual(result, [{'name': 'local.txt', 'path': '/remote/private/original.txt'}])
            self.assertNotIn(str(local), str(result))
            self.assertEqual(transfer.call_args.args[1]['operation'], 'upload')
            self.assertEqual(transfer.call_args.args[1]['scope'], DesktopUploads.scope('claude', 'session-id'))

    def test_disconnected_send_has_unknown_receipt_and_is_not_replayed(self):
        sid = 'session-id'
        row = {**self.row, 'id': sid, 'alias': 'fixture-loopback'}
        owned = SimpleNamespace(origin=row['origin'], process=Mock(), error=None,
                                condition=threading.Condition(), active=False, pending={},
                                model=None, permission=None, events=[], catalog={})
        owned.process.poll.return_value = None
        owned.close = Mock()
        owned.write = Mock(side_effect=BridgeUnavailable('Claude Code 写入结果不明，请检查会话'))
        self.cli.owned[sid] = owned
        body = {'text': 'send once', 'resolvedFiles': [], 'resolvedImages': []}
        with patch.object(self.cli, '_get', return_value=row):
            with self.assertRaisesRegex(BridgeUnavailable, '结果不明'):
                self.cli.call('send', sid, body)
            with self.assertRaisesRegex(BridgeUnavailable, '请等待'):
                self.cli.call('send', sid, body)
        owned.write.assert_called_once()
        owned.process.poll.return_value = 1


if __name__ == '__main__':
    unittest.main()
