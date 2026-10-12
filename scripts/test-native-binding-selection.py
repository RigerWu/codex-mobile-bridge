#!/usr/bin/env python3
"""Execute production cookie selection against first-login and stale-response cases."""
from pathlib import Path
import os
import subprocess

root = Path(__file__).resolve().parents[1]
work = root / '.tmp/native-binding-selection-tests'
work.mkdir(parents=True, exist_ok=True)
source = root / 'mobile/android/src/io/github/try2love/codexbridge/BindingSelection.java'
(work / 'BindingSelectionTests.java').write_text('''package io.github.try2love.codexbridge;
final class BindingSelectionTests {
 static void eq(String expected,String actual){if(!expected.equals(actual))throw new AssertionError("expected "+expected+" got "+actual);}
 public static void main(String[] args){
  String own="codex_mobile_session_0123456789abcdef01234567";
  String other="codex_mobile_session_ffffffffffffffffffffffff";
  String header=other+"=other-token; "+own+"=own-token";
  eq(own+"=own-token",BindingSelection.match(header,header,own));
  eq("",BindingSelection.match(other+"=other-token",header,own));
  eq(own+"=own-token",BindingSelection.match("codex_mobile_session=own-token",header,own));
  eq("",BindingSelection.match("codex_mobile_session=old-token",header,own));
  eq("",BindingSelection.match(header,header,"codex_mobile_session_bad"));
  eq("codex_mobile_session=legacy-token",BindingSelection.match("codex_mobile_session=legacy-token", "codex_mobile_session=legacy-token", "codex_mobile_session"));
  System.out.println("Native binding cookie selection passed");
 }
}
''')
java_home = Path(os.environ['JAVA_HOME']) if 'JAVA_HOME' in os.environ else None
def tool(name): return str(java_home / 'bin' / name) if java_home else name
subprocess.run([tool('javac'), '-d', str(work), str(source), str(work / 'BindingSelectionTests.java')], check=True)
subprocess.run([tool('java'), '-cp', str(work), 'io.github.try2love.codexbridge.BindingSelectionTests'], check=True)
