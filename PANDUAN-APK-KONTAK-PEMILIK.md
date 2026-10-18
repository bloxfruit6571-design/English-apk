# Menguji APK dengan nomor pemilik — HP Android

## Status dan batasan

APK: **English-Quest-1.7.1-owner-contact-localtest-debug.apk** di folder `artefak-apk`.
Ukuran **84.929.134 byte** (80,99 MiB). SHA-256:
`372b126c4bd22c6665c7c5bd7aab741ab0194f213ed8a987024ba12d5f193a58`.
Checksum juga tersedia pada file `.sha256` di samping APK.
Package `com.englishquest.app.debug`, versi `1.7.1-debug` / 13. Ini debug **lokal**, bukan rilis atau layanan publik siap jual. Tidak ada pengujian HP sungguhan yang sudah dilakukan oleh asisten.

**Jangan menerima pembayaran pelanggan sebelum login, aktivasi dan penyimpanan permanen benar-benar siap.** Nomor yang terlihat bukan bukti server online. Nama penerima belum dikonfirmasi; pemilik perlu memeriksanya di DANA. Tidak perlu transfer nyata untuk menguji salin/tautan/pesan.

## 1. Pasang APK

Unduh APK beserta `.sha256`. Jika memakai Termux:

```sh
cd ~/storage/downloads
sha256sum -c English-Quest-1.7.1-owner-contact-localtest-debug.apk.sha256
```

Pasang dari pengelola berkas Android. Signer sama dengan APK lokal register/manual/WhatsApp sebelumnya, tetapi berbeda dari APK debug historis di root repository. Jika update ditolak karena signature, **jangan uninstall, clear data, mengganti package atau memaksa downgrade**. Backup data dan gunakan profil/perangkat uji bersih atau signer yang cocok. Kunci/versi tidak diganti dan APK release tidak ditimpa.

## 2. Uji tanpa backend

Buka Premium: judul ENGLISH QUEST PREMIUM, Harga: Rp5.000,00, satu kali/permanen setelah aktivasi sah, tanpa langganan. DANA dan WhatsApp tampil **0856-5670-6104**, nama **belum dikonfirmasi**. Kontak berasal dari aset publik APK ketika server tidak terjangkau.

- Tekan SALIN NOMOR DANA lalu tempel pada catatan: harus **085656706104**. Sukses berbunyi **Nomor DANA berhasil disalin**. Semua jalur gagal harus memberi petunjuk salin manual.
- Tekan KONFIRMASI PEMBAYARAN VIA WHATSAPP: tujuan **6285656706104**. Periksa draft terhadap `KONFIGURASI-PEMILIK-PREMIUM.md`, termasuk paragrafnya. Jangan tekan Kirim untuk uji ini. Screenshot tidak terlampir otomatis.
- Copy/WhatsApp tidak mengaktifkan Premium, tidak menerbitkan token, tidak menandai pembayaran lunas.
- Demo maksimal 5 soal Easy. Login dan aktivasi tidak bisa diselesaikan tanpa backend.

## 3. Backend di HP yang sama (tanpa komputer)

`127.0.0.1` pada APK menunjuk HP itu sendiri, bukan server Arena. Paket **English-Quest-backend-owner-contact.zip** berisi API/admin, launcher dan JSON kontak publik; bukan PWA lengkap. Tidak berisi database pengguna, akun/password, pepper, signing key atau token Premium.

Dengan Termux resmi yang telah dipasang:

```sh
pkg update
pkg install python unzip
termux-setup-storage
mkdir -p ~/eq-owner-contact
cd ~/eq-owner-contact
unzip -n ~/storage/downloads/English-Quest-backend-owner-contact.zip
python3 tools/backend-lokal-manual.py serve
```

Pakai folder source baru; `unzip -n` tidak memperbarui file lama. Hentikan server lama pada port 8088 dahulu. Launcher menghapus environment EQ_* warisan untuk mencegah pemakaian kredensial produksi, tetapi sekarang membaca nomor pemilik dari `premium-contact.json`.

State tetap di `~/.local/share/english-quest-manual-debug/`: DB DAN pepper harus dipertahankan bersama. Jangan membagikannya atau mengganti pepper. Backup privat diperlukan. Android dapat menghentikan Termux di background; jalankan kembali jika terputus. Ini penyimpanan lokal, bukan hosting publik atau backup cloud.

Buka `http://127.0.0.1:8088/api/health` di browser HP. Kembali ke APK; uji daftar, login, logout/relogin dan restart dengan akun uji pilihan sendiri. Tidak ada akun bawaan. Jangan memberikan password lewat chat.

## 4. Operator admin

Pada sesi Termux kedua, folder source yang sama:

```sh
python3 tools/backend-lokal-manual.py create-admin --email EMAIL_OPERATOR_ANDA
```

Ganti email dengan milik operator; password diminta privat di terminal (minimal 12 karakter). Buka `http://127.0.0.1:8088/admin`. Pengguna biasa tidak berhak mencatat/ menerbitkan token.

Alur sebenarnya: pembeli transfer → WhatsApp screenshot + identitas akun → **admin memeriksa riwayat DANA secara langsung** → catat pembayaran terverifikasi → tindakan terpisah Terbitkan satu token → kirim manual → pembeli aktivasi lewat backend. Jangan menandai fixture atau screenshot sebagai uang nyata. Token terikat akun, hanya hash disimpan; kode tampil sekali dan tidak dapat dipulihkan dari hash.

## 5. Checklist yang masih harus diuji pada HP

- [ ] Instalasi/update aman, navigasi dan fitur belajar tidak terganggu.
- [ ] DANA/WhatsApp/harga/nama belum dikonfirmasi tampil benar, termasuk backend mati.
- [ ] Clipboard Android benar dan pesan sukses jujur.
- [ ] WhatsApp membuka tujuan serta draft yang benar; tidak mengirim otomatis.
- [ ] Klik salin/WhatsApp/screenshot tidak memberi Premium.
- [ ] Demo tetap maksimal 5 soal Easy.
- [ ] Daftar/login/logout/relogin dengan backend lokal bekerja.
- [ ] Token salah/akun lain ditolak; token sah mengaktifkan satu akun dan bertahan setelah restart/relogin.
- [ ] Panel penerbitan hanya untuk admin; satu order tidak menerbitkan token berulang.
- [ ] Mikrofon/ASR/TTS diuji pada perangkat, bukan diasumsikan dari build.

Tes otomatis sudah memakai fixture terisolasi, bukan transfer pelanggan. HP/DANA/WhatsApp nyata dan HTTPS publik belum diuji. Untuk komputer tersedia alternatif `adb reverse tcp:8088 tcp:8088`; APK ini tidak mendukung HTTP alamat LAN. Produksi membutuhkan domain HTTPS, penyimpanan persisten/backup dan izin deployment terpisah; tidak ada pembelian hosting/deploy dilakukan.
