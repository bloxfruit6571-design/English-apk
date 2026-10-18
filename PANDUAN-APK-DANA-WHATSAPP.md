# Menguji APK debug DANA–WhatsApp di HP

> **Arsip sebelum nomor pemilik diisi.** Ketentuan aktif dan APK terbaru: [konfigurasi pemilik](KONFIGURASI-PEMILIK-PREMIUM.md). Nomor kini 0856-5670-6104, nama belum dikonfirmasi; APK debug versi lama telah digantikan.


## Artefak terbaru

`English-Quest-1.7.1-dana-whatsapp-localtest-debug.apk`

- 84.920.526 byte (sekitar 80,99 MiB).
- SHA-256: `af7f5fc21c57c6fb59b552b3cf2768e61173d6e69057936c3aa0d5f1bede7280`.
- applicationId `com.englishquest.app.debug`; versionName `1.7.1-debug`; versionCode `13`.
- Backend debug: `http://127.0.0.1:8088`. Bukan server publik dan bukan APK produksi.

APK ini hasil build baru dari source DANA–WhatsApp. Perubahan dibaca dari dalam APK: 17 aset
identik dengan source, UI/URL WhatsApp ada, wrapper checkout tidak ada, bridge/native allowlist
`wa.me` ada di DEX, model Vosk lengkap dengan hash yang benar, signature valid.
Pengujian HP nyata **belum** dilakukan; verifikasi statis bukan bukti WhatsApp sudah terbuka di HP.

## 1. Instalasi aman

Unduh APK dan berkas `.sha256`, verifikasi checksum, kemudian pasang melalui pengelola berkas Android.
Jika menggunakan Termux:

```sh
cd ~/storage/downloads
sha256sum -c English-Quest-1.7.1-dana-whatsapp-localtest-debug.apk.sha256
```

Signer sama dengan APK uji lokal pendaftaran/manual terdahulu, tetapi berbeda dari APK historis
`English-Quest-1.7.1-debug.apk` di root repository. Android mungkin menolak update di atas APK
historis tersebut. Jangan uninstall, clear data, mengganti package atau memaksa downgrade tanpa
backup dan keputusan pemilik. Gunakan profil/perangkat uji bersih atau signer yang cocok.
VersionCode tidak diubah, APK rilis tidak ditimpa, dan kunci signing baru tidak dibuat.

## 2. Uji saat backend mati

Buka aplikasi: demo lokal tetap terbatas lima soal Easy, halaman Premium dapat dibuka, harga Rp5.000
terlihat, kontak tidak direka dan pembayaran belum tersedia. Login/aktivasi tidak dapat diselesaikan
tanpa server. Jangan menganggap tombol WhatsApp atau screenshot sebagai sukses pembayaran.

## 3. Backend lokal di HP yang sama, tanpa komputer

`127.0.0.1` di APK berarti HP itu sendiri, bukan workspace Arena. Gunakan Termux resmi dan paket
`English-Quest-backend-dana-whatsapp.zip` yang disertakan. Paket API/admin ini tidak berisi akun,
database, password, pepper, nomor penerima atau APK; bukan PWA lengkap.

```sh
pkg update
pkg install python unzip
termux-setup-storage
mkdir -p ~/eq-dana-whatsapp
cd ~/eq-dana-whatsapp
unzip -n ~/storage/downloads/English-Quest-backend-dana-whatsapp.zip
python3 tools/backend-lokal-manual.py serve
```

Gunakan folder baru jika sudah ada source lama; `unzip -n` tidak memperbarui file yang sudah ada.
State uji disimpan di `~/.local/share/english-quest-manual-debug/`. DB dan pepper dipertahankan saat
restart. Jangan hapus/bagikan folder state atau mengganti pepper. Hentikan backend lama sebelum
menjalankan yang baru pada port yang sama. Backup state privat bila datanya perlu dipertahankan.

Buka `http://127.0.0.1:8088/api/health` pada browser HP. Setelah server siap, kembali ke APK untuk
menguji daftar, login, logout dan login ulang. Gunakan akun/password uji pilihan Anda, bukan
kredensial produksi. Proses Termux dapat dihentikan Android di background; jalankan kembali bila
koneksi terputus. File lokal persisten bukan hosting yang selalu aktif atau backup cloud.

Launcher uji sengaja mengosongkan **semua tiga kontak**. Jadi kondisi default memang “informasi
pembayaran belum dikonfigurasi”; tombol WhatsApp/salin harus nonaktif. Tidak ada nomor palsu untuk
membuat tampilan seolah siap dibayar.

## 4. Konfigurasi kontak nantinya

Pemilik mengisi environment backend normal:

```dotenv
EQ_DANA_MANUAL_NUMBER=
EQ_DANA_MANUAL_NAME=
EQ_WHATSAPP_ADMIN_NUMBER=
```

Template ada di `deploy/env.produksi.example`; detail normalisasi dan alur admin ada di
`PEMBAYARAN-DANA-WHATSAPP.md`. Tidak perlu mengubah/menanamkan nomor ke APK. Helper uji sengaja
mengabaikan konfigurasi produksi, sehingga pengisian environment saja tidak mengaktifkannya melalui
helper tersebut. Untuk pengujian kontak yang memang sudah dikonfirmasi pemilik, jalankan backend
normal dengan environment yang sesuai dan DB/pepper uji yang sama. Tidak perlu transfer nyata hanya
untuk mengecek nomor, salin dan URL/pesan WhatsApp. Jangan menekan Kirim jika tidak bermaksud mengirim.

Jika belum memberikan tiga nilai, pemilik cukup menguji keadaan tidak terkonfigurasi. Jangan
menggunakan nomor fixture tes atau membuat token untuk memberi kesan pembayaran berhasil.

## 5. Admin lokal

Di sesi Termux kedua, folder paket yang sama:

```sh
python3 tools/backend-lokal-manual.py create-admin --email EMAIL_OPERATOR_ANDA
```

Ganti penanda email; password diminta langsung di terminal (minimal 12 karakter), bukan lewat chat.
Buka `http://127.0.0.1:8088/admin`. Bootstrap lokal untuk client admin telah diperbaiki dan diuji
terhadap server nyata. Akun biasa tetap ditolak. Tidak ada akun/PIN admin bawaan.

Setelah konfigurasi sah tersedia dan dana nyata benar-benar diperiksa, admin mencatat email akun
pembeli + nominal + referensi transaksi + konfirmasi. Pencatatan belum menghasilkan token; tombol
**Terbitkan satu token** tetap tindakan terpisah. Kode tampil sekali dan dikirim manual lewat WhatsApp.
Tidak ada penerbitan hanya dari screenshot. Tes otomatis alur ini memakai fixture terisolasi, bukan
transaksi pelanggan atau bukti uang masuk.

## 6. Checklist HP yang belum boleh dianggap lulus

- [ ] Instalasi sesuai signer/identitas dan aplikasi terbuka.
- [ ] Demo tetap maksimal 5 soal Easy; fitur Premium tidak terbuka dari XP/flag lokal.
- [ ] Backend mati memberikan pesan koneksi, bukan akses Premium palsu.
- [ ] Backend lokal hidup: register, login/logout/relogin dan restart mempertahankan akun.
- [ ] Premium menampilkan judul/harga/syarat yang benar; kontak kosong dinyatakan belum tersedia.
- [ ] Setelah kontak sah dikonfigurasi: nomor/nama benar, salin DANA benar.
- [ ] Tombol WhatsApp membuka nomor yang tepat, draft pesan Rp5.000 benar, tidak otomatis mengirim.
- [ ] Klik WhatsApp tidak mencatat lunas, menerbitkan token atau membuka Premium.
- [ ] Panel admin hanya dapat menjalankan tindakan dengan sesi admin.
- [ ] Setelah aktivasi sah, status Premium tetap pada akun setelah aplikasi/server dibuka ulang.

Tidak mengklaim instalasi, pesan WhatsApp, mikrofon/ASR/TTS atau pembayaran nyata telah diuji di HP.
Untuk backend komputer, alternatifnya `adb reverse tcp:8088 tcp:8088`. HTTP alamat LAN tidak didukung.
Produksi harus memakai HTTPS/persistent DB/backup dan izin deployment terpisah.
