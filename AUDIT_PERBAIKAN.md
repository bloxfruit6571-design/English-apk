# AUDIT & PERBAIKAN — English Quest 1.7.1

**Tanggal:** 15 Oktober 2026
**Objek:** repo `bloxfruit6571-design/English-apk` @ `d05e4e6` (English Quest 1.7.1)
**Metode:** audit source code, reproduksi bug dengan probe, perbaikan minimal, regresi test.
**Checkpoint Git:** branch `audit-baseline-1.7.1`, commit `6bd40a4` (belum di-push).
**Bukti mentah:** folder `/home/user/evidence/` (`probe-output.txt` = sebelum, `probe-after-fix.txt` = sesudah).

---

## 1. Ringkasan

**Sebelum:** aplikasi sudah tertata rapi (pemisahan frontend/Android/backend baik, premium fail-closed, tanpa tracker, izin Android minimal), tetapi ada **satu bug keamanan yang membuat proteksi brute-force tidak berfungsi sama sekali**, satu bug keandalan yang bisa membuat aplikasi **macet permanen** tanpa internet yang menggantung, dan beberapa masalah ketahanan (HTTP handler, housekeeping database, siklus hidup WebView).

**Sesudah:** keempat masalah inti diperbaiki dengan perubahan kecil dan beralasan, ditambah **25 pengujian baru** (14 backend + 8 klien + 1 parity aset) yang mengunci perilaku perbaikan tersebut. Seluruh pengujian lama **tetap lulus tanpa diubah**.

| Aspek | Sebelum | Sesudah |
|---|---|---|
| Rate limit login | 20 percobaan gagal → **0 baris counter**, tidak pernah 429 | 429 tepat pada percobaan ke-13 (limit 12/15 menit) |
| Klien vs server menggantung | `health()` **tidak pernah selesai** (>5 detik, UI macet) | selesai **705 ms** dengan `NETWORK_TIMEOUT`, fallback demo lokal |
| Content-Type POST | `text/plain` diterima → **201**, akun terbuat | ditolak **415** `UNSUPPORTED_MEDIA_TYPE` |
| HEAD request | **404** untuk semua | **200** (header saja) untuk file allowlist, 404 untuk sisanya |
| Sesi & rate-limit table | tumbuh tanpa batas | dipangkas otomatis (sesi mati >7 hari, bucket >2 hari) |
| WebView renderer mati | aplikasi ikut mati | UI dibangun ulang (maks 2×), lalu `finish()` dengan pesan |
| Test | 16 backend + 10 grup klien | 30 backend + 10 + 8 grup klien + parity aset |
| Build Android | belum pernah dijalankan | ✅ **BUILD SUCCESSFUL** — APK 84.916.010 byte, terverifikasi (lihat §5) |

---

## 2. Daftar masalah

### KRITIS

**K-1 — Rate limit tidak efektif pada semua percobaan yang gagal (brute force tanpa batas)**
- **Tingkat:** KRITIS (keamanan)
- **Reproduksi:** 20× `POST /api/login` dengan kata sandi salah → 20× `401`, tabel `rate_limits` **kosong**. Bandingkan: 1× login **berhasil** → langsung muncul baris counter. Artinya yang dihitung justru percobaan yang berhasil, bukan yang gagal.
- **Akar penyebab:** `server.py` memanggil `_rate_limit(...)` **di dalam** transaksi `BEGIN IMMEDIATE`. Setiap kegagalan (login salah, registrasi duplikat, klaim token gagal) melempar `ApiError`, lalu `except Exception: db.rollback()` **ikut membatalkan increment counter**. Limit 12/900 detik, 5/900 detik, dan 8/900 detik pada dasarnya tidak pernah tercapai.
- **Dampak:** penyerang bisa mencoba kata sandi akun tanpa batas (hanya dibatasi 310.000 iterasi PBKDF2 per percobaan). Registrasi massal dan penebakan token Premium juga tidak terbatas.
- **Status:** ✅ **DIPERBAIKI** — `_rate_limit_standalone()` menghitung percobaan di transaksi terpisah yang di-commit lebih dulu.

### TINGGI

**T-1 — Klien tanpa timeout: aplikasi bisa macet permanen**
- **Tingkat:** TINGGI (keandalan)
- **Reproduksi:** server yang menerima koneksi TCP tetapi tidak pernah menjawab → `EnglishQuestAccess.health()` **tidak pernah resolve** (>5 detik, diuji); di aplikasi nyata layar berhenti di "Memeriksa akun…" dan di APK WebView halaman tidak pernah selesai.
- **Akar penyebab:** `access-client.js` memanggil `fetch()` tanpa `AbortController`/timeout apa pun.
- **Dampak:** pengguna tidak bisa masuk, tidak mendapat pesan error, dan tidak bisa memakai demo offline — padahal aplikasi ini dirancang offline-first.
- **Status:** ✅ **DIPERBAIKI** — abort setelah 15 detik (`window.EQ_REQUEST_TIMEOUT_MS`), kode baru `NETWORK_TIMEOUT`, dan kode itu dimasukkan ke daftar kegagalan yang bisa dipulihkan di `app.js` sehingga otomatis jatuh ke demo lokal (Premium tetap terkunci).

### SEDANG

**S-1 — Tabel `sessions` dan `rate_limits` tumbuh tanpa batas**
- **Akar penyebab:** hanya ada `INSERT`/`UPDATE`; tidak ada pembersihan. `_expire_tokens()` hanya menandai token kedaluwarsa.
- **Dampak:** pada deployment jangka panjang, file SQLite membengkak dan pencarian sesi melambat.
- **Status:** ✅ DIPERBAIKI — `_prune_stale_rows()` memanggil hapus sesi mati > 7 hari dan bucket rate-limit > 2 hari. Sesi yang masih hidup **tidak** tersentuh (diuji).

**S-2 — HTTP handler tanpa socket timeout**
- **Akar penyebab:** `Handler` tidak menetapkan `timeout`, sehingga klien yang membuka koneksi lalu diam menahan worker thread selamanya (`ThreadingHTTPServer` membuat thread per koneksi).
- **Dampak:** puluhan koneksi menggantung = server tidak bisa melayani siapa pun.
- **Status:** ✅ DIPERBAIKI — `timeout = 60` (per operasi socket, jadi transfer lambat tapi berjalan normal tetap aman) + pengujian bahwa koneksi menggantung diputus.

**S-3 — Renderer WebView mati ⇒ aplikasi ikut mati**
- **Akar penyebab:** `WebViewClient` tidak meng-override `onRenderProcessGone`. Di API 26+ perilaku default adalah mematikan proses aplikasi.
- **Dampak:** aplikasi "crash" tanpa sebab jelas saat memori perangkat menipis, dan posisi belajar pengguna hilang.
- **Status:** ✅ DIPERBAIKI (kode) — handler membangun ulang UI dengan **anggaran restart 2×** (guard `static`, agar tidak loop) lalu `finish()` dengan pesan. `rendererRestartCount` di-reset setiap halaman selesai dimuat. **BELUM DIUJI di perangkat/emulator.**

**S-4 — Service worker bisa menyajikan aset lama selamanya**
- **Akar penyebab:** `sw.js` memakai strategi *cache-first* untuk aset non-navigasi, dan `CACHE_NAME` tidak pernah dinaikkan saat `app.js`/`access-client.js` berubah.
- **Dampak:** perbaikan klien tidak sampai ke pengguna yang sudah memasang PWA.
- **Status:** ✅ DIPERBAIKI — `CACHE_NAME` `english-quest-shell-v15` → `v16`; pengujian baru akan gagal bila versi tidak dinaikkan lagi di masa depan.

**S-5 — Klaim "17 aset cocok byte-for-byte" tidak ditegakkan apa pun**
- **Akar penyebab:** tidak ada test yang membandingkan aset root dengan `english-quest-android/.../assets/`.
- **Dampak:** perbaikan bisa masuk ke PWA tetapi tidak ke APK (atau sebaliknya) tanpa terdeteksi.
- **Status:** ✅ DIPERBAIKI — `tests/asset_parity.test.js` memverifikasi 17 file identik + konsistensi versi cache.

### RENDAH

**R-1 — POST menerima Content-Type apa pun** (form HTML cross-site bisa kirim `text/plain` tanpa preflight CORS). ✅ DIPERBAIKI → `415` bila ada body dan bukan `application/json`. Dashboard admin & klien sudah mengirim JSON, jadi tidak ada yang rusak (diuji).

**R-2 — `HEAD` selalu 404** (monitor/link-checker menganggap layanan mati). ✅ DIPERBAIKI → header yang sama dengan GET, tanpa body, tetap memakai allowlist yang sama.

**R-3 — Tidak ada pengujian jalur gagal** (hanya jalur sukses). ✅ DIPERBAIKI → 25 pengujian baru.

### Diverifikasi AMAN — sengaja TIDAK diubah

| Yang diperiksa | Hasil |
|---|---|
| Path traversal (`/../`, `/backend/server.py`, `/backend/data/`, `/tests/…`) | 404 — allowlist + `relative_to(ROOT)` bekerja |
| Endpoint admin untuk akun biasa | ditolak oleh `require_admin` di server (bukan di UI) |
| Premium dari server rusak/timeout/401/500 | **tidak pernah** diberikan — fail-closed (diuji klien & server) |
| Token plaintext | hanya pada respons pembuatan; DB menyimpan HMAC-SHA-256 |
| Sesi kedaluwarsa / dicabut | ditolak 401 `SESSION_EXPIRED`; 401 di klien menghapus sesi tersimpan |
| Rahasia | `EQ_TOKEN_PEPPER` dari environment, tidak ada di source/APK/repo |
| Password | PBKDF2-HMAC-SHA-256 310.000 iterasi + salt unik + `compare_digest` |
| Tracker/analytics | tidak ada sama sekali |
| `/api/*` tidak dikenal oleh pengguna anonim | 401 seragam — **sengaja dipertahankan** agar penyerang tidak bisa memetakan endpoint (kalau diubah ke 404, justru membocorkan daftar endpoint) |

---

## 3. Perubahan source code

| Berkas | Perubahan | Alasan | Dampak |
|---|---|---|---|
| `backend/server.py` | `_rate_limit_standalone()` baru; 6 pemanggilan rate limit dipindah ke luar transaksi bisnis (register, login, guest demo, demo sync, demo consume, activate) | K-1 | Limit benar-benar berlaku; limit & angka batas tidak diubah |
| `backend/server.py` | `_prune_stale_rows()` dipanggil dari `_expire_tokens()`; konstanta `SESSION_RETENTION_DAYS=7`, `RATE_LIMIT_RETENTION_SECONDS=172800` | S-1 | Hanya baris mati yang dihapus; tanpa perubahan skema |
| `backend/server.py` | `Handler.timeout = 60` | S-2 | Koneksi menggantung diputus; transfer lambat tetap jalan |
| `backend/server.py` | Validasi `Content-Type: application/json` pada body POST (415) | R-1 | Hanya memengaruhi klien yang mengirim tipe salah |
| `backend/server.py` | `_public_file()` + `_send_public_file()`; `do_GET`/`do_HEAD` memakai satu allowlist | R-2 | HEAD tidak lagi 404; tidak ada file yang jadi bisa diakses |
| `access-client.js` (+ salinan Android) | Timeout 15 dtk via `AbortController`, kode `NETWORK_TIMEOUT`, `clearTimeout` di `finally`; mundur ke perilaku lama bila `AbortController` tak ada | T-1 | UI tidak bisa macet; tidak ada perubahan pada jalur sukses |
| `app.js` (+ salinan Android) | `NETWORK_TIMEOUT` ditambahkan ke `isRecoverableServerFailure` | T-1 | Timeout → demo lokal terbatas, Premium tetap terkunci |
| `sw.js` (+ salinan Android) | `CACHE_NAME` → `english-quest-shell-v16` | S-4 | PWA mengambil aset baru |
| `MainActivity.java` | Import `RenderProcessGoneDetail`/`Toast`; `onRenderProcessGone` + `MAX_RENDERER_RESTARTS=2` + `static rendererRestartCount`; reset di `onPageFinished` | S-3 | Proses tidak lagi mati; restart dibatasi |
| `tests/test_failure_paths.py` (baru) | 14 tes jalur gagal | R-3 | Mengunci K-1, S-1, S-2, R-1, R-2 |
| `tests/client_failure_paths.test.js` (baru) | 8 grup tes klien dengan server HTTP nyata | T-1 | Mengunci timeout + fail-closed |
| `tests/asset_parity.test.js` (baru) | Parity 17 aset + versi cache | S-4, S-5 | Mencegah drift source ↔ APK |

Tidak ada file yang dihapus. Tidak ada perubahan pada: identitas aplikasi, `applicationId`, nama paket, struktur navigasi, jumlah/isi materi, aturan bisnis Premium (harga, masa berlaku, jumlah token), skema database, atau signing.

---

## 4. Hasil pengujian

| Suite | Perintah | Hasil |
|---|---|---|
| Backend (asli) | `python3 -m unittest discover -s tests -v` | ✅ 16/16 lolos (baseline juga 16/16) |
| Backend (baru) | idem | ✅ 14/14 lolos → **total 30 lolos, 0 gagal, 0 dilewati** (13,8 dtk) |
| Client gates (asli) | `node tests/client_gates.test.js` | ✅ 10 grup + 43 renderer |
| Client failure paths (baru) | `node tests/client_failure_paths.test.js` | ✅ 8/8 grup |
| Parity aset (baru) | `node tests/asset_parity.test.js` | ✅ 17 file identik, cache v16 |
| Sintaks | `node --check` (9 file) + `python3 -m py_compile` (3 file) | ✅ semua lolos |
| Build Android | `bash ./gradlew --no-daemon :app:assembleDebug` | ✅ **BUILD SUCCESSFUL** (43 dtk, JDK 17 + SDK 35) — APK terverifikasi (§5) |
| Uji perangkat | instalasi, mikrofon, Vosk, TTS, renderer restart | ⛔ **BELUM DIUJI** (tidak ada perangkat/emulator) |

**Bukti sebelum vs sesudah (probe yang sama, handler asli):**

```
                                    SEBELUM                                  SESUDAH
POST /api/register (text/plain)  -> 201 (akun terbuat)                   -> 415
login gagal 15×                  -> semua 401, 429: NEVER               -> 429 pada percobaan 13
HEAD /                           -> 404                                  -> 200 (header saja)
klien vs server menggantung      -> hang > 5 detik                       -> selesai 705 ms (NETWORK_TIMEOUT)
```

Tidak ada test lama yang diubah agar lulus. Semua tes lama dijalankan ulang setelah perbaikan dan tetap hijau.

---

## 5. Hasil build

**BUILD SUCCESSFUL — APK berhasil dibangun ulang dan diverifikasi.**

Toolchain yang saya pasang untuk pengujian (di `/var/tmp/eq`, di luar repositori agar tidak mengotori project):

| Komponen | Versi |
|---|---|
| JDK | Temurin **17.0.20.1** (sesuai persyaratan AGP 8.7.3) |
| Gradle | 8.9 (via wrapper project) |
| Android SDK Platform | **android-35** |
| Build-Tools | **35.0.0** + platform-tools |
| Perintah | `bash ./gradlew --no-daemon :app:assembleDebug` → **BUILD SUCCESSFUL in 43s** (35 task) |

**Artefak:** `english-quest-android/app/build/outputs/apk/debug/app-debug.apk`

| Properti | Nilai |
|---|---|
| Ukuran | 84.916.010 byte |
| SHA-256 | `ce4fc3ed7e22bbb1b20a39729db50e69b069d5862afa4b41b9d2f43d0a36a933` |
| Package / versi | `com.englishquest.app.debug` · versionCode 13 · `1.7.1-debug` |
| minSdk / targetSdk | 23 / 35 |
| Penandatanganan | sertifikat **Android Debug** (`CN=Android Debug`) — APK debug, bukan rilis |

**Verifikasi isi APK hasil build:**

- **17/17 aset web identik byte-for-byte** dengan source yang sudah diperbaiki.
- Perbaikan **benar-benar ada di dalam APK**: `access-client.js` memuat `AbortController`, `DEFAULT_TIMEOUT_MS = 15000`, dan `NETWORK_TIMEOUT`; `sw.js` memakai `english-quest-shell-**v16**`; `classes*.dex` memuat `onRenderProcessGone` serta teks Toast yang baru → perubahan `MainActivity.java` ikut terkompilasi.
- Model Vosk di dalam APK: 41.205.931 byte, SHA-256 `30f26242…42498` — **cocok dengan pin di `build.gradle`**.
- Native libs lengkap untuk 4 ABI (arm64-v8a, armeabi-v7a, x86, x86_64).

**Catatan penting — kompilasi menemukan bug di kode saya sendiri.** Build pertama gagal dengan 2 error nyata: `Toast.makeText(this, …)` di dalam anonymous `WebViewClient` — `this` menunjuk ke client, bukan Activity. Sudah diperbaiki menjadi `MainActivity.this`, lalu build ulang sukses. Ini contoh mengapa klaim "berhasil build" tanpa benar-benar menjalankan build tidak dapat dipercaya.

**APK di repo tidak diubah:** `English-Quest-1.7.1-debug.apk` (85.079.299 byte, SHA-256 `05e06641…bbd86`) **masih APK lama**. Mengganti artefak rilis yang sudah didokumentasikan README memerlukan persetujuan Anda (§8).

**Keterbatasan yang jujur:**

- APK baru berada di direktori `build/`, yang **tidak ikut tersimpan** dalam snapshot workspace ini. Untuk menyimpannya permanen silakan pilih di §8. Perintah build ulang ada di Lampiran.
- Toolchain (`/var/tmp/eq`) juga tidak permanen dan perlu dipasang ulang bila lingkungan di-reset.
- Sesuai keputusan Anda, `versionCode`/`versionName` **belum dinaikkan** (tetap 13 / `1.7.1-debug`).
- Yang **BELUM DIUJI**: pemasangan di perangkat/emulator, mikrofon, Vosk, TTS, dan pemulihan renderer. Tidak ada perangkat di lingkungan ini.

---

## 6. Keamanan

**Risiko yang sudah ditangani:** K-1 (brute force login/registrasi/klaim token), S-2 (koneksi menggantung), R-1 (POST lintas situs bertipe salah), S-1 (pertumbuhan data), T-1 (kegagalan jaringan tidak lagi bisa membuat aplikasi tampak "menunggu" tanpa akhir, dan tidak pernah dianggap sukses).

**Risiko yang masih tersisa (jujur):**

1. **APK belum di-build ulang** → perbaikan belum ada di APK yang beredar.
2. **Belum ada pengujian perangkat nyata** → mikrofon, Vosk, TTS, dan pemulihan renderer **belum terbukti berjalan**.
3. **Backend belum di-deploy** → HTTPS, TLS, reverse proxy, `EQ_TRUST_PROXY`, dan CORS produksi belum pernah diuji di lingkungan nyata.
4. **Rate limit berbasis alamat IP** bergantung pada `EQ_TRUST_PROXY`/`X-Forwarded-For` yang diatur benar oleh proxy; bila salah, penyerang bisa memalsukan sumber IP.
5. **Demo lokal tetap tidak tahan manipulasi** (diakui penulis; `localStorage` dapat dihapus). Ini konsekuensi desain offline-first, **tidak** saya ubah.
6. **Sesi disimpan di `localStorage`** (bukan cookie httpOnly) — konsekuensi arsitektur WebView; XSS akan berdampak pada sesi. Tidak ada XSS yang ditemukan dalam audit ini.
7. **SQLite/satu node**, tanpa replikasi; `EQ_TOKEN_PEPPER` harus di-backup (kalau berubah, token belum diklaim tak dapat ditemukan lagi).
8. **Tidak ada CI** → regresi hanya tertangkap bila tes dijalankan manual.
9. **APK debug + `debuggable`** dan tanpa file LICENSE → tidak layak untuk distribusi publik.

**Klaim keamanan:** pengujian otomatis lulus **bukan** bukti aplikasi bebas celah. Audit ini terbatas pada kode yang tersedia dan lingkungan lokal.

---

## 7. Konfigurasi yang masih dibutuhkan (tidak ada nilai yang dikarang)

| Konfigurasi | Status | Catatan |
|---|---|---|
| `EQ_API_BASE_URL` (Android, saat build) | **kosong** | origin publik HTTPS; diberikan via `-PEQ_API_BASE_URL=https://…` atau env |
| `window.EQ_API_BASE` (PWA, bila API beda origin) | **belum diset** | diset sebelum `access-client.js` |
| `EQ_TOKEN_PEPPER` | **wajib**, ≥32 byte acak | dari secret manager; jangan di source/APK/log |
| `EQ_DB_PATH` + backup | belum ada | direktori persisten + backup terjadwal |
| `EQ_ALLOWED_ORIGINS` | belum diset | origin PWA + `https://appassets.androidplatform.net` |
| TLS: `EQ_TLS_CERT_FILE`/`EQ_TLS_KEY_FILE` **atau** `EQ_TRUST_PROXY=1` + reverse proxy | belum ada | hanya loopback yang boleh tanpa TLS |
| Akun admin | belum dibuat | `python3 backend/manage.py create-admin --email …` |
| `EQ_REQUEST_TIMEOUT_MS` (baru, opsional) | default 15.000 ms | bisa diperbesar untuk jaringan sangat lambat |
| Keystore rilis | belum ada | release saat ini tanpa signingConfig (debug-signed) |

---

## 8. Persetujuan yang diperlukan

Perbaikan di atas **tidak menyentuh** database pengguna, skema, sistem autentikasi dasar, aturan Premium, identitas paket, atau signing — semuanya perubahan aman. Yang berikut **memerlukan keputusan Anda**:

1. **Rebuild APK** — perlu JDK 17 + Android SDK 35 (tidak ada di lingkungan ini). Pilih: (a) saya coba siapkan toolchain-nya, (b) Anda build sendiri dengan `bash ./gradlew --no-daemon :app:assembleDebug`, atau (c) tunda.
2. **Push commit ke GitHub?** Perbaikan saat ini **hanya lokal** di branch `audit-baseline-1.7.1` dan belum di-push. Mengunggah ke layanan eksternal butuh izin Anda.
3. **Naikkan versi aplikasi** (`versionCode` 13 → 14, `versionName` 1.7.2) bila akan dirilis? Ini mengubah identitas rilis → butuh persetujuan.
4. **Deploy backend produksi** (domain, HTTPS, hosting, pepper) — butuh keputusan dan kredensial dari Anda; saya tidak akan melakukannya sendiri.
1. **APK baru berada di direktori `build/`** yang tidak tersimpan permanen di snapshot workspace ini (lihat §5) — beri tahu saya bila ingin saya tempatkan di lokasi yang tersimpan, atau menggantikan APK di repo (memerlukan persetujuan karena artefak rilis & hash di README ikut berubah).
2. **Push ke GitHub belum bisa saya lakukan**: lingkungan ini tidak memiliki kredensial git sama sekali (`git push --dry-run` → `could not read Username for 'https://github.com'`). Opsi: (a) Anda memberi token akses dengan cakupan minimum (sebaiknya di-cabut setelah push), (b) Anda push sendiri dari salinan workspace ini ke branch `audit-baseline-1.7.1`, atau (c) saya siapkan file patch (`.patch`) yang bisa Anda terapkan. **Tidak ada kode, data, atau rahasia yang saya unggah ke layanan eksternal tanpa persetujuan.**

Berikutnya, keputusan lama yang masih terbuka:

3. **Menghapus atau mengganti database pengguna** — tidak diperlukan; saya tidak melakukannya.
4. **Mengubah skema database** — tidak dilakukan. Pruning (§3) hanya menghapus baris sesi/bucket yang sudah mati, tanpa mengubah skema.
5. **Rilis & distribusi**: bila APK baru hendak diedarkan, apakah `versionCode` 13 → 14 dan `versionName` 1.7.1 → 1.7.2 (butuh persetujuan, karena mengubah identitas rilis)?
6. **Deploy backend produksi** (domain, HTTPS, hosting, pepper) — saya tidak akan melakukannya tanpa keputusan dan kredensial dari Anda.
7. **Signing rilis**: APK saat ini masih ditandatangani debug key. Pembuatan keystore rilis harus Anda lakukan; jangan pernah menaruh kunci di repo.
8. **Memperkuat demo lokal** agar lebih tahan manipulasi (pengikatan per perangkat) — mengubah perilaku fitur, butuh persetujuan; tidak bisa menjamin pencegahan total.

---

## 9. Rekomendasi tahap berikutnya

1. **Build & uji di perangkat**: siapkan JDK 17 + Android SDK 35, jalankan `assembleDebug`, pasang di HP, lalu uji mikrofon/Vosk/TTS dan matikan paksa proses renderer untuk memverifikasi `onRenderProcessGone`. (Menutup S-3 dan risiko #2.)
2. **Integrasi & uji beban ringan backend** di staging: login gagal berulang (harus 429), klaim token bersamaan, restart server, backup/restore SQLite.
3. **Tambahkan CI** (GitHub Actions) yang menjalankan 3 suite tes ini pada setiap push, sehingga regresi tertangkap otomatis.
4. **Deploy produksi** dengan checklist §7 (HTTPS, pepper di secret manager, `EQ_ALLOWED_ORIGINS`, reverse proxy + `EQ_TRUST_PROXY`, monitoring, backup).
5. **Naikkan versi + buat rilis bertag** setelah diuji, dengan keystore rilis milik Anda.
6. **Baru kemudian** pertimbangkan pengerasan lanjutan (token binding per perangkat, batas berbasis akun bukan IP, CSP lebih ketat tanpa `unsafe-inline`).

---

### Lampiran — cara mereproduksi hasil audit

```sh
# Semua pengujian
cd /home/user/english-quest
python3 -m unittest discover -s tests -v        # 30 lolos
node tests/client_gates.test.js                 # 10 grup
node tests/client_failure_paths.test.js         # 8 grup
node tests/asset_parity.test.js                 # 17 file identik

# Bukti sebelum/sesudah (handler asli)
PYTHONPATH=. python3 /home/user/evidence/probe_failures.py   # bandingkan dengan probe-output.txt
```

Perbedaan branch: `git diff d05e4e6 6bd40a4` (8 file diubah, 3 file tes baru, 0 file dihapus).

# Build ulang APK (toolchain harus tersedia lagi; tidak ikut tersimpan)
export JAVA_HOME=/var/tmp/eq/jdk17
export ANDROID_HOME=/var/tmp/eq/android
export GRADLE_USER_HOME=/var/tmp/eq/gradle-home
cd english-quest-android && bash ./gradlew --no-daemon :app:assembleDebug
# -> app/build/outputs/apk/debug/app-debug.apk

