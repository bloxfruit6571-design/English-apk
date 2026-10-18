# Premium manual DANA + WhatsApp

> **Arsip sebelum nomor pemilik diisi.** Ketentuan aktif dan APK terbaru: [konfigurasi pemilik](KONFIGURASI-PEMILIK-PREMIUM.md). Nomor kini 0856-5670-6104, nama belum dikonfirmasi; APK debug versi lama telah digantikan.


**Spesifikasi aktif.** Menggantikan tampilan checkout dan tombol buyer “Saya Sudah Membayar”.
Harga tetap **Rp5.000**, satu kali bayar, akses permanen setelah aktivasi token yang sah.
Demo tetap maksimal lima soal Easy. Tidak ada langganan, integrasi merchant DANA, webhook aktif,
verifikasi saldo otomatis, token otomatis, upload screenshot ke backend atau pengiriman WhatsApp otomatis.

## Konfigurasi yang harus diisi pemilik

Pada lingkungan backend, bukan pada APK:

```dotenv
EQ_DANA_MANUAL_NUMBER=
EQ_DANA_MANUAL_NAME=
EQ_WHATSAPP_ADMIN_NUMBER=
```

Template: `deploy/env.produksi.example`. Ketiganya sengaja kosong. Jangan menyalin nomor fixture tes
sebagai penerima sungguhan. Nomor kontak ini data publik untuk pembeli, bukan PIN/OTP/password DANA.
Tidak ada nilai pribadi pemilik ditebak atau ditanamkan ke aplikasi.

`GET /api/manual-payments/config` mengembalikan harga, nomor/nama DANA, `whatsapp_number`,
`whatsapp_message`, dan `available`. Semua tiga konfigurasi harus valid untuk `available=true`.
Jika ada yang kosong/tidak valid, seluruh kontak disembunyikan dan pembayaran belum tersedia.
Ini validasi format saja, **bukan verifikasi kepemilikan nomor atau keberadaan akun DANA/WhatsApp**.

WhatsApp menerima format lokal Indonesia berawalan `0` (dinormalisasi menjadi `62`), awalan `+`,
atau `00` internasional; spasi, tanda hubung, titik dan kurung dibersihkan. Hasil berupa 8–15 digit,
tanpa nol di awal. Huruf, URL, query, karakter injeksi dan panjang tidak valid ditolak. `wa.me`
menggunakan digit internasional tanpa tanda `+`. Nama penerima di-escape sebelum ditampilkan.

## Tampilan dan alur pembeli

Halaman berjudul **ENGLISH QUEST PREMIUM**, menampilkan harga dan syarat satu kali/permanen/tanpa
langganan; nomor DANA/nama penerima; **SALIN NOMOR DANA**; nomor WhatsApp dan **HUBUNGI ADMIN VIA WHATSAPP**.
Informasi dapat dibuka sebelum login, tetapi aktivasi memerlukan akun permanen yang diverifikasi.
Saat API atau konfigurasi tidak tersedia, tombol kontak/salin dinonaktifkan tanpa nomor rekaan.

Tautan dibuat sebagai `https://wa.me/<digit-internasional>?text=<pesan-terenkode>` dengan pesan:

> Halo Admin English Quest, saya ingin membeli Premium seharga Rp5.000. Saya akan mengirimkan bukti pembayaran setelah transfer.

Membuka tautan hanya membuka percakapan/pesan draft. Pengguna tetap harus meninjau dan mengirim sendiri.
Tidak ada API order, checkout, token, aktivasi atau tanda lunas yang dipanggil oleh klik WhatsApp.
Tidak ada email/password/token dikirim otomatis dalam draft. Pembeli diminta menyampaikan email akun
English Quest secara manual kepada admin supaya token terikat akun yang tepat.

Petunjuk UI:
1. Transfer Rp5.000 ke nomor DANA yang ditampilkan.
2. Pastikan nama penerima benar sebelum mengirim uang.
3. Setelah transfer, buka WhatsApp admin.
4. Kirim screenshot bukti pembayaran.
5. Tunggu verifikasi manual dari admin.
6. Setelah dana diperiksa dan dinyatakan masuk, admin memberikan token.
7. Masukkan token pada halaman aktivasi Premium.

**Screenshot dan tombol WhatsApp bukan bukti uang masuk.** Tidak ada pesan “pembayaran berhasil”
hanya karena tindakan tersebut. Aplikasi tidak membaca isi percakapan atau bukti screenshot.

Android memakai mekanisme tautan eksternal yang sudah ada. Allowlist ditambah hanya host HTTPS
`wa.me`, path nomor valid, tanpa userinfo/port asing. Host serupa seperti `wa.me.example` tidak diizinkan.
Tautan Wikipedia/Creative Commons yang sudah ada tidak dihapus. Browser memakai tautan `_blank`
dengan `noopener noreferrer`. Keberhasilan membuka aplikasi WhatsApp pada HP masih perlu diuji.

## Admin: tidak perlu pesanan dari pembeli

Sistem order/audit lama dipertahankan **sebagai catatan internal admin**, bukan tahap checkout pembeli.
Tidak membuat tabel baru, tidak menghapus DB, token atau riwayat.

1. Pembeli membuat/login akun English Quest, membayar dan menghubungi admin melalui WhatsApp.
2. Admin memeriksa transaksi pada akun DANA penerima: nominal Rp5.000, nama/nomor penerima, waktu,
   referensi dan pembeli. Screenshot hanya membantu mencocokkan, tidak cukup untuk persetujuan.
3. Login `/admin` memakai akun admin yang diprovisikan operator. Akun biasa ditolak backend.
4. Form **Pembayaran dari WhatsApp** meminta email akun pembeli, nominal dana masuk, referensi
   transaksi serta checkbox pemeriksaan dana nyata.
5. **Catat pembayaran terverifikasi** memanggil `POST /api/admin/manual-payments/record`. Backend
   memeriksa admin, akun pembeli, konfigurasi, nominal dan receipt; membuat/menggunakan kembali
   catatan internal dan status verified dalam satu transaksi. **Belum membuat token.**
6. Admin menekan **Terbitkan satu token** pada catatan verified. Endpoint issue lama tetap digunakan.
7. Salin kode yang tampil sekali dan kirim secara privat melalui WhatsApp kepada pemilik akun.
8. Pembeli mengaktifkan token; `/api/premium/activate` dan `/api/me` tetap menentukan hak Premium.

Body endpoint record: `buyer_email`, `received_amount:5000`, `receipt_reference`,
`confirmed_received:true`. Identitas operator diambil dari sesi, bukan body. Role dicek kembali dalam
transaksi. Self-review dilarang; buyer tidak ditemukan/guest, nominal salah, tanpa konfirmasi, atau
receipt sudah dipakai akun lain ditolak. Gagal validasi/penyimpanan tidak meninggalkan pesanan yatim.

Retry dengan email/ref yang sama menggunakan catatan yang sama; enam request bersamaan tidak
menggandakan order. Penerbitan tetap satu token per order. Record sesudah issued tidak merotasi kode.
Audit baru memakai `recorded_by_admin` lalu `verified`; audit issue/aktivasi/revoke tetap ada.
Referensi disimpan sebagai HMAC, bukan teks asli. Catatan pending lama dapat diverifikasi melalui
form lama; riwayat/ownership/index/check harga dan mata uang tetap dipertahankan.

## Aktivasi dan batas token

Token acak disimpan sebagai hash/HMAC, bukan plaintext. Token manual terikat ke akun pembeli bahkan
sebelum klaim, tidak bisa dipakai akun lain. Retry aktivasi oleh pemilik hanya idempoten, bukan
klaim kedua atau entitlement baru. Token tidak memiliki expiry; status Premium bertahan pada DB
setelah logout/login/restart/restore. Server tetap harus tersedia untuk memverifikasi entitlement;
flag JavaScript/localStorage bukan sumber Premium.

Kode hanya ditampilkan pada penerbitan awal. **Jika respons/kode hilang, tidak dapat dibuka ulang
dari DB dan tidak diterbitkan ulang otomatis.** Pemilik perlu kebijakan dukungan/refund sebelum
melayani transaksi nyata. Jangan membuat token pengganti hanya dari screenshot.

## Kode lama: apa yang dibersihkan dan yang dipertahankan

- Renderer checkout, pengambilan token otomatis dan pemanggilan order pembayaran dibuang dari UI.
- Wrapper checkout/order/claim otomatis dan pembuatan order buyer dibuang dari `access-client.js`.
- Nama action lama hanya menjadi alias aman menuju kontak/manual refresh; tidak bisa memanggil checkout.
- Implementasi provider/backend lama dan tabel riwayat tidak dihapus. HTTP otomatis tetap ditolak
  pada produksi; hanya regresi loopback dengan flag insecure-dev yang dapat memakai jalur legacy.
  Launcher HP mematikan provider. Ini bukan integrasi DANA aktif atau bukti pembayaran otomatis sukses.
- Pesanan lama masih dapat dibaca/dikelola admin. Endpoint buyer pending lama dipertahankan untuk
  kompatibilitas data/klien lama, tidak dipanggil klien baru dan tidak pernah memberi token/Premium.

Satu kelompok tes UI checkout lama diganti dengan assert bahwa seluruh panggilan otomatis tidak
terjadi. Tes manual UI diperbarui mengikuti penghapusan tombol pending. Tes backend lama tetap
menjaga aktivasi, quota, auth, legacy data dan transaksi. Perubahan assertion bukan klaim bahwa
produk masih menyediakan fitur checkout yang sengaja dihapus.

## Lokal, APK dan produksi

`tools/backend-lokal-manual.py` adalah helper uji yang sengaja mengosongkan ketiga kontak, mengikat
loopback, dan mempertahankan DB/pepper privat. Ia tidak mewarisi kontak atau credential produksi.
Untuk mengisi kontak nyata nantinya, operator mengatur environment backend normal sesuai template;
bukan mengubah frontend/menaruh secret dalam APK. Deploy tetap perlu persetujuan.

APK debug memakai `http://127.0.0.1:8088` (HP yang sama/adb reverse), bukan server internet. Produksi
masih memerlukan `EQ_API_BASE_URL` HTTPS nyata, `EQ_ALLOWED_ORIGINS`, `EQ_TOKEN_PEPPER` stabil,
DB persisten/backup serta akses admin HTTPS yang dibatasi. Jangan menjalankan produksi di disk
sementara, memakai flag insecure debug, atau menganggap harga/kontak yang tampil membuktikan
layanan pembayaran sudah online. Tidak ada hosting/domain/layanan dibeli atau dideploy.
