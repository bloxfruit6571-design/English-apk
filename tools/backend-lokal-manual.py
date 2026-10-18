#!/usr/bin/env python3
"""Backend khusus APK debug lokal. Tidak mengaktifkan penerima atau transaksi DANA.

python3 tools/backend-lokal-manual.py serve
python3 tools/backend-lokal-manual.py create-admin --email EMAIL_OPERATOR
Simpan direktori state: database DAN pepper diperlukan saat server dibuka kembali.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def local_environment(state: Path) -> dict[str, str]:
    state = state.expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    db, pepper_file = state / 'debug.sqlite3', state / 'pepper.private'
    if not pepper_file.exists():
        if db.exists() or Path(str(db) + '-wal').exists():
            raise ValueError('Database ada tetapi pepper hilang. Pulihkan pepper; jangan membuat rahasia pengganti.')
        # Atomic exclusive create: never truncate a prior pepper.
        try:
            fd = os.open(pepper_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as handle:
                handle.write(secrets.token_hex(32))
    pepper = pepper_file.read_text().strip()
    if len(pepper.encode()) < 32:
        raise ValueError('Pepper lokal tidak valid; tidak diganti otomatis.')
    pepper_file.chmod(0o600)
    env = os.environ.copy()
    # Never inherit production keys, TLS/proxy settings or receiver config into this fixture.
    for key in list(env):
        if key.startswith('EQ_'):
            del env[key]
    env.update(EQ_TOKEN_PEPPER=pepper, EQ_DB_PATH=str(db), EQ_HOST='127.0.0.1', EQ_PORT='8088',
               EQ_ALLOW_INSECURE_DEV='1', EQ_TRUST_PROXY='0',
               EQ_ALLOWED_ORIGINS='https://appassets.androidplatform.net',
               EQ_DANA_MANUAL_NUMBER='', EQ_DANA_MANUAL_NAME='', EQ_PAYMENT_PROVIDER='')
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('serve', 'create-admin'))
    parser.add_argument('--state-dir', type=Path, default=Path.home()/'.local/share/english-quest-manual-debug')
    parser.add_argument('--email', help='Email operator; password diminta di terminal, bukan argumen/chat.')
    args = parser.parse_args()
    if args.command == 'create-admin' and not args.email:
        parser.error('--email wajib untuk create-admin')
    try:
        env = local_environment(args.state_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command == 'create-admin':
        return subprocess.call([sys.executable, str(ROOT/'backend/manage.py'), 'create-admin', '--email', args.email], env=env)
    print('UJI LOKAL SAJA: http://127.0.0.1:8088 — penerima DANA kosong, tidak untuk transaksi nyata.', flush=True)
    print('DB/pepper dipertahankan pada direktori state. Jangan hapus atau bagikan direktori itu.', flush=True)
    os.execve(sys.executable, [sys.executable, str(ROOT/'backend/server.py')], env)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
