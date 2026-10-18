#!/usr/bin/env python3
"""English Quest account, demo-quota and premium-token API (standard-library only).

Production must use HTTPS directly (EQ_TLS_CERT_FILE/EQ_TLS_KEY_FILE) or run behind a
trusted HTTPS reverse proxy (EQ_TRUST_PROXY=1, bind to 127.0.0.1). Plain HTTP is
refused unless EQ_ALLOW_INSECURE_DEV=1 and the listener is loopback-only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import mimetypes
import os
import re
import secrets
import signal
import socket
import sqlite3
import ssl
import sys
import time
import unicodedata
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:  # Imported as a package ("from backend import server") …
    from . import payments, manual_payments
except ImportError:  # … or executed directly from the backend/ directory.
    import payments  # type: ignore[no-redef]
    import manual_payments

ROOT = Path(__file__).resolve().parent.parent
DB_DEFAULT = Path(__file__).resolve().parent / "data" / "english-quest.sqlite3"
PBKDF2_ROUNDS = 310_000
SESSION_DAYS = 30
# Demo allowance: five unique Easy questions (one complete Easy mission). Business rule set by
# the product owner; the server is authoritative for the quota and the client mirrors it.
DEMO_LIMIT = 5
# Premium is a single one-off payment that unlocks access permanently (no subscription).
PREMIUM_PRICE_IDR = 5_000
PAYMENT_CURRENCY = "IDR"
# An unpaid order stops being payable after this window (a late success notification is kept for
# manual review instead of silently issuing a token).
PAYMENT_ORDER_TTL_SECONDS = 86_400
# Housekeeping windows: only rows that can no longer influence an access decision are pruned.
SESSION_RETENTION_DAYS = 7
# Longest rate-limit window in use is 24h (guest demo sessions); keep two days of history.
RATE_LIMIT_RETENTION_SECONDS = 2 * 86_400
DEMO_QUESTION_IDS = frozenset(
    [f"easy-greetings-q{i}" for i in range(1, DEMO_LIMIT + 1)]
)
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@.]{1,128}(?:\.[^\s@.]{1,63})+$")
TOKEN_RE = re.compile(r"^EQP-[A-F0-9]{48}$")
MAX_BODY = 32 * 1024
# User-facing registration policy. Kept next to EMAIL_RE so the mirror in app.js
# (PASSWORD_MIN_LENGTH) and this rule cannot drift apart; the server always re-checks.
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 256
# Administrator accounts are provisioned offline and keep the stricter legacy policy.
ADMIN_PASSWORD_MIN_LENGTH = 12


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_token(value: Any) -> str:
    # Keep the fixed EQP- prefix; users may add whitespace while copying a code.
    return "".join(str(value or "").split()).upper()


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    raw_salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), raw_salt, PBKDF2_ROUNDS, dklen=32)
    return base64.urlsafe_b64encode(raw_salt).decode("ascii"), base64.urlsafe_b64encode(derived).decode("ascii")


def verify_password(password: str, salt_text: str, hash_text: str) -> bool:
    try:
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_text.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Store(manual_payments.ManualPaymentsMixin):
    manual_error = ApiError
    def __init__(self, db_path: str | Path, token_pepper: str | bytes):
        self.db_path = str(db_path)
        self.pepper = token_pepper.encode("utf-8") if isinstance(token_pepper, str) else token_pepper
        if len(self.pepper) < 32:
            raise ValueError("EQ_TOKEN_PEPPER must contain at least 32 bytes of random secret material.")
        database_parent = Path(self.db_path).parent
        database_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(database_parent, 0o700)
        except OSError:
            pass
        self._init_db()
        self.init_manual_schema()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    def _init_db(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
                anonymous INTEGER NOT NULL DEFAULT 0 CHECK(anonymous IN (0,1)),
                created_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS sessions (
                session_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
              );
              CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
              CREATE TABLE IF NOT EXISTS demo_answers (
                user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                question_id TEXT NOT NULL,
                answered_at TEXT NOT NULL,
                answer_hash TEXT,
                PRIMARY KEY(user_id, question_id)
              );
              CREATE TABLE IF NOT EXISTS premium_tokens (
                token_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('available','claimed','revoked','expired')),
                claimed_by_user_id TEXT REFERENCES users(user_id),
                claimed_at TEXT,
                created_at TEXT NOT NULL,
                created_by_user_id TEXT REFERENCES users(user_id),
                expires_at TEXT
              );
              CREATE INDEX IF NOT EXISTS token_status_idx ON premium_tokens(status, created_at);
              CREATE INDEX IF NOT EXISTS token_claimed_user_idx ON premium_tokens(claimed_by_user_id, status);
              CREATE TABLE IF NOT EXISTS rate_limits (
                bucket TEXT PRIMARY KEY,
                window_start INTEGER NOT NULL,
                requests INTEGER NOT NULL
              );
              CREATE TABLE IF NOT EXISTS payment_orders (
                order_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK(amount > 0),
                currency TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('created','pending','paid','failed','expired','review')),
                token_id TEXT REFERENCES premium_tokens(token_id),
                token_delivered_at TEXT,
                checkout_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paid_at TEXT
              );
              CREATE INDEX IF NOT EXISTS payment_orders_user_idx ON payment_orders(user_id, created_at);
              CREATE INDEX IF NOT EXISTS payment_orders_status_idx ON payment_orders(status, created_at);
              CREATE TABLE IF NOT EXISTS payment_events (
                event_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                order_reference TEXT NOT NULL,
                order_id TEXT,
                state TEXT NOT NULL,
                raw_status TEXT NOT NULL,
                amount INTEGER,
                currency TEXT,
                received_at TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS payment_events_order_idx ON payment_events(order_id, received_at);
            """)
            token_columns = {row["name"] for row in db.execute("PRAGMA table_info(premium_tokens)").fetchall()}
            if "issued_for_order_id" not in token_columns:
                db.execute("ALTER TABLE premium_tokens ADD COLUMN issued_for_order_id TEXT")
            # One entitlement per paid order, enforced by the database and not only by code.
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS token_issued_order_idx ON premium_tokens(issued_for_order_id) WHERE issued_for_order_id IS NOT NULL")
            user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
            if "anonymous" not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN anonymous INTEGER NOT NULL DEFAULT 0 CHECK(anonymous IN (0,1))")
            demo_columns = {row["name"] for row in db.execute("PRAGMA table_info(demo_answers)").fetchall()}
            if "answer_hash" not in demo_columns:
                db.execute("ALTER TABLE demo_answers ADD COLUMN answer_hash TEXT")

    def token_hash(self, token: str) -> str:
        return hmac.new(self.pepper, normalize_token(token).encode("ascii"), hashlib.sha256).hexdigest()

    @staticmethod
    def session_hash(raw_session: str) -> str:
        return hashlib.sha256(raw_session.encode("ascii")).hexdigest()

    def _rate_limit(self, db: sqlite3.Connection, scope: str, identity: str, maximum: int, window_seconds: int) -> None:
        ident = hmac.new(self.pepper, identity.encode("utf-8", "replace"), hashlib.sha256).hexdigest()
        bucket = f"{scope}:{ident}"
        now = int(time.time())
        row = db.execute("SELECT window_start, requests FROM rate_limits WHERE bucket=?", (bucket,)).fetchone()
        if row is None or now - row["window_start"] >= window_seconds:
            db.execute("INSERT INTO rate_limits(bucket,window_start,requests) VALUES(?,?,1) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,requests=1", (bucket, now))
            return
        if row["requests"] >= maximum:
            retry = max(1, window_seconds - (now - row["window_start"]))
            raise ApiError(429, "RATE_LIMITED", f"Terlalu banyak percobaan. Coba lagi dalam sekitar {retry} detik.")
        db.execute("UPDATE rate_limits SET requests=requests+1 WHERE bucket=?", (bucket,))

    def _rate_limit_standalone(self, scope: str, identity: str, maximum: int, window_seconds: int) -> None:
        """Apply a rate limit in its own committed transaction.

        Security-relevant counters must survive a rolled-back business transaction. Failed logins,
        rejected registrations and refused token claims raise ApiError, and the caller's
        ``except Exception: db.rollback()`` would otherwise discard an increment made inside the
        same transaction — leaving every failing attempt uncounted (unlimited brute force). The
        counter therefore lives in a separate connection and is committed before the caller opens
        its own BEGIN IMMEDIATE, so the two write transactions never overlap.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._rate_limit(db, scope, identity, maximum, window_seconds)
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _create_session(self, db: sqlite3.Connection, user_id: str) -> tuple[str, str]:
        raw = secrets.token_urlsafe(32)
        created = utc_now()
        expires = created + timedelta(days=SESSION_DAYS)
        db.execute("INSERT INTO sessions(session_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)", (self.session_hash(raw), user_id, iso(created), iso(expires)))
        return raw, iso(expires)

    def register(self, email: str, password: str, ip: str) -> dict[str, Any]:
        email = normalize_email(email)
        if not EMAIL_RE.fullmatch(email):
            raise ApiError(400, "INVALID_EMAIL", "Masukkan alamat email yang valid.")
        if len(password) < PASSWORD_MIN_LENGTH or len(password) > PASSWORD_MAX_LENGTH:
            raise ApiError(400, "WEAK_PASSWORD", f"Kata sandi harus {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} karakter.")
        now = iso()
        # Counted in its own committed transaction so a rejected registration still consumes an
        # attempt; see _rate_limit_standalone for why this cannot live inside the transaction below.
        self._rate_limit_standalone("register", ip, 5, 900)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                    raise ApiError(409, "ACCOUNT_EXISTS", "Akun dengan email tersebut sudah ada. Silakan masuk.")
                salt, pwd_hash = hash_password(password)
                user_id = str(uuid.uuid4())
                db.execute("INSERT INTO users(user_id,email,password_salt,password_hash,created_at) VALUES(?,?,?,?,?)", (user_id, email, salt, pwd_hash, now))
                session, expiry = self._create_session(db, user_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"session_token": session, "session_expires_at": expiry}

    def start_anonymous_demo(self, ip: str) -> dict[str, Any]:
        """Create a server-backed guest session so demo entry never requires an account.

        Guest accounts are intentionally not recoverable by email/password. The server limits
        anonymous session creation per source IP; users can later sign in/create a named account,
        and the client merges its non-authoritative local demo ledger into that account.
        """
        now = iso()
        user_id = str(uuid.uuid4())
        email = f"guest-{user_id}@demo.english-quest.invalid"
        # An anonymous account has no usable password. Store random, non-derivable values rather
        # than spending PBKDF2 time on an unguessable credential that is never disclosed.
        salt = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii")
        password_hash = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        self._rate_limit_standalone("anonymous-demo-start", ip, 20, 86_400)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("INSERT INTO users(user_id,email,password_salt,password_hash,role,anonymous,created_at) VALUES(?,?,?,?, 'user',1,?)", (user_id,email,salt,password_hash,now))
                session, expiry = self._create_session(db, user_id)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"session_token": session, "session_expires_at": expiry}

    def login(self, email: str, password: str, ip: str) -> dict[str, Any]:
        email = normalize_email(email)
        if len(password) > 256:
            raise ApiError(401, "INVALID_CREDENTIALS", "Email atau kata sandi salah.")
        # Failed logins raise ApiError, which rolls the transaction below back. Counting the
        # attempt in a committed transaction first is what makes this limit actually hold.
        self._rate_limit_standalone("login", ip, 12, 900)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT user_id,password_salt,password_hash FROM users WHERE email=?", (email,)).fetchone()
                # Do PBKDF2 even for an unknown email, reducing account-enumeration timing signals.
                if row is None:
                    hash_password(password, b"EnglishQuestDummySalt")
                    raise ApiError(401, "INVALID_CREDENTIALS", "Email atau kata sandi salah.")
                if not verify_password(password, row["password_salt"], row["password_hash"]):
                    raise ApiError(401, "INVALID_CREDENTIALS", "Email atau kata sandi salah.")
                session, expiry = self._create_session(db, row["user_id"])
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"session_token": session, "session_expires_at": expiry}

    def authenticate(self, raw_session: str | None) -> sqlite3.Row:
        if not raw_session or len(raw_session) > 256:
            raise ApiError(401, "AUTH_REQUIRED", "Silakan masuk ke akun English Quest.")
        with self.connection() as db:
            row = db.execute("""
              SELECT u.user_id,u.email,u.role,u.anonymous,s.expires_at
              FROM sessions s JOIN users u ON u.user_id=s.user_id
              WHERE s.session_hash=? AND s.revoked_at IS NULL
            """, (self.session_hash(raw_session),)).fetchone()
        if row is None or (parse_iso(row["expires_at"]) or utc_now()) <= utc_now():
            raise ApiError(401, "SESSION_EXPIRED", "Sesi berakhir. Silakan masuk kembali.")
        return row

    def logout(self, raw_session: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE sessions SET revoked_at=? WHERE session_hash=? AND revoked_at IS NULL", (iso(), self.session_hash(raw_session)))

    def _expire_tokens(self, db: sqlite3.Connection) -> None:
        now = iso()
        db.execute("UPDATE premium_tokens SET status='expired' WHERE status IN ('available','claimed') AND expires_at IS NOT NULL AND expires_at<=?", (now,))
        # An unpaid order that is older than the payment window can no longer settle; a success
        # notification that arrives afterwards goes to manual review instead of issuing a token.
        cutoff = iso(utc_now() - timedelta(seconds=PAYMENT_ORDER_TTL_SECONDS))
        db.execute("UPDATE payment_orders SET status='expired', updated_at=? WHERE status IN ('created','pending') AND created_at<=?", (now, cutoff))
        self._prune_stale_rows(db)

    def _prune_stale_rows(self, db: sqlite3.Connection) -> None:
        """Delete housekeeping rows that can no longer affect any access decision.

        ``sessions`` and ``rate_limits`` are append-only while the service runs, so on a
        long-lived deployment both tables grow without bound. Only dead rows are removed:
        sessions that expired or were revoked more than SESSION_RETENTION_DAYS ago, and
        rate-limit windows older than the longest window in use. Removing an expired or revoked
        session does not change any outcome — both already authenticate as a 401.
        """
        session_cutoff = iso(utc_now() - timedelta(days=SESSION_RETENTION_DAYS))
        db.execute("DELETE FROM sessions WHERE COALESCE(revoked_at, expires_at) <= ?", (session_cutoff,))
        db.execute("DELETE FROM rate_limits WHERE window_start <= ?", (int(time.time()) - RATE_LIMIT_RETENTION_SECONDS,))

    def account_status(self, user: sqlite3.Row) -> dict[str, Any]:
        with self.connection() as db:
            self._expire_tokens(db)
            premium = db.execute("""
              SELECT token_id,expires_at FROM premium_tokens
              WHERE claimed_by_user_id=? AND status='claimed'
              ORDER BY claimed_at DESC LIMIT 1
            """, (user["user_id"],)).fetchone()
            used_rows = db.execute("SELECT question_id FROM demo_answers WHERE user_id=? ORDER BY answered_at,question_id", (user["user_id"],)).fetchall()
        used = [row["question_id"] for row in used_rows]
        is_premium = premium is not None
        return {
            "user_id": user["user_id"],
            "email": user["email"],
            "role": user["role"],
            "anonymous": bool(user["anonymous"]) if "anonymous" in user.keys() else False,
            "access_level": "premium" if is_premium else "demo",
            "premium": is_premium,
            "premium_expires_at": premium["expires_at"] if premium else None,
            "demo_limit": DEMO_LIMIT,
            "demo_used": len(used),
            "demo_remaining": max(0, DEMO_LIMIT - len(used)),
            "used_question_ids": used,
            "allowed_demo_question_ids": sorted(DEMO_QUESTION_IDS),
        }

    def sync_demo_questions(self, user: sqlite3.Row, question_ids: Any, ip: str) -> dict[str, Any]:
        """Merge locally consumed demo IDs into a server account without ever refunding quota.

        The client ledger is deliberately treated as untrusted: importing IDs can only consume a
        user's own quota. Imported rows have no answer hash, so they can never be replayed or edited.
        """
        if not isinstance(question_ids, list) or len(question_ids) > DEMO_LIMIT:
            raise ApiError(400, "INVALID_QUESTION_LIST", "Daftar soal demo tidak valid.")
        ids = list(dict.fromkeys(str(item) for item in question_ids))
        if any(item not in DEMO_QUESTION_IDS for item in ids):
            raise ApiError(403, "QUESTION_NOT_IN_DEMO", "Daftar memuat soal di luar paket demo.")
        self._rate_limit_standalone("demo-sync", user["user_id"] + ":" + ip, 12, 600)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                count = db.execute("SELECT COUNT(*) AS n FROM demo_answers WHERE user_id=?", (user["user_id"],)).fetchone()["n"]
                imported = 0
                for question_id in ids:
                    exists = db.execute("SELECT 1 FROM demo_answers WHERE user_id=? AND question_id=?", (user["user_id"], question_id)).fetchone()
                    if exists:
                        continue
                    if count >= DEMO_LIMIT:
                        break
                    db.execute("INSERT INTO demo_answers(user_id,question_id,answered_at,answer_hash) VALUES(?,?,?,NULL)", (user["user_id"], question_id, iso()))
                    imported += 1
                    count += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "imported": imported,
            "demo_used": count,
            "demo_remaining": max(0, DEMO_LIMIT - count),
        }

    def consume_demo_question(self, user: sqlite3.Row, question_id: str, ip: str, answer: str = "answer") -> dict[str, Any]:
        qid = str(question_id or "")
        if qid not in DEMO_QUESTION_IDS:
            raise ApiError(403, "QUESTION_NOT_IN_DEMO", "Soal ini tidak termasuk paket demo gratis.")
        raw_answer = str(answer or "")
        if not raw_answer.strip() or len(raw_answer) > 1000:
            raise ApiError(400, "INVALID_ANSWER", "Jawaban tidak valid. Lengkapi jawaban sebelum dikirim.")
        canonical = " ".join(unicodedata.normalize("NFKC", raw_answer).casefold().split())
        answer_digest = hmac.new(self.pepper, ("demo-answer:" + qid + ":" + canonical).encode("utf-8"), hashlib.sha256).hexdigest()
        self._rate_limit_standalone("demo", user["user_id"] + ":" + ip, 40, 600)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._expire_tokens(db)
                premium = db.execute("SELECT 1 FROM premium_tokens WHERE claimed_by_user_id=? AND status='claimed' LIMIT 1", (user["user_id"],)).fetchone()
                used_row = db.execute("SELECT question_id,answer_hash FROM demo_answers WHERE user_id=? AND question_id=?", (user["user_id"], qid)).fetchone()
                count = db.execute("SELECT COUNT(*) AS n FROM demo_answers WHERE user_id=?", (user["user_id"],)).fetchone()["n"]
                if premium:
                    db.commit()
                    return {"access_level": "premium", "premium": True, "consumed": False, "duplicate": False, "demo_used": count, "demo_remaining": max(0, DEMO_LIMIT-count)}
                if used_row:
                    if not used_row["answer_hash"]:
                        raise ApiError(409, "DEMO_ANSWER_ALREADY_USED", "Soal ini sudah pernah dijawab; soal demo tidak dapat diulang.")
                    if not hmac.compare_digest(used_row["answer_hash"], answer_digest):
                        raise ApiError(409, "DEMO_ANSWER_MISMATCH", "Jawaban soal ini sudah tercatat dan tidak dapat diubah atau dicoba ulang.")
                    db.commit()
                    return {"access_level": "demo", "premium": False, "consumed": False, "duplicate": True, "question_id": qid, "demo_used": count, "demo_remaining": max(0, DEMO_LIMIT-count)}
                if count >= DEMO_LIMIT:
                    raise ApiError(403, "DEMO_EXHAUSTED", "Jatah 10 soal demo sudah habis. Buka Premium untuk melanjutkan.")
                db.execute("INSERT INTO demo_answers(user_id,question_id,answered_at,answer_hash) VALUES(?,?,?,?)", (user["user_id"], qid, iso(), answer_digest))
                count += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"access_level": "demo", "premium": False, "consumed": True, "duplicate": False, "question_id": qid, "demo_used": count, "demo_remaining": max(0, DEMO_LIMIT-count)}

    def activate_token(self, user: sqlite3.Row, token: str, ip: str) -> dict[str, Any]:
        if "anonymous" in user.keys() and bool(user["anonymous"]):
            raise ApiError(403, "ACCOUNT_REQUIRED", "Buat akun atau masuk ke akun permanen sebelum mengaktifkan Premium.")
        normalized = normalize_token(token)
        if not TOKEN_RE.fullmatch(normalized):
            raise ApiError(400, "INVALID_TOKEN", "Format token tidak valid.")
        digest = self.token_hash(normalized)
        self._rate_limit_standalone("activate", user["user_id"] + ":" + ip, 8, 900)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._expire_tokens(db)
                row = db.execute("SELECT token_id,status,claimed_by_user_id,expires_at FROM premium_tokens WHERE token_hash=?", (digest,)).fetchone()
                if row is None:
                    raise ApiError(404, "INVALID_TOKEN", "Token tidak ditemukan atau tidak valid.")
                status = row["status"]
                if status == "revoked":
                    raise ApiError(403, "TOKEN_REVOKED", "Token ini telah dicabut dan tidak dapat digunakan.")
                if status == "expired":
                    raise ApiError(403, "TOKEN_EXPIRED", "Token ini sudah kedaluwarsa.")
                if status == "claimed":
                    if row["claimed_by_user_id"] == user["user_id"]:
                        db.commit()
                        return {"activated": True, "already_yours": True, "message": "Token ini sudah terikat pada akun Anda."}
                    raise ApiError(409, "TOKEN_ALREADY_USED", "Token ini sudah digunakan dan tidak dapat diklaim lagi.")
                manual_order = db.execute("SELECT order_id,user_id,status FROM manual_payment_orders WHERE token_id=?", (row["token_id"],)).fetchone()
                if manual_order and manual_order["user_id"] != user["user_id"]:
                    raise ApiError(403, "TOKEN_ACCOUNT_MISMATCH", "Token ini hanya untuk akun pemilik pesanan.")
                changed = db.execute("""
                  UPDATE premium_tokens
                  SET status='claimed',claimed_by_user_id=?,claimed_at=?
                  WHERE token_id=? AND status='available' AND claimed_by_user_id IS NULL
                """, (user["user_id"], iso(), row["token_id"])).rowcount
                if changed != 1:
                    raise ApiError(409, "TOKEN_ALREADY_USED", "Token ini sudah digunakan dan tidak dapat diklaim lagi.")
                if manual_order:
                    self._manual_audit(db,manual_order["order_id"],user,"activated",manual_order["status"],manual_order["status"])
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"activated": True, "already_yours": False, "message": "Premium aktif pada akun ini."}

    # ------------------------------------------------------------------ payments
    # Premium is a single one-off purchase of PREMIUM_PRICE_IDR that must be verified by the
    # backend before anything is granted. Nothing in this section trusts the client, and no
    # notification is acted upon before its signature has been checked by a provider adapter
    # (see payments.py). A token for a normal transaction is issued automatically here — an
    # administrator never has to approve it by hand.

    @staticmethod
    def _require_permanent_account(user: sqlite3.Row, action: str) -> None:
        if "anonymous" in user.keys() and bool(user["anonymous"]):
            raise ApiError(403, "ACCOUNT_REQUIRED", f"Buat akun atau masuk ke akun permanen sebelum {action}.")

    @staticmethod
    def _order_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "order_id": row["order_id"],
            "provider": row["provider"],
            "amount": row["amount"],
            "currency": row["currency"],
            "price_label": f"Rp{row['amount']:,}".replace(",", "."),
            "status": row["status"],
            "paid": row["status"] == "paid",
            "token_issued": bool(row["token_id"]),
            "token_delivered": bool(row["token_delivered_at"]),
            "created_at": row["created_at"],
            "paid_at": row["paid_at"],
        }

    def create_payment_order(self, user: sqlite3.Row, ip: str) -> dict[str, Any]:
        """Create (or reuse) a one-off Premium order for a permanent account."""
        self._require_permanent_account(user, "membeli Premium")
        self._rate_limit_standalone("payment-order", user["user_id"] + ":" + ip, 6, 900)
        provider = payments.provider_from_env()
        if provider is None or not provider.configured():
            raise ApiError(503, "PAYMENTS_NOT_CONFIGURED", "Pembayaran otomatis belum diaktifkan pada server ini. Hubungi pengelola layanan; jangan mengirim uang ke rekening pribadi mana pun.")
        now = iso()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute("""
                  SELECT * FROM payment_orders
                  WHERE user_id=? AND provider=? AND status IN ('created','pending')
                  ORDER BY created_at DESC LIMIT 1
                """, (user["user_id"], provider.name)).fetchone()
                if existing is not None:
                    order = existing
                else:
                    order_id = "EQO-" + secrets.token_hex(10).upper()
                    db.execute("""
                      INSERT INTO payment_orders(order_id,user_id,provider,amount,currency,status,created_at,updated_at)
                      VALUES(?,?,?,?,?,'created',?,?)
                    """, (order_id, user["user_id"], provider.name, PREMIUM_PRICE_IDR, PAYMENT_CURRENCY, now, now))
                    order = db.execute("SELECT * FROM payment_orders WHERE order_id=?", (order_id,)).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        payload = self._order_payload(order)
        try:
            payload["checkout"] = provider.create_checkout(order_id=payload["order_id"], amount=order["amount"], currency=order["currency"])
        except payments.PaymentError as error:
            # The order exists and can be reused, but no checkout can be produced yet. The
            # client is told why instead of being sent to a made-up payment page.
            raise ApiError(error.status, error.code, error.message) from error
        payload["message"] = f"Pesanan dibuat. Harga Premium Rp{order['amount']:,}".replace(",", ".")
        return payload

    def payment_order_status(self, user: sqlite3.Row, order_id: str) -> dict[str, Any]:
        self._require_permanent_account(user, "memeriksa pesanan")
        reference = str(order_id or "").strip()[:120]
        if not reference:
            raise ApiError(400, "INVALID_ORDER", "ID pesanan wajib diisi.")
        with self.connection() as db:
            self._expire_tokens(db)
            row = db.execute("SELECT * FROM payment_orders WHERE order_id=?", (reference,)).fetchone()
        if row is None or row["user_id"] != user["user_id"]:
            raise ApiError(404, "ORDER_NOT_FOUND", "Pesanan tidak ditemukan untuk akun ini.")
        payload = self._order_payload(row)
        payload["token_pending"] = payload["paid"] and not payload["token_delivered"]
        return payload

    def claim_payment_token(self, user: sqlite3.Row, order_id: str, ip: str) -> dict[str, Any]:
        """Hand the buyer the one Premium token their paid order entitles them to.

        The plaintext code is generated here, returned exactly in this response and never stored
        (only its HMAC is kept, exactly like administrator-issued tokens). Until the token is
        activated the owner may fetch a fresh code — each fetch replaces the previous hash, so an
        old code stops working. This removes the "paid but the code was lost" failure mode
        without ever writing a usable secret to the database.
        """
        self._require_permanent_account(user, "mengambil token Premium")
        reference = str(order_id or "").strip()[:120]
        if not reference:
            raise ApiError(400, "INVALID_ORDER", "ID pesanan wajib diisi.")
        self._rate_limit_standalone("payment-claim", user["user_id"] + ":" + ip, 10, 900)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                order = db.execute("SELECT * FROM payment_orders WHERE order_id=?", (reference,)).fetchone()
                if order is None or order["user_id"] != user["user_id"]:
                    raise ApiError(404, "ORDER_NOT_FOUND", "Pesanan tidak ditemukan untuk akun ini.")
                if order["status"] == "review":
                    raise ApiError(409, "ORDER_NEEDS_REVIEW", "Pesanan ini sedang ditinjau manual karena pembayaran tidak cocok dengan nilai pesanan.")
                if order["status"] != "paid":
                    raise ApiError(409, "ORDER_NOT_PAID", "Token hanya diterbitkan setelah pembayaran diverifikasi sukses oleh server.")
                token = db.execute("SELECT token_id,status,claimed_at FROM premium_tokens WHERE issued_for_order_id=?", (reference,)).fetchone()
                if token is None:
                    raise ApiError(409, "TOKEN_NOT_ISSUED", "Token untuk pesanan ini belum diterbitkan. Muat ulang status pesanan.")
                if token["status"] == "revoked":
                    raise ApiError(403, "TOKEN_REVOKED", "Token untuk pesanan ini telah dicabut. Hubungi dukungan.")
                if token["status"] == "claimed" or token["claimed_at"]:
                    raise ApiError(409, "TOKEN_ALREADY_ACTIVATED", "Token untuk pesanan ini sudah diaktifkan. Premium mengikuti akun dan tetap aktif permanen.")
                code = "EQP-" + secrets.token_hex(24).upper()
                db.execute("UPDATE premium_tokens SET token_hash=? WHERE token_id=?", (self.token_hash(code), token["token_id"]))
                db.execute("UPDATE payment_orders SET token_delivered_at=?, updated_at=? WHERE order_id=?", (iso(), iso(), reference))
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "order_id": reference,
            "token": code,
            "message": "Token Premium diterbitkan untuk pesanan ini. Simpan lalu aktifkan pada akun Anda.",
        }

    def settle_payment(self, provider_name: str, notification: payments.PaymentNotification) -> dict[str, Any]:
        """Apply one verified provider notification. Idempotent per provider event id.

        Called only after signature verification. A duplicate notification of the same event
        never issues a second token, and only a positively recognised success state can settle an
        order — every other state (pending, failed, expired, unknown) is recorded and ignored.
        """
        event_key = f"{provider_name}:{notification.event_id}"[:200]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM payment_events WHERE event_key=?", (event_key,)).fetchone() is not None:
                    db.commit()
                    return {"accepted": True, "duplicate": True, "issued": False, "state": notification.state, "matched": True}
                order = db.execute("SELECT * FROM payment_orders WHERE order_id=?", (notification.order_reference,)).fetchone()
                db.execute("""
                  INSERT INTO payment_events(event_key,provider,order_reference,order_id,state,raw_status,amount,currency,received_at)
                  VALUES(?,?,?,?,?,?,?,?,?)
                """, (event_key, provider_name, notification.order_reference, order["order_id"] if order else None,
                      notification.state, notification.raw_status, notification.amount, notification.currency, iso()))
                if order is None:
                    # Unknown reference: recorded for reconciliation, nothing is granted.
                    db.commit()
                    return {"accepted": True, "duplicate": False, "issued": False, "state": notification.state, "matched": False}
                outcome = self._apply_payment_state(db, order, notification)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"accepted": True, "duplicate": False, **outcome}

    def _apply_payment_state(self, db: sqlite3.Connection, order: sqlite3.Row, notification: payments.PaymentNotification) -> dict[str, Any]:
        now = iso()
        state = notification.state
        if state == payments.STATUS_PAID:
            if notification.amount is None or notification.amount != order["amount"] or (notification.currency and notification.currency != order["currency"]):
                db.execute("UPDATE payment_orders SET status='review', updated_at=? WHERE order_id=?", (now, order["order_id"]))
                return {"issued": False, "state": state, "reason": "amount_mismatch", "review": True}
            if order["status"] == "paid":
                return {"issued": False, "state": state, "reason": "already_paid", "review": False}
            if order["status"] not in ("created", "pending", "failed"):
                # The order stopped being payable (expired window, previous success, manual
                # review) yet money arrived for it: a human reconciles it, nothing is issued.
                db.execute("UPDATE payment_orders SET status='review', updated_at=? WHERE order_id=?", (now, order["order_id"]))
                return {"issued": False, "state": state, "reason": "late_payment", "review": True}
            created = parse_iso(order["created_at"])
            if created is not None and (utc_now() - created).total_seconds() > PAYMENT_ORDER_TTL_SECONDS:
                db.execute("UPDATE payment_orders SET status='review', updated_at=? WHERE order_id=?", (now, order["order_id"]))
                return {"issued": False, "state": state, "reason": "order_expired", "review": True}
            # Exactly one entitlement per paid order: the unique index on issued_for_order_id is
            # the last line of defence if two successes for the same order ever race.
            token_id = str(uuid.uuid4())
            placeholder = "EQP-" + secrets.token_hex(24).upper()
            db.execute("""
              INSERT INTO premium_tokens(token_id,token_hash,status,created_at,issued_for_order_id)
              VALUES(?,?,'available',?,?)
            """, (token_id, self.token_hash(placeholder), now, order["order_id"]))
            db.execute("UPDATE payment_orders SET status='paid', paid_at=?, updated_at=?, token_id=? WHERE order_id=?", (now, now, token_id, order["order_id"]))
            return {"issued": True, "state": state, "reason": "paid", "review": False, "token_id": token_id}
        if state == payments.STATUS_PENDING:
            db.execute("UPDATE payment_orders SET status='pending', updated_at=? WHERE order_id=? AND status IN ('created','pending')", (now, order["order_id"]))
            return {"issued": False, "state": state, "reason": "pending", "review": False}
        if state == payments.STATUS_FAILED:
            # Not terminal: the buyer may retry with the same order while its window is open.
            db.execute("UPDATE payment_orders SET status='failed', updated_at=? WHERE order_id=? AND status IN ('created','pending')", (now, order["order_id"]))
            return {"issued": False, "state": state, "reason": state, "review": False}
        if state == payments.STATUS_EXPIRED:
            # Terminal for the provider too: money arriving after this is reconciled by hand.
            db.execute("UPDATE payment_orders SET status='expired', updated_at=? WHERE order_id=? AND status IN ('created','pending','failed')", (now, order["order_id"]))
            return {"issued": False, "state": state, "reason": state, "review": False}
        # An unrecognised provider word leaves the order untouched: it cannot pay for anything.
        return {"issued": False, "state": payments.STATUS_UNVERIFIED, "reason": "unrecognised_status", "review": False}

    def admin_payments(self, admin: sqlite3.Row, status: str = "all", search: str = "") -> list[dict[str, Any]]:
        self.require_admin(admin)
        allowed = {"all", "created", "pending", "paid", "failed", "expired", "review"}
        if status not in allowed:
            raise ApiError(400, "INVALID_STATUS", "Status filter tidak valid.")
        with self.connection() as db:
            self._expire_tokens(db)
            clauses, params = [], []
            if status != "all":
                clauses.append("o.status=?"); params.append(status)
            if search:
                clauses.append("(o.order_id LIKE ? OR o.user_id LIKE ? OR u.email LIKE ?)")
                needle = f"%{search[:80]}%"; params.extend([needle, needle, needle])
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(f"""
              SELECT o.order_id,o.provider,o.amount,o.currency,o.status,o.token_id,o.token_delivered_at,
                     o.created_at,o.updated_at,o.paid_at,u.email AS user_email,t.status AS token_status
              FROM payment_orders o
              LEFT JOIN users u ON u.user_id=o.user_id
              LEFT JOIN premium_tokens t ON t.token_id=o.token_id
              {where} ORDER BY o.created_at DESC LIMIT 1000
            """, params).fetchall()
        return [dict(row) for row in rows]

    def require_admin(self, user: sqlite3.Row) -> None:
        if user["role"] != "admin":
            raise ApiError(403, "ADMIN_REQUIRED", "Fitur ini hanya tersedia untuk administrator.")

    def create_tokens(self, admin: sqlite3.Row, count: int, expires_at: str | None) -> list[dict[str, str]]:
        self.require_admin(admin)
        if not isinstance(count, int) or count < 1 or count > 500:
            raise ApiError(400, "INVALID_COUNT", "Jumlah token harus 1–500.")
        expires = parse_iso(expires_at)
        if expires_at and (expires is None or expires <= utc_now()):
            raise ApiError(400, "INVALID_EXPIRY", "Tanggal kedaluwarsa harus berupa ISO-8601 di masa depan.")
        now, output = iso(), []
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for _ in range(count):
                    code = "EQP-" + secrets.token_hex(24).upper()
                    token_id = str(uuid.uuid4())
                    db.execute("INSERT INTO premium_tokens(token_id,token_hash,status,created_at,created_by_user_id,expires_at) VALUES(?,?,'available',?,?,?)", (token_id, self.token_hash(code), now, admin["user_id"], iso(expires) if expires else None))
                    # Plaintext exists only in this one-time response, never in the database.
                    output.append({"token_id": token_id, "code": code, "created_at": now, "expires_at": iso(expires) if expires else ""})
                db.commit()
            except Exception:
                db.rollback()
                raise
        return output

    def admin_tokens(self, admin: sqlite3.Row, status: str = "all", search: str = "") -> list[dict[str, Any]]:
        self.require_admin(admin)
        allowed = {"all", "available", "used", "revoked", "expired"}
        if status not in allowed:
            raise ApiError(400, "INVALID_STATUS", "Status filter tidak valid.")
        internal_status = "claimed" if status == "used" else status
        with self.connection() as db:
            self._expire_tokens(db)
            clauses, params = [], []
            if status != "all":
                clauses.append("t.status=?"); params.append(internal_status)
            if search:
                clauses.append("(t.token_id LIKE ? OR u.email LIKE ? OR u.user_id LIKE ?)")
                needle = f"%{search[:80]}%"; params.extend([needle,needle,needle])
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = db.execute(f"""
              SELECT t.token_id,t.status,t.claimed_by_user_id,u.email AS claimed_email,
                     t.claimed_at,t.created_at,t.expires_at
              FROM premium_tokens t LEFT JOIN users u ON u.user_id=t.claimed_by_user_id
              {where} ORDER BY t.created_at DESC LIMIT 1000
            """, params).fetchall()
        return [{**dict(row), "status": "used" if row["status"] == "claimed" else row["status"]} for row in rows]

    def admin_summary(self, admin: sqlite3.Row) -> dict[str, int]:
        self.require_admin(admin)
        with self.connection() as db:
            self._expire_tokens(db)
            values = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM premium_tokens GROUP BY status")}
            accounts = db.execute("SELECT COUNT(*) n FROM users WHERE role='user'").fetchone()["n"]
            premium_accounts = db.execute("SELECT COUNT(DISTINCT claimed_by_user_id) n FROM premium_tokens WHERE status='claimed'").fetchone()["n"]
            orders = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM payment_orders GROUP BY status")}
            revenue = db.execute("SELECT COALESCE(SUM(amount),0) n FROM payment_orders WHERE status='paid'").fetchone()["n"]
            unsettled_events = db.execute("""
              SELECT COUNT(*) n FROM payment_events e
              LEFT JOIN payment_orders o ON o.order_id=e.order_id
              WHERE e.state='paid' AND (o.order_id IS NULL OR o.status!='paid')
            """).fetchone()["n"]
        return {
            "available": values.get("available", 0), "used": values.get("claimed", 0),
            "revoked": values.get("revoked", 0), "expired": values.get("expired", 0),
            "users": accounts, "premium_accounts": premium_accounts,
            "orders_paid": orders.get("paid", 0), "orders_pending": orders.get("pending", 0) + orders.get("created", 0),
            "orders_review": orders.get("review", 0), "revenue_idr": int(revenue),
            # Paid notifications that did not settle an order (unmatched reference, amount
            # mismatch, expired order). These are exactly the cases a human must reconcile.
            "payments_unsettled": unsettled_events,
        }

    def revoke_token(self, admin: sqlite3.Row, token_id: str) -> None:
        self.require_admin(admin)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT status FROM premium_tokens WHERE token_id=?", (token_id,)).fetchone()
                if row is None:
                    raise ApiError(404, "TOKEN_NOT_FOUND", "Token tidak ditemukan.")
                if row["status"] == "revoked":
                    db.commit(); return
                db.execute("UPDATE premium_tokens SET status='revoked' WHERE token_id=?", (token_id,))
                manual_order = db.execute("SELECT order_id,status FROM manual_payment_orders WHERE token_id=?",(token_id,)).fetchone()
                if manual_order:
                    self._manual_admin(db,admin)
                    self._manual_audit(db,manual_order["order_id"],admin,"token_revoked",manual_order["status"],manual_order["status"])
                db.commit()
            except Exception:
                db.rollback(); raise


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "EnglishQuestAPI/1.0"
    sys_version = ""
    # Idle timeout per socket operation. Without it a client that opens a connection and then
    # stalls (slowloris-style) holds a worker thread forever; the per-operation timeout means a
    # slow but progressing transfer is unaffected while a stalled peer is dropped.
    timeout = 60
    PUBLIC_FILES = frozenset({
        "index.html", "styles.css", "app.js", "access-client.js", "library-data.js",
        "mission-data.js", "mission-data-extras.js", "sw.js", "manifest.webmanifest",
        "icon.svg", "icon-192.png", "icon-512.png",
        "images/aki.webp", "images/rina.webp", "images/suki.webp", "images/naya.webp", "images/bimo.webp",
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Suppress access URLs and request data entirely; credentials and token codes must never enter logs.
        return

    def end_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        allowed = {item.strip() for item in os.getenv("EQ_ALLOWED_ORIGINS", "").split(",") if item.strip()}
        if origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' https:; media-src 'self' data: blob:; object-src 'none'; base-uri 'self'; form-action 'self'")
        if isinstance(self.connection, ssl.SSLSocket) or os.getenv("EQ_TRUST_PROXY") == "1":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _ensure_secure(self) -> None:
        if isinstance(self.connection, ssl.SSLSocket):
            return
        if os.getenv("EQ_TRUST_PROXY") == "1":
            if self.headers.get("X-Forwarded-Proto", "").lower() != "https":
                raise ApiError(400, "HTTPS_REQUIRED", "Gunakan koneksi HTTPS.")
            return
        if os.getenv("EQ_ALLOW_INSECURE_DEV") == "1" and self.server.server_address[0] in ("127.0.0.1", "::1", "localhost"):
            return
        raise ApiError(400, "HTTPS_REQUIRED", "API mewajibkan HTTPS.")

    def _client_ip(self) -> str:
        if os.getenv("EQ_TRUST_PROXY") == "1":
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if forwarded:
                return forwarded[:80]
        return self.client_address[0]

    def _body(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "INVALID_LENGTH", "Permintaan tidak valid.")
        if size < 0 or size > MAX_BODY:
            raise ApiError(413, "BODY_TOO_LARGE", "Permintaan terlalu besar.")
        if size > 0:
            # State-changing endpoints accept JSON only. A cross-site HTML form can post
            # text/plain or form-encoded bodies without a CORS preflight, so rejecting any
            # non-JSON content type keeps those blind posts from reaching the handlers.
            media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if media_type != "application/json":
                raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Gunakan Content-Type application/json.")
        raw = self.rfile.read(size)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "INVALID_JSON", "Format permintaan tidak valid.")
        if not isinstance(parsed, dict):
            raise ApiError(400, "INVALID_JSON", "Format permintaan tidak valid.")
        return parsed

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self, payload: dict[str, Any] | None = None, status: int = 200) -> None:
        self._send_json(status, {"ok": True, **(payload or {})})

    def _error(self, error: ApiError) -> None:
        self._send_json(error.status, {"ok": False, "code": error.code, "message": error.message})

    def _bearer(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return header[7:].strip()

    def _admin_user(self) -> sqlite3.Row:
        user = self.store.authenticate(self._bearer())
        self.store.require_admin(user)
        return user

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def _public_file(self, path: str) -> Path | None:
        """Resolve a request path to an allowlisted file inside the project root, else None."""
        if path in ("/", "/index.html"):
            relative = "index.html"
        elif path in ("/admin", "/admin/"):
            relative = "backend/admin.html"
        else:
            relative = urllib.parse.unquote(path.lstrip("/"))
        if relative not in self.PUBLIC_FILES and relative != "backend/admin.html":
            return None
        target = (ROOT / relative).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            return None
        return target if target.is_file() else None

    def _send_public_file(self, path: str, send_body: bool = True) -> None:
        target = self._public_file(path)
        if target is None:
            self.send_error(404, "Not found")
            return
        try:
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type in ("application/javascript", "application/manifest+json") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if send_body:
                self.wfile.write(data)
        except OSError:
            self.send_error(500, "Unable to serve file")

    def do_HEAD(self) -> None:
        # HEAD answers for exactly the same allowlist as GET, with headers only. The inherited
        # SimpleHTTPRequestHandler file serving (which would bypass the allowlist) stays off.
        self._send_public_file(urllib.parse.urlsplit(self.path).path, send_body=False)

    def _legacy_payment_test_only(self) -> None:
        # Historical automated-payment tests remain usable locally. No production
        # checkout/webhook/token-rotation path survives the switch to manual DANA.
        if not (os.getenv("EQ_ALLOW_INSECURE_DEV") == "1" and
                self.server.server_address[0] in ("127.0.0.1", "::1", "localhost")):
            raise ApiError(410, "AUTOMATIC_PAYMENTS_DISABLED", "Checkout otomatis nonaktif. Gunakan pembayaran manual yang diverifikasi admin.")

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/"):
            try:
                self._ensure_secure()
                if path == "/api/health":
                    self._ok({"service": "english-quest", "status": "ready"}); return
                if path == "/api/manual-payments/config":
                    self._ok(manual_payments.configuration()); return
                if path.startswith("/api/payments/"):
                    self._legacy_payment_test_only()
                if path == "/api/payments/config":
                    # Public on purpose: the app must be able to show the price and whether
                    # automatic payment is live without holding a session. No secret is exposed.
                    provider = payments.provider_from_env()
                    self._ok({
                        "price_idr": PREMIUM_PRICE_IDR,
                        "currency": PAYMENT_CURRENCY,
                        "provider": provider.name if provider else "",
                        "available": bool(provider and provider.configured()),
                    }); return
                user = self.store.authenticate(self._bearer())
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if path in ("/api/manual-payments/orders", "/api/manual-payments/order"):
                    reference = query.get("order_id", [""])[0] if path.endswith("/order") else None
                    if reference == "":
                        raise ApiError(400,"INVALID_ORDER","ID pesanan wajib diisi.")
                    self._ok({"orders": self.store.manual_orders(user, reference)}); return
                if path == "/api/admin/manual-payments":
                    admin = self._admin_user()
                    self._ok({"orders": self.store.manual_orders(admin, admin=True, status=query.get("status", ["all"])[0], search=query.get("q", [""])[0])}); return
                if path == "/api/admin/manual-payments/audit":
                    admin = self._admin_user()
                    self._ok({"events": self.store.manual_audit(admin,query.get("order_id",[""])[0])}); return
                if path == "/api/me":
                    self._ok({"account": self.store.account_status(user)}); return
                if path == "/api/admin/summary":
                    admin = self._admin_user()
                    summary = self.store.admin_summary(admin)
                    with self.store.connection() as db:
                        summary["manual_pending"] = db.execute("SELECT COUNT(*) FROM manual_payment_orders WHERE status='pending'").fetchone()[0]
                        summary["manual_verified"] = db.execute("SELECT COUNT(*) FROM manual_payment_orders WHERE status IN ('verified','issued')").fetchone()[0]
                        summary["manual_revenue_idr"] = db.execute("SELECT COALESCE(SUM(received_amount),0) FROM manual_payment_orders WHERE status IN ('verified','issued')").fetchone()[0]
                    self._ok({"summary":summary}); return
                if path == "/api/admin/tokens":
                    admin = self._admin_user(); query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    items = self.store.admin_tokens(admin, query.get("status", ["all"])[0], query.get("q", [""])[0])
                    self._ok({"tokens": items}); return
                if path == "/api/admin/payments":
                    admin = self._admin_user(); query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    items = self.store.admin_payments(admin, query.get("status", ["all"])[0], query.get("q", [""])[0])
                    self._ok({"orders": items}); return
                if path == "/api/payments/order":
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    result = self.store.payment_order_status(user, query.get("order_id", [""])[0])
                    self._ok(result); return
                raise ApiError(404, "NOT_FOUND", "Endpoint tidak ditemukan.")
            except ApiError as error:
                self._error(error)
            except Exception:
                self._send_json(500, {"ok": False, "code": "SERVER_ERROR", "message": "Layanan mengalami masalah. Coba lagi nanti."})
            return
        self._send_public_file(path)

    def _raw_body(self) -> bytes:
        """Read the request body as received, without content-type or JSON requirements.

        Payment notifications are authenticated by their signature over the exact bytes the
        provider sent, so they must not be re-encoded or forced through the JSON-only path used
        by the state-changing account endpoints.
        """
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "INVALID_LENGTH", "Permintaan tidak valid.")
        if size < 0 or size > MAX_BODY:
            raise ApiError(413, "BODY_TOO_LARGE", "Permintaan terlalu besar.")
        return self.rfile.read(size) if size else b""

    def _settle_with_provider(self, provider: payments.PaymentProvider, raw: bytes | None = None, headers: Any = None) -> None:
        """Verify one notification and let the store apply it. Used by every webhook route."""
        body = self._raw_body() if raw is None else raw
        head = self.headers if headers is None else headers
        try:
            notification = provider.verify_and_parse(head, body)
        except payments.PaymentError as error:
            raise ApiError(error.status, error.code, error.message) from error
        result = self.store.settle_payment(provider.name, notification)
        self._ok(result)

    def _post_payment_webhook(self) -> None:
        provider = payments.provider_from_env()
        if provider is None or not provider.configured():
            # Fail closed: an unconfigured deployment accepts no payment notifications at all.
            raise ApiError(503, "PAYMENTS_NOT_CONFIGURED", "Webhook pembayaran tidak aktif: provider pembayaran belum dikonfigurasi pada server ini.")
        self._settle_with_provider(provider)

    def _post_sandbox_settlement(self) -> None:
        """Local simulator: signs the caller's request server-side and replays the real path.

        Only reachable when EQ_PAYMENT_PROVIDER=sandbox is combined with EQ_ALLOW_INSECURE_DEV=1
        on a loopback listener, and only the owner of the order can trigger it. It exists so the
        full payment path (signature verification -> settlement -> token issuance -> activation)
        can be exercised without any real money and without inventing provider endpoints.
        """
        provider = payments.provider_from_env()
        if not isinstance(provider, payments.SandboxProvider) or not provider.configured():
            raise ApiError(404, "NOT_FOUND", "Endpoint tidak ditemukan.")
        user = self.store.authenticate(self._bearer())
        raw = self._raw_body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "INVALID_JSON", "Format permintaan tidak valid.")
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_JSON", "Format permintaan tidak valid.")
        reference = str(payload.get("order_id", "") or "")
        order = self.store.payment_order_status(user, reference)  # also enforces ownership
        amount = payload.get("amount", "")
        if amount in ("", None):
            amount = order["amount"]
        notification = json.dumps({
            "order_id": reference,
            "amount": amount,
            "currency": str(payload.get("currency", order["currency"]) or order["currency"]),
            "status": str(payload.get("status", "SUCCESS") or "SUCCESS"),
        }, separators=(",", ":")).encode("utf-8")
        # Any signature the caller supplied is ignored: the simulator signs with its own secret,
        # and the shared verification path cannot tell the difference from a provider call.
        signed_headers = {
            "X-EQ-Signature": provider.sign(notification),
            "X-EQ-Event-Id": str(payload.get("event_id") or ("sbx-" + secrets.token_hex(8))),
        }
        self._settle_with_provider(provider, raw=notification, headers=signed_headers)

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            self._ensure_secure()
            if path.startswith("/api/payments/"):
                self._legacy_payment_test_only()
            if path == "/api/payments/webhook":
                self._post_payment_webhook(); return
            if path == "/api/payments/sandbox/settle":
                self._post_sandbox_settlement(); return
            body = self._body()
            ip = self._client_ip()
            if path == "/api/demo/start":
                result = self.store.start_anonymous_demo(ip)
                self._ok(result, 201); return
            if path == "/api/register":
                result = self.store.register(str(body.get("email", "")), str(body.get("password", "")), ip)
                self._ok(result, 201); return
            if path == "/api/login":
                result = self.store.login(str(body.get("email", "")), str(body.get("password", "")), ip)
                self._ok(result); return
            if path == "/api/logout":
                token = self._bearer()
                if token:
                    self.store.logout(token)
                self._ok({"logged_out": True}); return
            user = self.store.authenticate(self._bearer())
            if path == "/api/manual-payments/orders":
                self._ok(self.store.create_manual_order(user,ip,body.get("amount",5000)),201); return
            if path.startswith("/api/admin/manual-payments/"):
                admin = self._admin_user()
                self.store._rate_limit_standalone("manual-admin",admin["user_id"]+":"+ip,60,900)
                action = path.rsplit("/",1)[-1]
                if action == "record":
                    self._ok(self.store.record_manual_payment(admin,body)); return
                self._ok(self.store.manual_admin_action(admin,body.get("order_id"),action,body)); return
            if path == "/api/demo/sync":
                result = self.store.sync_demo_questions(user, body.get("question_ids"), ip)
                self._ok(result); return
            if path == "/api/demo/consume":
                result = self.store.consume_demo_question(user, str(body.get("question_id", "")), ip, str(body.get("answer", "")))
                self._ok(result); return
            if path == "/api/premium/activate":
                result = self.store.activate_token(user, str(body.get("token", "")), ip)
                self._ok(result); return
            if path == "/api/payments/order":
                result = self.store.create_payment_order(user, ip)
                self._ok(result, 201); return
            if path == "/api/payments/claim":
                result = self.store.claim_payment_token(user, str(body.get("order_id", "")), ip)
                self._ok(result); return
            if path == "/api/admin/tokens":
                admin = self._admin_user()
                self._legacy_payment_test_only()
                expires_at = body.get("expires_at") or None
                result = self.store.create_tokens(admin, body.get("count", 1), str(expires_at) if expires_at else None)
                self._ok({"tokens": result}, 201); return
            if path == "/api/admin/tokens/revoke":
                admin = self._admin_user()
                self.store.revoke_token(admin, str(body.get("token_id", "")))
                self._ok({"revoked": True}); return
            raise ApiError(404, "NOT_FOUND", "Endpoint tidak ditemukan.")
        except ApiError as error:
            self._error(error)
        except Exception:
            self._send_json(500, {"ok": False, "code": "SERVER_ERROR", "message": "Layanan mengalami masalah. Coba lagi nanti."})


def create_admin(email: str, password: str, store: Store) -> str:
    email = normalize_email(email)
    if not EMAIL_RE.fullmatch(email) or len(password) < ADMIN_PASSWORD_MIN_LENGTH:
        raise ValueError(f"Email invalid atau kata sandi admin kurang dari {ADMIN_PASSWORD_MIN_LENGTH} karakter.")
    salt, digest = hash_password(password)
    user_id = str(uuid.uuid4())
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO users(user_id,email,password_salt,password_hash,role,created_at) VALUES(?,?,?,?, 'admin',?)", (user_id,email,salt,digest,iso()))
            db.commit()
        except Exception:
            db.rollback(); raise
    return user_id


def build_server() -> http.server.ThreadingHTTPServer:
    db_path = os.getenv("EQ_DB_PATH", str(DB_DEFAULT))
    pepper = os.getenv("EQ_TOKEN_PEPPER", "")
    store = Store(db_path, pepper)
    host = os.getenv("EQ_HOST", "127.0.0.1")
    if not (os.getenv("EQ_TLS_CERT_FILE") and os.getenv("EQ_TLS_KEY_FILE")):
        if os.getenv("EQ_TRUST_PROXY") != "1" and os.getenv("EQ_ALLOW_INSECURE_DEV") != "1":
            raise RuntimeError("Configure TLS files or a trusted TLS proxy. For loopback development only, set EQ_ALLOW_INSECURE_DEV=1.")
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("Without direct TLS, bind only to loopback behind a trusted HTTPS reverse proxy.")
    server = http.server.ThreadingHTTPServer((host, int(os.getenv("EQ_PORT", "8088"))), Handler)
    server.daemon_threads = True
    server.store = store  # type: ignore[attr-defined]
    cert, key = os.getenv("EQ_TLS_CERT_FILE"), os.getenv("EQ_TLS_KEY_FILE")
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


if __name__ == "__main__":
    try:
        server = build_server()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(f"English Quest API listening on {server.server_address[0]}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
