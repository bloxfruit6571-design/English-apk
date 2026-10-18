# Persiapan backend HTTPS English Quest

> **Pembaruan pembayaran:** gunakan [DANA–WhatsApp](../PEMBAYARAN-DANA-WHATSAPP.md).
> Isi `EQ_DANA_MANUAL_NUMBER`/`EQ_DANA_MANUAL_NAME`/`EQ_WHATSAPP_ADMIN_NUMBER` hanya setelah penerima sah dikonfirmasi.
> Biarkan `EQ_PAYMENT_PROVIDER` kosong; kredensial merchant/webhook tidak diperlukan.
> Template proxy publik masih memblokir admin: siapkan jalur operator privat HTTPS yang disetujui,
> bukan membuka port HTTP backend. Source ini belum dideploy atau diuji melalui internet.


**Status: template lokal, belum di-deploy. Belum siap untuk pendaftaran online.**
Basis kode diverifikasi langsung dari branch `audit-baseline-1.7.1`, tip `502ec4c`.
Berkas deployment ini belum ada di GitHub: terapkan patch yang disertakan ke klon branch tersebut,
atau kirim paket source yang disertakan. Jangan memakai APK lama di root repositori.

## Keputusan yang diperlukan sebelum deployment

- Pilih server Linux dengan Python 3.10+, systemd, disk persisten, akses sudo dan port 80/443.
  VPS kecil dapat menjadi titik awal; kapasitas nyata harus diuji terhadap beban pengguna.
- Tentukan domain/subdomain yang Anda kuasai dan DNS A/AAAA yang cocok dengan server.
  Domain milik penyedia hosting dengan HTTPS juga bisa dipakai, asalkan disk persisten tersedia.
- Setujui penyedia, biaya, deployment, lokasi data dan tujuan backup di luar server.
- Jangan kirim token, SSH private key, pepper, kata sandi atau signing key melalui percakapan.
  Simpan rahasia langsung di server/secret manager; saya hanya membutuhkan pilihan dan origin publik.

## Kompatibilitas hosting — keputusan belum dipilih

Konfigurasi ini ditujukan untuk Linux/systemd dengan reverse proxy pada host yang sama.
Backend menolak bind HTTP non-loopback walaupun `EQ_TRUST_PROXY=1`. Jangan langsung
mengganti `EQ_HOST=0.0.0.0` untuk Render/Railway: perlu desain adapter/proxy platform dan
uji forwarded-header, port, health check serta volume setelah penyedia dipilih.
PythonAnywhere memerlukan interface WSGI; backend saat ini bukan aplikasi WSGI.
Lihat `PILIHAN-HOSTING.md`; belum ada layanan yang dibuat.

Unit API menjalankan pemeriksaan konfigurasi sebelum start (`ExecStartPre`).
Ini menolak placeholder pepper/mode dev, bukan bukti HTTPS publik atau persistensi disk.

## Konfigurasi server (tidak boleh masuk APK)

| Variabel | Konfigurasi |
|---|---|
| `EQ_TOKEN_PEPPER` | Rahasia acak minimal 32 byte, dibuat sekali dan disimpan stabil. Bukan placeholder. Cadangkan terenkripsi terpisah dari DB. Perubahan memengaruhi token Premium dan identitas kuota; jangan rotasi tanpa migrasi. |
| `EQ_DB_PATH` | `/var/lib/english-quest/english-quest.sqlite3`; di luar folder kode, disk persisten. |
| `EQ_ALLOWED_ORIGINS` | `https://appassets.androidplatform.net`; tambah origin PWA HTTPS hanya bila memang dipakai. Bukan wildcard. |
| `EQ_HOST`, `EQ_PORT` | `127.0.0.1`, `8088` untuk proxy satu host. Jangan buka port ini di firewall. |
| `EQ_TRUST_PROXY` | `1` hanya bila proxy TLS tepercaya berada di host yang sama. Proxy mengganti X-Forwarded-For, bukan meneruskan nilai kiriman klien. |
| `EQ_TLS_CERT_FILE`, `EQ_TLS_KEY_FILE` | Alternatif TLS langsung: keduanya wajib valid, fullchain dan private key cocok. Nonaktifkan `EQ_TRUST_PROXY` pada mode ini. |
| `EQ_ALLOW_INSECURE_DEV` | Tidak diaktifkan di produksi. |
| Pembayaran | Biarkan tidak terkonfigurasi. Tidak ada transaksi, sandbox atau DANA yang diaktifkan oleh paket ini. |

`EQ_API_BASE_URL` hanya origin HTTPS publik untuk **build APK**, bukan rahasia server.
`EQ_ALLOW_HTTP_DEV=0` dipaksakan oleh skrip build release. Versi dan applicationId tidak diubah.

## Pemasangan yang dilakukan operator setelah persetujuan

Perintah berikut adalah panduan, **belum dijalankan di server mana pun**.

1. Pasang source branch yang telah ditambahi patch ke `/opt/english-quest`.
   Buat pengguna layanan `englishquest`; kode sebaiknya root-owned dan hanya dapat dibaca layanan.
2. Buat direktori berikut:

```sh
sudo install -d -m 700 -o englishquest -g englishquest /var/lib/english-quest
sudo install -d -m 750 -o root -g englishquest /etc/english-quest
sudo install -m 640 -o root -g englishquest deploy/env.produksi.example /etc/english-quest/api.env
```

3. Ganti placeholder pepper pada `api.env` melalui editor aman. Buat nilai acak dengan CSPRNG,
   jangan memasukkannya ke Git, shell history, log atau APK. Simpan salinan terenkripsi secara terpisah.
4. Jalankan pemeriksa sebagai pengguna layanan:

```sh
sudo -u englishquest python3 /opt/english-quest/deploy/cek-kesiapan-produksi.py \
  --env-file /etc/english-quest/api.env
```

Pemeriksa memvalidasi sebagian konfigurasi dan menjalankan fixture lokal **terpisah** dengan DB,
pepper serta sertifikat sintetis. Ia tidak menguji domain, sertifikat produksi, nginx/Caddy asli,
volume setelah reboot, atau kredensial produksi. Lulus bukan izin untuk menyatakan siap online.
Pastikan port uji 8611–8613 kosong. OpenSSL diperlukan; Node diperlukan untuk opsi `--klien-js`.

5. Pasang unit API dan mulai layanan:

```sh
sudo cp deploy/englishquest-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now englishquest-api
```

Unit memakai `ReadWritePaths=/var/lib/english-quest`, `UMask=0077` dan pengguna non-root.
Bila lokasi DB berubah, ubah unit juga. Jangan taruh DB di `/tmp`, folder rilis, atau filesystem
container yang dapat dibuang. Gunakan satu instance penulis; SQLite bukan konfigurasi multi-replika.

## HTTPS: pilih satu proxy

### Caddy (pilihan lebih sederhana)

Pasang versi Caddy yang mendukung konfigurasi ini. Arahkan DNS dahulu, buka 80/443,
ganti `GANTI_DOMAIN_ANDA`, lalu:

```sh
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy mengelola sertifikat otomatis. Template menolak semua path selain `/api/*`, juga
menolak `/admin*` dan `/api/admin*`. Access log tidak diaktifkan. Tidak ada akses panel admin
publik; jangan menjalankan server kedua dengan HTTP dev sebagai jalan pintas.
Pengelolaan admin memerlukan jalur privat tersendiri yang disetujui (VPN/proxy privat TLS).

### nginx (alternatif)

Template `nginx-englishquest.conf` memerlukan sertifikat **sudah tersedia**. Jangan langsung
memuat konfigurasi 443 sebelum berkas sertifikat ada. Untuk bootstrap:

1. Pasang konfigurasi HTTP-only sementara untuk domain asli, dengan
   `/.well-known/acme-challenge/` dari `/var/www/html`, tanpa proxy API publik.
2. Uji `nginx -t`, reload, lalu jalankan `certbot certonly --webroot -w /var/www/html -d <domain-asli>`.
3. Ganti placeholder pada template final, pasang, jalankan `nginx -t`, lalu reload.
4. Uji `certbot renew --dry-run` dan atur deploy hook untuk reload nginx setelah pembaruan.

Template menghapus kepercayaan pada X-Forwarded-For kiriman klien dengan memakai `$remote_addr`.
Jika nanti memakai CDN/load balancer di depan proxy, konfigurasi IP tepercaya harus ditinjau lagi.
**Konfigurasi nginx/Caddy/systemd belum dijalankan di sandbox ini.**

## Persistensi, backup dan pemulihan

- DB berada di disk/volume persisten; folder mode 0700 dan berkas dibuat dengan umask 0077.
- `cadangkan-db.py` menggunakan SQLite Backup API, bukan `cp` saat DB hidup (termasuk WAL).
- Setiap backup diperiksa dengan `integrity_check`, diberi checksum, lalu dirotasi (14 snapshot).
- Backup harian 03:15 menurut timezone server (+ jitter). Contoh cron alternatif juga tersedia;
  jangan aktifkan keduanya sekaligus. Buat folder `cadangan` sebelum cron menulis log.

```sh
sudo cp deploy/cadangan.service deploy/cadangan.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start cadangan.service
sudo systemctl enable --now cadangan.timer
sudo systemctl status cadangan.service --no-pager
sudo systemctl list-timers cadangan.timer
```

Timer membawa `EQ_DB_PATH` dari EnvironmentFile. Pemeriksaan manual:

```sh
sudo -u englishquest python3 /opt/english-quest/deploy/cadangkan-db.py periksa \
  --db /var/lib/english-quest/english-quest.sqlite3
```

Cadangan mengandung data akun, salt/hash password dan hash sesi: tetap data sensitif.
Salin keluar server lewat kanal terenkripsi ke akun/lokasi pilihan Anda; atur akses terbatas,
retensi, pemantauan kegagalan dan uji restore berkala. Pepper/kunci dicadangkan terenkripsi
secara terpisah. Jadwal harian berarti potensi kehilangan hingga sekitar 24 jam data;
ini target awal, bukan jaminan RPO/RTO yang sudah diuji.

**Restore tidak boleh menimpa DB aktif.** Skrip hanya menerima tujuan baru tanpa WAL/SHM:

```sh
python3 deploy/cadangkan-db.py pulihkan --db /var/lib/english-quest/restored.sqlite3 \
  --dari /lokasi/backup.sqlite3 --saya-yakin
```

Berkas `.sha256` pendamping wajib tersedia dan cocok. Uji data staging dahulu. Setelah ada
persetujuan pemulihan: hentikan API dan timer; simpan DB lama beserta WAL/SHM, jangan hapus;
ubah `EQ_DB_PATH` ke staging yang telah diverifikasi, pastikan owner/izin benar; restart;
uji login dan backup. Jangan menyalin snapshot di atas DB hidup. Rencana rollback: berhenti lagi
lalu kembalikan path lama. Langkah pergantian DB **tidak** dijalankan oleh skrip.

## Setelah HTTPS publik tersedia

1. Verifikasi DNS, sertifikat/hostname dan `/api/health` dari jaringan luar tanpa `-k`.
2. Periksa OPTIONS CORS dengan origin WebView dan bahwa port 8088 tak terbuka publik.
3. Dengan izin akun uji: daftar password 6, tolak 5, login, baca akun, cek duplikat serta offline.
   Jangan mencetak session token/password; jangan menghapus akun pengguna saat membersihkan uji.
4. Uji persistensi setelah restart/reboot serta backup/restore staging.
5. Bangun hanya dengan origin yang benar:

```sh
bash tools/build-apk-untuk-domain.sh https://<origin-nyata-yang-sudah-disetujui>
```

Skrip memeriksa health HTTPS read-only, paritas aset, memaksa release dan flag HTTP dev 0,
lalu memverifikasi isi APK. Hasil release masih **unsigned**; gunakan signing key milik pemilik
aplikasi dengan persetujuan. Jangan membuat key baru jika aplikasi sudah punya identitas signing.
Verifikasi `apksigner` dan SHA-256 setelah signing. Build debug memiliki applicationId berbeda;
perpindahan ke release tidak otomatis memindahkan progres lokal.

6. Instal APK bertanda tangan pada HP nyata, uji melalui Wi-Fi **dan data seluler tanpa adb reverse**.
   Barulah hasil pendaftaran online boleh dinyatakan berhasil.
