"""DANA/WhatsApp: synthetic isolated tests, no WhatsApp messages or real transfers."""
import concurrent.futures
import os
import json
import subprocess
from pathlib import Path
import secrets
import unittest
from unittest.mock import patch
from backend import manual_payments as manual
import test_manual_payments as fixture

class WhatsAppConfigTests(unittest.TestCase):
    def test_local_plus_country_and_idd_normalization(self):
        digits='8'+''.join(str(secrets.randbelow(10)) for _ in range(10))
        expected='62'+digits
        for value in ('0'+digits,'+62 '+digits,'62'+digits,'0062-'+digits):
            self.assertEqual(manual.normalize_whatsapp(value),expected)
    def test_invalid_link_injection_and_lengths_rejected(self):
        for value in ('','+000000000','abc','https://wa.me/628000000000','62800000?text=x','++628000000000','123', '1'*16,'62\n8000000000'):
            self.assertEqual(manual.normalize_whatsapp(value),'',repr(value))
    def test_explicit_missing_contact_disables_payment(self):
        with patch.dict(os.environ,{'EQ_DANA_MANUAL_NUMBER':'080000000000','EQ_DANA_MANUAL_NAME':'SYNTHETIC TEST','EQ_WHATSAPP_ADMIN_NUMBER':''},clear=True):
            c=manual.configuration();self.assertFalse(c['available'])
            for key in ('recipient_number','recipient_name','whatsapp_number'):self.assertEqual(c[key],'')
    def test_public_config_contains_expected_prefill(self):
        with patch.dict(os.environ,{'EQ_DANA_MANUAL_NUMBER':'080000000000','EQ_DANA_MANUAL_NAME':'SYNTHETIC TEST','EQ_WHATSAPP_ADMIN_NUMBER':'+62 (800) 0000-0000'},clear=True):
            c=manual.configuration();self.assertTrue(c['available']);self.assertEqual(c['whatsapp_number'],'6280000000000')
            self.assertEqual(c['price_idr'],5000);self.assertIn('Rp5.000',c['whatsapp_message'])
            self.assertNotIn('berhasil',c['whatsapp_message'])

class WhatsAppRecordTests(fixture.ManualFixture):
    def data(self,**changes):
        d={'buyer_email':'buyer@example.test','received_amount':5000,'receipt_reference':'SYNTHETIC-WA-1','confirmed_received':True}
        d.update(changes);return d
    def record(self,**changes):return self.store.record_manual_payment(self.admin,self.data(**changes))
    def test_record_without_buyer_pending_then_explicit_issue_and_activate(self):
        o=self.record();self.assertEqual(o['status'],'verified');self.assertEqual(self.token_count(o),0)
        self.assertNotIn('token',o);self.assertFalse(self.store.account_status(self.user)['premium'])
        token=self.issue(o)['token'];self.store.activate_token(self.user,token,'fixture')
        self.assertTrue(self.store.account_status(self.user)['premium'])
        self.fails('TOKEN_ALREADY_USED',lambda:self.store.activate_token(self.other,token,'fixture'))
        self.assertEqual([x['action'] for x in self.store.manual_audit(self.admin,o['order_id'])],['recorded_by_admin','verified','token_issued','activated'])
    def test_ordinary_user_and_self_record_forbidden(self):
        self.fails('ADMIN_REQUIRED',lambda:self.store.record_manual_payment(self.user,self.data()))
        self.fails('SELF_REVIEW_FORBIDDEN',lambda:self.record(buyer_email='admin@example.test'))
    def test_screenshot_unconfirmed_cannot_record_or_issue(self):
        self.fails('CONFIRM_RECEIPT_REQUIRED',lambda:self.record(confirmed_received=False))
        self.assertEqual(self.store.manual_orders(self.user),[])
    def test_wrong_amount_missing_buyer_invalid_receipt_roll_back(self):
        for amount in (4000,5001,'5000',True):self.fails('INVALID_AMOUNT',lambda:self.record(received_amount=amount))
        self.fails('BUYER_NOT_FOUND',lambda:self.record(buyer_email='absent@example.test'))
        self.fails('RECEIPT_REFERENCE_REQUIRED',lambda:self.record(receipt_reference=''))
        self.assertEqual(self.store.manual_orders(self.user),[])
    def test_receipt_reuse_for_second_buyer_rejected(self):
        self.record();self.fails('RECEIPT_ALREADY_USED',lambda:self.record(buyer_email='other@example.test'))
        self.assertEqual(self.store.manual_orders(self.other),[])
    def test_concurrent_records_and_issues_are_idempotent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:orders=list(pool.map(lambda _:self.record(),range(6)))
        self.assertEqual(len({o['order_id'] for o in orders}),1)
        self.assertEqual(len(self.store.manual_audit(self.admin,orders[0]['order_id'])),2)
        first=self.issue(orders[0]);self.assertIn('token',first);self.assertNotIn('token',self.issue(orders[0]))
        self.assertTrue(self.record()['duplicate']);self.assertEqual(self.token_count(first),1)
    def test_existing_pending_is_preserved_not_recreated(self):
        old=self.pending();new=self.record();self.assertEqual(old['order_id'],new['order_id'])
        self.assertEqual(self.store.manual_audit(self.admin,old['order_id'])[0]['action'],'requested')
    def test_record_requires_configuration(self):
        self.config['available']=False;self.config['message']='unavailable'
        self.fails('MANUAL_NOT_CONFIGURED',self.record)

class WhatsAppHTTPTests(unittest.TestCase):
    def test_real_admin_local_client_bootstrap_and_login(self):
        f=fixture.ManualHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        data={'base':f'http://127.0.0.1:{f.server.server_port}','email':'admin@example.test','password':f.password}
        root=Path(__file__).resolve().parents[1]
        with patch.dict(os.environ,{'EQ_ALLOW_INSECURE_DEV':'1','EQ_TRUST_PROXY':'0'}):
            result=subprocess.run(['node',str(root/'tools/uji-admin-lokal.js')],input=json.dumps(data),capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS real local admin client',result.stdout)

    def test_record_endpoint_requires_admin_and_does_not_issue_automatically(self):
        f=fixture.ManualHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        data={'buyer_email':'buyer@example.test','confirmed_received':True,'received_amount':5000,'receipt_reference':'SYNTHETIC-HTTP-WA'}
        endpoint='/api/admin/manual-payments/record'
        self.assertEqual(f.call(endpoint,body=data)[0],401)
        self.assertEqual(f.call(endpoint,f.user_session,data)[0],403)
        status,result=f.call(endpoint,f.admin_session,data)
        self.assertEqual(status,200);self.assertEqual(result['status'],'verified');self.assertNotIn('token',result)
        self.assertFalse(f.call('/api/me',f.user_session)[1]['account']['premium'])
        result=f.call('/api/admin/manual-payments/issue',f.admin_session,{'order_id':result['order_id']})[1]
        self.assertIn('token',result)

if __name__=='__main__':unittest.main()
