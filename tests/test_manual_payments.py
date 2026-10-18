"""Manual DANA regression and HTTP authorization tests. Synthetic temp databases only.
No real DANA numbers, user credentials or payment transactions are used/logged.
"""
import concurrent.futures
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from backend import server as api, manual_payments


class ManualFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='eq-manual-tests-')
        self.db=Path(self.tmp.name)/'accounts.sqlite3'
        self.pepper=secrets.token_hex(32)
        self.store=api.Store(self.db,self.pepper)
        self.password=secrets.token_urlsafe(18)
        self.user_session=self.store.register('buyer@example.test',self.password,'buyer-ip')['session_token']
        self.other_session=self.store.register('other@example.test',self.password,'other-ip')['session_token']
        self.user=self.store.authenticate(self.user_session)
        self.other=self.store.authenticate(self.other_session)
        aid=api.create_admin('admin@example.test',self.password,self.store)
        self.admin_session=self.store.login('admin@example.test',self.password,'admin-ip')['session_token']
        self.admin=self.store.authenticate(self.admin_session)
        self.config={'mode':'manual_dana','price_idr':5000,'currency':'IDR','available':True,
                     'recipient_number':'SYNTHETIC-NOT-PAYABLE','recipient_name':'TEST ONLY','message':'fixture'}
        self.cfg=patch.object(manual_payments,'configuration',return_value=self.config)
        self.cfg.start();self.addCleanup(self.cfg.stop)
        self.addCleanup(self.tmp.cleanup)

    def pending(self,user=None):
        return self.store.create_manual_order(user or self.user,'fixture-ip')
    def verify(self,order,**extra):
        data={'received_amount':5000,'confirmed_received':True,'receipt_reference':'TEST-'+order['order_id']}
        data.update(extra)
        return self.store.manual_admin_action(self.admin,order['order_id'],'verify',data)
    def issue(self,order,admin=None):
        return self.store.manual_admin_action(admin or self.admin,order['order_id'],'issue',{})
    def fails(self,code,call):
        with self.assertRaises(api.ApiError) as c: call()
        self.assertEqual(c.exception.code,code)
    def token_count(self,order):
        with self.store.connection() as db:
            return db.execute('SELECT COUNT(*) FROM premium_tokens WHERE issued_for_order_id=?',(order['order_id'],)).fetchone()[0]


class ManualTests(ManualFixture):
    def test_01_request_pending_does_not_grant_anything(self):
        order=self.pending()
        self.assertEqual(order['status'],'pending');self.assertEqual(order['amount'],5000)
        self.assertEqual(order['user_id'],self.user['user_id'])
        self.assertEqual(self.token_count(order),0)
        self.assertFalse(self.store.account_status(self.user)['premium'])
        again=self.pending();self.assertEqual(order['order_id'],again['order_id'])
    def test_02_unprivileged_cannot_verify_issue_reject_or_audit(self):
        o=self.pending()
        for action in ('verify','issue','reject'):
            self.fails('ADMIN_REQUIRED',lambda:self.store.manual_admin_action(self.user,o['order_id'],action,{}))
        self.fails('ADMIN_REQUIRED',lambda:self.store.manual_audit(self.user,o['order_id']))
        self.fails('ADMIN_REQUIRED',lambda:self.store.manual_orders(self.user,admin=True))
    def test_03_admin_verifies_no_token_until_explicit_issue(self):
        o=self.pending();v=self.verify(o)
        self.assertEqual(v['status'],'verified');self.assertEqual(v['verified_by'],self.admin['user_id'])
        self.assertEqual(self.token_count(o),0)
        self.assertFalse(self.store.account_status(self.user)['premium'])
    def test_04_pending_and_rejected_cannot_issue(self):
        o=self.pending();self.fails('PAYMENT_NOT_VERIFIED',lambda:self.issue(o))
        self.store.manual_admin_action(self.admin,o['order_id'],'reject',{'reason':'not_found'})
        self.fails('PAYMENT_NOT_VERIFIED',lambda:self.issue(o))
        self.fails('INVALID_ORDER_STATE',lambda:self.verify(o))
        self.assertEqual(self.token_count(o),0)
    def test_05_issue_is_idempotent_no_code_rotation(self):
        o=self.pending();self.verify(o);first=self.issue(o);again=self.issue(o)
        self.assertEqual(self.token_count(o),1);self.assertEqual(first['token_id'],again['token_id'])
        self.assertTrue(again['duplicate']);self.assertNotIn('token',again)
        self.assertTrue(self.store.activate_token(self.user,first['token'],'activate')['activated'])
    def test_06_token_owner_binding_and_single_activation(self):
        o=self.pending();self.verify(o);token=self.issue(o)['token']
        self.fails('TOKEN_ACCOUNT_MISMATCH',lambda:self.store.activate_token(self.other,token,'other'))
        self.assertTrue(self.store.activate_token(self.user,token,'owner')['activated'])
        self.fails('TOKEN_ALREADY_USED',lambda:self.store.activate_token(self.other,token,'other'))
        self.assertTrue(self.store.activate_token(self.user,token,'owner')['already_yours'])
    def test_07_premium_survives_logout_login_store_restart(self):
        o=self.pending();self.verify(o);token=self.issue(o)['token']
        self.store.activate_token(self.user,token,'owner');self.store.logout(self.user_session)
        restarted=api.Store(self.db,self.pepper)
        s=restarted.login('buyer@example.test',self.password,'relogin')
        self.assertTrue(restarted.account_status(restarted.authenticate(s['session_token']))['premium'])
        with restarted.connection() as db:
            self.assertIsNone(db.execute('SELECT expires_at FROM premium_tokens WHERE issued_for_order_id=?',(o['order_id'],)).fetchone()[0])
    def test_08_other_account_cannot_read_order(self):
        o=self.pending()
        self.fails('ORDER_NOT_FOUND',lambda:self.store.manual_orders(self.other,o['order_id']))
        self.assertEqual(self.store.manual_orders(self.other),[])
    def test_09_wrong_amount_and_unconfirmed_receipt_denied(self):
        self.fails('INVALID_AMOUNT',lambda:self.store.create_manual_order(self.user,'ip',4999))
        o=self.pending()
        for amount in (4999,5001,True,'5000',None):
            self.fails('INVALID_AMOUNT',lambda:self.verify(o,received_amount=amount))
        self.fails('CONFIRM_RECEIPT_REQUIRED',lambda:self.verify(o,confirmed_received=False))
        self.fails('RECEIPT_REFERENCE_REQUIRED',lambda:self.verify(o,receipt_reference=''))
        self.assertEqual(self.store.manual_orders(self.user,o['order_id'])[0]['status'],'pending')
    def test_10_audit_complete_and_no_plaintext_secret(self):
        o=self.pending();self.verify(o);issued=self.issue(o);self.store.activate_token(self.user,issued['token'],'owner')
        events=self.store.manual_audit(self.admin,o['order_id'])
        self.assertEqual([e['action'] for e in events],['requested','verified','token_issued','activated'])
        self.assertEqual(events[1]['actor_id'],self.admin['user_id'])
        self.assertNotIn(issued['token'],json.dumps(events))
        with self.store.connection() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            stored=db.execute('SELECT token_hash FROM premium_tokens WHERE token_id=?',(issued['token_id'],)).fetchone()[0]
            self.assertNotEqual(stored,issued['token'])
        self.assertNotIn(issued['token'].encode(),self.db.read_bytes())
    def test_11_duplicate_receipt_cannot_approve_other_order(self):
        first=self.pending();second=self.pending(self.other)
        self.verify(first,receipt_reference='SYNTHETIC-RECEIPT-1')
        self.fails('RECEIPT_ALREADY_USED',lambda:self.verify(second,receipt_reference='synthetic-receipt-1'))
    def test_12_concurrent_issue_creates_exactly_one_code(self):
        o=self.pending();self.verify(o)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:self.issue(o),range(6)))
        self.assertEqual(sum('token' in v for v in results),1);self.assertEqual(self.token_count(o),1)
        self.assertEqual(sum(e['action']=='token_issued' for e in self.store.manual_audit(self.admin,o['order_id'])),1)
    def test_13_failure_rolls_back_order_token_and_audit(self):
        o=self.pending();self.verify(o)
        with patch.object(self.store,'_manual_audit',side_effect=RuntimeError('synthetic storage failure')):
            with self.assertRaises(RuntimeError):self.issue(o)
        self.assertEqual(self.token_count(o),0)
        self.assertEqual(self.store.manual_orders(self.user,o['order_id'])[0]['status'],'verified')
        self.assertFalse(self.store.account_status(self.user)['premium'])
    def test_14_no_self_verification_even_admin(self):
        o=self.pending(self.admin)
        self.fails('SELF_REVIEW_FORBIDDEN',lambda:self.verify(o))
        self.fails('SELF_REVIEW_FORBIDDEN',lambda:self.issue(o))
    def test_15_revoked_admin_role_rechecked_inside_transaction(self):
        o=self.pending()
        with self.store.connection() as db: db.execute("UPDATE users SET role='user' WHERE user_id=?",(self.admin['user_id'],))
        self.fails('ADMIN_REQUIRED',lambda:self.verify(o))
    def test_16_no_receiver_no_order_and_no_fake_number(self):
        with patch.object(manual_payments,'configuration',return_value={'available':False,'message':'Pembayaran belum tersedia.'}):
            self.fails('MANUAL_NOT_CONFIGURED',self.pending)
        self.assertEqual(self.store.manual_orders(self.user),[])
    def test_17_receiver_snapshot_and_repeated_verify(self):
        o=self.pending();self.verify(o);self.assertTrue(self.verify(o)['duplicate'])
        self.config['recipient_number']='CHANGED-TEST-NOT-PAYABLE'
        persisted=self.store.manual_orders(self.user,o['order_id'])[0]
        self.assertEqual(persisted['recipient_number'],'SYNTHETIC-NOT-PAYABLE')
        self.assertEqual(len(self.store.manual_audit(self.admin,o['order_id'])),2)
    def test_18_legacy_claim_cannot_rotate_manual_code(self):
        o=self.pending();self.verify(o);token=self.issue(o)['token']
        self.fails('ORDER_NOT_FOUND',lambda:self.store.claim_payment_token(self.user,o['order_id'],'owner'))
        self.assertTrue(self.store.activate_token(self.user,token,'owner')['activated'])
    def test_19_additive_migration_preserves_legacy_rows(self):
        # Remove ONLY the new empty tables from this synthetic DB to emulate pre-migration schema.
        # Existing users/session/token/order rows remain; no production database is opened.
        code=self.store.create_tokens(self.admin,1,None)[0]
        with self.store.connection() as db:
            db.execute("INSERT INTO payment_orders(order_id,user_id,provider,amount,currency,status,created_at,updated_at) VALUES('LEGACY',?,'sandbox',5000,'IDR','pending',?,?)",(self.user['user_id'],api.iso(),api.iso()))
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in ('users','sessions','premium_tokens','payment_orders')}
            for table in ('manual_payment_audit','manual_payment_orders','eq_schema_migrations'): db.execute('DROP TABLE '+table)
        for _ in range(2): api.Store(self.db,self.pepper)
        with self.store.connection() as db:
            for table,rows in before.items():self.assertEqual(rows,[tuple(r) for r in db.execute('SELECT * FROM '+table)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM eq_schema_migrations WHERE name='manual_dana_v1'").fetchone()[0],1)
    def test_20_guest_cannot_request_or_read(self):
        guest=self.store.authenticate(self.store.start_anonymous_demo('guest')['session_token'])
        self.fails('ACCOUNT_REQUIRED',lambda:self.pending(guest))
        self.fails('ACCOUNT_REQUIRED',lambda:self.store.manual_orders(guest))
    def test_21_rejection_is_audited_and_new_request_allowed(self):
        o=self.pending();self.store.manual_admin_action(self.admin,o['order_id'],'reject',{'reason':'wrong_amount'})
        self.store.manual_admin_action(self.admin,o['order_id'],'reject',{'reason':'wrong_amount'})
        events=self.store.manual_audit(self.admin,o['order_id'])
        self.assertEqual(events[-1]['reason'],'wrong_amount');self.assertEqual(len(events),2)
        self.assertNotEqual(self.pending()['order_id'],o['order_id'])


    def test_22_manual_backup_restore_preserves_audit_and_premium(self):
        o=self.pending();self.verify(o);issued=self.issue(o)
        self.store.activate_token(self.user,issued['token'],'owner')
        backup_script=Path(__file__).resolve().parents[1]/'deploy/cadangkan-db.py'
        subprocess.run(['python3',str(backup_script),'backup','--db',str(self.db)],check=True,capture_output=True)
        snapshot=next((self.db.parent/'cadangan').glob('*.sqlite3'))
        restored=self.db.parent/'restored.sqlite3'
        subprocess.run(['python3',str(backup_script),'pulihkan','--db',str(restored),'--dari',str(snapshot),'--saya-yakin'],check=True,capture_output=True)
        store=api.Store(restored,self.pepper)
        self.assertTrue(store.account_status(store.authenticate(self.user_session))['premium'])
        self.assertEqual(len(store.manual_audit(self.admin,o['order_id'])),4)
        self.assertEqual(store.manual_orders(self.user,o['order_id'])[0]['status'],'issued')


class ManualHTTPTests(ManualFixture):
    def setUp(self):
        super().setUp()
        self.env=patch.dict(os.environ,{'EQ_TRUST_PROXY':'1','EQ_ALLOW_INSECURE_DEV':'0','EQ_ALLOWED_ORIGINS':'https://appassets.androidplatform.net'})
        self.env.start();self.addCleanup(self.env.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),api.Handler);self.server.store=self.store
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
    def call(self,path,token='',body=None,secure=True):
        headers={'Content-Type':'application/json','Origin':'https://appassets.androidplatform.net'}
        if secure:headers['X-Forwarded-Proto']='https'
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,
            data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=10) as resp:return resp.status,json.loads(resp.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
    def test_http_authorization_and_complete_flow(self):
        self.assertEqual(self.call('/api/manual-payments/orders')[0],401)
        self.assertEqual(self.call('/api/manual-payments/config',secure=False)[0],400)
        self.assertEqual(self.call('/api/manual-payments/config')[1]['mode'],'manual_dana')
        status,o=self.call('/api/manual-payments/orders',self.user_session,{})
        self.assertEqual(status,201);self.assertEqual(o['status'],'pending');oid=o['order_id']
        self.assertEqual(self.call('/api/admin/manual-payments',self.user_session)[0],403)
        self.assertEqual(self.call('/api/manual-payments/order?order_id='+oid,self.other_session)[0],404)
        data={'order_id':oid,'received_amount':5000,'confirmed_received':True,'receipt_reference':'HTTP-SYNTHETIC-1'}
        for action in ('verify','reject','issue'):
            self.assertEqual(self.call('/api/admin/manual-payments/'+action,self.user_session,data)[0],403)
        self.assertEqual(self.call('/api/admin/manual-payments/verify',self.admin_session,data)[0],200)
        status,issued=self.call('/api/admin/manual-payments/issue',self.admin_session,{'order_id':oid})
        self.assertEqual(status,200);self.assertIn('token',issued)
        status,repeat=self.call('/api/admin/manual-payments/issue',self.admin_session,{'order_id':oid})
        self.assertEqual(status,200);self.assertNotIn('token',repeat)
        read=self.call('/api/manual-payments/orders',self.user_session)[1]
        self.assertNotIn(issued['token'],json.dumps(read))
        self.assertEqual(self.call('/api/premium/activate',self.user_session,{'token':issued['token']})[0],200)
        self.assertTrue(self.call('/api/me',self.user_session)[1]['account']['premium'])
        self.assertEqual(self.call('/api/admin/manual-payments/audit?order_id='+oid,self.admin_session)[1]['events'][-1]['action'],'activated')
    def test_http_old_automatic_and_generic_issue_closed_in_production(self):
        for path in ('/api/payments/order','/api/payments/claim','/api/payments/webhook','/api/payments/sandbox/settle'):
            self.assertEqual(self.call(path,self.user_session,{})[0],410)
        self.assertEqual(self.call('/api/admin/tokens',self.admin_session,{'count':1})[0],410)
        self.assertEqual(self.call('/api/admin/tokens',self.user_session,{'count':1})[0],403)
    def test_http_storage_failure_is_500_without_entitlement(self):
        o=self.pending();self.verify(o)
        with patch.object(self.store,'_manual_audit',side_effect=RuntimeError('synthetic')):
            self.assertEqual(self.call('/api/admin/manual-payments/issue',self.admin_session,{'order_id':o['order_id']})[0],500)
        self.assertEqual(self.token_count(o),0)
        self.assertFalse(self.call('/api/me',self.user_session)[1]['account']['premium'])

class ManualConfigTests(unittest.TestCase):
    def test_unset_partial_invalid_receiver_unavailable(self):
        for values in ({},{'EQ_DANA_MANUAL_NAME':'TEST ONLY'},
                       {'EQ_DANA_MANUAL_NUMBER':'invalid','EQ_DANA_MANUAL_NAME':'TEST ONLY'}):
            with patch.dict(os.environ,values,clear=True):
                c=manual_payments.configuration()
                self.assertFalse(c['available']);self.assertEqual(c['recipient_number'],'');self.assertEqual(c['recipient_name'],'')
    def test_complete_synthetic_configuration(self):
        number='08'+''.join(str(secrets.randbelow(10)) for _ in range(10))
        with patch.dict(os.environ,{'EQ_DANA_MANUAL_NUMBER':number,'EQ_DANA_MANUAL_NAME':'SYNTHETIC TEST ONLY','EQ_WHATSAPP_ADMIN_NUMBER':number},clear=True):
            c=manual_payments.configuration()
            self.assertTrue(c['available']);self.assertEqual(c['recipient_number'],number);self.assertEqual(c['price_idr'],5000)


class ManualTLSTests(ManualFixture):
    def test_real_tls_account_order_admin_token_activation_relogin(self):
        cert=Path(self.tmp.name)/'test-cert.pem';key=Path(self.tmp.name)/'test-key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-keyout',str(key),'-out',str(cert),'-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
        key.chmod(0o600)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert,key)
        client=ssl.create_default_context(cafile=str(cert))
        server=ThreadingHTTPServer(('127.0.0.1',0),api.Handler);server.store=self.store
        server.socket=context.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def call(path,body=None,token=''):
            headers={'Content-Type':'application/json','Origin':'https://appassets.androidplatform.net'}
            if token:headers['Authorization']='Bearer '+token
            req=urllib.request.Request(f'https://127.0.0.1:{server.server_port}/api'+path,
                data=json.dumps(body).encode() if body is not None else None,headers=headers)
            with urllib.request.urlopen(req,context=client,timeout=10) as response:
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'),'https://appassets.androidplatform.net')
                return json.loads(response.read())
        try:
            with patch.dict(os.environ,{'EQ_TRUST_PROXY':'0','EQ_ALLOW_INSECURE_DEV':'0','EQ_ALLOWED_ORIGINS':'https://appassets.androidplatform.net'}):
                password=secrets.token_hex(3)
                session=call('/register',{'email':'tls@example.test','password':password})['session_token']
                order=call('/manual-payments/orders',{},session)
                self.assertEqual(order['status'],'pending');self.assertFalse(call('/me',token=session)['account']['premium'])
                admin=call('/login',{'email':'admin@example.test','password':self.password})['session_token']
                data={'order_id':order['order_id'],'confirmed_received':True,'received_amount':5000,'receipt_reference':'TEST-TLS-RECEIPT-1'}
                self.assertEqual(call('/admin/manual-payments/verify',data,admin)['status'],'verified')
                code=call('/admin/manual-payments/issue',{'order_id':order['order_id']},admin)['token']
                call('/premium/activate',{'token':code},session)
                self.assertTrue(call('/me',token=session)['account']['premium'])
                call('/logout',{},session)
                session=call('/login',{'email':'tls@example.test','password':password})['session_token']
                self.assertTrue(call('/me',token=session)['account']['premium'])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)

if __name__=='__main__':unittest.main()
