# APK debug pembayaran manual — uji lokal dari HP

Build ini **bukan produksi**. Endpoint tertanam: `http://127.0.0.1:8088`, flag HTTP dev aktif,
package debug tetap `com.englishquest.app.debug`, versionName `1.7.1-debug`, versionCode `13`.
Tidak ada endpoint publik, nomor DANA, akun admin, password, pepper, token Premium atau DB di APK.
`127.0.0.1` pada HP berarti **HP itu sendiri**, bukan sandbox Arena atau komputer lain.

## 1. Unduh dan periksa sebelum memasang

Unduh `English-Quest-1.7.1-dana-manual-localtest-debug.apk` dan berkas `.sha256`.
Ukuran: **84.922.352 byte (80,99 MiB)**. SHA-256:

```text
38cd8f9560937fde17404eb8ef9ff382a087a4e652df9dfb2ec80fe7d4d4923e
```

Tanda tangan valid dengan kunci debug yang sudah tersedia. Tidak membuat kunci signing produksi.
Signer sama dengan APK `register-test-localtest-debug` terdahulu, tetapi **berbeda** dari APK historis
`English-Quest-1.7.1-debug.apk` di root repository. Android dapat menolak pembaruan di atas APK historis
tersebut meski package/version sama. **Jangan uninstall, clear data, force downgrade, atau mengganti
package ID untuk menghindari konflik tanpa backup dan keputusan pemilik.** Pilihan aman: perangkat/
profil pengujian bersih, atau APK debug sebelumnya dengan signer yang cocok. Instalasi belum diuji
pada perangkat fisik. VersionCode sengaja tidak dinaikkan.

Jika memakai Termux, setelah izin storage diberikan:

```sh
cd ~/storage/downloads
sha256sum -c English-Quest-1.7.1-dana-manual-localtest-debug.apk.sha256
```

Harus `OK`. Instal melalui pengelola berkas Android, izinkan pemasangan dari sumber tersebut hanya
jika Anda mempercayai artefak. APK perlu ruang untuk library/model Vosk yang tetap dibundel lengkap.

## 2. Tanpa backend: uji negatif yang memang diharapkan

- Aplikasi tetap dapat dibuka dan demo lokal terbatas tersedia.
- Login/pendaftaran tidak dapat selesai ketika tidak ada server pada loopback.
- Kegagalan koneksi harus memberi pesan, bukan sukses palsu.
- Info Premium tetap Rp5.000; pembayaran tidak tersedia dan Premium tidak boleh terbuka otomatis.

Jangan menafsirkan kegagalan login ini sebagai tanda APK memakai source lama: APK baru tetap
membutuhkan backend. Ia tidak membawa server Python di dalam paket Android.

## 3. Jalankan backend di HP yang sama (tanpa komputer)

Gunakan Termux resmi dari F-Droid; petunjuk ini memerlukan Android yang didukung Termux.
Belum diuji langsung di HP pemilik. Instalasi paket gratis memerlukan internet dan ruang penyimpanan.

Paket kecil `English-Quest-backend-lokal-manual.zip` berisi backend/admin dan launcher, **tanpa data
akun, secret, APK atau model Vosk**. Ini paket API/admin, bukan salinan PWA lengkap.
Unduh bersama `SHA256SUMS.txt`, verifikasi hash, lalu:

```sh
pkg update
pkg install python unzip
termux-setup-storage
mkdir -p ~/eq-manual-debug
cd ~/eq-manual-debug
unzip -n ~/storage/downloads/English-Quest-backend-lokal-manual.zip
python3 tools/backend-lokal-manual.py serve
```

`unzip -n` tidak menimpa berkas yang sudah ada. Jika folder tersebut berisi versi lain, gunakan folder
baru; jangan menganggap ekstraksi yang melewatkan berkas telah memperbarui source.

Launcher hanya bind `127.0.0.1:8088`. Ia menyimpan DB dan pepper uji secara privat pada:

```text
~/.local/share/english-quest-manual-debug/
```

Pepper dibuat sekali dan digunakan lagi setelah restart; tidak dicetak. Jika DB ada tetapi pepper
hilang, launcher menolak berjalan, bukan membuat pepper baru yang merusak pencarian token.
Jangan hapus/bagikan folder ini. Menghapus data Termux akan menghilangkan database lokal.
Simpan backup konsisten DB beserta pepper secara privat jika data uji perlu dipertahankan.

Launcher sengaja **mengosongkan nomor/nama DANA dan mematikan provider otomatis**. Ini mencegah
konfigurasi produksi dari lingkungan lain ikut terpakai. Tidak ada transaksi nyata yang diperlukan.

Biarkan terminal hidup, lalu buka `http://127.0.0.1:8088/api/health` dari browser HP. Harus merespons
JSON sukses. Buka kembali APK, daftar dengan akun uji pilihan Anda (password minimal 6 karakter),
logout/login dan periksa bahwa data akun tidak hilang setelah server dibuka kembali dengan state sama.
Jangan memakai password produksi. Sistem Android dapat menghentikan Termux di background; jika itu
terjadi, server tidak tersedia sampai dijalankan lagi. Ini bukan hosting yang selalu aktif.

### Panel admin lokal

Pada sesi Termux kedua, dari folder paket yang sama:

```sh
python3 tools/backend-lokal-manual.py create-admin --email EMAIL_OPERATOR_ANDA
```

Ganti `EMAIL_OPERATOR_ANDA` dengan email operator uji yang Anda pilih. Password diminta di terminal,
minimal 12 karakter, tidak ditaruh pada argumen/percakapan. Buka `http://127.0.0.1:8088/admin` di browser
HP. Login admin harus masuk; akun biasa harus ditolak dari API admin. Pembuatan admin ini adalah
**tindakan Anda saat menguji**, bukan akun palsu yang dibuat dan disisipkan ke APK oleh agen.

Karena nomor/nama DANA sengaja kosong, tombol “Saya Sudah Membayar” harus nonaktif. Jangan mengisi
nomor contoh lalu melakukan pembayaran. Alur pending/verifikasi/token/aktivasi lengkap sudah diuji
oleh suite terisolasi menggunakan fixture sintetis, bukan saldo/transfer DANA sungguhan. Sebelum alur
itu diuji dengan dana nyata, pemilik harus mengonfirmasi penerima dan memeriksa transaksi masuk.

## 4. Alternatif backend di komputer lokal

Jalankan source backend dengan launcher yang sama di komputer, lalu gunakan `adb reverse tcp:8088
tcp:8088` dengan perangkat USB-debugging milik Anda. Setelah itu loopback HP diteruskan ke komputer.
Alamat LAN HTTP tidak didukung oleh klien ini; jangan mengganti build menjadi HTTP publik.
Pilihan ini tidak wajib: metode Termux di atas tidak memerlukan komputer.

## 5. Checklist HP — catat hasil, jangan diasumsikan lulus

- [ ] Instalasi sukses, package dan versi sesuai; jika konflik signer, hentikan tanpa menghapus data.
- [ ] Aplikasi terbuka; konten/misi/ilustrasi tetap ada, demo maksimal 5 soal Easy.
- [ ] Saat server mati: pesan koneksi benar, tidak memberi Premium.
- [ ] Saat server hidup: daftar, logout, login kembali berhasil.
- [ ] Premium menampilkan Rp5.000, sekali bayar/permanen dan pembayaran manual DANA.
- [ ] Konfigurasi kosong: pembayaran belum tersedia, tidak ada nomor rekaan, tombol bayar nonaktif.
- [ ] Akun biasa tidak dapat menyetujui/terbitkan token atau mengakses data admin.
- [ ] Tutup dan buka APK/server: akun masih dapat login dengan DB/pepper yang sama.
- [ ] Mikrofon/ASR/TTS diuji terpisah jika diperlukan; belum diverifikasi pada perangkat oleh agen.

Persistensi Premium setelah aktivasi sah telah diuji di backend SQLite/TLS dan backup/restore,
bukan pada HP. Jangan membuat token manual/flag localStorage untuk memberi kesan tes HP sudah lulus.

## 6. Rebuild dan pemulihan source

Source aplikasi yang dibangun dipulihkan dari commit `9c105ee` pada `audit-baseline-1.7.1`.
Helper baru tersedia untuk menghindari APK dengan aset Android stale:

```sh
# Pada lingkungan build dengan JDK17, SDK35, Node, Python, OpenSSL, dan debug keystore yang disetujui:
export JAVA_HOME=/path/ke/jdk17
export ANDROID_HOME=/path/ke/android-sdk
bash tools/build-apk-debug-manual.sh
```

Ganti path dengan instalasi nyata. Skrip memeriksa parity/bridge, menjalankan seluruh tes,
`assembleDebug`, lalu memverifikasi APK. Tidak deploy/push/sign release. `cryptography` diperlukan
untuk menjalankan tes adapter otomatis lama tanpa skip, bukan untuk backend manual runtime.
Hasil Gradle: `english-quest-android/app/build/outputs/apk/debug/app-debug.apk`.

Verifier mandiri: `python3 tools/verifikasi-apk-debug-manual.py PATH_APK` dengan SDK/Java tersedia.
Ia memeriksa manifest, signer valid, 17 aset terhadap source, endpoint debug di DEX, bridge,
Vosk SHA-256 dan pola berkas/kredensial yang tidak diharapkan. Ini bukan pengganti uji HP atau audit
rahasia menyeluruh. Build rilis/identitas produksi tetap tidak diubah.
