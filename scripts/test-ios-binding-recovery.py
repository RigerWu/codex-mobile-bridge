#!/usr/bin/env python3
"""Compile production iOS recovery validation and exercise bounded local auth responses."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/ios-binding-tests'
WORK.mkdir(parents=True, exist_ok=True)
NAME = 'codex_mobile_session_0123456789abcdef01234567'


class Handler(BaseHTTPRequestHandler):
    mode = 'valid'

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.startswith('/control/'):
            Handler.mode = self.path.split('/')[-1]
            self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers(); return
        if Handler.mode == 'redirect':
            self.send_response(302); self.send_header('Location', '/capture'); self.send_header('Content-Length', '0'); self.end_headers(); return
        if self.path == '/capture':
            raise AssertionError('Recovery followed a redirect')
        relay = self.path.startswith('/d/fixture/')
        legacy = Handler.mode == 'legacy'
        selected = 'codex_mobile_session' if relay or legacy else NAME
        valid = self.headers.get('Cookie') == selected + '=valid-device-binding'
        if Handler.mode == 'revoked': valid = False
        name = 'codex_mobile_session_ffffffffffffffffffffffff' if Handler.mode == 'wrong-name' else selected
        import json
        body = json.dumps({'authenticated': valid, 'cookieName': name} if not relay and not legacy else {'authenticated': valid, 'relay': relay, 'trustedDevice': True}).encode()
        if Handler.mode == 'oversize': body += b' ' * 65537
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    source = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()
    binding = source[source.index('private enum DeviceBinding'):source.index('final class BridgeNavigationController')]
    redirect = source[source.index('final class NoRedirect:'):source.index('// Foreground-only')]
    main = '''import Foundation
import Security
''' + binding + '\n' + redirect + '''
let address = CommandLine.arguments[1]
private let scoped = DeviceBinding.Credential(name: "codex_mobile_session_0123456789abcdef01234567", token: "valid-device-binding")
func mode(_ value: String) async throws {
    _ = try await URLSession.shared.data(from: URL(string: address + "/control/" + value)!)
}
private func check(_ address: String, _ credential: DeviceBinding.Credential, _ expected: Bool?) async {
    let actual = await DeviceBinding.verify(address, credential); precondition(actual == expected)
}
Task {
    await check(address, scoped, true)
    try await mode("wrong-name")
    await check(address, scoped, false)
    try await mode("revoked")
    await check(address, scoped, false)
    try await mode("redirect")
    await check(address, scoped, nil)
    try await mode("oversize")
    await check(address, scoped, nil)
    try await mode("valid")
    let relay = DeviceBinding.Credential(name: "codex_mobile_session", token: "valid-device-binding")
    await check(address + "/d/fixture", relay, true)
    try await mode("legacy")
    await check(address, relay, true)
    await check(address, scoped, false)
    print("iOS binding validation passed")
    exit(0)
}
dispatchMain()
'''
    (WORK / 'main.swift').write_text(main)
    subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK / 'cache'),
                    str(ROOT / 'mobile/ios/BridgePreview/GatewayURL.swift'), str(WORK / 'main.swift'),
                    '-o', str(WORK / 'binding-tests')], check=True)
    subprocess.run([str(WORK / 'binding-tests'), f'http://127.0.0.1:{server.server_port}'], check=True, timeout=30)
finally:
    server.shutdown(); server.server_close()
