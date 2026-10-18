# Pembayaran Premium manual via DANA

> **Arsip tahap sebelum WhatsApp.** Spesifikasi aktif: [DANA–WhatsApp](PEMBAYARAN-DANA-WHATSAPP.md). APK/checksum dan alur buyer pending di dokumen ini bersifat historis; gunakan laporan/artefak sesi WhatsApp terbaru.


**Status: diimplementasikan dan diuji lokal. Belum tersedia online.**
Harga **Rp5.000**, sekali bayar, akses permanen tanpa langganan. Demo tetap maksimal 5 soal Easy.
Tidak ada checkout DANA otomatis, merchant credential, webhook produksi atau transaksi nyata yang diaktifkan.

## Konfigurasi penerima

Isi hanya di konfigurasi server yang dikelola pemilik:

```dotenv
EQ_DANA_MANUAL_NUMBER=
EQ_DANA_MANUAL_NAME=
```

Nilai sengaja kosong; tidak ada nomor atau nama penerima contoh yang ditampilkan sebagai rekening nyata.
Nomor harus berformat telepon dengan awalan 0/62/+62, 8–13 digit tambahan; nama 2–100 karakter tanpa
karakter kontrol. Ini hanya validasi format, **bukan verifikasi bahwa akun DANA ada atau milik pemilik**.
Pemilik wajib memeriksa nomor/nama sendiri sebelum mengaktifkan pembayaran. Kedua nilai ini memang
akan dibaca pembeli; bukan tempat menyimpan PIN/OTP/password DANA.

Jika salah satu kosong/tidak valid, API menyatakan `available=false`, menyembunyikan keduanya,
dan pembuatan permintaan ditolak `503 MANUAL_NOT_CONFIGURED`. Pendaftaran/login tetap dapat berjalan.
Jangan isi `EQ_PAYMENT_PROVIDER` untuk produksi manual. Pemeriksa produksi menolak variabel tersebut.

## Alur pembeli

1. Login akun permanen; demo tamu tidak dapat membuat permintaan pembayaran.
2. Halaman Premium menampilkan harga, nomor/nama penerima dari server, tombol salin dan instruksi.
3. Pembeli mencocokkan nama penerima pada DANA sebelum transfer tepat Rp5.000.
4. **Saya Sudah Membayar** hanya mencatat permintaan `pending`; tidak menghasilkan token atau Premium.
5. Pembeli menyampaikan ID `EQM-…` kepada admin lewat kanal komunikasi resmi pemilik.
   Kanal komunikasi itu belum dikonfigurasi di proyek; jangan mengarang nomor WhatsApp/URL dukungan.
6. Pembeli dapat memuat ulang status; pesanan diambil dari server, bukan dipercayai dari localStorage.
7. Setelah menerima token dari admin, masukkan pada kolom aktivasi. Backend memvalidasi token lalu
   aplikasi memeriksa ulang `/api/me`; hanya hak Premium yang terkonfirmasi server membuka fitur.

Permintaan pending/verified/issued untuk akun yang sama digunakan kembali (idempoten). Pesanan
rejected dapat diikuti permintaan baru. Satu akun tidak membuat banyak pesanan aktif hanya karena retry.
Jika koneksi putus setelah menekan tombol, periksa status sebelum mencoba lagi.

## Alur admin

Panel `/admin` tetap membutuhkan login dan peran admin yang diverifikasi backend.

- Daftar pesanan dapat difilter pending/verified/rejected/issued dan dicari berdasarkan ID pesanan,
  ID akun atau email; maksimal 200 hasil tiap pencarian.
- Periksa **snapshot nomor/nama penerima pada pesanan**, akun pembeli, nominal, waktu dan referensi
  transaksi pada riwayat DANA penerima. Jangan mempercayai screenshot atau tombol pembeli saja.
- Pilih Verifikasi, masukkan nominal dana masuk **tepat 5000**, referensi transaksi dan centang
  konfirmasi bahwa dana benar-benar diterima. Backend tidak dapat memeriksa saldo DANA otomatis:
  admin tetap bertanggung jawab atas kebenaran verifikasi ini.
- Referensi dinormalisasi dan disimpan sebagai HMAC, bukan nilai aslinya. Referensi yang sama tidak
  boleh dipakai untuk dua pesanan. Simpan bukti eksternal dengan aman sesuai kebijakan pemilik.
- Verifikasi mengubah status menjadi `verified`, **belum menerbitkan token**.
- Tombol Terbitkan satu token menjalankan transaksi atomik dan mengubah status menjadi `issued`.
  Kode plaintext muncul **sekali** kepada admin; salin dan kirim privat hanya ke pembeli terkait.
- Tolak hanya pesanan pending, dengan alasan terstruktur: tidak ditemukan, nominal salah, penerima
  salah, duplikat, atau lainnya. Pesanan yang sudah verified/issued tidak bisa dibalik ke pending
  lewat endpoint ini. Admin tidak boleh memverifikasi/menolak/menerbitkan pesanan miliknya sendiri.
- Riwayat menampilkan aktor, waktu dan aksi: requested, verified, rejected, token_issued,
  activated, token_revoked. Tidak memuat plaintext token, hash token, password, atau raw receipt.

### Jika kode token hilang atau respons penerbitan putus

**Tidak ada penerbitan ulang otomatis dan tidak ada rotasi kode.** Retry mengembalikan ID/status token
lama tanpa kode, bukan token baru. Karena database hanya menyimpan hash, kode tidak dapat diambil lagi.
Jangan membuat pesanan atau token kompensasi tanpa rekonsiliasi/persetujuan pemilik. Kebijakan
penanganan kehilangan token/refund perlu ditetapkan operator sebelum penggunaan publik.
Panel memperingatkan admin untuk menyalin kode sebelum menutup halaman/menerbitkan kode lain.

## Token dan keamanan

- Menggunakan `premium_tokens` yang sudah ada; token acak 192-bit disimpan sebagai HMAC-SHA256.
- Indeks unik `issued_for_order_id` menjamin satu token per pesanan, termasuk permintaan bersamaan.
- Token manual terikat ke **akun pemilik pesanan**, bahkan sebelum aktivasi. Akun lain ditolak.
- Aktivasi memanfaatkan transaksi yang sudah ada: satu akun saja; retry akun yang sama idempoten.
- Tidak ada expiry pada token pembayaran manual. Premium bertahan saat logout/login/restart;
  kewenangan pencabutan token admin yang sudah ada tetap dipertahankan dan dicatat untuk token manual.
- Semua endpoint mutasi memakai bearer session, JSON, TLS/trusted proxy dan pembatasan percobaan.
- Backend memeriksa peran admin lagi di dalam transaksi; menyembunyikan tombol bukan pengaman utama.
- Audit tidak menyediakan endpoint edit/hapus. Ini **bukan** log antiperubahan oleh pemilik DB/OS;
  cadangan dan pembatasan akses server tetap wajib.
- Jalur checkout, webhook otomatis, klaim/rotasi kode otomatis, dan penerbitan token generik HTTP
  lama **ditolak pada produksi**. Kode lama dipertahankan untuk data lama dan tes regresi di listener
  loopback dengan `EQ_ALLOW_INSECURE_DEV=1`; UI klien terbaru selalu memilih alur manual.
- Renderer lama tersedia hanya sebagai kompatibilitas modul klien lama. Ini bukan checkout produksi;
  backend tetap menolaknya. Tes lama tidak diubah supaya lulus.

## Database dan migrasi

Migrasi `manual_dana_v1` bersifat aditif dan idempoten, dalam transaksi SQLite:

- `manual_payment_orders`: pemilik, Rp5.000/IDR, snapshot penerima, status, verifikasi, receipt HMAC,
  ID token dan waktu. Satu pesanan aktif per akun, receipt unik, token unik.
- `manual_payment_audit`: kejadian, pesanan, aktor, aksi, status sebelum/sesudah, alasan dan waktu.
- `eq_schema_migrations`: pencatatan migrasi bernama.

Tidak mengganti/menghapus tabel `users`, `sessions`, `premium_tokens`, `payment_orders`,
`payment_events`, progres atau ledger demo. Tes migrasi membandingkan baris lama sebelum/sesudah.
Pesanan otomatis lama tidak otomatis dikonversi menjadi manual atau dianggap lunas; operator harus
merekonsiliasi pesanan lama yang belum tuntas. Token sah yang sudah dimiliki tetap bisa diaktifkan.

Sebelum menjalankan versi ini di data sungguhan: hentikan mutasi, buat backup SQLite yang konsisten,
simpan pepper secara aman, uji migrasi pada salinan, baru jalankan setelah persetujuan. Gunakan
`deploy/cadangkan-db.py`; pemulihan hanya ke path staging baru, bukan menimpa DB aktif.

## Endpoint

| Metode/path | Akses dan fungsi |
|---|---|
| GET `/api/manual-payments/config` | Publik melalui HTTPS; konfigurasi penerima dan harga, tanpa rahasia server |
| POST `/api/manual-payments/orders` | Akun permanen; membuat/menggunakan kembali pending; nominal server 5000 |
| GET `/api/manual-payments/orders` | Akun permanen; hanya pesanan sendiri |
| GET `/api/manual-payments/order?order_id=…` | Akun permanen; detail pesanan sendiri, akun lain mendapat 404 |
| GET `/api/admin/manual-payments?status=…&q=…` | Admin; daftar dan pencarian |
| POST `/api/admin/manual-payments/verify` | Admin; `order_id`, `received_amount:5000`, `receipt_reference`, `confirmed_received:true` |
| POST `/api/admin/manual-payments/reject` | Admin; `order_id`, `reason` (not_found/wrong_amount/wrong_recipient/duplicate/other) |
| POST `/api/admin/manual-payments/issue` | Admin; `order_id`; hanya verified; kode sekali saja |
| GET `/api/admin/manual-payments/audit?order_id=…` | Admin; riwayat |
| POST `/api/premium/activate` | Sistem lama dipertahankan; ditambah pengikatan token manual ke pemilik |
| GET `/api/admin/summary` | Ditambah metrik pesanan manual; kunci lama dipertahankan |
| POST `/api/admin/tokens/revoke` | Dipertahankan; pencabutan token manual ikut audit |

## Hosting dan APK

Pembayaran manual **tetap membutuhkan backend HTTPS**. Tidak ada alamat produksi, deployment,
domain/hosting berbayar, atau rahasia baru yang dibuat. Anggaran tetap nol; pilihan hosting lama
hanya rencana, bukan layanan aktif. VPS template mengasumsikan loopback+proxy satu host; PaaS perlu
adapter yang disetujui, bukan menonaktifkan pemeriksaan TLS agar bisa bind publik.

Wajib: `EQ_API_BASE_URL` origin publik yang nyata pada build APK; `EQ_ALLOWED_ORIGINS` memuat
`https://appassets.androidplatform.net`; `EQ_TOKEN_PEPPER` stabil dan dirahasiakan; `EQ_DB_PATH`
menunjuk disk/volume persisten; backup off-site terenkripsi. Pepper jangan diganti sembarangan
karena memengaruhi hash token dan receipt. Semua data akun/permintaan backup tetap sensitif.

Template proxy publik sebelumnya memblokir `/admin*` dan `/api/admin*`. Panel tidak otomatis
bisa dibuka lewat host publik. Sediakan jalur operator privat HTTPS/VPN yang disetujui untuk
panel dan API admin, tanpa mematikan autentikasi. Jangan membuka port HTTP backend ke internet.

Aset Android disinkronkan, cache web dinaikkan ke v20; identitas aplikasi/versionCode tidak diubah.
Belum membangun/mengganti APK pada pekerjaan ini. APK lama tidak otomatis menerima perubahan source.
Build final menunggu origin HTTPS nyata, persetujuan signing dan pengujian HP tanpa adb reverse.
