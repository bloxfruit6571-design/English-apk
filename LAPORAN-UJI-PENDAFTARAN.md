> **Catatan historis:** angka/hash/status dalam laporan ini merekam tahap sebelumnya.
> Status terkini ada di [Laporan Hosting dan Premium](LAPORAN-HOSTING-PREMIUM.md).

# Laporan Uji: Backend Pendaftaran, EQ_API_BASE_URL, dan APK

Tanggal: 15 Oktober 2026 · Branch: `audit-baseline-1.7.1` (source = commit `502ec4c` di GitHub)
Lingkungan: sandbox Linux. **Tidak ada HP/emulator di sini**, jadi belum ada klaim "berhasil di HP".

---

## 1. Ringkasan jawaban singkat

| Pertanyaan | Jawaban sebenarnya |
|---|---|
| Apakah `EQ_API_BASE_URL` sudah dikonfigurasi? | **Belum berisi URL produksi.** Di source, nilainya default kosong dan mengalir: `build.gradle` → `BuildConfig.EQ_API_BASE_URL` → `MainActivity.apiBaseUrl()` → bridge `EnglishQuestAndroid.getApiBaseUrl()` → `access-client.js`. Tidak ada URL yang dikarang. |
| Apakah backend pendaftaran bisa dijalankan? | **Ya, sudah dijalankan dan diuji di sini** (loopback, database sementara di `/tmp`): register → `201`, `/api/me` → `200`, login → `200`, kata sandi 5 karakter → `400 WEAK_PASSWORD`, duplikat → `409 ACCOUNT_EXISTS`. |
| Apakah pendaftaran sudah bisa dari APK sekarang? | Hanya jika APK dibangun dengan `EQ_API_BASE_URL` yang menunjuk server yang benar-benar hidup. **Belum diuji di HP**, jadi belum saya klaim berfungsi di HP. |
| Apakah alamat backend masuk ke APK? | **Ya, bisa diverifikasi.** Build uji-lokal di APK: `EQ_API_BASE_URL = "http://127.0.0.1:8088"`, `EQ_ALLOW_HTTP_DEV = "1"` (dibaca langsung dari `BuildConfig.java` hasil build). Build tanpa URL: string loopback tidak ada di dex. |
| Temuan baru yang memblokir uji di HP | Manifest utama memasang `android:usesCleartextTraffic="false"` → permintaan HTTP ke `127.0.0.1` **diblokir Android** (ERR_CLEARTEXT_NOT_PERMITTED) sebelum sampai ke server. Sudah diperbaiki untuk build **debug saja** (detail di bagian 5). |

---

## 2. Konfigurasi backend dan jalur `EQ_API_BASE_URL`

### 2.1 Di sisi Android (APK)

```
english-quest-android/app/build.gradle
  -PEQ_API_BASE_URL / env EQ_API_BASE_URL   →  BuildConfig.EQ_API_BASE_URL   (default "")
  -PEQ_ALLOW_HTTP_DEV / env EQ_ALLOW_HTTP_DEV → BuildConfig.EQ_ALLOW_HTTP_DEV (default "")

MainActivity.getApiBaseUrl()      → mengembalikan BuildConfig.EQ_API_BASE_URL ke JavaScript
MainActivity.isHttpDevAllowed()   → true HANYA bila BuildConfig.DEBUG dan flag dev = "1"

access-client.js → apiBase()
  1) window.EQ_API_BASE (bila PWA dipisah dari API)
  2) bridge EnglishQuestAndroid.getApiBaseUrl()
  3) origin halaman — DITOLAK bila origin aset WebView (appassets.androidplatform.net)
  → kosong berarti isConfigured() = false
```

Bukti di APK (dari `app/build/generated/source/buildConfig/debug/com/englishquest/app/BuildConfig.java`):

```
public static final String EQ_ALLOW_HTTP_DEV = "1";                       // hanya build uji-lokal
public static final String EQ_API_BASE_URL = "http://127.0.0.1:8088";     // hanya build uji-lokal
```

Pada build biasa (tanpa properti), keduanya `""` dan **string loopback tidak ditemukan di dex** (diverifikasi).

### 2.2 Di sisi server (backend)

Variabel yang benar-benar dipakai `backend/server.py`:

| Variabel | Gunanya |
|---|---|
| `EQ_HOST`, `EQ_PORT` | alamat dengar (default `127.0.0.1:8088`) |
| `EQ_DB_PATH` | lokasi file SQLite |
| `EQ_TOKEN_PEPPER` | pepper ≥32 byte untuk HMAC token/sesi (**wajib**, kalau kosong server menolak start) |
| `EQ_ALLOW_INSECURE_DEV=1` | izin HTTP **loopback** untuk pengembangan (wajib bila tanpa TLS) |
| `EQ_TRUST_PROXY=1` | bila berada di belakang reverse proxy TLS |
| `EQ_TLS_CERT_FILE`, `EQ_TLS_KEY_FILE` | TLS langsung |
| `EQ_ALLOWED_ORIGINS` | daftar origin CORS; **harus memuat `https://appassets.androidplatform.net`** agar WebView APK boleh membaca respons |
| `EQ_PAYMENT_*`, `EQ_DANA_*` | pembayaran Premium (tidak dipakai untuk pendaftaran) |

Bukti server menolak jalan tanpa konfigurasi yang benar (tanpa TLS, tanpa flag dev):

```
$ python3 server.py
Configuration error: EQ_TOKEN_PEPPER must contain at least 32 bytes of random secret material.
exit=2
```

---

## 3. Bukti backend benar-benar berjalan (smoke test nyata)

Dijalankan: `EQ_ALLOW_INSECURE_DEV=1 EQ_HOST=127.0.0.1 EQ_PORT=8088 EQ_DB_PATH=/tmp/eq-smoke/registration-smoke.sqlite3 EQ_TOKEN_PEPPER=<acak 32 byte> EQ_ALLOWED_ORIGINS=https://appassets.androidplatform.net python3 backend/server.py`

Database yang dipakai adalah file sementara di `/tmp` (bukan database pengguna, dan tidak ada database pengguna di repo ini). Kata sandi pada uji ini **sintetis** (`ab12cd` / `ab12c`) dan tidak pernah ditampilkan di log.

| # | Permintaan | Hasil |
|---|---|---|
| 1 | `GET /api/health` | **200**, `ok: true` |
| 2 | `OPTIONS /api/register` (Origin: appassets) | **204**, `Access-Control-Allow-Origin: https://appassets.androidplatform.net`, methods `GET, POST, OPTIONS` |
| 3 | `POST /api/register` (kata sandi 6 karakter) | **201**, `ok: true`, `session_token` ada, ACAO benar |
| 4 | `GET /api/me` (Bearer) | **200**, email sesuai, `premium: false`, `demo_remaining: 5` |
| 5 | `POST /api/login` (kredensial sama) | **200**, `ok: true` |
| 6 | `POST /api/register` (kata sandi 5 karakter) | **400**, `WEAK_PASSWORD`, "Kata sandi harus 6–256 karakter." |
| 7 | `POST /api/register` (email yang sama lagi) | **409**, `ACCOUNT_EXISTS`, "Akun dengan email tersebut sudah ada. Silakan masuk." |
| 8 | `GET /` | **200**, `text/html`, memuat `id="app"` (PWA + API satu origin) |

Artinya: **endpoint pendaftaran bisa dijalankan dan dijangkau**; akun baru tersimpan (128-bit salt + PBKDF2-HMAC-SHA256), bukan Premium, dan bisa login ulang.

---

## 4. Cara menjalankan backend dan menghubungkan HP Android

### 4.1 Jalankan backend (di komputer)

```sh
cd backend
export EQ_ALLOW_INSECURE_DEV=1
export EQ_HOST=127.0.0.1
export EQ_PORT=8088
export EQ_DB_PATH=./data/dev.sqlite3
export EQ_TOKEN_PEPPER="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
export EQ_ALLOWED_ORIGINS="https://appassets.androidplatform.net"
python3 server.py
# → English Quest API listening on 127.0.0.1:8088
```

Catatan: `EQ_TOKEN_PEPPER` harus sama setiap kali server dijalankan ulang, kalau tidak sesi/token lama tidak dapat ditemukan lagi. Untuk menguji halaman PWA dari browser: buka `http://127.0.0.1:8088`.

### 4.2 Hubungkan HP ke backend

```sh
# HP tersambung USB, USB debugging aktif
adb reverse tcp:8088 tcp:8088
# cek: adb reverse --list  →  tcp:8088 tcp:8088
```

Setelah itu, alamat `127.0.0.1:8088` **di dalam HP** menunjuk ke komputer Anda. Inilah sebabnya APK uji memakai `http://127.0.0.1:8088`, bukan IP LAN: klien hanya menerima HTTP untuk loopback (`localhost`/`127.0.0.1`/`::1`), sehingga IP LAN seperti `192.168.x.x` sengaja ditolak dan harus memakai HTTPS.

### 4.3 Untuk server asli (bukan loopback)

Backend harus dilayani lewat HTTPS (TLS langsung dengan `EQ_TLS_CERT_FILE`/`EQ_TLS_KEY_FILE`, atau di belakang reverse proxy dengan `EQ_TRUST_PROXY=1`), lalu APK dibangun dengan origin tersebut:

```sh
bash ./gradlew --no-daemon :app:assembleDebug -PEQ_API_BASE_URL=https://api.domain-anda.example
```

**Jangan** memakai `-PEQ_ALLOW_HTTP_DEV=1` pada build yang diberikan ke pengguna: flag itu hanya untuk pengujian loopback dan diabaikan oleh release build.

---

## 5. Temuan baru: HTTP loopback diblokir Android (dan perbaikannya)

`app/src/main/AndroidManifest.xml` memuat `android:usesCleartextTraffic="false"`. Pada Android modern (targetSdk 35 / API 24+), artinya **semua** permintaan plain-HTTP diblokir — termasuk `http://127.0.0.1:8088`. Gejalanya menyesatkan: seolah "server tidak dapat dihubungi" padahal permintaan tidak pernah keluar dari aplikasi.

Perbaikan (khusus build debug):

| Berkas baru | Isi |
|---|---|
| `english-quest-android/app/src/debug/AndroidManifest.xml` | overlay manifest: `networkSecurityConfig` + `usesCleartextTraffic="true"` (dengan `tools:replace`) |
| `english-quest-android/app/src/debug/res/xml/eq_network_security_config_debug.xml` | `base-config cleartextTrafficPermitted="false"`, satu `domain-config` `true` untuk **`127.0.0.1`** dan **`localhost`** saja |

Bukti bahwa rilis tidak terpengaruh:

- Berkasnya berada di source set `src/debug` → hanya masuk build debug.
- Manifest release hasil merge (`:app:processReleaseMainManifest`) tetap `usesCleartextTraffic="false"` **tanpa** `networkSecurityConfig`.
- Isi NSC di APK debug: `cleartextTrafficPermitted=false` (base) + satu domain-config `true` untuk 2 host loopback — tidak ada LAN/publik.

---

## 6. Hasil build APK (dari source terbaru, 15 Oktober 2026)

Keduanya `BUILD SUCCESSFUL`, `versionCode 13`, `versionName 1.7.1-debug`, `applicationId com.englishquest.app.debug`, `minSdk 23` / `targetSdk 35`, sertifikat debug `CN=Android Debug` (SHA-256 `7659afc5…50a3`, sama seperti sebelumnya), 17 aset web cocok byte-for-byte dengan repo, service worker `english-quest-shell-v19`.

| Varian | Perintah | Ukuran | SHA-256 |
|---|---|---|---|
| **A. Build biasa (tanpa URL)** | `:app:assembleDebug` | 84.920.148 B | `01b0f054ee0e1be839937adb0258c18407cf0a18be1a6fe55a7737432fcfd6c2` |
| **B. Build uji-lokal (disimpan)** | `:app:assembleDebug -PEQ_API_BASE_URL=http://127.0.0.1:8088 -PEQ_ALLOW_HTTP_DEV=1` | 84.946.369 B | `5db0c656b98c7c7e96919c4c9c2555533a2551f16f5f28539cb73f67ec2357fa` |

**Lokasi APK yang disimpan:**

```
/home/user/artefak-apk/English-Quest-1.7.1-register-test-localtest-debug.apk
/home/user/artefak-apk/English-Quest-1.7.1-register-test-localtest-debug.apk.sha256
```

Build A tidak disimpan (85 MB) karena dapat dihasilkan ulang dengan perintah di tabel; hash-nya dicatat di sini. **APK di dalam repository (`English-Quest-1.7.1-debug.apk`) tidak diubah.**

Isi yang diverifikasi pada kedua APK: metode bridge `getApiBaseUrl` + `isHttpDevAllowed` ada di `classes.dex`; `assets/app.js` memuat `PASSWORD_MIN_LENGTH=6` dan `toggle-password-visibility`; `assets/index.html` membaca flag dari bridge; NSC debug ada di `res/xml/`. Pada build B, string `http://127.0.0.1:8088` ada di dex; pada build A tidak ada.

---

## 7. Langkah menguji pendaftaran dari HP Android

1. Jalankan backend seperti bagian 4.1 (biarkan terminal tetap hidup).
2. `adb devices` (pastikan HP terdeteksi), lalu `adb reverse tcp:8088 tcp:8088`.
3. Pasang APK uji-lokal: `adb install -r /home/user/artefak-apk/English-Quest-1.7.1-register-test-localtest-debug.apk` (atau salin ke HP dan buka).
4. Buka aplikasi → **Masuk / Buat Akun** → tab **Daftar akun**.
   - Tombol **Buat akun & lanjut** harus **aktif** (tidak abu-abu) karena APK ini punya alamat server.
   - Tekan ikon **👁️ Lihat** di kolom kata sandi: teks harus terlihat, lalu **🙈 Sembunyikan** mengembalikannya tanpa menghapus yang sudah diketik.
5. Isi email (mis. `uji.hp@example.test`) dan kata sandi **minimal 6 karakter**, tekan **Buat akun & lanjut**.
   - Berhasil → kembali ke beranda dengan status akun terverifikasi, kuota demo 5 soal.
   - Coba juga kata sandi 5 karakter → harus muncul pesan "Kata sandi minimal 6 karakter…" **sebelum** dikirim.
   - Daftar dua kali dengan email sama → pesan "Akun dengan email tersebut sudah ada. Silakan masuk."
6. Uji kondisi server mati: hentikan backend (Ctrl+C) lalu tekan **Buat akun & lanjut** lagi → harus muncul pesan "Tidak dapat terhubung ke server. Periksa internet, lalu coba lagi." (atau pesan batas waktu bila server menggantung), dan aplikasi tidak macet.
7. Di komputer, cek akun yang dibuat tercatat (mis. `sqlite3 backend/data/dev.sqlite3 "select email,created_at from users;"`) — kolom kata sandi berisi salt + hash, bukan teks biasa.
8. Untuk pemasangan di HP lain atau tanpa USB: aktifkan `adb tcpip 5555` / gunakan `adb -s <serial>`, atau layani backend lewat HTTPS dan bangun APK dengan `-PEQ_API_BASE_URL=https://…`.

---

## 8. Tombol "Buat Akun", pesan kesalahan, kata sandi

- **Tombol aktif hanya saat konfigurasi benar.** `renderAccessAuth()` memberi atribut `disabled` bila `state.accessBusy` atau `!AccessApi.isConfigured()`. `isConfigured()` bernilai benar hanya bila ada origin API yang valid (bridge/`window.EQ_API_BASE`), tidak menerima origin aset WebView. Bila belum dikonfigurasi, halaman juga menampilkan "Server akun belum dikonfigurasi. Demo lokal tetap bisa digunakan tanpa login; pendaftaran, sinkronisasi akun, dan Premium perlu server HTTPS."
- **Pesan kesalahan jelas** untuk tiap kondisi: `NETWORK_ERROR` (server tak terjangkau), `NETWORK_TIMEOUT` (server menggantung, dibatalkan setelah 15 detik), `INVALID_SERVER_RESPONSE` (respons bukan JSON), `WEAK_PASSWORD`/`INVALID_EMAIL`/`ACCOUNT_EXISTS` dari server ditampilkan apa adanya.
- **Kata sandi tetap minimal 6 karakter** pada klien (`PASSWORD_MIN_LENGTH=6`, `minlength="6"`, label "minimal 6 karakter") **dan** server (`PASSWORD_MIN_LENGTH = 6`, `PASSWORD_MAX_LENGTH = 256`, pesan "Kata sandi harus 6–256 karakter."). Akun admin yang dibuat offline tetap 12 karakter.
- **Tombol lihat/sembunyikan** bekerja dengan mengubah node DOM secara langsung sehingga teks yang sudah diketik tidak hilang; juga memperbarui `aria-label`/`aria-pressed`.

---

## 9. Hasil tes (dijalankan pada source terbaru)

| Suite | Hasil sebenarnya |
|---|---|
| `python3 -m unittest discover -s tests` | **65 tes OK** (19,5 detik) |
| `node tests/client_registration.test.js` | **13 kelompok lulus** (naik dari 12: tambahan pengujian tombol & markup kata sandi) |
| `node tests/client_gates.test.js` | 11 kelompok lulus, 43 renderer di-smoke-test |
| `node tests/client_failure_paths.test.js` | 8 kelompok lulus |
| `node tests/asset_parity.test.js` | 17 berkas identik, cache `v19` |
| Sintaks | `node --check app.js` OK, `server.py` OK, XML source set debug valid |

Grup 13 yang baru ditambahkan memastikan: tanpa konfigurasi server tombol **disabled** + alasan tampil; dengan konfigurasi tombol **tidak** disabled, `minlength="6"` dan label "minimal 6 karakter" ada, serta tombol lihat/sembunyikan ikut dirender.

---

## 10. Yang belum ada / belum diuji (jujur)

1. **Belum diuji di HP/emulator** — tidak ada perangkat di lingkungan ini. Semua yang di atas adalah uji kode, APK, dan server di mesin ini.
2. **Belum ada URL backend produksi** dan tidak ada hosting HTTPS. Sampai itu ada, pendaftaran online untuk pengguna belum bisa dinyatakan berfungsi.
3. **Database pengguna tidak disentuh** (tidak ada database di repo; uji memakai file sementara di `/tmp`), tanpa deployment produksi, tanpa kredensial produksi, dan `main` tidak diubah.
4. APK di dalam repository masih build lama (demo "10 soal"); menggantinya perlu persetujuan Anda karena menyangkut rilis.

## 11. Perubahan lokal yang belum ada di GitHub

Karena push terakhir memakai token yang sudah Anda cabut, perubahan sesi ini **belum terkirim** dan masih tersimpan di workspace:

- `english-quest-android/app/src/debug/AndroidManifest.xml` (baru)
- `english-quest-android/app/src/debug/res/xml/eq_network_security_config_debug.xml` (baru)
- `tests/client_registration.test.js` (tambahan grup 13)
- `LAPORAN-UJI-PENDAFTARAN.md` (laporan ini)

Beri tahu saya bila ingin dikirim ke branch `audit-baseline-1.7.1` (bukan `main`), dan saya akan siapkan commit untuk di-push.

---

## Pembaruan 15 Oktober 2026 (tugas lanjutan)

- Sumber diverifikasi ulang langsung dari GitHub: tip branch `audit-baseline-1.7.1` adalah
  **`502ec4c`**; 7 berkas ruang kerja dipulihkan dari klon tersebut.
- APK varian uji-lokal **dibangun ulang** dari sumber terverifikasi. Isi paket identik 137/137 entri
  dengan build sebelumnya (hanya metadata zip yang berbeda), sehingga SHA-256 berkas berubah:
  **`b891f33dd0056958880b8969795b4ee3ddbf2bccc2c7ca2c3cf88e50b3faa6d6`** (84.920.154 byte).
  SHA lama `5db0c656b98c7c7e96919c4c9c2555533a2551f16f5f28539cb73f67ec2357fa` tidak lagi dipakai;
  tabel pada bab APK di atas merujuk versi lama. Berkas `.sha256` di folder `artefak-apk/` sudah
  diperbarui dan lolos `sha256sum -c`.
- Varian polos (tanpa `-P`) dibangun sebagai pembanding: SHA-256
  `a7d52288f2c09bf45c3aa3bf50b2988db32dc63e1f696df5733c23f173250519` (84.946.366 byte), tidak
  diarsipkan.
- Akar masalah aktual APK repo, hasil reproduksi klien/server, dan matriks konfigurasi lengkap ada di
  `LAPORAN-AKAR-MASALAH-PENDAFTARAN.md`.
