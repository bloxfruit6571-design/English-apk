# Pilihan hosting English Quest — keputusan belum dibuat

Diperiksa 15 Oktober 2026 melalui dokumentasi resmi. Harga USD/bulan, bukan konversi rupiah;
pajak, kurs kartu, bandwidth berlebih, backup eksternal dan domain dapat menambah biaya.
Harga dan ketersediaan harus diperiksa lagi pada checkout. Tidak ada akun/layanan yang dibuat.

## Ringkasan pilihan

| Pilihan | Biaya/batas resmi | Kesesuaian proyek saat ini |
|---|---|---|
| **Render paid + disk** | Compute 512 MB US$7 + disk US$0,25/GB/bulan. Contoh 1 GB disk: **US$7,25/bulan** sebelum tambahan; workspace Hobby US$0 + compute. [1](https://render.com/pricing) | Pilihan terkelola yang masuk akal. Python, subdomain `onrender.com`, konfigurasi env/secret, health check dan disk didukung. [2](https://render.com/docs/web-services) TLS terkelola tersedia. [3](https://render.com/docs/free) **Belum plug-and-play:** backend menolak listener HTTP non-loopback; perlu adapter/proxy platform yang diuji dahulu. |
| **Railway Hobby** | **Minimum US$5/bulan**, termasuk US$5 penggunaan; penggunaan di atasnya dibayar selisihnya. Jangan menganggap US$5 batas maksimum. [4](https://docs.railway.com/pricing/plans) | Domain bawaan + SSL otomatis, environment/secret variables dan volume persisten tersedia. [5](https://docs.railway.com/networking/public-networking) [6](https://docs.railway.com/variables) [7](https://docs.railway.com/volumes) Perlu konfigurasi proses Python/port, batas kepercayaan proxy, volume serta backup yang spesifik platform; belum dibuat. |
| **VPS Linux (contoh DigitalOcean)** | Basic 512 MiB/10 GiB SSD **US$4**, atau 1 GiB/25 GiB SSD **US$6/bulan**. [8](https://www.digitalocean.com/pricing/droplets) | Paling dekat dengan template systemd + Caddy/nginx yang tersedia. Saya menyarankan 1 GiB sebagai titik awal, bukan jaminan kapasitas. Operator mengurus Python, env file, HTTPS, patch OS, firewall, monitoring dan backup; perlu nama DNS yang dikuasai atau solusi hostname yang disetujui. Domain dan backup belum termasuk estimasi ini. |
| **PythonAnywhere** | Beginner **US$0**, 1 web app, 512 MB file storage, outbound internet terbatas, tanpa scheduled/always-on tasks; Developer **US$10/bulan**, 5 GB disk, outbound unrestricted. SSL gratis dan subdomain bawaan tersedia. [9](https://www.pythonanywhere.com/pricing/) | Bukan jalur langsung: dokumentasi web app memakai WSGI, sementara source kita `ThreadingHTTPServer`, bukan aplikasi WSGI. [10](https://help.pythonanywhere.com/pages/Flask) Perlu adapter yang diuji dan peninjauan SQLite/backup. Batas outbound gratis juga perlu ditinjau sebelum DANA. Tidak dipilih otomatis. |

## Yang gratis: cocok untuk apa?

**Render Free tidak cocok untuk database SQLite akun nyata.** Filesystem berubah menjadi hilang
saat restart/redeploy/spin-down, persistent disk tidak tersedia, service tidur setelah 15 menit
idle (bangun sekitar satu menit), dan ada 750 jam instance gratis/bulan. PostgreSQL gratis juga
kedaluwarsa 30 hari; mengganti SQLite dengan PostgreSQL membutuhkan migrasi kode dan persetujuan,
bukan sekadar mengganti `EQ_DB_PATH`. [3](https://render.com/docs/free)

**Railway Trial bukan hosting gratis permanen tanpa risiko.** Trial memberi US$5 sampai 30 hari;
sesudah batas waktu/kredit, Free menyediakan US$1 kredit bulanan. Trial terbatas dapat membatasi
outbound. Volume trial dihapus 30 hari setelah kredit berakhir; ekspor backup sebelum kedaluwarsa.
Jangan menaruh data pelanggan yang tidak punya backup di sana. [11](https://docs.railway.com/pricing/free-trial)
Layanan volume tersedia, tetapi batas dan retensi bergantung paket. [4](https://docs.railway.com/pricing/plans)
Kecukupan kredit untuk terus aktif belum diukur; jangan menganggap Free cukup untuk produksi.

**Tidak ada rekomendasi gratis permanen yang saya nyatakan telah memenuhi semua kebutuhan
source sekarang.** Kalau anggaran nol, kita dapat merencanakan pilot terbatas dengan data uji
setelah persetujuan, bukan menjanjikan akun pengguna akan aman dan selalu tersedia.

## Domain sendiri tidak selalu perlu

Render memberikan subdomain bawaan; Railway menyediakan domain dan sertifikat otomatis.
Jadi membeli domain belum wajib untuk dua pilihan itu. URL spesifik baru sah setelah layanan
benar-benar dibuat; tidak ada nama host produksi yang ditebak atau ditanam ke APK. [2](https://render.com/docs/web-services) [5](https://docs.railway.com/networking/public-networking)

## Kesenjangan kompatibilitas yang perlu dibereskan setelah memilih

Source `backend/server.py:build_server()` mengizinkan HTTP hanya pada loopback meskipun
`EQ_TRUST_PROXY=1`; tidak membaca `PORT` platform secara otomatis. Render mensyaratkan bind
`0.0.0.0` pada port yang bisa dijangkau ingress, default 10000. [2](https://render.com/docs/web-services)
Maka template VPS saat ini **tidak boleh** dipromosikan sebagai deploy langsung Render/Railway.

Opsi setelah pilihan disetujui: proxy/container yang terkontrol atau perubahan listener
platform-specific dengan pengamanan forwarded headers, pembatasan ingress, health check,
port mapping, dan test. Saya belum melemahkan kebijakan TLS/loopback yang ada.

Sistem file persisten harus berupa mount/volume yang nyata, bukan sekadar direktori bernama
`data`. Railway, misalnya, memasang volume saat runtime, bukan build/pre-deploy. [7](https://docs.railway.com/volumes)
SQLite dipakai satu instance penulis; jangan otomatis mengaktifkan multi-replica.

## Rekomendasi dan titik berhenti

- Jika mengutamakan kemudahan tanpa membeli domain: pilih **Render paid + disk**, dengan anggaran
  awal US$7,25/bulan plus kemungkinan tambahan, lalu siapkan adapter sebelum deployment.
- Jika ingin mulai dengan eksperimen dan memahami batas kredit: **Railway Trial**, tetapi buat
  rencana ekspor/backup dan keputusan setelah trial; jangan otomatis upgrade berbayar.
- Jika mengutamakan sedikit perubahan pada backend: **VPS**, dengan beban administrasi lebih tinggi.
- Jika anggaran harus nol: berhenti pada rencana pilot; jangan mengaktifkan layanan berbayar.

**Memilih salah satu hanya menyetujui persiapan konfigurasi.** Pembuatan akun, aktivasi biaya,
deployment produksi, kredensial merchant dan transaksi tetap membutuhkan persetujuan terpisah.

## DANA terpisah dari biaya hosting

DANA Hosted Checkout memerlukan pendaftaran bisnis di Merchant Portal, solusi pembayaran yang
dipilih, testing credentials, webhook dan redirect URL. Alurnya mencakup Create Order dan
notifikasi pembayaran; source saat ini belum mengimplementasikan checkout live. [12](https://dashboard.dana.id/api-docs-v2/guide/payment-gateway/hosted-checkout)
Dokumen yang diperiksa tidak menetapkan biaya merchant untuk akun Anda; biaya/minimum transaksi
Rp5.000 harus dikonfirmasi pada kontrak/onboarding, bukan dikarang.
