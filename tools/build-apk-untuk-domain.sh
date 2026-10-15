#!/usr/bin/env bash
# Build RELEASE saja. Jalankan setelah HTTPS publik tersedia dan disetujui.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORIGIN="${1:-}"
# Jangan mencetak input URL mentah: dapat mengandung kredensial yang salah ditempel.
python3 - "$ORIGIN" <<'PY'
import sys, urllib.parse, ipaddress
u=urllib.parse.urlsplit(sys.argv[1])
try:
 assert u.scheme=='https' and u.hostname and u.path in ('','/')
 assert not (u.username or u.password or u.query or u.fragment)
 assert u.port in (None,443)
 h=u.hostname.lower()
 assert '.' in h and not h.endswith(('.local','.localhost','.internal','.test','.example','.invalid'))
 assert h not in ('example.com','example.org','example.net') and 'ganti' not in h
 try: assert ipaddress.ip_address(h).is_global
 except ValueError: assert all(part and all(c.isalnum() or c=='-' for c in part) for part in h.split('.'))
except (AssertionError,ValueError):
 sys.exit('DITOLAK: butuh origin HTTPS publik nyata, tanpa kredensial/path/query. Belum ada build.')
PY
if [[ -n "${EQ_ALLOW_HTTP_DEV:-}" || -n "${EQ_API_BASE_URL:-}" ]]; then
  echo 'Kosongkan EQ_ALLOW_HTTP_DEV/EQ_API_BASE_URL lingkungan; gunakan argumen origin.' >&2
  exit 2
fi
# Ini bukan uji pendaftaran publik; hanya verifikasi TLS dan endpoint health read-only.
curl --fail --silent --show-error --connect-timeout 10 --max-time 20 "${ORIGIN%/}/api/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("ok") and d.get("service")=="english-quest"'
cd "$ROOT"
node tests/asset_parity.test.js
cd english-quest-android
# JDK 17 dan SDK 35 harus dikonfigurasi operator lewat JAVA_HOME/ANDROID_HOME.
bash ./gradlew --no-daemon :app:assembleRelease \
  -PEQ_API_BASE_URL="${ORIGIN%/}" -PEQ_ALLOW_HTTP_DEV=0
APK="$PWD/app/build/outputs/apk/release/app-release-unsigned.apk"
python3 "$ROOT/tools/verifikasi-apk-produksi.py" "$APK" "${ORIGIN%/}"
echo 'APK release masih UNSIGNED, belum bisa dipasang. Gunakan signing key pemilik yang sudah ada; jangan membuat/mengganti key tanpa persetujuan.'
echo 'Sesudah ditandatangani: verifikasi apksigner, catat SHA-256 final, lalu uji Android nyata.'
