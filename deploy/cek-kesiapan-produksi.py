#!/usr/bin/env python3
"""Pemeriksa kesiapan produksi English Quest API.

Skrip ini TIDAK melakukan deployment dan TIDAK menyentuh database produksi. Ia:
  1. memeriksa konfigurasi (env) yang akan dipakai di server produksi;
  2. menjalankan instance UJI sementara (database di folder sementara) untuk membuktikan
     konfigurasi itu benar-benar bekerja — baik mode TLS langsung maupun mode
     reverse proxy (proxy TLS tiruan di depan, persis seperti Caddy/nginx);
  3. melaporkan setiap pemeriksaan sebagai LULUS / PERINGATAN / GAGAL.

Contoh:
  python3 deploy/cek-kesiapan-produksi.py --env-file /etc/english-quest/api.env
  python3 deploy/cek-kesiapan-produksi.py --mode proxy            # dari variabel lingkungan
  python3 deploy/cek-kesiapan-produksi.py --mode tls
  python3 deploy/cek-kesiapan-produksi.py --tanpa-self-test       # hanya periksa konfigurasi

Kode keluar: 0 = pemeriksaan lokal lulus (BUKAN siap online), 1 = ada GAGAL, 2 = pemakaian salah.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ASAL_WEBVIEW = "https://appassets.androidplatform.net"
SERTIFIKAT_UJI = None
KATA_SANDI_UJI = "ab12cd"        # sintetis, tepat 6 karakter (kontrak minimum)
KATA_SANDI_PENDEK = "ab12c"      # sintetis, 5 karakter (harus ditolak)
LOOPBACK = ("127.0.0.1", "::1", "localhost")

ROOT = Path(__file__).resolve().parent.parent


class Laporan:
    def __init__(self) -> None:
        self.baris: list[tuple[str, str, str]] = []

    def lulus(self, label: str, keterangan: str = "") -> None:
        self.baris.append(("LULUS", label, keterangan))

    def ingat(self, label: str, keterangan: str = "") -> None:
        self.baris.append(("PERINGATAN", label, keterangan))

    def gagal(self, label: str, keterangan: str = "") -> None:
        self.baris.append(("GAGAL", label, keterangan))

    def jumlah(self, status: str) -> int:
        return sum(1 for s, _, _ in self.baris if s == status)

    def cetak(self) -> int:
        print("\n=== Hasil pemeriksaan ===")
        for status, label, keterangan in self.baris:
            tanda = {"LULUS": "OK ", "PERINGATAN": "!! ", "GAGAL": "XX "}[status]
            ekor = f" — {keterangan}" if keterangan else ""
            print(f"  [{tanda}] {label}{ekor}")
        print(f"\nRingkasan: {self.jumlah('LULUS')} lulus, "
              f"{self.jumlah('PERINGATAN')} peringatan, {self.jumlah('GAGAL')} gagal")
        return 1 if self.jumlah("GAGAL") else 0


def baca_env_file(path: Path) -> dict[str, str]:
    nilai: dict[str, str] = {}
    for baris in path.read_text(encoding="utf-8").splitlines():
        baris = baris.strip()
        if not baris or baris.startswith("#") or "=" not in baris:
            continue
        kunci, _, isi = baris.partition("=")
        nilai[kunci.strip()] = isi.strip().strip('"').strip("'")
    return nilai


def periksa_konfigurasi(cfg: dict[str, str], laporan: Laporan) -> tuple[str, str]:
    """Kembalikan (mode, host_bind) dan catat temuan ke laporan."""
    print("=== Memeriksa konfigurasi ===")

    pepper = cfg.get("EQ_TOKEN_PEPPER", "")
    if not pepper:
        laporan.gagal("EQ_TOKEN_PEPPER tersedia", "belum diisi")
    elif "GANTI" in pepper.upper() or "CONTOH" in pepper.upper():
        laporan.gagal("EQ_TOKEN_PEPPER diisi nilai sebenarnya", "masih memakai nilai contoh")
    elif len(pepper.encode()) < 32:
        laporan.gagal("EQ_TOKEN_PEPPER >= 32 byte", f"hanya {len(pepper.encode())} byte")
    else:
        laporan.lulus("EQ_TOKEN_PEPPER >= 32 byte", "nilai tidak ditampilkan")

    db_text = cfg.get("EQ_DB_PATH", "")
    if not db_text:
        laporan.gagal("EQ_DB_PATH diisi", "wajib: database harus punya lokasi tetap")
    else:
        db = Path(db_text)
        if not db.is_absolute():
            laporan.gagal("EQ_DB_PATH absolut", f"'{db_text}' bukan jalur absolut")
        elif db.parent == ROOT or ROOT in db.parents:
            laporan.ingat("Database di luar folder rilis",
                          "disarankan /var/lib/english-quest agar rilis baru tidak menimpanya")
        elif not os.access(db.parent if db.parent.exists() else db.parent.parent, os.W_OK):
            laporan.gagal("Folder database dapat ditulis", f"tidak bisa menulis ke {db.parent}")
        else:
            laporan.lulus("Folder database dapat ditulis", str(db.parent))

    origins = [item.strip() for item in cfg.get("EQ_ALLOWED_ORIGINS", "").split(",") if item.strip()]
    if not origins:
        laporan.gagal("EQ_ALLOWED_ORIGINS diisi",
                      "tanpa ini WebView APK akan memblokir respons API (CORS)")
    elif "*" in origins:
        laporan.gagal("EQ_ALLOWED_ORIGINS tidak memakai '*'", "sebutkan origin secara eksplisit")
    elif ASAL_WEBVIEW not in origins:
        laporan.gagal("EQ_ALLOWED_ORIGINS memuat origin WebView APK", ASAL_WEBVIEW)
    elif any(not o.startswith("https://") for o in origins):
        laporan.ingat("Semua origin memakai https://", f"ada origin non-HTTPS: {origins}")
    else:
        laporan.lulus("EQ_ALLOWED_ORIGINS benar", ", ".join(origins))

    host = cfg.get("EQ_HOST", "127.0.0.1")
    sertifikat = cfg.get("EQ_TLS_CERT_FILE", "")
    kunci = cfg.get("EQ_TLS_KEY_FILE", "")
    trust_proxy = cfg.get("EQ_TRUST_PROXY", "") == "1"
    insecure_dev = cfg.get("EQ_ALLOW_INSECURE_DEV", "") == "1"

    if insecure_dev:
        laporan.gagal("EQ_ALLOW_INSECURE_DEV tidak aktif di produksi",
                      "variabel ini hanya untuk uji lokal (HTTP polos)")

    if sertifikat and kunci:
        mode = "tls"
        for label, jalur in (("Sertifikat TLS", sertifikat), ("Kunci privat TLS", kunci)):
            p = Path(jalur)
            if not p.is_file():
                laporan.gagal(f"{label} ada", f"tidak ditemukan: {jalur}")
            elif label.startswith("Kunci") and p.stat().st_mode & 0o077:
                laporan.ingat(f"{label} izin ketat",
                              "sebaiknya chmod 600 / hanya root yang bisa membaca")
            else:
                laporan.lulus(f"{label} ada", jalur)
        laporan.lulus("Mode TLS langsung", f"mendengarkan {host}:{cfg.get('EQ_PORT', '8088')}")
    elif trust_proxy:
        mode = "proxy"
        if host not in LOOPBACK:
            laporan.gagal("EQ_HOST loopback pada mode reverse proxy",
                          f"EQ_HOST={host}; tanpa TLS, server hanya boleh dengar loopback")
        else:
            laporan.lulus("Mode reverse proxy", f"backend {host}:{cfg.get('EQ_PORT', '8088')}, TLS oleh proxy")
    else:
        mode = "tidak-aman"
        laporan.gagal("TLS dikonfigurasi",
                      "set EQ_TLS_CERT_FILE + EQ_TLS_KEY_FILE, atau EQ_TRUST_PROXY=1 di belakang proxy HTTPS")

    port = cfg.get("EQ_PORT", "8088")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        laporan.gagal("EQ_PORT valid", f"'{port}'")
    else:
        laporan.lulus("EQ_PORT valid", port)

    if cfg.get("EQ_PAYMENT_PROVIDER"):
        laporan.gagal("EQ_PAYMENT_PROVIDER harus kosong; checkout otomatis produksi nonaktif")
    if bool(sertifikat) != bool(kunci):
        laporan.gagal("TLS cert/key harus berpasangan")
    if trust_proxy and (sertifikat or kunci):
        laporan.gagal("Pilih TLS langsung atau proxy, jangan kedua mode sekaligus")
    if sys.version_info < (3, 10):
        laporan.ingat("Versi Python", f"{sys.version.split()[0]} (dibutuhkan 3.10+)")
    else:
        laporan.lulus("Versi Python", sys.version.split()[0])
    return mode, host


def buat_sertifikat_uji(tmp: Path) -> tuple[Path, Path]:
    sertifikat, kunci = tmp / "uji-cert.pem", tmp / "uji-key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", str(kunci), "-out", str(sertifikat),
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)
    os.chmod(kunci, 0o600)
    return sertifikat, kunci


def tunggu_port(port: int, batas: float = 15.0) -> bool:
    tenggat = time.time() + batas
    while time.time() < tenggat:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def jalankan_backend(overrides: dict[str, str], port: int) -> tuple[subprocess.Popen, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="eq-siap-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("EQ_")}
    env.update({
        "PYTHONPATH": str(ROOT),
        "EQ_HOST": "127.0.0.1",
        "EQ_PORT": str(port),
        "EQ_DB_PATH": str(tmp / "uji.sqlite3"),
        "EQ_TOKEN_PEPPER": overrides.get("EQ_TOKEN_PEPPER") or secrets.token_hex(32),
        "EQ_ALLOWED_ORIGINS": overrides.get("EQ_ALLOWED_ORIGINS", ASAL_WEBVIEW),
    })
    # override pengujian (TLS, trust proxy) menang atas nilai bawaan di atas
    env.update({k: v for k, v in overrides.items() if k.startswith("EQ_") and v})

    proses = subprocess.Popen([sys.executable, str(ROOT / "backend" / "server.py")],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not tunggu_port(port) or proses.poll() is not None:
        proses.terminate()
        proses.communicate(timeout=5)
        shutil.rmtree(tmp)
        raise RuntimeError("Backend uji gagal start; cek port uji dan dependensi.")
    return proses, tmp


class ProxyTLS(http.server.BaseHTTPRequestHandler):
    """Proxy TLS sederhana: TLS di depan, backend HTTP polos di belakang (mirip Caddy)."""

    protocol_version = "HTTP/1.1"
    backend_port = 0

    def _teruskan(self) -> None:
        panjang = int(self.headers.get("Content-Length") or 0)
        isi = self.rfile.read(panjang) if panjang else None
        koneksi = http.client.HTTPConnection("127.0.0.1", self.backend_port, timeout=20)
        header = {k: v for k, v in self.headers.items()
                  if k.lower() not in ("connection", "host", "content-length")}
        header["X-Forwarded-Proto"] = "https"
        header["X-Forwarded-For"] = self.client_address[0]
        if panjang:
            header["Content-Length"] = str(panjang)
        try:
            koneksi.request(self.command, self.path, body=isi, headers=header)
            respons = koneksi.getresponse()
            data = respons.read()
        except OSError as exc:
            self.send_response(502)
            data = json.dumps({"ok": False, "code": "PROXY_ERROR", "message": str(exc)}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        finally:
            koneksi.close()
        self.send_response(respons.status)
        for kunci, nilai in respons.getheaders():
            if kunci.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(kunci, nilai)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_OPTIONS = do_HEAD = _teruskan

    def log_message(self, *args) -> None:  # senyap
        pass


def jalankan_proxy(front_port: int, backend_port: int, sertifikat: Path, kunci: Path):
    ProxyTLS.backend_port = backend_port
    server = http.server.ThreadingHTTPServer(("127.0.0.1", front_port), ProxyTLS)
    server.daemon_threads = True
    konteks = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    konteks.minimum_version = ssl.TLSVersion.TLSv1_2
    konteks.load_cert_chain(str(sertifikat), str(kunci))
    server.socket = konteks.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    return server


def panggil(port: int, path: str, metode: str = "GET", isi: dict | None = None,
            origin: str | None = None, bearer: str | None = None,
            tambahan: dict | None = None, https: bool = True) -> tuple[int, dict, dict]:
    konteks = None
    if https:
        konteks = ssl.create_default_context(cafile=str(SERTIFIKAT_UJI))
    koneksi = (http.client.HTTPSConnection("127.0.0.1", port, timeout=20, context=konteks)
               if https else http.client.HTTPConnection("127.0.0.1", port, timeout=20))
    data = json.dumps(isi).encode() if isi is not None else None
    header = {}
    if data is not None:
        header["Content-Type"] = "application/json"
    if origin:
        header["Origin"] = origin
    if bearer:
        header["Authorization"] = f"Bearer {bearer}"
    header.update(tambahan or {})
    koneksi.request(metode, path, body=data, headers=header)
    respons = koneksi.getresponse()
    mentah = respons.read()
    try:
        payload = json.loads(mentah)
    except Exception:
        payload = {}
    hasil = (respons.status, dict(respons.getheaders()), payload)
    koneksi.close()
    return hasil


def uji_penolakan_non_https(port: int, laporan: Laporan, label: str) -> None:
    """Backend di belakang proxy harus menolak permintaan yang tidak lewat HTTPS."""
    status, _, payload = panggil(port, "/api/register", "POST",
                                 {"email": f"nonhttps-{secrets.token_hex(3)}@example.test",
                                  "password": KATA_SANDI_UJI},
                                 origin=ASAL_WEBVIEW, https=False)
    (laporan.lulus if status == 400 and payload.get("code") == "HTTPS_REQUIRED" else laporan.gagal)(
        f"{label}: menolak koneksi non-HTTPS", f"HTTP {status} {payload.get('code')}")
    status, _, payload = panggil(port, "/api/health", https=False)
    (laporan.lulus if status == 400 else laporan.gagal)(
        f"{label}: /api/health pun menolak non-HTTPS", f"HTTP {status}")


def uji_fungsional(port: int, laporan: Laporan, label: str, https: bool = True) -> None:
    email = f"siap-{secrets.token_hex(4)}@example.test"
    status, header, payload = panggil(port, "/api/health", https=https)
    (laporan.lulus if status == 200 and payload.get("ok") else laporan.gagal)(
        f"{label}: /api/health", f"HTTP {status}")

    status, header, _ = panggil(port, "/api/register", "OPTIONS", origin=ASAL_WEBVIEW, https=https)
    acao = header.get("Access-Control-Allow-Origin")
    (laporan.lulus if status in (200, 204) and acao == ASAL_WEBVIEW else laporan.gagal)(
        f"{label}: preflight CORS", f"HTTP {status}, ACAO={acao}")

    status, _, payload = panggil(port, "/api/register", "POST",
                                 {"email": email, "password": KATA_SANDI_UJI},
                                 origin=ASAL_WEBVIEW, https=https)
    sesi = payload.get("session_token", "")
    (laporan.lulus if status == 201 and sesi else laporan.gagal)(
        f"{label}: pendaftaran akun (6 karakter)", f"HTTP {status}")

    if sesi:
        status, _, payload = panggil(port, "/api/me", bearer=sesi, https=https)
        email_server = (payload.get("account") or {}).get("email")
        (laporan.lulus if status == 200 and email_server == email else laporan.gagal)(
            f"{label}: sesi membaca akun", f"HTTP {status} akun={email_server}")
        status, _, _ = panggil(port, "/api/login", "POST",
                               {"email": email, "password": KATA_SANDI_UJI},
                               origin=ASAL_WEBVIEW, https=https)
        (laporan.lulus if status == 200 else laporan.gagal)(f"{label}: login ulang", f"HTTP {status}")

    status, _, payload = panggil(port, "/api/register", "POST",
                                 {"email": f"pendek-{secrets.token_hex(3)}@example.test",
                                  "password": KATA_SANDI_PENDEK},
                                 origin=ASAL_WEBVIEW, https=https)
    (laporan.lulus if status == 400 and payload.get("code") == "WEAK_PASSWORD" else laporan.gagal)(
        f"{label}: kata sandi 5 karakter ditolak", f"HTTP {status} {payload.get('code')}")

    status, _, payload = panggil(port, "/api/register", "POST",
                                 {"email": email, "password": KATA_SANDI_UJI},
                                 origin=ASAL_WEBVIEW, https=https)
    (laporan.lulus if status == 409 and payload.get("code") == "ACCOUNT_EXISTS" else laporan.gagal)(
        f"{label}: email ganda ditolak", f"HTTP {status} {payload.get('code')}")


def uji_klien_node(port: int, laporan: Laporan, jalur: Path) -> None:
    """Jalankan kode klien asli (access-client.js) lewat HTTPS terhadap proxy uji."""
    if not jalur.is_file():
        laporan.ingat("Uji klien JS", f"berkas tidak ditemukan: {jalur}")
        return
    if not shutil.which("node"):
        laporan.ingat("Uji klien JS", "node tidak tersedia; uji dilewati")
        return
    env = dict(os.environ, NODE_EXTRA_CA_CERTS=str(SERTIFIKAT_UJI))
    env.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
    try:
        hasil = subprocess.run(["node", str(jalur), f"https://127.0.0.1:{port}"],
                               env=env, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        laporan.gagal("Uji klien JS melalui HTTPS", "waktu habis")
        return
    ringkas = [b.strip() for b in hasil.stdout.splitlines() if b.strip()][-3:]
    (laporan.lulus if hasil.returncode == 0 else laporan.gagal)(
        "Klien JS asli melalui HTTPS (daftar/login/gagal koneksi)",
        f"exit={hasil.returncode} | {' | '.join(ringkas)[:200]}")


def self_test(modes: list[str], cfg: dict[str, str], laporan: Laporan, klien_js: str = "") -> None:
    if shutil.which("openssl") is None:
        laporan.ingat("Uji TLS langsung", "openssl tidak tersedia; uji dilewati")
    global SERTIFIKAT_UJI
    for mode in modes:
        print(f"\n=== Uji fungsional sementara: mode {mode} ===")
        tmp = Path(tempfile.mkdtemp(prefix="eq-siap-sert-"))
        proses = None
        db_tmp = None
        proxy = None
        try:
            if mode == "tls":
                sertifikat, kunci = buat_sertifikat_uji(tmp)
                SERTIFIKAT_UJI = sertifikat
                proses, db_tmp = jalankan_backend(
                    {"EQ_TLS_CERT_FILE": str(sertifikat), "EQ_TLS_KEY_FILE": str(kunci)}, 8611)
                if tunggu_port(8611):
                    uji_fungsional(8611, laporan, "TLS langsung", https=True)
            else:
                proses, db_tmp = jalankan_backend({"EQ_TRUST_PROXY": "1"}, 8612)
                if tunggu_port(8612):
                    sertifikat, kunci = buat_sertifikat_uji(tmp)
                    SERTIFIKAT_UJI = sertifikat
                    proxy = jalankan_proxy(8613, 8612, sertifikat, kunci)
                    if tunggu_port(8613):
                        uji_fungsional(8613, laporan, "Reverse proxy TLS", https=True)
                        uji_penolakan_non_https(8612, laporan, "Backend polos di belakang proxy")
                        if klien_js:
                            uji_klien_node(8613, laporan, Path(klien_js))
        except Exception as exc:  # noqa: BLE001
            laporan.gagal(f"Uji fungsional mode {mode}", f"error: {exc}")
        finally:
            for srv in (proxy,):
                if srv:
                    srv.shutdown()
                    srv.server_close()
            if proses:
                proses.terminate()
                try:
                    proses.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proses.kill()
                    proses.wait()
            if db_tmp:
                shutil.rmtree(db_tmp, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Periksa kesiapan produksi English Quest API")
    parser.add_argument("--env-file", default="", help="berkas KEY=VALUE (mis. /etc/english-quest/api.env)")
    parser.add_argument("--mode", choices=["proxy", "tls", "keduanya"], default="keduanya",
                        help="mode uji fungsional (default: keduanya)")
    parser.add_argument("--tanpa-self-test", action="store_true", help="hanya periksa konfigurasi")
    parser.add_argument("--klien-js", default="", help="skrip uji klien lokal HTTPS")
    args = parser.parse_args(argv)

    print("=== Pemeriksa kesiapan produksi English Quest ===")
    if args.env_file:
        jalur = Path(args.env_file)
        if not jalur.is_file():
            print(f"GAGAL: berkas env tidak ditemukan: {jalur}", file=sys.stderr)
            return 2
        print(f"Konfigurasi dibaca dari: {jalur}")
        cfg = baca_env_file(jalur)
    else:
        print("Konfigurasi dibaca dari variabel lingkungan proses ini.")
        cfg = {k: v for k, v in os.environ.items() if k.startswith("EQ_")}

    laporan = Laporan()
    mode, _ = periksa_konfigurasi(cfg, laporan)
    if not args.tanpa_self_test:
        uji = ["proxy", "tls"] if args.mode == "keduanya" else [args.mode]
        self_test(uji, cfg, laporan, args.klien_js)
    print("\nCatatan: pengujian di atas memakai database SEMENTARA di folder sementara "
          "dan tidak menyentuh database produksi.")
    print("Lulus hanya berarti pemeriksaan lokal; HTTPS publik dan HP belum diuji.")
    return laporan.cetak()


if __name__ == "__main__":
    raise SystemExit(main())
