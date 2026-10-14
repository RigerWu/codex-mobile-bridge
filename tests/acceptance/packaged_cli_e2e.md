# Installed CLI gateway acceptance

`packaged_cli_e2e.py` uses the installed **Test** gateway worker, not `desktop.py`, for configuration, start, stop, login, and every HTTP assertion. Source client transports seed two closed conversations through the native CLIs before gateway startup. The test sends only synthetic prompts to loopback Responses and Anthropic SSE servers with placeholder keys. It records no prompt bodies or credentials in `result.json`.

Run as the normal desktop user after the Test application has been installed. Provide absolute paths to the installed bundled worker and the two native CLIs. `--gui` additionally checks that the worker is inside the distinct installed Test controller's resources. `--codex-package` should point to a complete installed daemon package root containing `bin/codex[.exe]` when one exists; without it, the harness stages just the supplied binary and reports native daemon startup failure if that distribution needs other package files. `--source-root` points to the matching repository checkout staged on the test host; source modules are used only to seed closed native sessions.

Ubuntu example (replace the executable paths with the installed paths found on that host):

```sh
python3 -B tests/acceptance/packaged_cli_e2e.py \
  --source-root /home/user/.tmp/cmb-package-20261014/source \
  --test-root /home/user/.tmp/cmb-package-20261014/e2e \
  --gui '/opt/codex-mobile-bridge-test/codex-mobile-bridge-test' \
  --gateway '/opt/codex-mobile-bridge-test/resources/gateway/codex-mobile-gateway' \
  --codex-cli '/usr/lib/chatgpt/resources/codex' \
  --claude-cli '/home/user/.tmp/cmb-claude-platform-20261014/claude'
```

Windows example from a normal, non-elevated CMD terminal (replace the paths with the installed paths found on that host):

```bat
"F:\ANACONDA3\python.exe" -B tests\acceptance\packaged_cli_e2e.py ^
  --source-root "C:\Users\19297\.tmp\cmb-package-20261014\source" ^
  --test-root "C:\Users\19297\.tmp\cmb-package-20261014\e2e" ^
  --gui "C:\Program Files\Codex Mobile Bridge Test\Codex Mobile Bridge Test.exe" ^
  --gateway "C:\Program Files\Codex Mobile Bridge Test\resources\gateway\codex-mobile-gateway.exe" ^
  --codex-cli "C:\Users\19297\.tmp\cmb-codex-platform-20261014\codex.exe" ^
  --claude-cli "C:\Users\19297\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
```

The harness creates a short, new run directory below the test root to fit Windows AF_UNIX socket limits. It fails before touching native state if a supplied path or gateway version is wrong. Each run leaves a sanitized `result.json` in its own directory and stops the gateway, daemon, and mock listeners it owns. HTTP mutations each get a fresh UUID and are never retried after an uncertain response; only reads are polled. A failure is evidence for the reported stage, not proof that later stages passed. No daily `.codex` or `.claude` profile is selected.
