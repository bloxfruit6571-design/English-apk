# Konfigurasi pemilik — Premium manual DANA

**Spesifikasi aktif setelah pemilik memberikan nomor.** Tidak ada checkout, merchant API,
webhook produksi, pengiriman WhatsApp otomatis atau Premium berdasarkan screenshot.

## Kontak yang disetujui pemilik

| Penggunaan | Nilai |
|---|---|
| Nomor DANA yang ditampilkan | **0856-5670-6104** |
| Isi clipboard untuk DANA | **085656706104** — nomor sama, pemisah dihilangkan agar mudah ditempel |
| WhatsApp Admin yang ditampilkan | **0856-5670-6104** |
| Nomor pada tautan WhatsApp | **6285656706104** |
| Harga | **Rp5.000,00**, satu kali, Premium permanen sesudah aktivasi sah |
| Nama penerima | **Belum dikonfirmasi**, tidak dikarang dan tidak dinyatakan terverifikasi |

Sumber tunggal kontak publik: **`premium-contact.json`** di root, disalin byte-identik ke aset
Android. File ini sengaja dibundel ke APK/PWA karena nomor diberikan pemilik untuk dilihat pembeli.
File tersebut bukan tempat menyimpan password, OTP, PIN, token Premium, pepper atau kunci signing.

Backend membaca file yang sama. Operator dapat memberikan environment `EQ_DANA_MANUAL_NUMBER`,
`EQ_WHATSAPP_ADMIN_NUMBER`, `EQ_DANA_MANUAL_NAME`; template `deploy/env.produksi.example` sudah berisi
nomor publik pemilik dan nama kosong. Tidak ada konfigurasi produksi rahasia yang diisi.
Environment yang tidak diset memakai file publik; nomor yang sengaja diset kosong/invalid membuat
API mengembalikan pembayaran tidak tersedia. Nama kosong saja **tidak** menyembunyikan nomor pemilik.
Mengubah nomor atau nama di masa depan harus berdasarkan konfirmasi pemilik, bukan dugaan.

Validasi format tidak memeriksa identitas atau keberadaan akun DANA/WhatsApp. Pembeli tetap perlu
memeriksa nomor dan nama penerima pada aplikasi DANA. Jika nama tidak sesuai, jangan transfer;
konfirmasi kepada pemilik. Aplikasi tidak mendeteksi saldo atau uang masuk secara otomatis.

## Tampilan dan tindakan

Halaman **ENGLISH QUEST PREMIUM** menampilkan harga Rp5.000,00, pembayaran satu kali, akses permanen
setelah token sah diaktifkan, tanpa biaya langganan, nomor DANA dan WhatsApp persis seperti tabel.
Nama penerima ditampilkan sebagai **belum dikonfirmasi**. Kontak terlihat sebelum login, tetapi
aktivasi tetap memerlukan akun permanen yang diverifikasi backend.

**SALIN NOMOR DANA**:
- Memakai `EnglishQuestAndroid.copyDanaNumber` pada WebView Android bila tersedia.
- Bridge hanya menerima bentuk nomor, menulis ClipboardManager Android, lalu mengembalikan hasil.
- Fallback: Clipboard API browser, kemudian copy textarea untuk browser lama.
- Hanya saat salah satu jalur menyatakan sukses, UI menampilkan **“Nomor DANA berhasil disalin”**.
- Jika semua gagal, UI memberi petunjuk salin manual, bukan klaim sukses.
- Tidak menandai transfer, mengubah Premium, membuat order atau menerbitkan token.

**KONFIRMASI PEMBAYARAN VIA WHATSAPP** membuka `https://wa.me/6285656706104?text=…`.
Nomor lokal dinormalisasi dengan benar. Teks di-URL-encode, dapat diedit pengguna sebelum mengirim:

```text
Halo Admin English Quest.

Saya ingin membeli Premium seharga Rp5.000.

Saya sudah melakukan pembayaran melalui DANA.
Berikut saya kirimkan screenshot bukti transfer untuk diperiksa.

Mohon verifikasi pembayaran saya dan berikan token Premium
jika pembayaran sudah benar-benar diterima.

Terima kasih.
```

Pengguna hanya seharusnya memakai pernyataan “sudah melakukan pembayaran” setelah benar-benar
transfer; halaman mengingatkan untuk meninjau/mengedit draft. Tidak ada pesan atau screenshot
terkirim otomatis. Android membuka link lewat ACTION_VIEW dengan allowlist host HTTPS `wa.me`
dan path angka valid; tidak mengizinkan domain tiruan. Browser memakai `noopener noreferrer`.

Petunjuk UI: salin nomor → buka DANA → transfer Rp5.000 → periksa ulang penerima sebelum membayar →
hubungi admin → kirim screenshot → tunggu pemeriksaan transaksi → terima token setelah dana masuk.
Lalu masukkan token di halaman aktivasi. Screenshot hanya membantu pencocokan, bukan bukti final.

## Saat server belum ada

Kontak pemilik sekarang tersedia dari file publik di dalam APK melalui `getPremiumContactConfig`,
atau dari aset JSON PWA. Ini memastikan APK benar-benar menyertakan nomor, bukan hanya menunggu
server online. Halaman tetap mencoba memperbarui konfigurasi dari backend:

- Respons server sukses dipakai, termasuk jika server secara eksplisit menonaktifkan kontak.
- Jika server tidak dapat dijangkau, kontak publik bawaan dapat ditampilkan dengan peringatan
  bahwa berasal dari APK. Tidak ada status Premium atau keberhasilan pembayaran yang dipulihkan.
- Jika file publik juga hilang/rusak, tidak membuat nomor pengganti.
- Kontak bawaan adalah snapshot versi aplikasi, bisa usang jika pemilik menggantinya kemudian.
  Konfirmasikan penerima; jika tidak dapat login/aktivasi karena server belum tersedia, tunggu
  kesiapan layanan sebelum membayar. **Tampilan nomor bukan bukti layanan siap online.**

Data kontak hanya informasi publik. Backend tetap satu-satunya sumber hak Premium. Pendaftaran,
login dan aktivasi memerlukan server; tidak menjadi offline hanya karena nomor muncul di APK.

## Admin dan keamanan token — dipertahankan

Pembeli menyampaikan email akun English Quest kepada admin melalui WhatsApp. Admin login ke panel,
memeriksa transaksi DANA langsung (nominal tepat Rp5.000, referensi, penerima dan pembeli), mencatat
pembayaran lewat form yang berwenang, lalu menekan **Terbitkan satu token** secara terpisah.
Pencatatan verified tidak otomatis menerbitkan token. Admin mengirim kode secara manual di WhatsApp.

Tidak ada endpoint publik untuk menerbitkan token. Role dicek backend/dalam transaksi; self-review,
nominal salah, receipt duplikat dan pengguna biasa ditolak. Satu order hanya satu token; token
terikat akun pembeli dan disimpan sebagai hash/HMAC. Aktivasi akun lain/invalid ditolak, pemilik
retry hanya idempoten. Premium tidak memiliki expiry dan bertahan setelah logout/login/restart
selama DB/pepper dipertahankan. Bukan flag lokal. Akses tetap fail-closed bila server tidak terverifikasi.

Kode plaintext tampil sekali saat admin menerbitkan. Tidak dapat diambil kembali dari hash;
retry tidak membuat pengganti. Pemilik perlu prosedur kehilangan kode/refund sebelum menerima uang.
Tabel, akun dan riwayat tidak dihapus. Kode provider backend legacy hanya dipertahankan untuk data/
regresi lokal, diblokir pada produksi; frontend tidak memiliki checkout/order/claim otomatis.

Demo tetap **maksimal lima soal, satu misi Easy**. Dua teks lama yang keliru menyebut sepuluh soal/
dua misi dibetulkan agar sesuai batas yang sudah berlaku; kuota/harga tidak berubah.

## APK dan pengujian

Build debug tetap package `com.englishquest.app.debug`, versionCode 13, versionName `1.7.1-debug`,
origin API lokal `http://127.0.0.1:8088`. Versi/identitas tidak diganti, rilis tidak ditimpa.
Aset yang diperiksa sekarang **18**, termasuk `premium-contact.json`; cache web v22.
Verifier APK memeriksa nomor/nama kosong/harga, pesan, aset, bridge clipboard/config, signature dan
hash model Vosk, serta pola secret yang tidak diharapkan. Pemindaian pola bukan audit rahasia lengkap.

Tes baru mencakup angka sebenarnya yang diberikan pemilik tanpa menghubungi layanan tersebut,
clipboard native/browser/fallback/gagal, URL dan pesan persis, kontak fallback tanpa entitlement,
server disable, konfigurasi rusak, serta teks demo. Tes token/auth/database lama tetap dijalankan.
Tes Node memakai stub untuk ClipboardManager bridge/WhatsApp: bukan klaim uji HP sungguhan.

`tools/backend-lokal-manual.py` sekarang memakai kontak publik bawaan, tetap loopback, menolak
mewarisi credential produksi, menyimpan pepper privat secara stabil dan tidak memakai provider
pembayaran. Penggunaan kontak publik tidak menjalankan transaksi. Jangan memakai data uji untuk
mengklaim pembayaran pelanggan berhasil. Layanan publik tetap memerlukan HTTPS, penyimpanan
persisten/backup, dan persetujuan deployment yang belum dilakukan.
