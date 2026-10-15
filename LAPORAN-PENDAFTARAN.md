# Laporan Perbaikan Pendaftaran Akun English Quest

Tanggal: 15 Oktober 2026 · Branch: `audit-baseline-1.7.1` · Lingkungan: sandbox Linux (tanpa HP/emulator)

---

## 1. Ringkasan

Pendaftaran akun gagal karena **dua akar masalah yang berbeda**, bukan karena tampilan:

| # | Akar masalah | Dampak nyata pada pengguna | Status |
|---|---|---|---|
| 1 | Origin API tidak pernah sampai ke WebView: `access-client.js` meminta `EnglishQuestAndroid.getApiBaseUrl()`, tetapi `MainActivity` tidak punya metode itu | Di dalam APK, `isConfigured()` selalu `false` → tombol "Buat akun & lanjut" **disabled** dan muncul pesan "Server akun belum dikonfigurasi…". Pendaftaran mustahil walau APK dibangun dengan `EQ_API_BASE_URL` terisi. | **Diperbaiki** |
| 2 | Kebijakan kata sandi 12 karakter, sementara kontrak produk 6 karakter | Kata sandi 6–11 karakter selalu ditolak `400 WEAK_PASSWORD` ("Kata sandi harus 12–256 karakter.") | **Diperbaiki** |
| 3 | Tidak ada tombol lihat/sembunyikan kata sandi; email dan panjang kata sandi tidak divalidasi sebelum request | Salah ketik tidak terlihat, pengguna menunggu putaran jaringan untuk kesalahan sepele | **Diperbaiki** |

Perbaikan menyentuh klien web (`app.js`, `index.html`, `styles.css`), klien API (`access-client.js` **tidak diubah** — sudah benar meminta ke bridge), shell Android (`MainActivity.java`, `app/build.gradle`), server (`backend/server.py`), dan tes.

**Belum** dan tidak boleh diklaim selesai: pendaftaran belum diuji pada HP/emulator sungguhan, dan **belum ada URL backend produksi**. Selama `EQ_API_BASE_URL` kosong, tombol pendaftaran memang tetap nonaktif dengan pesan yang jelas — itu perilaku yang disengaja, bukan kegagalan yang tersisa.

---

## 2. Akar masalah #1 — rincian dan bukti

Jalur yang seharusnya bekerja (dan sekarang bekerja):

```
APK (BuildConfig.EQ_API_BASE_URL)
  → MainActivity.getApiBaseUrl()            [BARU: sebelumnya tidak ada]
  → jembatan JS window.EnglishQuestAndroid
  → access-client.js apiBase()              [sudah ada sebelumnya]
  → fetch(origin + /api/register)
  → backend/server.py Store.register()
```

Sebelum perbaikan, langkah 2 tidak ada, sehingga `apiBase()` jatuh ke cabang terakhir: origin halaman. Di dalam APK, origin halaman adalah `https://appassets.androidplatform.net` (WebViewAssetLoader), yang **sengaja** ditolak oleh klien sebagai origin API. Hasilnya `apiBase()` mengembalikan string kosong → `AccessApi.isConfigured()` `false` → `renderAccessAuth()` merender tombol dengan atribut `disabled` → tap tidak melakukan apa pun.

Bukti yang dikumpulkan sebelum mengubah kode:

- `grep -rn "EQ_API_BASE_URL|getApiBaseUrl|BuildConfig" app/src/` pada source Android: **tidak ada hasil** untuk `getApiBaseUrl`, sementara `access-client.js` memanggilnya.
- `access-client.js` baris 6–9: `const bridge = nativeBridge(); if (bridge && typeof bridge.getApiBaseUrl === 'function') …`.
- `index.html` baris 15: hanya menyalakan `EQ_ALLOW_HTTP_DEV` berdasarkan `location.protocol === 'http:'`, yang di APK selalu `https:`.

Perbaikan:

- `MainActivity.java` — menambah dua metode bridge:
  - `getApiBaseUrl()` → `BuildConfig.EQ_API_BASE_URL` (string kosong bila build memang tanpa server);
  - `isHttpDevAllowed()` → `BuildConfig.DEBUG && "1".equals(BuildConfig.EQ_ALLOW_HTTP_DEV)`; **release build selalu `false`**, jadi build yang dibagikan ke pengguna tidak bisa diturunkan ke HTTP biasa.
- `app/build.gradle` — field baru `BuildConfig.EQ_ALLOW_HTTP_DEV` dari `-PEQ_ALLOW_HTTP_DEV` atau environment variable.
- `index.html` — `EQ_ALLOW_HTTP_DEV` kini juga bertanya ke bridge (`isHttpDevAllowed()`), sehingga build debug dapat menunjuk server loopback (uji di HP via `adb reverse`).

Catatan penting: **memasang URL produksi tetap wajib** untuk pendaftaran nyata. Baris perintahnya:

```sh
bash ./gradlew --no-daemon :app:assembleDebug -PEQ_API_BASE_URL=https://api.domain-anda.example
```

Tidak ada URL, endpoint, atau kredensial yang dikarang di dalam source/APK.

---

## 3. Akar masalah #2 — kebijakan kata sandi

Sebelum: klien `minlength="12"` + label "(minimal 12 karakter)", server menolak `< 12` dengan `400 WEAK_PASSWORD` "Kata sandi harus 12–256 karakter." Setiap pengguna yang mengikuti kontrak 6 karakter pasti gagal.

Sesudah (server adalah otoritas, klien hanya mencerminkan agar pesan bisa muncul seketika):

- `backend/server.py`: `PASSWORD_MIN_LENGTH = 6`, `PASSWORD_MAX_LENGTH = 256` (satu tempat, dipakai `register`), pesan menjadi "Kata sandi harus 6–256 karakter."; akun admin yang dibuat offline tetap `ADMIN_PASSWORD_MIN_LENGTH = 12`.
- `app.js`: `const PASSWORD_MIN_LENGTH=6,PASSWORD_MAX_LENGTH=256` + validasi email dan panjang kata sandi sebelum request, dengan pesan berbahasa Indonesia yang menyebut jumlah karakter yang kurang.
- Login tidak berubah: kata sandi lama apa pun tetap bisa dipakai (tidak ada reset paksa).

---

## 4. Gap #3 — tombol lihat/sembunyikan dan validasi klien

- Tombol `data-action="toggle-password-visibility"` ditambahkan di samping kolom kata sandi. Ia **mengubah DOM secara langsung, bukan re-render**, sehingga teks yang sudah diketik tidak hilang; label, `aria-label`, dan `aria-pressed` ikut diperbarui. Kata sandi tidak pernah disalin ke `state` atau penyimpanan.
- Validasi klien: format email (mencerminkan `EMAIL_RE` server) dan panjang kata sandi (6–256) — kesalahan sepele ditolak lokal, tanpa memanggil jaringan, sedangkan keputusan akhir tetap di server.
- `styles.css`: gaya `.access-password-row` untuk input + tombol.
- Service worker `CACHE_NAME` dinaikkan `v18 → v19` dan salinan aset Android disinkronkan (17/17 byte-identik).

---

## 5. Hasil tes (semua dijalankan ulang setelah perubahan)

| Suite | Hasil |
|---|---|
| `python3 -m unittest discover -s tests` | **65 tes OK** (naik 1: kontrak pendaftaran 6 karakter, hash PBKDF2, login, duplikat, email invalid) |
| `node tests/client_gates.test.js` | 11 kelompok lulus, 43 renderer di-smoke-test |
| `node tests/client_failure_paths.test.js` | 8 kelompok lulus |
| `node tests/asset_parity.test.js` | 17 file identik root ↔ aset Android (cache `v19`) |
| `node tests/client_registration.test.js` (baru) | **12 kelompok lulus** |

Cakupan `tests/client_registration.test.js` (yang diminta pada butir 7):

1. pendaftaran sukses (sesi tersimpan, akun baru **bukan** Premium, kuota demo 5 utuh);
2. kata sandi 5 karakter ditolak `WEAK_PASSWORD` (klien + server, pesan menyebut minimal 6);
3. email tidak valid ditolak `INVALID_EMAIL` (server) dan diblokir lebih awal di klien;
4. akun sudah ada → `ACCOUNT_EXISTS` dengan pesan server;
5. backend tidak tersedia → `NETWORK_ERROR`, pengguna tetap di formulir, tidak ada hak akses yang terbuka;
6. server menggantung → `NETWORK_TIMEOUT` (< 4 detik, tidak membeku);
7. respons server tidak valid → `INVALID_SERVER_RESPONSE`;
8. **bridge Android**: tanpa `getApiBaseUrl()` klien melaporkan `API_NOT_CONFIGURED` (merekam kegagalan asli APK); dengan bridge, pendaftaran benar-benar mengirim ke origin tersebut dan mendapat `200`;
9. validasi lokal + tombol lihat/sembunyikan (teks yang diketik tidak hilang, `aria-pressed` benar);
10. pendaftaran lewat formulir asli lalu login kembali dengan kredensial yang sama berhasil;
11. kegagalan server menampilkan pesan dan tidak menaikkan status akun;
12. **end-to-end dengan `backend/server.py` asli**: daftar dengan kata sandi 6 karakter → `/api/me` benar → kata sandi 5 karakter ditolak → duplikat ditolak → login dari sesi baru (seperti instal ulang) berhasil → kata sandi salah ditolak → **kata sandi tidak ditemukan sebagai teks biasa di berkas database**.

Tidak ada tes lama yang dilonggarkan atau dihapus. Satu assertion tes lama disesuaikan hanya agar toleran terhadap nilai kosong vs `undefined` (bukan perubahan aturan).

Catatan: hasil uji ini **tidak** memakai akun, database, atau kredensial produksi; semua kata sandi pengujian adalah string sintetis sekali pakai, dan tes tidak pernah mencetak kata sandi.

---

## 6. Hasil build APK

Dua build dari source yang sama (version code **13**, `1.7.1-debug`, package `com.englishquest.app.debug`, min API 23 / target 35, sertifikat debug lama yang sama: `C:US, O:Android, CN=Android Debug`, SHA-256 `7659afc5…50a3`).

| Build | Perintah | Ukuran | SHA-256 | Catatan |
|---|---|---|---|---|
| **A. APK biasa** (deliverable) | `:app:assembleDebug` | 84.919.309 B | `67b4707227f61a6bff8f5abb8254faf3b1f976a84b8ecb5d6e526a829fcd0dfe` | `EQ_API_BASE_URL=""` (tanpa URL karangan) |
| **B. APK uji lokal** | `:app:assembleDebug -PEQ_API_BASE_URL=http://127.0.0.1:8088 -PEQ_ALLOW_HTTP_DEV=1` | 84.945.530 B | `44f560a692c70e7501edb955096cf0540de712e81ae388a335c4260c0006e0e5` | khusus menguji pendaftaran di HP dengan `adb reverse` |

Keduanya **BUILD SUCCESSFUL** dan diverifikasi isinya: `assets/app.js` memuat `PASSWORD_MIN_LENGTH=6` dan `toggle-password-visibility`, `assets/index.html` membaca `isHttpDevAllowed`, service worker `v19`, dan dex memuat `getApiBaseUrl`, `isHttpDevAllowed`, `EQ_API_BASE_URL`, serta `EQ_ALLOW_HTTP_DEV`. Model Vosk diverifikasi SHA-256 saat build (`30f26242…42498`, 41.205.931 B) dan ikut terbundel.

**Artefak yang disimpan:** build **B** disalin ke `/home/user/artefak-apk/English-Quest-1.7.1-register-test-localtest-debug.apk` beserta `.sha256`. APK lama `English-Quest-1.7.1-demo5-premium-debug.apk` (sha256 `f3605181…7f1f0`) dihapus setelah digantikan build ini, mengikuti kebijakan repository yang sudah ada (APK dapat dihasilkan ulang dari source).

Cara menguji pendaftaran di HP dengan build B:

```sh
# 1. Jalankan backend di komputer (loopback, data contoh untuk pengembangan)
cd backend
export EQ_ALLOW_INSECURE_DEV=1 EQ_ALLOWED_ORIGINS="https://appassets.androidplatform.net"
export EQ_DB_PATH=./data/dev.sqlite3 EQ_TOKEN_PEPPER="ganti-dengan-pepper-dev-minimal-32-byte"
python3 server.py            # mendengarkan 127.0.0.1:8088
# 2. Sambungkan HP via USB dan balikkan port
adb reverse tcp:8088 tcp:8088
# 3. Pasang APK B, lalu daftar akun di aplikasi
```

**Batasan:** build A tidak dapat mendaftar sampai operator mengisi `EQ_API_BASE_URL` ke origin HTTPS miliknya (disengaja; tidak ada URL produksi di repo). Build B hanya untuk pengujian lokal dan tidak boleh dibagikan ke pengguna.

---

## 7. Konfigurasi yang masih dibutuhkan

1. **`EQ_API_BASE_URL`** — origin HTTPS publik tempat `backend/server.py` dilayani. Tanpa ini pendaftaran/login/Premium tidak bisa hidup di APK. Tidak diisi karena tidak ada domain resmi pada repository ini.
2. **Hosting HTTPS + reverse proxy** — backend harus dijalankan di belakang TLS (`EQ_TRUST_PROXY=1`) atau memakai `EQ_TLS_CERT_FILE`/`EQ_TLS_KEY_FILE`.
3. **`EQ_ALLOWED_ORIGINS`** — berisi origin PWA dan `https://appassets.androidplatform.net` agar APK lintas-origin diizinkan.
4. **`EQ_TOKEN_PEPPER`** (≥32 byte, dari secret manager) dan **direktori SQLite persisten + backup**.
5. **Akun admin** lewat `python3 backend/manage.py create-admin` (kata sandi admin tetap minimal 12 karakter).
6. **Uji perangkat nyata**: pasang APK, daftar, login, dan instal ulang — belum dijalankan di sini karena tidak ada HP/emulator di lingkungan ini.

---

## 8. Yang sudah diuji vs belum diuji

**Sudah diuji:** seluruh jalur kode klien dan server untuk pendaftaran (validasi, pemetaan error, timeout, respons rusak), bridge Android (unit-level melalui VM), backend asli end-to-end di loopback, hash PBKDF2 dan penyimpanan database, login setelah pendaftaran, serta kedua build APK memuat perbaikan (inspeksi isi APK).

**Belum diuji:** instalasi dan penggunaan di HP/emulator sungguhan (menekan tombol di layar sentuh, WebView asli, jaringan seluler), serta pendaftaran terhadap server HTTPS publik — keduanya butuh perangkat/domain yang belum tersedia di sini.

---

## 9. Status repository dan langkah GitHub

- Riwayat git lokal dipulihkan dari `/home/user/english-quest-audit-baseline-1.7.1.bundle` (berisi `main` + branch audit hingga `c2aac64`) dan digabung dengan salinan publik `github.com/bloxfruit6571-design/English-apk`, lalu hasil kerja sekarang di-commit pada branch `audit-baseline-1.7.1`. Branch `main` tidak disentuh.
- **Push ke GitHub belum bisa dijalankan dari sandbox ini**: tidak ada `~/.git-credentials`, tidak ada `gh` CLI, tidak ada token di environment, dan konektor GitHub Arena tidak tersedia. Push memerlukan kredensial Anda (token akses dengan scope repo), atau Anda dapat menarik dari repo lokal ini lalu push sendiri.
- Tidak ada branch/commit yang dipaksakan ke `main`, dan tidak ada deployment yang dijalankan.

---

## 10. Pembersihan file sampah

Yang dihapus dan alasannya (tidak ada berkas source, laporan, atau APK deliverable yang hilang):

| Item | Alasan |
|---|---|
| `english-quest-android/app/build/` (~291 MB) | Keluaran Gradle yang selalu dapat dihasilkan ulang (`gradlew`); model Vosk diunduh kembali saat build dan diverifikasi SHA-256 |
| `english-quest-android/.gradle/` (504 KB) | Cache Gradle lokal |
| `__pycache__/`, `.pytest_cache/` | Cache tes Python (sudah masuk `.gitignore`) |
| APK uji lama `English-Quest-1.7.1-demo5-premium-debug.apk` + `.sha256` (83 MB) | Digantikan build yang lebih baru (berisi perbaikan pendaftaran); dapat dihasilkan kembali dari source |
| Berkas sementara di `/tmp` (repo percobaan, log build) | Tidak diperlukan lagi |

APK di dalam repository (`English-Quest-1.7.1-debug.apk`) **tidak** diubah maupun dihapus.
