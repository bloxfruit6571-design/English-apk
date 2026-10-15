#!/usr/bin/env python3
"""Cadangan aman untuk database English Quest (SQLite).

Kenapa tidak sekadar `cp`: server berjalan dengan mode WAL, sehingga menyalin berkas
saat server hidup bisa menghasilkan cadangan yang tidak konsisten. Skrip ini memakai
API backup bawaan modul sqlite3 (aman untuk database yang sedang dipakai), memeriksa
keutuhan hasilnya, menulis sidik jari SHA-256, lalu merotasi cadangan lama.

Perintah:
  cadangkan-db.py backup            # buat cadangan baru + rotasi
  cadangkan-db.py daftar            # tampilkan cadangan yang ada
  cadangkan-db.py periksa           # periksa keutuhan cadangan terbaru
  cadangkan-db.py pulihkan --dari FILE --saya-yakin   # salin cadangan ke EQ_DB_PATH BARU (staging)

Opsi umum:
  --db PATH        database sumber (default: $EQ_DB_PATH)
  --dir PATH       folder cadangan (default: <folder db>/cadangan)
  --simpan N       jumlah cadangan terbaru yang disimpan (default: 14)

Skrip ini tidak pernah menampilkan isi database maupun rahasia; akun tersimpan
sebagai hash PBKDF2, bukan kata sandi biasa.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

FORMAT_NAMA = "english-quest-%Y%m%d-%H%M%S.sqlite3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cadangan_tersedia(folder: Path) -> list[Path]:
    return sorted((p for p in folder.glob("english-quest-*.sqlite3") if p.is_file()), reverse=True)


def periksa_keutuhan(path: Path) -> tuple[bool, str]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return False, f"tidak dapat dibuka: {exc}"
    try:
        hasil = connection.execute("PRAGMA integrity_check").fetchone()
        tabel = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    except sqlite3.Error as exc:
        return False, f"berkas rusak atau bukan database SQLite ({exc})"
    finally:
        connection.close()
    if not hasil or hasil[0] != "ok":
        return False, f"integrity_check: {hasil[0] if hasil else 'tidak ada hasil'}"
    return True, f"integritas ok · {len(tabel)} tabel"


def buat_cadangan(db: Path, folder: Path, simpan: int) -> int:
    if not db.is_file():
        print(f"GAGAL: database tidak ditemukan di {db}", file=sys.stderr)
        return 2
    os.umask(0o077)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass

    tujuan = folder / (time.strftime("english-quest-%Y%m%d-%H%M%S-") + str(time.time_ns()) + ".sqlite3")
    sementara = tujuan.with_suffix(".sqlite3.part")
    sumber = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    keluaran = sqlite3.connect(sementara)
    try:
        sumber.backup(keluaran)          # API backup resmi: konsisten walau server hidup
        keluaran.commit()
    finally:
        keluaran.close()
        sumber.close()

    os.chmod(sementara, 0o600)
    os.replace(sementara, tujuan)

    utuh, keterangan = periksa_keutuhan(tujuan)
    if not utuh:
        tujuan.unlink(missing_ok=True)
        print(f"GAGAL: cadangan dibuang karena {keterangan}", file=sys.stderr)
        return 3

    sidik = sha256(tujuan)
    (folder / (tujuan.name + ".sha256")).write_text(f"{sidik}  {tujuan.name}\n", encoding="ascii")

    lama = cadangan_tersedia(folder)
    for usang in lama[simpan:]:
        usang.unlink(missing_ok=True)
        usang.with_name(usang.name + ".sha256").unlink(missing_ok=True)

    ukuran_kib = tujuan.stat().st_size / 1024
    print(f"Cadangan dibuat : {tujuan}")
    print(f"Ukuran          : {ukuran_kib:.1f} KiB")
    print(f"SHA-256         : {sidik}")
    print(f"Keterangan      : {keterangan}")
    print(f"Disimpan        : {min(len(lama), simpan)} berkas terbaru di {folder}")
    return 0


def daftar(folder: Path) -> int:
    berkas = cadangan_tersedia(folder)
    if not berkas:
        print(f"Belum ada cadangan di {folder}")
        return 1
    print(f"{len(berkas)} cadangan di {folder}:")
    for path in berkas:
        waktu = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
        print(f"  {waktu}  {path.stat().st_size / 1024:8.1f} KiB  {path.name}")
    return 0


def periksa(folder: Path) -> int:
    berkas = cadangan_tersedia(folder)
    if not berkas:
        print(f"Belum ada cadangan di {folder}", file=sys.stderr)
        return 1
    gagal = 0
    for path in berkas:
        utuh, keterangan = periksa_keutuhan(path)
        catatan = folder / (path.name + ".sha256")
        cocok = "SIDIK-JARI TIDAK ADA"
        if catatan.is_file():
            harapan = (catatan.read_text(encoding="ascii").split() or [""])[0]
            cocok = "sidik-jari cocok" if harapan == sha256(path) else "SIDIK-JARI TIDAK COCOK"
        print(f"  [{'OK ' if utuh and 'TIDAK' not in cocok else 'BAD'}] {path.name}: {keterangan} · {cocok}")
        gagal += 0 if (utuh and "TIDAK" not in cocok) else 1
    return 1 if gagal else 0


def pulihkan(db: Path, sumber: Path, yakin: bool) -> int:
    if not yakin:
        print("Ditolak: tambahkan --saya-yakin untuk benar-benar memulihkan ke berkas staging baru.", file=sys.stderr)
        return 2
    if not sumber.is_file():
        print(f"GAGAL: berkas cadangan tidak ada: {sumber}", file=sys.stderr)
        return 2
    utuh, keterangan = periksa_keutuhan(sumber)
    if not utuh:
        print(f"GAGAL: cadangan tidak utuh ({keterangan}); pemulihan dibatalkan.", file=sys.stderr)
        return 3
    if db.exists() or any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm")):
        print("DITOLAK: tujuan harus berkas BARU tanpa WAL/SHM. Pulihkan ke staging, bukan DB aktif.", file=sys.stderr)
        return 2
    checksum = sumber.with_name(sumber.name + ".sha256")
    if not checksum.is_file() or not checksum.read_text().split() or checksum.read_text().split()[0] != sha256(sumber):
        print("DITOLAK: checksum cadangan tidak ada atau tidak cocok.", file=sys.stderr)
        return 3
    db.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement, even on concurrent restore.
    fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as dst, sumber.open("rb") as src:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    print(f"Salinan staging dipulihkan: {db}. DB aktif tidak disentuh.")
    print("Uji staging; hentikan layanan sebelum pergantian path database yang disetujui operator.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cadangan/verifikasi database English Quest")
    parser.add_argument("perintah", nargs="?", default="backup",
                        choices=["backup", "daftar", "periksa", "pulihkan"])
    parser.add_argument("--db", default=os.getenv("EQ_DB_PATH", ""),
                        help="database sumber (default: $EQ_DB_PATH)")
    parser.add_argument("--dir", default="", help="folder cadangan")
    parser.add_argument("--simpan", type=int, default=14, help="jumlah cadangan yang disimpan")
    parser.add_argument("--dari", default="", help="berkas cadangan untuk perintah pulihkan")
    parser.add_argument("--saya-yakin", action="store_true", help="konfirmasi pemulihan")
    args = parser.parse_args(argv)

    if not args.db:
        print("GAGAL: tentukan --db atau set EQ_DB_PATH.", file=sys.stderr)
        return 2
    db = Path(args.db).expanduser().resolve()
    folder = Path(args.dir).expanduser().resolve() if args.dir else db.parent / "cadangan"

    if args.perintah == "backup":
        return buat_cadangan(db, folder, max(1, args.simpan))
    if args.perintah == "daftar":
        return daftar(folder)
    if args.perintah == "periksa":
        return periksa(folder)
    if not args.dari:
        print("GAGAL: perintah pulihkan butuh --dari FILE.", file=sys.stderr)
        return 2
    return pulihkan(db, Path(args.dari).expanduser().resolve(), args.saya_yakin)


if __name__ == "__main__":
    raise SystemExit(main())
