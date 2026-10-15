> **Catatan historis:** angka/hash/status dalam laporan ini merekam tahap sebelumnya.
> Status terkini ada di [Laporan Hosting dan Premium](LAPORAN-HOSTING-PREMIUM.md).

# English Quest — hasil persiapan backend HTTPS

**15 Oktober 2026 · Status: belum siap untuk pendaftaran online dari HP.**
Belum ada origin backend publik yang diberikan/diverifikasi, belum ada deployment,
dan belum ada uji Android nyata. Keberhasilan lokal tidak menggantikan ketiga syarat ini.

## 1. Source dan branch yang dipakai

Tip remote diperiksa langsung, bukan diasumsikan dari laporan:
`audit-baseline-1.7.1` → `502ec4c59d1d59cb19c29b8ff528e07412f53252`.
Bukti: `evidence/branch-tip.txt`.

Salinan workspace pada awal pekerjaan tertinggal di beberapa berkas Android: Java bridge,
flag build dan empat aset web. Dipulihkan dari source kanonik yang telah dibandingkan.
Perbaikan pendaftaran tidak ditulis ulang. `backend/server.py` tetap identik dengan branch.
`getApiBaseUrl()`/`isHttpDevAllowed()` ada; paritas 17 aset sekarang lulus. Uji pendaftaran
ke-13 dan overlay debug-only lokal dipertahankan. Versi dan identitas aplikasi tidak dinaikkan.

Berkas deployment belum ada di remote; tidak ada commit/push atau perubahan `main`.
Patch diperbarui dari basis tersebut, dan paket source tanpa APK/cache disediakan untuk
memudahkan pemindahan serta menghindari ketergantungan pada folder build yang tidak persisten.

## 2. Yang sudah disiapkan

| Kebutuhan | Hasil lokal |
|---|---|
| HTTPS | Template Caddy dan nginx; backend di loopback, TLS di proxy. Alternatif TLS langsung didokumentasikan. |
| Lingkungan | `deploy/env.produksi.example`, pemeriksa konfigurasi dan layanan systemd. Nama TLS yang benar: `EQ_TLS_CERT_FILE`/`EQ_TLS_KEY_FILE`. |
| Rahasia | Pepper minimal 32 byte; template tidak berisi rahasia nyata. Rahasia server tidak masuk APK. |
| Persistensi | Rencana `/var/lib/english-quest/english-quest.sqlite3`, di luar folder rilis; izin ketat. Volume/server nyata masih harus disiapkan. |
| Backup | SQLite Backup API untuk DB hidup/WAL, integrity check, checksum, retensi 14 snapshot, timer harian dan contoh cron. |
| Restore | Hanya ke berkas staging baru, checksum wajib; menolak penimpaan DB aktif. Pergantian DB tetap tindakan operator setelah persetujuan dan penghentian layanan. |
| Perlindungan proxy | X-Forwarded-For diganti dari IP koneksi; hanya API publik diteruskan. Panel dan API admin ditutup di proxy publik. |
| Build domain | `tools/build-apk-untuk-domain.sh`: HTTPS publik, health read-only, paritas aset, release saja, `EQ_ALLOW_HTTP_DEV=0`. |
| Verifikasi APK | Memeriksa bridge, kebijakan 6 karakter, manifest dan NSC debug. Menolak tanpa origin atau bila alat pemeriksa manifest tidak tersedia. |

Panduan operasional dan kebutuhan persetujuan: **`deploy/README-DEPLOY.md`**.
Tidak ada pengadaan hosting/domain, penerbitan sertifikat publik, layanan berbayar,
penggunaan kredensial produksi, perubahan database pengguna, atau deployment.

## 3. Bukti pengujian

### HTTPS lokal: 23 pemeriksaan lulus

`evidence/uji-https-lokal.txt` merekam fixture TLS langsung dan proxy TLS buatan lokal.
Database, pepper dan sertifikat semuanya sintetis serta terpisah dari data pengguna.
Sertifikat lokal dipercaya secara eksplisit oleh Python/Node; verifikasi hostname/rantai
sertifikat tidak dimatikan. HTTP dev nonaktif pada uji klien HTTPS.

- Daftar kata sandi 6 karakter → **201**.
- Baca akun melalui sesi dan login ulang → **200**.
- Kata sandi 5 karakter → **400 WEAK_PASSWORD**.
- Email duplikat → **409 ACCOUNT_EXISTS**.
- OPTIONS → **204**, CORS sesuai origin WebView.
- Backend proxy tanpa penanda HTTPS → **400 HTTPS_REQUIRED**.
- Kode klien asli melalui HTTPS → daftar/login berhasil; kegagalan koneksi → **NETWORK_ERROR**.

**Batas:** ini bukan pengujian nginx/Caddy yang terpasang, sertifikat publik, domain,
firewall produksi, atau WebView/layar HP nyata. Skrip pemeriksa membaca konfigurasi tetapi
fixture-nya sengaja menggunakan rahasia dan sertifikat uji sendiri, bukan produksi.

### Regresi seluruh suite

- **73 tes Python lulus**: 65 tes lama + 8 tes deployment baru.
- **13 kelompok pendaftaran**, **8 failure paths**, **11 gates / 43 route** lulus.
- **17 berkas aset identik**.
- Backup diuji saat WAL aktif; restore staging mempertahankan baris; korupsi/checksum hilang,
  tujuan aktif dan pemulihan tanpa konfirmasi ditolak; rotasi dan nama unik diuji.
- Input URL lokal/HTTP/contoh/kredensial ditolak skrip build sebelum kontak jaringan.

Bukti: `evidence/uji-python.txt`, `evidence/uji-node.txt`.
Template service/proxy belum dijalankan atau divalidasi oleh systemd/nginx/Caddy pada host tujuan.
Tidak ada klaim bahwa timer, off-site backup, disk persisten setelah reboot atau kapasitas beban
sudah berfungsi pada server produksi.

## 4. APK: hasil inspeksi, bukan rilis online

Build release inspeksi berhasil (`evidence/build-release.log`). Tidak ada origin palsu ditanam.

| Properti | Release inspeksi tanpa origin |
|---|---|
| Lokasi sementara | `english-quest-android/app/build/outputs/apk/release/app-release-unsigned.apk` |
| Ukuran | 84.366.598 byte |
| SHA-256 | `e9d64ec75c4f5e81adcb371947c07d7cde7448fbbbf02dbf63749a7d6631c30f` |
| applicationId | `com.englishquest.app` |
| versionName / versionCode | `1.7.1` / `13` |
| Signing | Unsigned; tidak untuk dipasang/dibagikan |
| HTTP lokal | `usesCleartextTraffic=false`, tanpa NSC debug dan tanpa alamat loopback di dex |
| Bridge/kata sandi | Kedua metode bridge ada; kebijakan 6 karakter ada |
| Kesiapan | **Ditolak verifier karena belum ada origin** |

Folder build bersifat sementara dan tidak termasuk paket source. Bukti inspeksi disimpan
pada `evidence/apk-release-tanpa-origin.txt`. Ini bukan APK produksi selesai.

Arsip **uji-lokal debug** diperbarui selama pemeriksaan:
`/home/user/artefak-apk/English-Quest-1.7.1-register-test-localtest-debug.apk`.
Ukuran **84.975.122 byte**, SHA-256
`5223ac7f2974bc2591aefa8275119de2c4d612f009a029754c2a8b4a9b806004`.
Identitas `com.englishquest.app.debug`, `1.7.1-debug`, kode `13`.
APK ini memakai loopback/adb reverse dan pengecualian cleartext debug; **bukan APK online**.
Hash di laporan sesi sebelumnya bersifat historis. APK lama di root repo tidak diganti.

Setelah origin nyata tersedia: build release dengan origin tersebut, tandatangani dengan
key milik pemilik yang disetujui, verifikasi hash/signing final, lalu uji di HP tanpa adb reverse.
Tidak ada signing key baru dibuat oleh pekerjaan ini.

## 5. Tindakan yang masih diperlukan dari Anda

1. Pilih: sudah mempunyai hosting/domain atau ingin panduan memilihnya. Sebutkan penyedia,
   OS dan domain publik bila tersedia—**jangan kirim rahasia**.
2. Setujui lingkup deployment dan biaya terlebih dahulu, jika ada.
3. Siapkan disk persisten, lokasi backup di luar server dan penyimpanan pepper yang aman.
4. Tentukan cara menerapkan perubahan: paket source/patch lokal, atau izin push branch terpisah.
5. Setelah HTTPS tersedia, beri izin membuat akun uji dan uji restart/backup staging pada server itu.
6. Sediakan HP pengujian dan keputusan signing untuk APK final.

**Gerbang selesai:** HTTPS publik dapat dijangkau dan dipercaya HP; CORS benar; daftar/login,
password minimum 6, tombol mata dan offline diuji dari APK final lewat Wi-Fi/data seluler;
data tetap ada setelah restart; backup/restore staging terbukti. Sampai itu selesai,
aplikasi tidak dinyatakan siap untuk pendaftaran online.
