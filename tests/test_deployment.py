"""Offline deployment regressions; synthetic SQLite only, never production."""
import importlib.util
import subprocess
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backup_eq', ROOT/'deploy/cadangkan-db.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='eq-backup-test-')
        self.root = Path(self.tmp.name)
        self.db = self.root/'active.sqlite3'
        self.folder = self.root/'backups'
        self.conn = sqlite3.connect(self.db)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY, value TEXT)')
        self.conn.execute("INSERT INTO fixture(value) VALUES ('synthetic-record')")
        self.conn.commit()
    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
    def snapshot(self):
        self.assertEqual(backup.buat_cadangan(self.db, self.folder, 2),0)
        return backup.cadangan_tersedia(self.folder)[0]
    def test_live_wal_snapshot_and_restore_new_path(self):
        snap=self.snapshot()
        self.assertEqual(backup.periksa(self.folder),0)
        staging=self.root/'restored.sqlite3'
        self.assertEqual(backup.pulihkan(staging,snap,True),0)
        with sqlite3.connect(staging) as c:
            self.assertEqual(c.execute('SELECT value FROM fixture').fetchone()[0],'synthetic-record')
        self.assertEqual(staging.stat().st_mode & 0o777,0o600)
    def test_never_overwrite_active_db(self):
        snap=self.snapshot()
        self.assertEqual(backup.pulihkan(self.db,snap,True),2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fixture').fetchone()[0],1)
    def test_restore_needs_confirmation(self):
        snap=self.snapshot()
        self.assertEqual(backup.pulihkan(self.root/'restore',snap,False),2)
    def test_checksum_required(self):
        snap=self.snapshot()
        snap.with_name(snap.name+'.sha256').unlink()
        self.assertEqual(backup.periksa(self.folder),1)
        self.assertEqual(backup.pulihkan(self.root/'restore',snap,True),3)
    def test_corruption_rejected(self):
        snap=self.snapshot()
        snap.write_bytes(b'not sqlite')
        self.assertEqual(backup.periksa(self.folder),1)
        self.assertEqual(backup.pulihkan(self.root/'restore',snap,True),3)
    def test_rotation_and_unique_names(self):
        for _ in range(4): self.snapshot()
        self.assertEqual(len(backup.cadangan_tersedia(self.folder)),2)
    def test_does_not_create_missing_source(self):
        path=self.root/'missing'
        self.assertEqual(backup.buat_cadangan(path,self.folder,2),2)
        self.assertFalse(path.exists())
    def test_build_rejects_nonproduction_urls_before_network(self):
        for url in ('http://localhost:8088','https://127.0.0.1','https://192.168.1.2','https://example.com','https://user:synthetic@host.invalid','https://host.test/api'):
            result=subprocess.run(['bash',str(ROOT/'tools/build-apk-untuk-domain.sh'),url],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('synthetic',result.stdout+result.stderr)

    def test_service_preflight_rejects_placeholder_configuration(self):
        service = (ROOT/'deploy/englishquest-api.service').read_text()
        self.assertIn('ExecStartPre=/usr/bin/python3 /opt/english-quest/deploy/cek-kesiapan-produksi.py --tanpa-self-test', service)
        result = subprocess.run(['python3',str(ROOT/'deploy/cek-kesiapan-produksi.py'),
            '--env-file',str(ROOT/'deploy/env.produksi.example'),'--tanpa-self-test'],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertIn('masih memakai nilai contoh',result.stdout)
        self.assertNotIn('GANTI_DENGAN_RAHASIA_ACAK_BUKAN_CONTOH',result.stdout)

if __name__=='__main__': unittest.main()
