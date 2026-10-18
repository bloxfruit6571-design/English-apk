# Hasil aktual — konfigurasi pemilik

Run final: `tools/build-apk-debug-manual.sh`, log `final-pipeline.txt`.
- Python unittest discover: **126 tes, 31.508 detik, OK**.
- Node seluruh `tests/*.test.js`: **53 kelompok** = failure 8, gates 11, manual 11, owner 10, registration 13; **43 renderer rute** smoke-tested.
- **18 aset** root/Android identik; service worker **english-quest-shell-v22**.
- Gradle **BUILD SUCCESSFUL**, 13 detik, 35 task (4 executed, 31 up-to-date). Build awal sebelumnya melakukan 35 task penuh; build final menyertakan koreksi newline draft sebelum kata “jika”.
- `apk-verification.json`, `apk-badging.txt`, `apk-signature.txt`: pemeriksaan APK final, bukan sekadar source. Signer cocok dengan debug lokal WhatsApp sebelumnya; package/versi tidak berubah.
- `backend-zip-smoke.txt`: server dijalankan dari ZIP yang diekstrak; health/config/JSON/admin/client HTTP 200, kontak tepat/nama kosong, tidak membuat akun/order/token. Perubahan panduan ZIP setelah smoke tidak mengubah kode yang diuji; parity kode ZIP diperiksa kembali.
- `python-baseline.txt`, `python.txt`, `node.txt` adalah run lebih awal; hasil final di atas menjadi acuan untuk draft akhir.

## Pemetaan 14 persyaratan

| # | Pemeriksaan | Bukti lokal / batasan |
|---|---|---|
| 1 | Nomor DANA tepat | Python owner + Node owner + JSON di APK |
| 2 | Nomor WA dan harga tepat; nama tidak direka | Python owner + Node owner + APK parity |
| 3 | Salin nomor dan pesan sukses persis | Node owner native/browser/textarea/gagal; native bridge ditemukan DEX. Clipboard HP **belum** diuji |
| 4 | URL internasional 6285656706104 | Python normalisasi + Node URL/allowlist |
| 5 | Draft persis termasuk paragraf/newline; editable | Literal harapan independen di Python/Node; JSON di APK; tidak ada pesan dikirim |
| 6 | Checkout otomatis hilang dari UI | Node gates/manual/owner, inspeksi client; backend legacy tidak dibongkar |
| 7 | WhatsApp/copy/screenshot tidak memberi Premium | Node no-authority + HTTP config tanpa side effect; admin memerlukan pemeriksaan dana terpisah |
| 8 | Token sah mengaktifkan akun | Test backend/manual dengan fixture terisolasi |
| 9 | Token salah ditolak | Test backend/manual/gates |
| 10 | Token dipakai akun lain ditolak; tidak sharing | Test backend/manual; hash-only dan satu token/order |
| 11 | Premium bertahan logout/relogin/reopen DB | Test backend + gates/auth; bergantung DB/pepper dipertahankan |
| 12 | Pengguna biasa tidak boleh menerbitkan | Test manual/backend: role/session/transaction checks |
| 13 | Demo maksimal 5 Easy | Backend quota + Node gates/owner copy |
| 14 | Tidak ada secret server dalam APK | Tidak ada berkas DB/.env/key/keystore dan pola credential/token/private-key yang diperiksa; bukan audit secret menyeluruh |

Tes ini tidak menghubungi DANA/WhatsApp, tidak memverifikasi identitas penerima, tidak menjalankan transfer nyata, tidak menguji Android fisik/emulator, dan tidak membuktikan HTTPS publik siap. Fixture bukan bukti transaksi pelanggan. Tidak ada deployment, rilis, atau push GitHub berhasil.
