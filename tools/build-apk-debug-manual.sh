#!/usr/bin/env bash
# Reproducible LOCAL-ONLY debug build. Does not deploy, push or replace a release APK.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
: "${ANDROID_HOME:?Set ANDROID_HOME ke SDK Android (platform 35 + build-tools).}"
: "${JAVA_HOME:?Set JAVA_HOME ke JDK 17+.}"
export PATH="$JAVA_HOME/bin:$PATH"
if [ ! -f "$HOME/.android/debug.keystore" ]; then
  echo 'Debug keystore belum tersedia. Hentikan; jangan mengganti identitas signer tanpa keputusan operator.' >&2
  exit 2
fi
# Fail before Gradle if a snapshot restored stale Android assets or removed the API bridge.
node tests/asset_parity.test.js
python3 - <<'PY'
from pathlib import Path
root=Path('english-quest-android/app')
java=(root/'src/main/java/com/englishquest/app/MainActivity.java').read_text()
for method in ('getApiBaseUrl()', 'isHttpDevAllowed()'):
    assert method in java, 'Bridge Android hilang: '+method
assert 'EQ_ALLOW_HTTP_DEV' in (root/'build.gradle').read_text(), 'Flag debug hilang'
assert (root/'src/debug/res/xml/eq_network_security_config_debug.xml').is_file(), 'Overlay loopback hilang'
assert 'submitManualPayment' in Path('app.js').read_text(), 'Source pembayaran manual hilang'
PY
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py' -v
for test in tests/*.test.js; do node "$test"; done
(
  cd english-quest-android
  bash ./gradlew --no-daemon --console=plain \
    -PEQ_API_BASE_URL=http://127.0.0.1:8088 -PEQ_ALLOW_HTTP_DEV=1 :app:assembleDebug
)
python3 tools/verifikasi-apk-debug-manual.py \
  english-quest-android/app/build/outputs/apk/debug/app-debug.apk
