#!/usr/bin/env python3
"""Periksa APK sebelum dibagikan: pastikan konfigurasi uji-lokal tidak terbawa.

Pemakaian:
  python3 tools/verifikasi-apk-produksi.py <apk> [https://origin-yang-diharapkan]

Pemeriksaan:
  1. Tidak ada berkas XML izin-cleartext khusus debug di dalam paket.
  2. AndroidManifest: usesCleartextTraffic=false dan tanpa networkSecurityConfig
     (memakai aapt2 bila tersedia; bila tidak, dilaporkan sebagai peringatan).
  3. classes.dex tidak memuat alamat loopback (127.0.0.1/localhost/10.0.2.2).
  4. classes.dex memuat origin HTTPS yang diminta (bila diberikan).
  5. Aset web memakai kebijakan kata sandi 6 karakter.
Kode keluar: 0 = lolos, 1 = ada temuan, 2 = pemakaian salah.
"""
from __future__ import annotations

import glob
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

TERLARANG_DI_DEX = (b"127.0.0.1", b"localhost", b"10.0.2.2")
NSC_DEBUG = "res/xml/eq_network_security_config_debug.xml"


def cari_aapt2() -> str | None:
    kandidat = [os.path.join(os.environ.get("ANDROID_HOME", ""), "build-tools", "*", "aapt2")]
    kandidat.append(os.path.join(os.environ.get("ANDROID_SDK_ROOT", ""), "build-tools", "*", "aapt2"))
    for pola in kandidat:
        for path in sorted(glob.glob(pola), reverse=True):
            if os.access(path, os.X_OK):
                return path
    return shutil.which("aapt2")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    apk = Path(argv[1])
    origin = argv[2].rstrip("/") if len(argv) > 2 else ""
    if not apk.is_file():
        print(f"GAGAL: APK tidak ditemukan: {apk}")
        return 1

    temuan: list[tuple[str, str, str]] = []

    def catat(status: str, label: str, keterangan: str = "") -> None:
        temuan.append((status, label, keterangan))

    sidik = hashlib.sha256(apk.read_bytes()).hexdigest()
    print(f"APK      : {apk}")
    print(f"Ukuran   : {apk.stat().st_size} byte")
    print(f"SHA-256  : {sidik}")

    paket = zipfile.ZipFile(apk)
    nama = set(paket.namelist())

    catat("GAGAL" if NSC_DEBUG in nama else "LULUS",
          "Tidak ada XML izin cleartext khusus debug",
          "berkas debug ditemukan" if NSC_DEBUG in nama else "")

    dex = b"".join(paket.read(n) for n in nama if n.endswith(".dex"))
    sisa = [pola.decode() for pola in TERLARANG_DI_DEX if pola in dex]
    catat("GAGAL" if sisa else "LULUS", "Tidak ada alamat loopback di dalam APK",
          f"ditemukan: {', '.join(sisa)}" if sisa else "")

    for metode in (b"getApiBaseUrl", b"isHttpDevAllowed"):
        catat("LULUS" if metode in dex else "GAGAL",
              f"Jembatan native {metode.decode()} ada di dex",
              "" if metode in dex else "APK tampaknya dibangun dari sumber lama tanpa jembatan")

    if origin and not origin.startswith("https://"):
        catat("GAGAL", "Origin wajib HTTPS")
    if origin:
        catat("LULUS" if origin.encode() in dex else "GAGAL",
              f"Origin produk tertanam: {origin}",
              "" if origin.encode() in dex else "origin tidak ditemukan di dex")
    else:
        catat("GAGAL", "Origin belum ditentukan: bukan APK siap online",
              "pemeriksaan struktur boleh dilanjutkan, tetapi jangan distribusikan")

    app_js = paket.read("assets/app.js").decode("utf-8", "replace") if "assets/app.js" in nama else ""
    if not app_js:
        catat("GAGAL", "assets/app.js ada", "berkas aset hilang")
    else:
        catat("LULUS" if "PASSWORD_MIN_LENGTH=6" in app_js else "GAGAL",
              "Klien memakai batas kata sandi 6 karakter",
              "" if "PASSWORD_MIN_LENGTH=6" in app_js else "konstanta 6 tidak ditemukan")
        catat("LULUS" if "minimal 12 karakter" not in app_js else "GAGAL",
              "Tidak ada sisa aturan 12 karakter")

    aapt2 = cari_aapt2()
    if aapt2:
        keluaran = subprocess.run([aapt2, "dump", "xmltree", "--file", "AndroidManifest.xml", str(apk)],
                                  capture_output=True, text=True).stdout
        baris = [b for b in keluaran.splitlines() if "usesCleartextTraffic" in b or "networkSecurityConfig" in b]
        cleartext = next((b for b in baris if "usesCleartextTraffic" in b), "")
        catat("LULUS" if cleartext.strip().endswith("=false") else "GAGAL",
              "usesCleartextTraffic=false", cleartext.strip().split("=")[-1] if cleartext else "tidak ada atribut")
        nsc = [b for b in baris if "networkSecurityConfig" in b]
        catat("GAGAL" if nsc else "LULUS", "Tidak memakai networkSecurityConfig khusus",
              nsc[0].strip() if nsc else "")
        badging = subprocess.run([aapt2, "dump", "badging", str(apk)],
                                 capture_output=True, text=True).stdout.splitlines()
        if badging:
            print(f"Paket    : {badging[0][:160]}")
    else:
        catat("GAGAL", "Pemeriksaan AndroidManifest",
              "aapt2 tidak ditemukan; jalankan dengan ANDROID_HOME yang benar untuk memastikan nilai cleartext")

    print("\n=== Hasil ===")
    for status, label, keterangan in temuan:
        tanda = {"LULUS": "OK ", "PERINGATAN": "!! ", "GAGAL": "XX "}[status]
        ekor = f" — {keterangan}" if keterangan else ""
        print(f"  [{tanda}] {label}{ekor}")
    gagal = sum(1 for s, _, _ in temuan if s == "GAGAL")
    print(f"\n{'PEMERIKSAAN STATIS LOLOS — uji HTTPS publik/HP tetap wajib' if not gagal else f'{gagal} TEMUAN GAGAL — jangan dibagikan sebelum diperbaiki'}")
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
