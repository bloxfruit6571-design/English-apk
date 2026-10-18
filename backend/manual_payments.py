"""Manual DANA payments. Additive schema; never trust a buyer's payment assertion.

This module has no payment-provider network calls. Real receipt verification is an
operator responsibility. Code issuance is a separate admin-only, atomic operation.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


WHATSAPP_MESSAGE = 'Halo Admin English Quest, saya ingin membeli Premium seharga Rp5.000. Saya akan mengirimkan bukti pembayaran setelah transfer.'


def normalize_whatsapp(value):
    raw = str(value or '').strip()
    if not raw or not re.fullmatch(r'[+0-9 ().-]+', raw):
        return ''
    number = re.sub(r'[ ().-]', '', raw)
    if number.startswith('+'):
        number = number[1:]
    elif number.startswith('00'):
        number = number[2:]
    elif number.startswith('0'):
        number = '62' + number[1:]
    return number if re.fullmatch(r'[1-9][0-9]{7,14}', number) else ''


def configuration():
    number = os.getenv('EQ_DANA_MANUAL_NUMBER', '').strip()
    name = os.getenv('EQ_DANA_MANUAL_NAME', '').strip()
    whatsapp = normalize_whatsapp(os.getenv('EQ_WHATSAPP_ADMIN_NUMBER', ''))
    available = bool(re.fullmatch(r'(?:\+62|62|0)[0-9]{8,13}', number)
                     and 2 <= len(name) <= 100 and not any(ord(c) < 32 for c in name) and whatsapp)
    return {'mode': 'manual_dana', 'price_idr': 5000, 'currency': 'IDR',
            'available': available, 'recipient_number': number if available else '',
            'recipient_name': name if available else '', 'whatsapp_number': whatsapp if available else '',
            'whatsapp_message': WHATSAPP_MESSAGE,
            'message': ('Hubungi admin melalui WhatsApp. Screenshot bukan verifikasi dana masuk.'
                        if available else 'Informasi pembayaran belum dikonfigurasi lengkap: nomor DANA, nama penerima, dan WhatsApp admin. Pembayaran belum tersedia.')}


class ManualPaymentsMixin:
    def init_manual_schema(self):
        # No existing table rebuilt, no data dropped. SQLite transaction makes the
        # additive migration all-or-nothing; re-opening a database is idempotent.
        with self.connection() as db:
            db.executescript('''
              BEGIN IMMEDIATE;
              CREATE TABLE IF NOT EXISTS manual_payment_orders (
                order_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                amount INTEGER NOT NULL CHECK(amount=5000),
                currency TEXT NOT NULL CHECK(currency='IDR'),
                status TEXT NOT NULL CHECK(status IN ('pending','verified','rejected','issued')),
                recipient_number TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                received_amount INTEGER,
                receipt_hash TEXT UNIQUE,
                verified_by TEXT REFERENCES users(user_id),
                verified_at TEXT,
                rejection_reason TEXT,
                token_id TEXT UNIQUE REFERENCES premium_tokens(token_id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
              );
              CREATE UNIQUE INDEX IF NOT EXISTS manual_active_user_idx
                ON manual_payment_orders(user_id) WHERE status != 'rejected';
              CREATE TABLE IF NOT EXISTS manual_payment_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL REFERENCES manual_payment_orders(order_id),
                actor_id TEXT NOT NULL REFERENCES users(user_id),
                action TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS manual_audit_order_idx ON manual_payment_audit(order_id,event_id);
              CREATE TABLE IF NOT EXISTS eq_schema_migrations (
                name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
              );
              INSERT OR IGNORE INTO eq_schema_migrations VALUES ('manual_dana_v1',CURRENT_TIMESTAMP);
              COMMIT;
            ''')

    def _manual_admin(self, db, actor):
        row = db.execute('SELECT role FROM users WHERE user_id=?', (actor['user_id'],)).fetchone()
        if not row or row['role'] != 'admin':
            raise self.manual_error(403, 'ADMIN_REQUIRED', 'Hanya admin berwenang yang dapat melakukan tindakan ini.')

    @staticmethod
    def _manual_audit(db, order, actor, action, before, after, reason=''):
        db.execute('''INSERT INTO manual_payment_audit
          (order_id,actor_id,action,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?,?,?)''',
          (order, actor['user_id'], action, before, after, reason, now()))

    @staticmethod
    def _manual_payload(row):
        fields = ['order_id','user_id','amount','currency','status','recipient_number','recipient_name',
                  'verified_by','verified_at','rejection_reason','token_id','created_at','updated_at']
        result = {key: row[key] for key in fields}
        result.update(mode='manual_dana', price_label='Rp5.000', token_issued=bool(row['token_id']))
        for key in ('token_status','claimed_by_user_id','claimed_at','user_email'):
            if key in row.keys(): result[key] = row[key]
        return result

    @staticmethod
    def _manual_query():
        return '''SELECT o.*,t.status AS token_status,t.claimed_by_user_id,t.claimed_at,u.email AS user_email
                  FROM manual_payment_orders o LEFT JOIN premium_tokens t ON t.token_id=o.token_id
                  JOIN users u ON u.user_id=o.user_id'''

    def create_manual_order(self, user, ip, amount=5000):
        self._require_permanent_account(user, 'membuat permintaan pembayaran')
        if type(amount) is not int or amount != 5000:
            raise self.manual_error(400, 'INVALID_AMOUNT', 'Nominal wajib tepat Rp5.000.')
        config = configuration()
        if not config['available']:
            raise self.manual_error(503, 'MANUAL_NOT_CONFIGURED', config['message'])
        self._rate_limit_standalone('manual-order', user['user_id']+':'+ip, 6, 900)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute(self._manual_query()+" WHERE o.user_id=? AND o.status!='rejected'", (user['user_id'],)).fetchone()
                if row is None:
                    oid = 'EQM-'+secrets.token_hex(12).upper()
                    stamp = now()
                    db.execute('''INSERT INTO manual_payment_orders
                      (order_id,user_id,amount,currency,status,recipient_number,recipient_name,created_at,updated_at)
                      VALUES(?,?,5000,'IDR','pending',?,?,?,?)''',
                      (oid,user['user_id'],config['recipient_number'],config['recipient_name'],stamp,stamp))
                    self._manual_audit(db,oid,user,'requested',None,'pending')
                    row = db.execute(self._manual_query()+' WHERE o.order_id=?',(oid,)).fetchone()
                result = self._manual_payload(row)
                db.commit()
            except Exception:
                db.rollback(); raise
        result['message'] = 'Permintaan tercatat, bukan bukti pembayaran. Berikan ID pesanan kepada admin; Premium belum aktif.'
        return result

    def manual_orders(self, user, order_id=None, admin=False, status='all', search=''):
        with self.connection() as db:
            if admin: self._manual_admin(db,user)
            else: self._require_permanent_account(user,'melihat pesanan')
            terms, args = [], []
            if not admin: terms.append('o.user_id=?'); args.append(user['user_id'])
            if order_id: terms.append('o.order_id=?'); args.append(str(order_id)[:120])
            if admin and search:
                terms.append('(o.order_id LIKE ? OR o.user_id LIKE ? OR u.email LIKE ?)')
                args.extend(['%'+str(search)[:120]+'%']*3)
            if status != 'all':
                if status not in ('pending','verified','rejected','issued'):
                    raise self.manual_error(400,'INVALID_STATUS','Filter status tidak valid.')
                terms.append('o.status=?'); args.append(status)
            sql=self._manual_query()+(' WHERE '+' AND '.join(terms) if terms else '')+' ORDER BY o.created_at DESC,o.order_id DESC LIMIT 200'
            rows = db.execute(sql,args).fetchall()
        if order_id and not rows:
            raise self.manual_error(404,'ORDER_NOT_FOUND','Pesanan tidak ditemukan untuk akun ini.')
        return [self._manual_payload(row) for row in rows]

    def _verify_manual_receipt(self, db, row, admin, data):
        before = row['status']; reference = row['order_id']; result = {}
        amount = data.get('received_amount')
        receipt = str(data.get('receipt_reference') or '').strip().upper()
        if data.get('confirmed_received') is not True:
            raise self.manual_error(400,'CONFIRM_RECEIPT_REQUIRED','Periksa transaksi masuk di akun DANA, bukan hanya tangkapan layar.')
        if type(amount) is not int or amount != 5000:
            raise self.manual_error(400,'INVALID_AMOUNT','Dana yang diverifikasi harus tepat Rp5.000.')
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9._/-]{3,119}',receipt):
            raise self.manual_error(400,'RECEIPT_REFERENCE_REQUIRED','Isi referensi transaksi masuk yang valid (4–120 karakter).')
        digest = hmac.new(self.pepper,('manual-receipt:'+receipt).encode(),hashlib.sha256).hexdigest()
        if before in ('verified','issued'):
            if row['receipt_hash'] != digest:
                raise self.manual_error(409,'ORDER_ALREADY_VERIFIED','Pesanan sudah diverifikasi dengan referensi lain.')
            result['duplicate'] = True
        elif before != 'pending':
            raise self.manual_error(409,'INVALID_ORDER_STATE','Pesanan ditolak tidak dapat disetujui.')
        else:
            if db.execute('SELECT 1 FROM manual_payment_orders WHERE receipt_hash=?',(digest,)).fetchone():
                raise self.manual_error(409,'RECEIPT_ALREADY_USED','Referensi transaksi sudah digunakan untuk pesanan lain.')
            db.execute("UPDATE manual_payment_orders SET status='verified',received_amount=5000,receipt_hash=?,verified_by=?,verified_at=?,updated_at=? WHERE order_id=?",
                       (digest,admin['user_id'],now(),now(),reference))
            self._manual_audit(db,reference,admin,'verified',before,'verified')
        return result

    def record_manual_payment(self, admin, data):
        """Admin records a real receipt after offline checking; never issues a token here."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self._manual_admin(db, admin)
                buyer = db.execute('SELECT * FROM users WHERE email=?',
                                   (str(data.get('buyer_email') or '').strip().lower(),)).fetchone()
                if buyer is None:
                    raise self.manual_error(404,'BUYER_NOT_FOUND','Pembeli harus membuat akun English Quest dahulu. Periksa email akun.')
                self._require_permanent_account(buyer,'menerima token Premium')
                if buyer['user_id'] == admin['user_id']:
                    raise self.manual_error(403,'SELF_REVIEW_FORBIDDEN','Admin tidak boleh memverifikasi pembayaran untuk dirinya sendiri.')
                config = configuration()
                if not config['available']:
                    raise self.manual_error(503,'MANUAL_NOT_CONFIGURED',config['message'])
                row = db.execute("SELECT * FROM manual_payment_orders WHERE user_id=? AND status!='rejected'",(buyer['user_id'],)).fetchone()
                if row is None:
                    reference = 'EQM-'+secrets.token_hex(12).upper(); stamp = now()
                    db.execute("""INSERT INTO manual_payment_orders
                        (order_id,user_id,amount,currency,status,recipient_number,recipient_name,created_at,updated_at)
                        VALUES(?,?,5000,'IDR','pending',?,?,?,?)""",
                        (reference,buyer['user_id'],config['recipient_number'],config['recipient_name'],stamp,stamp))
                    self._manual_audit(db,reference,admin,'recorded_by_admin',None,'pending')
                    row = db.execute('SELECT * FROM manual_payment_orders WHERE order_id=?',(reference,)).fetchone()
                result = self._verify_manual_receipt(db,row,admin,data)
                result.update(self._manual_payload(db.execute(self._manual_query()+' WHERE o.order_id=?',(row['order_id'],)).fetchone()))
                result['message'] = ('Token sudah diterbitkan untuk pembayaran ini. Kode tidak ditampilkan ulang; tidak ada token baru.' if result['status'] == 'issued' else 'Pembayaran dicatat sesuai pemeriksaan admin. Terbitkan token secara terpisah; kode tidak dibuat otomatis.')
                db.commit()
                return result
            except Exception:
                db.rollback(); raise

    def manual_admin_action(self, admin, order_id, action, data):
        reference = str(order_id or '').strip()[:120]
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                self._manual_admin(db,admin)
                row = db.execute('SELECT * FROM manual_payment_orders WHERE order_id=?',(reference,)).fetchone()
                if not row: raise self.manual_error(404,'ORDER_NOT_FOUND','Pesanan tidak ditemukan.')
                if row['user_id'] == admin['user_id']:
                    raise self.manual_error(403,'SELF_REVIEW_FORBIDDEN','Admin tidak boleh memverifikasi atau menerbitkan token untuk pesanannya sendiri.')
                if row['amount'] != 5000 or row['currency'] != 'IDR':
                    raise self.manual_error(409,'INVALID_AMOUNT','Nominal pesanan tidak tepat Rp5.000.')
                before = row['status']; result = {}
                if action == 'verify':
                    result.update(self._verify_manual_receipt(db,row,admin,data))
                elif action == 'reject':
                    reason = data.get('reason')
                    if reason not in ('not_found','wrong_amount','wrong_recipient','duplicate','other'):
                        raise self.manual_error(400,'REJECTION_REASON_REQUIRED','Pilih alasan penolakan.')
                    if before == 'rejected': result['duplicate'] = True
                    elif before != 'pending':
                        raise self.manual_error(409,'INVALID_ORDER_STATE','Hanya pesanan pending dapat ditolak.')
                    else:
                        db.execute("UPDATE manual_payment_orders SET status='rejected',rejection_reason=?,updated_at=? WHERE order_id=?",(reason,now(),reference))
                        self._manual_audit(db,reference,admin,'rejected',before,'rejected',reason)
                elif action == 'issue':
                    if before == 'issued':
                        result.update(duplicate=True,message='Token sudah diterbitkan. Kode tidak dapat ditampilkan ulang; tidak dibuat token tambahan.')
                    elif before != 'verified' or row['received_amount'] != 5000 or not row['verified_by'] or not row['receipt_hash']:
                        raise self.manual_error(409,'PAYMENT_NOT_VERIFIED','Token hanya untuk pembayaran yang telah diverifikasi admin.')
                    else:
                        code = 'EQP-'+secrets.token_hex(24).upper()
                        tid = str(uuid.uuid4())
                        db.execute("INSERT INTO premium_tokens(token_id,token_hash,status,created_at,created_by_user_id,expires_at,issued_for_order_id) VALUES(?,?,'available',?,?,NULL,?)",
                                   (tid,self.token_hash(code),now(),admin['user_id'],reference))
                        db.execute("UPDATE manual_payment_orders SET status='issued',token_id=?,updated_at=? WHERE order_id=?",(tid,now(),reference))
                        self._manual_audit(db,reference,admin,'token_issued',before,'issued')
                        result.update(token=code,message='Salin kode sekarang dan berikan hanya kepada pemilik pesanan. Kode ditampilkan sekali.')
                else: raise self.manual_error(400,'INVALID_ACTION','Tindakan tidak dikenal.')
                row = db.execute(self._manual_query()+' WHERE o.order_id=?',(reference,)).fetchone()
                result.update(self._manual_payload(row))
                db.commit()
            except Exception:
                db.rollback(); raise
        return result

    def manual_audit(self, admin, order_id):
        with self.connection() as db:
            self._manual_admin(db,admin)
            rows=db.execute('SELECT event_id,order_id,actor_id,action,from_status,to_status,reason,created_at FROM manual_payment_audit WHERE order_id=? ORDER BY event_id',(str(order_id)[:120],)).fetchall()
        return [dict(row) for row in rows]
