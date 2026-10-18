"""Public owner contact checks. Never send WhatsApp messages or make a transfer."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import manual_payments as manual
import test_manual_payments as fixture

MESSAGE = ('Halo Admin English Quest.\n\nSaya ingin membeli Premium seharga Rp5.000.\n\n'
           'Saya sudah melakukan pembayaran melalui DANA.\n'
           'Berikut saya kirimkan screenshot bukti transfer untuk diperiksa.\n\n'
           'Mohon verifikasi pembayaran saya dan berikan token Premium\njika pembayaran sudah benar-benar diterima.\n\n'
           'Terima kasih.')

class OwnerContactTests(unittest.TestCase):
    def test_authorized_default_numbers_price_and_unknown_name(self):
        with patch.dict(os.environ,{},clear=True):
            c=manual.configuration()
            self.assertTrue(c['available']);self.assertEqual(c['recipient_number_display'],'0856-5670-6104')
            self.assertEqual(c['recipient_number'],'085656706104')
            self.assertEqual(c['whatsapp_display'],'0856-5670-6104')
            self.assertEqual(c['whatsapp_number'],'6285656706104')
            self.assertEqual(c['recipient_name'],'');self.assertEqual(c['price_idr'],5000)
            self.assertIn('belum dikonfirmasi',c['message'])
    def test_exact_editable_message_from_shared_public_file(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(manual.configuration()['whatsapp_message'],MESSAGE)
        data=json.loads(manual.PUBLIC_CONTACT_FILE.read_text())
        self.assertEqual(data['whatsapp_message'],MESSAGE)
        self.assertEqual(set(data),{'dana_number','whatsapp_number','recipient_name','price_idr','whatsapp_message'})
    def test_international_link_is_owner_number(self):
        for value in ('0856-5670-6104','+62 856 5670 6104','6285656706104','006285656706104'):
            self.assertEqual(manual.normalize_whatsapp(value),'6285656706104')
    def test_explicit_empty_contact_disables_without_fabrication(self):
        for key in ('EQ_DANA_MANUAL_NUMBER','EQ_WHATSAPP_ADMIN_NUMBER'):
            with patch.dict(os.environ,{key:''},clear=True):
                c=manual.configuration();self.assertFalse(c['available'])
                self.assertEqual(c['recipient_number'],'');self.assertEqual(c['whatsapp_number'],'')
    def test_missing_or_corrupt_config_fails_closed_without_env(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=True):
            path=Path(directory)/'contact.json'
            with patch.object(manual,'PUBLIC_CONTACT_FILE',path):
                self.assertFalse(manual.configuration()['available'])
                path.write_text('not json');self.assertFalse(manual.configuration()['available'])
    def test_real_http_public_contact_has_no_entitlement_side_effect(self):
        f=fixture.ManualHTTPTests();f.setUp();self.addCleanup(f.doCleanups);f.cfg.stop()
        with patch.dict(os.environ,{'EQ_DANA_MANUAL_NUMBER':'0856-5670-6104','EQ_WHATSAPP_ADMIN_NUMBER':'0856-5670-6104','EQ_DANA_MANUAL_NAME':''}):
            status,c=f.call('/api/manual-payments/config')
            self.assertEqual(status,200);self.assertEqual(c['whatsapp_number'],'6285656706104');self.assertEqual(c['recipient_name'],'')
            status,static=f.call('/premium-contact.json')
            self.assertEqual(status,200);self.assertEqual(static['dana_number'],'0856-5670-6104')
            self.assertFalse(f.store.account_status(f.user)['premium']);self.assertEqual(f.store.manual_orders(f.user),[])
            with f.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM premium_tokens').fetchone()[0],0)

if __name__=='__main__':unittest.main()
