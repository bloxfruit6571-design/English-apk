# Laporan A–L: hosting, HTTPS, akun dan Premium

**15 Oktober 2026. Status: persiapan lokal; belum siap pendaftaran/pembayaran online.**
Tidak ada deployment, akun hosting, pembelian domain, aktivasi biaya atau transaksi nyata.
Laporan ini menggantikan kesimpulan status pada laporan lama; bukti lama tetap disimpan sebagai riwayat.

## A. Kondisi branch dan commit

Remote diperiksa langsung: `audit-baseline-1.7.1` =
`502ec4c59d1d59cb19c29b8ff528e07412f53252` (Clarify the branch state in the registration report).
`main` = `d05e4e6783131ea96734ce60889f05c1aa852305`; tidak diubah.

Workspace tidak memiliki `.git`; klon audit terpisah dibuat untuk menyiapkan perubahan berbasis
commit remote yang benar. Sebelum perubahan, source dan paket source lama dibandingkan.
Enam berkas Android workspace tertinggal (Java, Gradle, empat aset); dipulihkan dari remote,
dengan salinan sebelumnya disimpan di luar repositori. Dua overlay debug yang hilang dipulihkan
dari paket source yang checksum-nya lulus. Source backend dan pembayaran identik dengan remote.

**Push belum berhasil.** Izin push pengguna sudah diberikan, tetapi `git push --dry-run` gagal:
`could not read Username for 'https://github': terminal prompts disabled`.
Tidak ada credential helper terkonfigurasi dan tool konektor GitHub tidak mendukung sesi ini.
Commit/paket lokal disiapkan; ID commit final dan hasil percobaan push dicatat di berkas
serah-terima `STATUS-PUSH.md`. Tidak boleh menyatakan GitHub terbarui sebelum remote hash cocok.
Jangan mengirim token atau private key di percakapan; gunakan autentikasi Git di perangkat pemilik.

## B. Kondisi backend saat ini

Python `ThreadingHTTPServer` + SQLite; autentikasi dan kuota berasal dari server.
Password user minimal 6, maksimal 256; admin minimal 12. Password disimpan sebagai
PBKDF2-HMAC-SHA256 310.000 iterasi dengan salt acak 16 byte, bukan plaintext.
Sesi memakai token acak dengan hash server. Tidak ada autentikasi yang dinonaktifkan.
Access log handler dimatikan untuk menghindari kebocoran kredensial/URL; body dan header otorisasi
juga tidak boleh ditambahkan ke log proxy/platform.

`GET /api/health` mengembalikan penanda service/status tanpa rahasia; endpoint ini dilindungi TLS.
Ia bukan bukti menyeluruh bahwa disk/backup/DANA sehat. Health check ingress perlu sesuai topologi,
karena request HTTP tanpa header proxy tepercaya ditolak.

## C. Akar masalah pendaftaran yang masih tersisa

1. Belum ada backend HTTPS publik yang diberikan atau diverifikasi.
2. `EQ_API_BASE_URL` default kosong; APK tidak bisa mengetahui alamat server.
3. APK lama di root repo bukan build perbaikan pendaftaran; tidak diganti pada tugas ini.
4. Salinan Android workspace yang tertinggal telah dipulihkan, paritas dan bridge diuji ulang.
5. Konfigurasi VPS tidak langsung kompatibel dengan PaaS: backend menolak HTTP `0.0.0.0`;
   pemetaan port dan batas kepercayaan proxy harus disiapkan setelah penyedia dipilih.

## D. File diperiksa atau diubah

**Diperiksa:** `backend/server.py`, `backend/payments.py`, `access-client.js`, `app.js`,
`index.html`, Android `build.gradle`, `MainActivity.java`, manifest/aset, seluruh suite tests,
`deploy/*`, `tools/*`, laporan dan checksum paket serah-terima.

**Pemulihan (bukan implementasi ulang):** 6 berkas Android dari remote; 2 overlay debug-only
berasal dari paket terverifikasi. Tes pendaftaran ke-13 dipertahankan.

**Tambahan/perbaikan lokal:**
- `deploy/PILIHAN-HOSTING.md`: dokumentasi resmi, biaya, batas gratis dan kompatibilitas.
- `deploy/README-DEPLOY.md`: menjelaskan batas VPS/PaaS dan keputusan yang belum dibuat.
- `deploy/englishquest-api.service`: `ExecStartPre` memanggil pemeriksa konfigurasi tanpa self-test.
  Alasan: sebelumnya layanan dapat dijalankan tanpa pemeriksaan placeholder; sekarang konfigurasi
  contoh/tidak aman ditolak sebelum proses API dimulai.
- `deploy/env.produksi.example`: bedakan template publik dan salinan rahasia, koreksi perilaku
  `/api/payments/config` (tersedia dengan `available=false`, bukan seluruh endpoint selalu 503).
- `.gitignore`: tambah pengabaian berkas env terisi dan private TLS key.
- `tests/test_deployment.py`: tambah tes pengaman preflight.
- `evidence/hosting/*`, laporan ini, serta penanda status terbaru pada README/laporan historis.

Paket deployment lokal dari tahap sebelumnya ikut disiapkan untuk commit: backup/restore,
service/timer, Caddy/nginx, env template, checker, skrip build dan verifier. Tidak ada DB,
rahasia, cache build atau APK pengganti yang dimasukkan ke commit baru.

## E. Status HTTPS dan EQ_API_BASE_URL

| Variabel | Status/ketentuan |
|---|---|
| `EQ_API_BASE_URL` | Belum diisi origin publik; hanya ditanam saat build APK setelah server tersedia. |
| `EQ_TOKEN_PEPPER` | Tidak membuat rahasia produksi. Minimal 32 byte; operator membuat/menyimpan sekali secara aman, cadangan terenkripsi terpisah. |
| `EQ_DB_PATH` | Rencana VPS `/var/lib/english-quest/english-quest.sqlite3`; belum merupakan disk produksi nyata. PaaS harus memakai mount persistent volume. |
| `EQ_ALLOWED_ORIGINS` | Origin APK `https://appassets.androidplatform.net`; bukan wildcard. |
| `EQ_HOST`/`EQ_PORT` | Konfigurasi VPS `127.0.0.1:8088`, tidak dibuka publik. |
| `EQ_TRUST_PROXY` | `1` hanya bila ingress/proxy tepercaya mengganti forwarded headers dan backend tidak dapat dibypass. |
| `EQ_TLS_CERT_FILE`/`EQ_TLS_KEY_FILE` | Alternatif TLS langsung; nama sesuai source. Tidak aktif bersamaan dengan trust proxy pada template yang disarankan. |
| `EQ_ALLOW_INSECURE_DEV` | Tidak diaktifkan di produksi. |
| `EQ_ALLOW_HTTP_DEV` | Skrip release memaksa `0`; overlay cleartext hanya debug. |

Sertifikat/hostname publik, DNS, firewall, proxy nyata dan service manager **belum diuji**.
Template Caddy/nginx ditinjau, tetapi tidak dijalankan di hosting.

## F. Pilihan hosting dan biaya

Lihat **`deploy/PILIHAN-HOSTING.md`**, berdasarkan dokumentasi resmi yang dibaca pada audit ini.
Ringkasan:

- Render paid 512 MB US$7 + disk US$0,25/GB; contoh 1 GB = sekitar US$7,25/bulan sebelum tambahan.
  Free tidak mendukung disk persisten dan tidur setelah 15 menit. [1](https://render.com/pricing) [2](https://render.com/docs/free)
- Railway Hobby minimum US$5/bulan termasuk penggunaan US$5; kelebihan ditagih. Trial/Free
  terbatas kredit, bukan jaminan cukup untuk always-on. [3](https://docs.railway.com/pricing/plans) [4](https://docs.railway.com/pricing/free-trial)
- VPS DigitalOcean contoh 1 GiB/25 GiB SSD US$6/bulan; domain/backup/administrasi perlu direncanakan.
  [5](https://www.digitalocean.com/pricing/droplets)
- PythonAnywhere Free/Developer US$10 memiliki batas dan model WSGI, tidak langsung cocok dengan
  server saat ini. [6](https://www.pythonanywhere.com/pricing/) [7](https://help.pythonanywhere.com/pages/Flask)

Harga bukan total tagihan final, tidak dikonversi ke rupiah tanpa kurs nyata.
**Belum memilih atau mengaktifkan layanan.** Pilihan pengguna diperlukan sebelum adapter penyedia dibuat.

## G. Database persisten dan backup

Implementasi lokal memakai SQLite Backup API, integrity check, checksum SHA-256, nama unik,
retensi 14 snapshot, timer harian dan contoh cron. Restore hanya ke file staging baru, tidak
menimpa database aktif dan menolak WAL/SHM tujuan serta checksum hilang/tidak cocok.

Tes WAL hidup, rotasi, korupsi, backup hilang dan restore staging lulus. Tidak ada DB pengguna
dihapus; semua fixture memakai direktori sementara milik tes. Tidak ditemukan DB produksi
pada checkout yang diperiksa.

Yang belum ada: disk/volume produksi, destination off-site terenkripsi, jadwal yang benar-benar
aktif, pemantauan disk/backup, uji reboot/restore di host tujuan, dan target pemulihan yang disepakati.
Backup di disk yang sama saja tidak cukup. Secret pepper tetap dicadangkan terpisah.

## H. Sistem Premium dan DANA

Source mempertahankan demo 5 soal Easy; harga Rp5.000; pembelian sekali untuk akses permanen;
settlement terverifikasi memicu penerbitan token dan aktivasi normal tanpa persetujuan admin.
Kegagalan/pending/notifikasi tidak sah tidak boleh membuka Premium. Tes regresi tidak mengubah aturan ini.

**DANA produksi belum berfungsi.** Adapter memuat verifikasi signature, tetapi `create_checkout()`
masih menolak dengan `PROVIDER_NOT_READY`. Simulator lokal bukan pembayaran sungguhan.

DANA Hosted Checkout membutuhkan Merchant Portal/onboarding bisnis, testing credentials,
webhook dan redirect URL. Integrasi Create Order nyata serta pengujian notifikasi harus selesai
sebelum aktivasi. [8](https://dashboard.dana.id/api-docs-v2/guide/payment-gateway/hosted-checkout)
Biaya merchant dan minimum transaksi belum dikonfirmasi; tidak ada endpoint/checkout palsu,
kredensial merchant, transaksi nyata atau token Premium yang diberikan kepada pengguna tanpa verifikasi.

## I. Pengujian yang benar-benar dijalankan pada audit ini

- `python3 -m unittest discover -s tests -p 'test_*.py'`: **74 tes OK** (65 lama + 9 deployment).
- Node: pendaftaran **13 kelompok**, failure paths **8**, gates **11 / 43 route**, parity **17 berkas**.
- HTTPS lokal: **23 pemeriksaan lulus**. Daftar 6 karakter 201; login/me 200; password 5 karakter
  400 WEAK_PASSWORD; duplikat 409; CORS 204; HTTP tanpa TLS-proxy 400; klien gagal koneksi NETWORK_ERROR.
- Sertifikat uji lokal dipercaya eksplisit; verifikasi TLS tidak dimatikan. Tidak ada rahasia yang dicetak.
- Akun sintetis hanya fixture otomatis di DB sementara, bukan akun pelanggan atau bypass autentikasi.
- SHA-256 paket lama diverifikasi; source remote dibandingkan langsung sebelum pemulihan.
- Pemeriksaan push dry-run gagal karena autentikasi tidak tersedia, bukan bukti sinkronisasi GitHub.

Bukti audit ini: `evidence/hosting/python-tests.txt`, `node-tests.txt`, `https-local.txt`.
Bukti lama di folder evidence induk hanya riwayat; bukan hasil build baru pada audit ini.

## J. Status build APK

**Tidak membangun APK baru pada audit ini**, karena belum ada origin produksi yang benar.
Tidak mengganti APK rilis, menaikkan versi, membuat signing key atau mengunggah release.

Identitas source: release `com.englishquest.app`, versionName `1.7.1`, versionCode `13`;
debug memakai suffix `.debug`/`-debug`. `tools/build-apk-untuk-domain.sh` menolak origin lokal,
HTTP dan kredensial URL, memeriksa health HTTPS, paritas aset dan APK release.

Path/SHA-256 APK produksi baru: **belum ada**. Artefak uji-lokal terdahulu bukan APK online.
Setelah server tersedia dan disetujui, build dengan origin nyata, gunakan signing key pemilik,
catat hash final dan verifikasi signature. Pemasangan: `adb install -r <APK-final>` pada identitas
/signature yang cocok atau sideload; jangan uninstall/hapus data bila signature berbeda tanpa
persetujuan dan backup. Uji daftar/login/offline pada Wi-Fi dan data seluler tanpa adb reverse.
**Belum diuji pada HP Android nyata.**

## K. Tindakan yang diperlukan dari pengguna

1. Pilih anggaran/jalur hosting; pemilihan hanya izin menyiapkan konfigurasi, bukan membeli/deploy.
2. Sediakan akun hosting sendiri setelah keputusan, lalu origin publik yang benar. Jangan tempel rahasia.
3. Tentukan persistent disk/backup off-site, region data, serta batas biaya/kuota.
4. Untuk push: jalankan push melalui Git terautentikasi di perangkat pemilik, atau sediakan mekanisme
   autentikasi aman yang didukung lingkungan. Izin push saja tidak memberikan akses akun.
5. Setelah itu, persetujuan deployment, akun uji publik dan pengujian Android.
6. DANA: onboarding merchant, kredensial sandbox yang disimpan aman, kontrak biaya dan izin pengujian.

## L. Langkah berikutnya paling aman

Pilih hosting dahulu; siapkan adapter spesifik dan uji lokal tanpa melemahkan HTTPS; sinkronkan
commit yang sudah diuji ke branch audit dengan verifikasi remote. Setelah persetujuan terpisah,
deploy staging HTTPS, uji persistensi/backup dan alur akun; baru build APK dengan origin nyata,
signing dan uji HP. DANA tetap nonaktif sampai integrasi dan verifikasi resmi selesai.

| Tahap | Status |
|---|---|
| Source/perbaikan akun & Premium | Diterapkan; dipertahankan dan diuji lokal |
| Template deployment/backup/build guard | Diterapkan lokal; belum dipasang di server |
| HTTPS fixture / pendaftaran / login / kegagalan | Diuji lokal |
| Push ke GitHub | Belum berhasil; autentikasi terhalang |
| Server HTTPS publik / persistensi produksi | Belum dilakukan |
| HP Android nyata | Belum dilakukan |
| Checkout DANA / transaksi nyata | Belum dilakukan |
