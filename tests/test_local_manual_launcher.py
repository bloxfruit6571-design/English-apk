"""Tests for the phone-local launcher, using isolated synthetic state only."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('local_launcher',Path(__file__).resolve().parents[1]/'tools/backend-lokal-manual.py')
launcher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

class LocalLauncherTests(unittest.TestCase):
    def test_pepper_persists_across_restart_and_has_private_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state'
            first=launcher.local_environment(path);second=launcher.local_environment(path)
            self.assertEqual(first['EQ_TOKEN_PEPPER'],second['EQ_TOKEN_PEPPER'])
            self.assertGreaterEqual(len(first['EQ_TOKEN_PEPPER']),32)
            self.assertEqual((path/'pepper.private').stat().st_mode & 0o777,0o600)
            self.assertEqual(path.stat().st_mode & 0o777,0o700)
    def test_existing_database_missing_pepper_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'debug.sqlite3').write_bytes(b'synthetic fixture')
            with self.assertRaises(ValueError):launcher.local_environment(path)
            self.assertFalse((path/'pepper.private').exists())
            self.assertEqual((path/'debug.sqlite3').read_bytes(),b'synthetic fixture')
    def test_invalid_pepper_never_rotated(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'pepper.private').write_text('short')
            with self.assertRaises(ValueError):launcher.local_environment(path)
            self.assertEqual((path/'pepper.private').read_text(),'short')
    def test_local_only_no_inherited_receiver_provider_or_production_configuration(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{
            'EQ_HOST':'0.0.0.0','EQ_TRUST_PROXY':'1','EQ_DANA_MANUAL_NUMBER':'SYNTHETIC',
            'EQ_DANA_MANUAL_NAME':'TEST','EQ_PAYMENT_PROVIDER':'sandbox','EQ_DANA_CLIENT_SECRET':'SYNTHETIC'}):
            env=launcher.local_environment(Path(folder))
            self.assertEqual(env['EQ_HOST'],'127.0.0.1');self.assertEqual(env['EQ_TRUST_PROXY'],'0')
            self.assertEqual(env['EQ_PORT'],'8088')
            self.assertNotIn('EQ_DANA_MANUAL_NUMBER',env);self.assertNotIn('EQ_DANA_MANUAL_NAME',env);self.assertNotIn('EQ_WHATSAPP_ADMIN_NUMBER',env)
            self.assertEqual(env['EQ_PAYMENT_PROVIDER'],'');self.assertNotIn('EQ_DANA_CLIENT_SECRET',env)

if __name__=='__main__':unittest.main()
