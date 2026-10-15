> **Catatan historis:** angka/hash/status dalam laporan ini merekam tahap sebelumnya.
> Status terkini ada di [Laporan Hosting dan Premium](LAPORAN-HOSTING-PREMIUM.md).

# Laporan Akar Masalah Pendaftaran Akun — 15 Oktober 2026

Laporan ini menjawab tugas lanjutan 10 poin. Semua angka di bawah berasal dari pemeriksaan yang
dijalankan hari ini (15 Okt 2026) dan bisa diulang; tidak ada klaim yang tidak diuji.

**Ringkasan satu menit.** Masalahnya bukan pada backend dan bukan pada sumber kode terbaru, tetapi
pada **APK yang beredar**: `English-Quest-1.7.1-debug.apk` di repo adalah build **sebelum perbaikan**.
Di dalamnya tidak ada jembatan `getApiBaseUrl()`/`isHttpDevAllowed()`, sehingga aplikasi selalu
menganggap "server akun belum dikonfigurasi" dan tombol *Buat akun* mati; aset web-nya juga masih
memakai aturan **12 karakter**. Perbaikan hanya ada di branch `audit-baseline-1.7.1` dan belum pernah
menjadi APK yang dibagikan. Hari ini APK debug **berhasil dibangun ulang** dari branch tersebut dan
diverifikasi; pendaftaran diuji ujung-ke-ujung terhadap backend nyata dan berhasil (`201` → `me` →
`login`). Yang masih dibutuhkan agar orang lain bisa mendaftar dari HP-nya: **satu origin HTTPS**
tempat backend dihosting, lalu APK dibangun ulang dengan origin itu.

---

## 1. Verifikasi commit terbaru yang benar-benar tersedia

Karena berkas `.git` tidak ada di ruang kerja ini (dan tidak ikut tersimpan antar sesi), status
commit diverifikasi langsung ke GitHub, bukan dari ingatan laporan lama:

```sh
git clone --branch audit-baseline-1.7.1 https://github.com/bloxfruit6571-design/English-apk
# tip branch: 502ec4c  "Clarify the branch state in the registration report"
```

Dari klon itulah 7 berkas yang hilang dipulihkan byte-for-byte ke ruang kerja:

| Berkas | Alasan dipulihkan |
|---|---|
| `english-quest-android/app/src/main/java/com/englishquest/app/MainActivity.java` | memuat jembatan native (poin 1) |
| `english-quest-android/app/build.gradle` | memuat flag `EQ_ALLOW_HTTP_DEV` / `EQ_API_BASE_URL` |
| `english-quest-android/app/src/main/assets/{app.js,index.html,styles.css,sw.js}` | aset WebView |
| `tests/client_registration.test.js` | kelompok uji pendaftaran klien |

Dua berkas lagi **tidak ada di GitHub** karena memang tidak pernah di-commit (hanya untuk build
debug) dan hilang saat rollback; keduanya dibuat ulang hari ini dengan isi fungsional yang sama:
`app/src/debug/AndroidManifest.xml` dan `app/src/debug/res/xml/eq_network_security_config_debug.xml`
(izin cleartext **hanya** untuk `127.0.0.1`/`localhost`, khusus varian debug — rilis tetap
`usesCleartextTraffic="false"`).

## 2. Poin 1 — Jembatan native di sumber terbaru: **ADA**

`english-quest-android/app/src/main/java/com/englishquest/app/MainActivity.java`:

| Baris | Isi |
|---|---|
| 133 | `webView.addJavascriptInterface(new ExternalLinkBridge(), "EnglishQuestAndroid")` |
| 1126–1131 | gerbang dev: `BuildConfig.DEBUG && "1".equals(BuildConfig.EQ_ALLOW_HTTP_DEV)` |
| 1145 | `public String getApiBaseUrl()` — nilai yang dipakai klien saat `EQ_API_BASE` kosong |
| 1151 | `public boolean isHttpDevAllowed()` — mengizinkan HTTP **hanya** untuk loopback |

`app/build.gradle`: baris 13 membaca `-PEQ_ALLOW_HTTP_DEV` (atau env `EQ_ALLOW_HTTP_DEV`), baris 28
menanamkannya ke `BuildConfig`; `EQ_API_BASE_URL` default `""`.

Bukti tambahan: pada APK hasil build hari ini, kedua nama metode **ada di dalam `classes.dex`**
(bab 8).

## 3. Poin 2 — Konfigurasi `EQ_API_BASE_URL` dan ketersediaan backend

- Nilai `EQ_API_BASE_URL` pada sumber terbaru **kosong secara default** (diisi lewat
  `-PEQ_API_BASE_URL=…` saat build). Nilai kosong → `AccessApi.isConfigured()` bernilai `false` →
  tombol *Buat akun & lanjut* sengaja dinonaktifkan disertai pesan
  *"Server akun belum dikonfigurasi…"*.
- **Backend belum dihosting di mana pun.** Tidak ada alamat produksi di dalam sumber; saya tidak
  mengarang satu pun. Agar pendaftaran berfungsi, backend harus dijalankan/dihosting lebih dulu
  (dua cara di bawah), lalu APK dibangun dengan origin tersebut.

**Cara A — uji lokal hari ini juga (tanpa hosting).** Cocok untuk membuktikan alur pendaftaran:

```sh
bash /home/user/tools/jalankan-backend-uji.sh        # backend loopback di 127.0.0.1:8088
adb reverse tcp:8088 tcp:8088                        # HP fisik via USB debugging
# pasang APK varian uji-lokal (bab 8) → daftar akun
```

**Cara B — hosting sungguhan (agar semua orang bisa mendaftar).** Butuh satu domain/subdomain
HTTPS yang menunjuk ke server:

| Yang harus disiapkan | Nilai / cara |
|---|---|
| Origin API | `https://<domain-anda>/` → dipakai sebagai `-PEQ_API_BASE_URL=https://<domain-anda>` saat build APK |
| TLS | TLS langsung (`EQ_TLS_CERT`/`EQ_TLS_KEY`) **atau** reverse proxy + `EQ_TRUST_PROXY=1` |
| CORS | `EQ_ALLOWED_ORIGINS=https://appassets.androidplatform.net` (origin WebView) — bila lupa, respons tanpa header ACAO dan WebView memblokirnya (terbukti di bab 4) |
| Rahasia | `EQ_TOKEN_PEPPER` ≥ 32 byte acak (tanpa ini server menolak jalan) |
| Database | `EQ_DB_PATH` ke berkas yang persisten + punya cadangan |
| Admin | `python3 backend/manage.py create-admin --email <email> --db <db>` (kata sandi admin min. 12) |

Server **menolak start** bila disuruh mendengarkan jaringan tanpa TLS — ini bukan asumsi, ini hasil
uji: `EQ_HOST=0.0.0.0` tanpa TLS/`EQ_TRUST_PROXY` → `exit=2`,
*"Configuration error: Configure TLS files or a trusted TLS proxy. For loopback development only, set EQ_ALLOW_INSECURE_DEV=1."*

## 4. Poin 3 — Reproduksi masalah dan akar masalah aktual

**Akar masalah (bukti dari APK yang beredar, bukan dari laporan lama).** Isi
`English-Quest-1.7.1-debug.apk` (85.079.299 B, SHA-256 `05e066414f83…bd86`) diperiksa langsung dari
dalam paket:

| Pemeriksaan di dalam APK repo | Hasil | Arti bagi pengguna |
|---|---|---|
| `getApiBaseUrl` di dex | **tidak ada** | origin server tak pernah bisa diisi |
| `isHttpDevAllowed` di dex | **tidak ada** | build lama belum mengenal gerbang dev |
| `EQ_ALLOW_HTTP_DEV` di dex | **tidak ada** | idem |
| `assets/app.js` | `minlength="${register?12:1}"`, label *"(minimal 12 karakter)"* | kontrak kata sandi masih 12 |
| `assets/index.html` | memuat kata `EQ_ALLOW_HTTP_DEV`, tetapi **tidak memanggil** `isHttpDevAllowed()` | jembatan tidak dipakai |

Akibatnya: `isConfigured()` selalu `false` → tombol *Buat akun* **mati** dengan pesan
*"Server akun belum dikonfigurasi. Akses demo dan Premium perlu diverifikasi secara online."*
Bahkan bila pengguna punya server, APK itu baru menerima kata sandi 12 karakter.

**Reproduksi dengan kode klien yang benar-benar dikirim** (`tools/repro-client.js`, memuat
`access-client.js` + `index.html` yang identik dengan isi APK, dijalankan terhadap backend hidup):

| Kasus | Hasil |
|---|---|
| A. Build diperbaiki (jembatan ada) → backend hidup | `isConfigured()=true` → `register` **201**, sesi tersimpan, `me` mengembalikan akun (`premium=false`, sisa kuota 5), `login` **200** |
| B. Build tanpa jembatan (mewakili APK repo) | `register()` ditolak sebelum menyentuh jaringan → **`API_NOT_CONFIGURED`**, pesan persis seperti di atas |
| C. Backend mati (port tertutup) | **`NETWORK_ERROR`** → *"Tidak dapat terhubung ke server. Periksa internet, lalu coba lagi."* |

**Reproduksi sisi server/konfigurasi** (`tools/repro-http.py`, 5 skenario) — semuanya sesuai desain:
preflight `OPTIONS` **204** + ACAO benar; **tanpa** `EQ_ALLOWED_ORIGINS` respons tetap 201 tetapi
**tanpa ACAO** (WebView akan memblokirnya — inilah kenapa daftar konfigurasi di bab 3 penting);
`EQ_TRUST_PROXY=1` tanpa header `X-Forwarded-Proto: https` → **400 `HTTPS_REQUIRED`**, dengan header
→ **201**; percobaan ke-6 dalam 15 menit → **429 `RATE_LIMITED`**.

## 5. Poin 4 — Backend menerima dan menyimpan akun dengan aman

Diuji hari ini terhadap backend hidup (database uji terpisah di `/tmp`, **bukan** database pengguna):

| Uji | Hasil |
|---|---|
| Daftar kata sandi 6 karakter | **201**, sesi diterbitkan |
| `GET /api/me` dengan sesi itu | akun terbaca (`premium=false`, `demo_remaining=5`) |
| Login ulang | **200** |
| Kata sandi 5 karakter | **400 `WEAK_PASSWORD`** — *"Kata sandi harus 6–256 karakter."* |
| Email tidak valid | **400 `INVALID_EMAIL`** |
| Email sudah terdaftar | **409 `ACCOUNT_EXISTS`** |
| Percobaan ke-6/15 menit/IP | **429 `RATE_LIMITED`** (register 5/900 s; login 12/900 s) |
| Kata sandi tersimpan sebagai teks biasa? | **Tidak.** Baris `users` menyimpan `password_salt` (16 byte acak, base64url) + `password_hash` — **PBKDF2-HMAC-SHA256, 310.000 iterasi**, turunan 32 byte. Pencarian kata sandi sintetis di dalam berkas database (utuh maupun 6–8 karakter pertama) tidak menemukan apa pun |

Catatan: kata `password` memang muncul di berkas `.db`, tetapi hanya sebagai **nama kolom di skema
SQLite** — sudah diperiksa konteksnya. Tidak ada kata sandi pengguna di dalam catatan, log, atau tes
mana pun; semua kata sandi pada pengujian bersifat sintetis.

## 6. Poin 5 — Kata sandi minimal 6 karakter + tombol mata

Terverifikasi di sumber **dan** di dalam APK hasil build:

| Bukti | Lokasi |
|---|---|
| `const PASSWORD_MIN_LENGTH=6` | `app.js:365` (ada juga di `assets/app.js` dalam APK) |
| Pola email + pesan validasi | `app.js:366,370,371` |
| Bidang kata sandi | `minlength="${register?PASSWORD_MIN_LENGTH:1}"`, label `(minimal ${PASSWORD_MIN_LENGTH} karakter)` |
| Tombol daftar | `data-action="access-auth-submit" … ${state.accessBusy||!configured?'disabled':''}` |
| Tombol mata | `data-action="toggle-password-visibility"` dengan `aria-label`/`aria-pressed`; teks tidak hilang saat di-toggle |
| Tidak ada lagi aturan 12 | string "minimal 12 karakter" **tidak ada** di APK hasil build |

## 7. Poin 6 — Tanpa mengarang alamat server

Tidak ada endpoint, kredensial, atau URL produksi yang diciptakan. Satu-satunya alamat yang ditanam
adalah `http://127.0.0.1:8088` pada **varian uji-lokal** — dan itu loopback (hanya hidup bersama
`adb reverse`), bukan alamat palsu yang menyamar sebagai produksi. Aplikasi tetap gagal-tertutup
(fail-closed) bila origin belum diisi.

## 8. Poin 7 & 8 — APK hasil build hari ini (lokasi, SHA-256, identitas)

Dibangun hari ini dari sumber branch `audit-baseline-1.7.1` yang telah diverifikasi:

```sh
cd english-quest-android
JAVA_HOME=/var/tmp/eq/jdk17 ANDROID_HOME=/var/tmp/eq/android ANDROID_SDK_ROOT=/var/tmp/eq/android \
GRADLE_USER_HOME=/var/tmp/eq/gradle-home bash ./gradlew --no-daemon :app:assembleDebug \
  -PEQ_API_BASE_URL=http://127.0.0.1:8088 -PEQ_ALLOW_HTTP_DEV=1
# BUILD SUCCESSFUL in 32s · 35 actionable tasks
```

| | A. Varian **uji-lokal** (untuk dicoba sekarang) | B. Varian **polos** (tanpa origin) |
|---|---|---|
| Berkas | `/home/user/artefak-apk/English-Quest-1.7.1-register-test-localtest-debug.apk` | `english-quest-android/app/build/outputs/apk/debug/app-debug.apk` (tidak diarsipkan) |
| SHA-256 | `b891f33dd0056958880b8969795b4ee3ddbf2bccc2c7ca2c3cf88e50b3faa6d6` | `a7d52288f2c09bf45c3aa3bf50b2988db32dc63e1f696df5733c23f173250519` |
| Ukuran | 84.920.154 byte | 84.946.366 byte |
| Isi khas | jembatan native + URL loopback + NSC cleartext loopback | jembatan native, tanpa URL (tombol daftar nonaktif + pesan jelas) |
| Perintah ulang | seperti di atas | `bash ./gradlew --no-daemon :app:assembleDebug` |

Kedua varian (diverifikasi lewat `aapt2`/`apksigner`): `applicationId com.englishquest.app.debug`,
`versionCode 13`, `versionName 1.7.1-debug`, `minSdk 23`, `targetSdk 35`, sertifikat debug
`CN=Android Debug` SHA-256 `7659afc5…50a3`. **versionCode/versionName tidak dinaikkan** (sesuai
keputusan Anda). Berkas `.sha256` disertakan dan lolos `sha256sum -c`.

Dua catatan kejujuran:

1. Build hari ini **bukan** APK baru yang berbeda dari hasil sesi lalu: ke-137 entri di dalam zip
   (termasuk `classes.dex`, `assets/app.js`, dan `res/xml/eq_network_security_config_debug.xml`)
   **identik** dengan APK arsip sebelumnya. Hanya metadata kontainer zip yang membuat SHA berkas
   berbeda. Berkas arsip kini berisi build hari ini, dan SHA lama (`5db0c656…57fa`) tetap
   didokumentasikan di `LAPORAN-UJI-PENDAFTARAN.md`.
2. **APK di dalam repo tidak diganti** — `English-Quest-1.7.1-debug.apk` tetap
   `05e066414f83…bd86`. Menggantinya perlu izin Anda.

**Cara memasang dan mencoba (varian A):** salin APK ke HP → izinkan instal dari sumber tidak dikenal
→ pasang. Agar pendaftaran berhasil, jalankan `tools/jalankan-backend-uji.sh` lalu
`adb reverse tcp:8088 tcp:8088` sebelum menekan *Buat akun*.

## 9. Poin 9 — Kepatuhan batasan

Tidak ada deployment produksi, `main` tidak disentuh (dan di ruang kerja ini `.git` memang tidak
ada), tidak ada database pengguna yang dihapus (semua uji memakai database sementara di `/tmp`),
tidak ada kredensial produksi yang dipakai, APK repo tidak diganti, versi tidak dinaikkan, dan
signing key pihak lain tidak dibuat. Backend uji yang tadi hidup sudah dihentikan.

## 10. Poin 10 — Yang sudah diuji, dan yang **belum** (tanpa klaim berlebihan)

**Sudah diuji hari ini** (semua lolos):

- Backend: **65 tes** (`test_backend.py` 17, `test_failure_paths.py` 14, `test_payments.py` 34).
- Klien: `client_gates.test.js` **11 kelompok / 43 route**, `client_registration.test.js`
  **13 kelompok** (termasuk kelompok baru: tombol nonaktif tanpa server, tombol aktif + `minlength="6"`
  + label + toggle), `client_failure_paths.test.js`, `asset_parity.test.js` (**17 berkas** identik,
  cache `english-quest-shell-v19`), `node --check` untuk `app.js`/`access-client.js`/`sw.js`.
- Alur pendaftaran ujung-ke-ujung (bab 4 dan 5), penyimpanan aman, dan seluruh matriks konfigurasi.
- Build dua varian APK + verifikasi paket, tanda tangan, dan isi dex/aset.

**Belum diuji — dan karena itu tidak saya klaim berhasil:**

- **Pemasangan dan pendaftaran di HP/emulator sungguhan.** Lingkungan ini tidak menyediakan
  akselerasi emulator (`/dev/kvm` tidak ada), jadi yang diuji adalah kode klien yang sama persis
  (byte-for-byte) plus backend nyata — bukan layar sentuh sungguhan.
- **Server HTTPS publik** beserta reverse proxy-nya; belum ada domain, jadi belum ada yang diuji.
- Pembayaran DANA (di luar lingkup tugas ini) — tetap belum berfungsi dan belum ada kredensialnya.

## 11. Perubahan lokal yang belum di-commit (menunggu izin Anda)

1. 7 berkas hasil pemulihan dari branch `audit-baseline-1.7.1` (isi sama dengan GitHub).
2. 2 berkas overlay debug yang dibuat ulang (`src/debug/AndroidManifest.xml`,
   `src/debug/res/xml/eq_network_security_config_debug.xml`) — tidak ada di GitHub.
3. Kelompok uji ke-13 pada `tests/client_registration.test.js` (dipulihkan kembali; lulus).
4. Berkas bantu baru: `tools/repro-client.js`, `tools/repro-http.py`,
   `tools/jalankan-backend-uji.sh`, dan laporan ini.

Saya belum men-commit atau men-push apa pun. Bila Anda setuju, saya bisa menyiapkan commit di branch
terpisah (bukan `main`) — atau, karena ruang kerja ini tidak menyimpan `.git`, saya siapkan berkas
`.patch` yang bisa Anda terapkan sendiri.
