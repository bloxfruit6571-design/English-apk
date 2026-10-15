# Laporan: Sistem Premium & Pembayaran DANA (English Quest)

Cabang kerja: `audit-baseline-1.7.1` (lokal). `main` tidak disentuh. Tidak ada deployment produksi,
tidak ada kredensial produksi, dan tidak ada transaksi nyata dalam pekerjaan ini.

Aturan bisnis yang diminta dan statusnya:

| # | Ketentuan | Status |
|---|---|---|
| 1 | Harga Rp5.000 sekali bayar | Diterapkan (`PREMIUM_PRICE_IDR = 5_000`, ditampilkan klien) |
| 2 | Akses permanen, tanpa langganan/biaya berulang | Diterapkan (tanpa kedaluwarsa; diperiksa lintas sesi/instalasi) |
| 3 | Demo maksimal 5 soal mode Easy | Diterapkan (server 5 ID unik; klien 1 misi Easy) |
| 4 | Setelah pembayaran sukses terverifikasi → 1 token Premium otomatis | Diterapkan (dalam satu transaksi; unique index 1 token/pesanan) |
| 5 | Tanpa persetujuan admin manual untuk transaksi normal | Diterapkan (tidak ada langkah admin pada jalur pembayaran) |
| 6 | Token tidak diterbitkan untuk pending/gagal/kedaluwarsa/tidak valid | Diterapkan (fail closed, diuji per status) |
| 7 | Token divalidasi backend, tidak boleh dipakai ulang untuk aktivasi | Diterapkan (klaim tunggal per akun; akun lain ditolak) |
| 8 | Setelah aktivasi semua fitur Premium terbuka permanen | Diterapkan (perilaku lama dipertahankan) |
| 9 | Belajar offline & seluruh materi lama dipertahankan | Diterapkan (tidak ada materi/soal/gambar yang dihapus) |
| 10 | Tes untuk batas Demo, transaksi, webhook, token, akses Premium | Diterapkan (34 tes baru + tes lama disesuaikan) |

---

## 1. Fitur yang sudah DITERAPKAN (ada di kode)

**Backend baru — `backend/payments.py`** (lapisan provider-agnostic, hanya standard library kecuali verifikasi RSA opsional)
- Status internal: `paid`, `pending`, `failed`, `expired`, `unverified`; semua kata status yang tidak dikenali jatuh ke `unverified` dan **tidak dapat** menyelesaikan pesanan.
- `SandboxProvider` — simulator lokal untuk pengujian: notifikasi bertanda tangan HMAC-SHA-256 (`X-EQ-Signature: sha256=<hmac>`), hanya aktif bila `EQ_PAYMENT_PROVIDER=sandbox` **dan** `EQ_ALLOW_INSECURE_DEV=1` pada listener loopback.
- `DanaProvider` — adapter resmi DANA Gapura (BI-SNAP): memverifikasi `X-SIGNATURE`/`X-TIMESTAMP` dengan skema SNAP `SHA256withRSA` atas `POST:<EndpointUrl>:lowercase(hex(SHA256(body))):<X-TIMESTAMP>` memakai **public key DANA**; `create_checkout` **selalu** menolak (`PROVIDER_NOT_READY`, HTTP 503) sampai kredensial merchant benar-benar tersedia — tidak ada endpoint/URL/kredensial yang dikarang di repo ini.
- Tanpa paket `cryptography`, verifikasi notifikasi DANA selalu gagal (tidak ada notifikasi yang dipercaya tanpa verifikasi).

**Backend — `backend/server.py`**
- `DEMO_LIMIT = 5`; bank demo kini `easy-greetings-q1…q5` (satu misi Easy lengkap).
- Tabel baru: `payment_orders` (order_id, user_id, provider, amount, currency, status, token_id, token_delivered_at, paid_at, …) dan `payment_events` (dedupe peristiwa penyedia); kolom `premium_tokens.issued_for_order_id` + **unique index** `token_issued_order_idx` (satu token per pesanan, dijamin database, bukan hanya kode).
- `Store.create_payment_order()` — hanya akun permanen (sesi tamu ditolak `ACCOUNT_REQUIRED`), satu pesanan terbuka per akun (tidak menumpuk pesanan), rate limit 6/900 detik.
- `Store.settle_payment()` — idempoten per ID peristiwa; hanya status sukses yang tanda tangannya lolos verifikasi yang menyelesaikan pesanan; nominal/mata uang **harus** cocok (kalau tidak → status `review` tanpa token); pembayaran setelah pesanan kedaluwarsa → `review`; referensi tak dikenal hanya dicatat.
- `Store.claim_payment_token()` — pemilik pesanan mengambil kode; kode dibuat saat itu, plaintext **tidak pernah** ditulis ke database (hanya HMAC), pengiriman ulang merotasi kode lama sehingga tidak ada kondisi "sudah bayar tapi kode hilang"; setelah aktivasi klaim ditolak `TOKEN_ALREADY_ACTIVATED`.
- Endpoint: `GET /api/payments/config` (publik: harga + kesiapan provider), `POST /api/payments/order`, `GET /api/payments/order?order_id=`, `POST /api/payments/claim`, `POST /api/payments/webhook` (provider asli), `POST /api/payments/sandbox/settle` (simulator lokal, hanya pemilik pesanan, hanya dev), `GET /api/admin/payments`.
- Webhook tidak memakai autentikasi sesi: kepercayaan sepenuhnya dari verifikasi tanda tangan, dan `_ensure_secure()` tetap berlaku (HTTPS/loopback dev).
- `admin_summary` menambah `orders_paid`, `orders_pending`, `orders_review`, `revenue_idr`, `payments_unsettled` (pembayaran yang belum terselesaikan = daftar kerja rekonsiliasi manusia).

**Klien web — `app.js`, `access-client.js`**
- Demo 5 soal: satu misi Easy; misi Easy kedua kini materi Premium; semua teks "10 soal" diubah; penyaring ledger lokal hanya menerima 5 ID bank demo aktif.
- Kartu pembelian di halaman Premium: harga dari server (`/api/payments/config`, fallback label Rp5.000), tombol **Bayar (DANA)**, **Periksa status**, **Terima token Premium**, pesan status berbahasa Indonesia, catatan "sekali bayar, akses permanen, tanpa langganan", dan peringatan agar **tidak** mengirim uang ke rekening pribadi.
- Klien **tidak** memuat URL/endpoint penyedia apa pun: tautan checkout hanya muncul bila server mengirimkannya; tanpa data checkout, tidak ada tautan yang dikarang.
- Setelah token diterbitkan, kode otomatis mengisi kolom aktivasi dan pengguna menekan **Aktivkan Token** (jalur verifikasi server yang sudah ada).

**Admin — `backend/admin.html`**: panel "Pesanan pembayaran Premium" (filter status, pencarian, kolom token/dibayar) + angka pesanan/pendapatan/perlu ditinjau pada ringkasan.

**Alat uji lokal** — `backend/dev_payment_flow.py`: menjalankan 18 langkah alur pembayaran end-to-end di instance lokal (tanpa uang sungguhan, tanpa provider asli) dan mencetak transkrip bukti.

---

## 2. Yang sudah DIUJI (dengan bukti)

| Uji | Hasil |
|---|---|
| `python3 -m unittest discover -s tests` | **64 tes OK** (16 `test_backend`, 14 `test_failure_paths`, 34 `test_payments`) dalam 19 s |
| `node tests/client_gates.test.js` | **11 kelompok** lulus, 43 route renderer di-smoke-test |
| `node tests/client_failure_paths.test.js` | 8 kelompok jalur gagal lulus |
| `node tests/asset_parity.test.js` | 17 aset root ≡ aset Android, cache `english-quest-shell-v17` |
| `python3 backend/dev_payment_flow.py` | 18 langkah lulus (bukti: `evidence-pembayaran-lokal.txt`) |
| Build Android (`:app:assembleDebug`) | **BUILD SUCCESSFUL**; APK debug memuat aset baru |

Cakupan `tests/test_payments.py` (34 tes): batas demo 5 soal & kuota habis; harga/mata uang Rp5.000 IDR; pesanan ganda dipakai ulang; tamu ditolak membeli; pembayaran nonaktif → 503; simulator wajib opt-in dev + secret ≥32 byte; tanda tangan palsu/dipotong/diubah → 401 tanpa token; pending/failed/unrecognized tidak menerbitkan token; nominal tidak cocok → `review`; pembayaran terlambat → `review`; referensi tak dikenal dicatat tanpa hak; **satu token otomatis per pembayaran terverifikasi**; notifikasi diulang & dua kesuksesan paralel tetap satu token; klaim → aktivasi → Premium permanen tanpa kedaluwarsa; plaintext token tidak pernah ada di berkas database; rotasi kode saat pengiriman ulang; token tidak dapat dipakai akun lain; pesanan tidak dapat dibaca/diklaim akun lain; Premium bertahan setelah sesi berakhir & store restart; verifikasi SNAP RSA DANA dengan public key (dan penolakan tanda tangan dari kunci lain); adapter DANA menolak mengarang checkout; nama provider tak dikenal mematikan pembayaran; endpoint HTTP webhook/klaim/simulator/config/admin diuji lewat HTTP sungguhan.

Bukti alur lokal (ringkas, dari `evidence-pembayaran-lokal.txt`): `…/api/me → demo 5 soal` → 5 jawaban demo diterima, soal ke-6 ditolak → order `EQO-…` Rp5.000 → webhook bertanda tangan `SUCCESS` → `token_diterbitkan=True` → webhook diulang `duplikat=True, issued=False` → webhook tanpa tanda tangan `401 INVALID_SIGNATURE` → webhook `PENDING` tidak menerbitkan token → klaim token (masked) → aktivasi → `level=premium, kedaluwarsa=None` → token yang sama di akun lain `409 TOKEN_ALREADY_USED`.

APK hasil build ulang (tidak dipublikasikan): 85.086.828 byte, SHA-256 `e6a3f2faa6750281bf16c733fe699453827016ab1483dff46811e9f1a5d0e8ae`, paket `com.englishquest.app.debug`, versionCode 13, versionName `1.7.1-debug`, sertifikat Android Debug, dan aset `app.js`/`access-client.js` di dalamnya identik dengan repositori.

---

## 3. Yang BELUM DIUJI (jangan diklaim selesai)

1. **Integrasi DANA yang sebenarnya** — tidak ada kredensial merchant, jadi tidak ada permintaan checkout DANA yang pernah dibuat dan tidak ada satu pun webhook DANA nyata (sandbox DANA) yang pernah diverifikasi.
2. **Uang sungguhan / transaksi nyata** — belum pernah, dan tidak dilakukan atas instruksi.
3. **Uji di perangkat/emulator Android** — tidak ada `/dev/kvm` di lingkungan ini, jadi APK hanya diverifikasi secara statis (isi, paket, tanda tangan) dan dibangun sukses.
4. **Deployment HTTPS publik, reverse proxy, CORS, backup, pemantauan** — belum ada; webhook produksi memerlukan URL HTTPS publik yang stabil.
5. **Perilaku WebView Android lama** pada halaman pembayaran (semua uji klien memakai Node VM).
6. **Beban/kompetisi multi-node** — SQLite + satu proses; idempotensi diuji pada satu instance.
7. **DANA Gapura `create-order`/checkout** — sengaja belum diimplementasikan (butuh kontrak resmi + kredensial). Hanya jalur *penerimaan notifikasi* yang siap.

---

## 4. Konfigurasi yang masih DIBUTUHKAN

**Untuk mengaktifkan pembayaran nyata (semuanya di server, jangan pernah di APK/repositori):**

| Variabel | Isi | Catatan |
|---|---|---|
| `EQ_PAYMENT_PROVIDER` | `dana` | `sandbox` hanya untuk pengujian lokal; kosong = pembayaran nonaktif |
| `EQ_DANA_PUBLIC_KEY` atau `EQ_DANA_PUBLIC_KEY_PATH` | public key DANA | untuk memverifikasi tanda tangan notifikasi |
| `EQ_DANA_NOTIFY_PATH` | default `/v1.0/debit/notify` | harus sama persis dengan path yang ditandatangani DANA |
| `EQ_DANA_PARTNER_ID` | dari DANA Merchant Portal | wajib untuk checkout |
| `EQ_DANA_PRIVATE_KEY_PATH` | private key merchant | **jangan** ditempel di repo |
| `EQ_DANA_CLIENT_SECRET` | dari DANA Merchant Portal | wajib untuk checkout |
| `EQ_PAYMENT_WEBHOOK_SECRET` | ≥32 byte | hanya untuk simulator lokal |
| `EQ_ALLOW_INSECURE_DEV` | `1` | hanya loopback pengembangan |

Yang masih diperlukan dari pihak luar (tidak dapat saya isi sendiri):
1. Pendaftaran bisnis di **DANA Merchant Portal**, memilih solusi pembayaran (Gapura Custom/Hosted Checkout), dan **URL webhook** + **redirect URL** yang disetujui.
2. Dokumen kontrak API DANA (nama field persis, format respons sukses, kode status alternatif) untuk menulis `create-order`/checkout dan menyempurnakan pemetaan status.
3. HTTPS publik + domain tetap untuk webhook, lalu uji sandbox DANA end-to-end.
4. Keputusan penempatan DB produksi, backup, rotasi pepper, dan pemantauan (sebelum melayani pembeli nyata).

**Catatan tegas:** transfer ke rekening DANA pribadi **tidak** dapat diverifikasi otomatis — tanpa integrasi resmi yang menyuplai data transaksi, sistem tidak dapat memastikan pembayaran sah. Karena itu harga/UI klien sengaja tidak pernah menampilkan tujuan pembayaran manual.

---

## 5. Perubahan pada tes lama (transparansi)

Aturan bisnis demo berubah dari 10 → 5 soal, jadi asersi yang mengunci angka lama **harus** disesuaikan. Tidak ada asersi yang dilonggarkan: setiap asersi yang diubah tetap gagal bila perilakunya regresi.

- `tests/test_backend.py` (7 asersi): `demo_limit`/`demo_remaining` 10/9/8 → 5/4/3 (memakai `api.DEMO_LIMIT`); tes `…ten_question_quota` → `…demo_quota` dengan pembagian kerja 3+2 (bukan 8+2 dari 10 ID) supaya uji kompetisi tetap nyata; tes `test_ten_unique_answers…` → `test_five_unique_answers…`; `demo_remaining` setelah sync 8 → 3; dua asersi HTTP demo 10 → 5.
- `tests/client_gates.test.js`: bank demo 10 ID/dua misi → 5 ID/satu misi; `missionAvailability('easy',1)` kini `false` (materi Premium); alur demo lokal berhenti di 5 soal (sebelumnya berlanjut ke misi kedua); angka kuota sisa 8/9 → 3/4; ditambah **kelompok 11** untuk alur pembelian Premium (harga dari server, tidak ada URL/endpoint yang dikarang, klaim → isi kolom aktivasi, tamu diarahkan membuat akun).
- `tests/client_failure_paths.test.js`: kuota fallback offline 10 → 5 (nama grup uji ikut diperbarui); fixture Premium memakai `demo_limit: 5`.
- Tidak ada file tes yang dihapus; `tests/test_payments.py` adalah suite baru untuk seluruh perilaku pembayaran.

---

## 6. Cara menjalankan di lokal

```sh
# 1) Semua tes
PYTHONPATH="$PWD" python3 -m unittest discover -s tests -v
node tests/client_gates.test.js && node tests/client_failure_paths.test.js && node tests/asset_parity.test.js

# 2) Alur pembayaran end-to-end (simulator, tanpa uang sungguhan)
PYTHONPATH="$PWD" python3 backend/dev_payment_flow.py

# 3) Server pengembangan dengan pembayaran simulator
export EQ_TOKEN_PEPPER="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export EQ_DB_PATH="$PWD/backend/data/development.sqlite3"
export EQ_ALLOW_INSECURE_DEV=1 EQ_HOST=127.0.0.1 EQ_PORT=8088
export EQ_PAYMENT_PROVIDER=sandbox EQ_PAYMENT_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
python3 backend/server.py    # http://127.0.0.1:8088 (PWA + /admin)
```

---

## 7. Batasan yang harus diingat

- **Belum siap produksi.** Pembayaran nyata belum dapat diterima dengan aman sebelum: integrasi DANA diselesaikan, verifikasi webhook diuji pada lingkungan DANA, HTTPS publik aktif, dan uji end-to-end dilakukan. Tidak ada klaim "siap produksi" pada laporan ini.
- APK di repositori (`English-Quest-1.7.1-debug.apk`) **masih build lama** (demo 10 soal); APK baru hasil build ulang ada di `english-quest-android/app/build/outputs/apk/debug/app-debug.apk` dan bersifat sementara (direktori `build/` tidak ikut snapshot). Keputusan: mengganti APK repo dengan build baru perlu persetujuan Anda (ukuran ±85 MB per berkas).
- Push ke GitHub masih belum bisa dari sandbox ini: tidak ada kredensial git (`could not read Username for 'https://github.com'`). Opsi: token scope minimum, push sendiri, atau saya buatkan berkas `.patch`/bundle.
- Perubahan ini hanya ada di cabang `audit-baseline-1.7.1` (lokal), bukan di `main`.
