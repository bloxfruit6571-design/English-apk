import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

from backend import server as api


PEPPER = "test-pepper-not-for-production-0123456789abcdef"


class BackendAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        self.store = api.Store(self.db, PEPPER)
        self.accounts = {}

    def tearDown(self):
        self.tmp.cleanup()

    def user(self, email="learner@example.test", password="correct-horse-47", ip="198.51.100.10"):
        response = self.store.register(email, password, ip)
        user = self.store.authenticate(response["session_token"])
        self.accounts[email] = (password, response["session_token"], user)
        return user, response["session_token"], password

    def admin(self):
        uid = api.create_admin("admin@example.test", "admin-password-123", self.store)
        response = self.store.login("admin@example.test", "admin-password-123", "198.51.100.70")
        return self.store.authenticate(response["session_token"])

    def token(self):
        return self.store.create_tokens(self.admin(), 1, None)[0]["code"]

    def test_existing_database_migrates_without_losing_users_or_answer_rows(self):
        legacy_db = Path(self.tmp.name) / "legacy.sqlite3"
        qid = sorted(api.DEMO_QUESTION_IDS)[0]
        db = sqlite3.connect(legacy_db)
        try:
            db.executescript("""
              CREATE TABLE users(user_id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,password_salt TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',created_at TEXT NOT NULL);
              CREATE TABLE demo_answers(user_id TEXT NOT NULL,question_id TEXT NOT NULL,answered_at TEXT NOT NULL,PRIMARY KEY(user_id,question_id));
            """)
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", ("legacy-user","legacy@example.test","salt","hash","user",api.iso()))
            db.execute("INSERT INTO demo_answers VALUES(?,?,?)", ("legacy-user",qid,api.iso()))
            db.commit()
        finally:
            db.close()
        migrated = api.Store(legacy_db, PEPPER)
        with migrated.connection() as db:
            user = db.execute("SELECT user_id,email,anonymous FROM users WHERE user_id='legacy-user'").fetchone()
            answer = db.execute("SELECT question_id,answer_hash FROM demo_answers WHERE user_id='legacy-user'").fetchone()
        self.assertEqual(user["email"], "legacy@example.test")
        self.assertEqual(user["anonymous"], 0)
        self.assertEqual(answer["question_id"], qid)
        self.assertIsNone(answer["answer_hash"], "legacy-used questions remain consumed after migration")

    def test_registration_password_policy_email_and_duplicate_handling(self):
        """The documented registration contract: minimum 6 characters, valid email, one account
        per email, PBKDF2 hashing at rest, and a usable login afterwards."""
        ip = "198.51.100.41"
        with self.assertRaises(api.ApiError) as short:
            self.store.register("short@example.test", "ab12c", ip)          # five characters
        self.assertEqual(short.exception.code, "WEAK_PASSWORD")
        self.assertIn(str(api.PASSWORD_MIN_LENGTH), short.exception.message)

        created = self.store.register("six@example.test", "ab12cd", ip)       # exactly six
        account = self.store.authenticate(created["session_token"])
        self.assertEqual(account["email"], "six@example.test")
        self.assertEqual(account["role"], "user")
        status = self.store.account_status(account)
        self.assertFalse(status["premium"], "a new account never starts Premium")
        self.assertEqual(status["demo_remaining"], api.DEMO_LIMIT)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT password_salt, password_hash FROM users WHERE email=?", ("six@example.test",)
            ).fetchone()
        self.assertNotEqual(row["password_hash"], "ab12cd", "the password must not be stored in clear text")
        # Stored as base64url: a 16-byte salt and a 32-byte PBKDF2-HMAC-SHA256 digest.
        self.assertRegex(row["password_salt"], r"^[A-Za-z0-9_-]{22}==$")
        self.assertRegex(row["password_hash"], r"^[A-Za-z0-9_-]{43}=$", "PBKDF2-HMAC-SHA256 digest expected")

        login = self.store.login("six@example.test", "ab12cd", ip)
        self.assertEqual(self.store.authenticate(login["session_token"])["email"], "six@example.test")
        with self.assertRaises(api.ApiError) as wrong:
            self.store.login("six@example.test", "wrong-password", ip)
        self.assertEqual(wrong.exception.code, "INVALID_CREDENTIALS")

        with self.assertRaises(api.ApiError) as duplicate:
            self.store.register("six@example.test", "ab12cd", ip)
        self.assertEqual(duplicate.exception.code, "ACCOUNT_EXISTS")
        with self.assertRaises(api.ApiError) as invalid_email:
            self.store.register("not-an-email", "ab12cd", ip)
        self.assertEqual(invalid_email.exception.code, "INVALID_EMAIL")

    def test_new_account_starts_server_authoritative_demo(self):
        user, _, _ = self.user()
        status = self.store.account_status(user)
        self.assertEqual(status["access_level"], "demo")
        self.assertEqual(status["demo_limit"], api.DEMO_LIMIT)
        self.assertEqual(status["demo_limit"], 5, "the public demo is five unique Easy questions")
        self.assertEqual(status["demo_used"], 0)
        self.assertEqual(status["demo_remaining"], 5)
        self.assertEqual(status["used_question_ids"], [])

    def test_guest_demo_is_accountless_server_session_with_ip_start_limit(self):
        created = self.store.start_anonymous_demo("198.51.100.44")
        guest = self.store.authenticate(created["session_token"])
        self.assertTrue(self.store.account_status(guest)["anonymous"])
        self.assertEqual(self.store.account_status(guest)["demo_remaining"], 5)
        for question_id in sorted(api.DEMO_QUESTION_IDS):
            self.store.consume_demo_question(guest, question_id, "198.51.100.44", "one submitted answer")
        self.assertEqual(self.store.account_status(guest)["demo_remaining"], 0)
        # The guest start limiter is atomic and scoped by source IP.
        for _ in range(19):
            self.store.start_anonymous_demo("198.51.100.44")
        with self.assertRaises(api.ApiError) as limited:
            self.store.start_anonymous_demo("198.51.100.44")
        self.assertEqual(limited.exception.status, 429)
        self.assertEqual(limited.exception.code, "RATE_LIMITED")

    def test_demo_sync_imports_only_fixed_ids_is_idempotent_and_cannot_replay(self):
        user, _, _ = self.user()
        ids = sorted(api.DEMO_QUESTION_IDS)[:3]
        synced = self.store.sync_demo_questions(user, ids, "198.51.100.10")
        self.assertEqual(synced["imported"], 3)
        self.assertEqual(synced["demo_used"], 3)
        again = self.store.sync_demo_questions(user, ids, "198.51.100.10")
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["demo_used"], 3)
        with self.store.connection() as db:
            hashes = db.execute("SELECT answer_hash FROM demo_answers WHERE user_id=?", (user["user_id"],)).fetchall()
        self.assertTrue(all(row["answer_hash"] is None for row in hashes), "imports consume quota but do not create replayable answer bindings")
        with self.assertRaises(api.ApiError) as replay:
            self.store.consume_demo_question(user, ids[0], "198.51.100.10", "new answer")
        self.assertEqual(replay.exception.code, "DEMO_ANSWER_ALREADY_USED")
        with self.assertRaises(api.ApiError) as invalid:
            self.store.sync_demo_questions(user, ["not-in-the-demo"], "198.51.100.10")
        self.assertEqual(invalid.exception.code, "QUESTION_NOT_IN_DEMO")

    def test_demo_sync_and_answer_submission_share_atomic_demo_quota(self):
        user, _, _ = self.user()
        ids = sorted(api.DEMO_QUESTION_IDS)
        barrier = threading.Barrier(2)
        outcomes = []
        def sync_many():
            barrier.wait()
            outcomes.append(self.store.sync_demo_questions(user, ids[:3], "198.51.100.10"))
        def consume_many():
            barrier.wait()
            for qid in ids[3:]:
                try:
                    self.store.consume_demo_question(user, qid, "198.51.100.10", "submitted")
                except api.ApiError as exc:
                    self.assertEqual(exc.code, "DEMO_EXHAUSTED")
        first = threading.Thread(target=sync_many)
        second = threading.Thread(target=consume_many)
        first.start(); second.start(); first.join(timeout=20); second.join(timeout=20)
        self.assertFalse(first.is_alive() or second.is_alive())
        status = self.store.account_status(user)
        self.assertLessEqual(status["demo_used"], api.DEMO_LIMIT)
        self.assertEqual(status["demo_used"], api.DEMO_LIMIT)
        self.assertEqual(status["demo_remaining"], 0)

    def test_five_unique_answers_cut_off_and_duplicates_do_not_increment(self):
        user, _, _ = self.user()
        original = api.DEMO_QUESTION_IDS
        extra = "demo-extra-question"
        with patch.object(api, "DEMO_QUESTION_IDS", original | {extra}):
            ids = sorted(original)
            for qid in ids:
                result = self.store.consume_demo_question(user, qid, "198.51.100.10")
                self.assertTrue(result["consumed"])
            duplicate = self.store.consume_demo_question(user, ids[0], "198.51.100.10")
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["demo_used"], 5)
            with self.assertRaises(api.ApiError) as changed_answer:
                self.store.consume_demo_question(user, ids[0], "198.51.100.10", "a different guess")
            self.assertEqual(changed_answer.exception.code, "DEMO_ANSWER_MISMATCH")
            with self.assertRaises(api.ApiError) as caught:
                self.store.consume_demo_question(user, extra, "198.51.100.10")
            self.assertEqual(caught.exception.code, "DEMO_EXHAUSTED")
        status = self.store.account_status(user)
        self.assertEqual(status["demo_used"], 5)
        self.assertEqual(status["demo_remaining"], 0)

    def test_demo_ledger_survives_store_restart_and_session_changes(self):
        user, _, password = self.user()
        qid = sorted(api.DEMO_QUESTION_IDS)[0]
        self.store.consume_demo_question(user, qid, "198.51.100.10")
        reloaded = api.Store(self.db, PEPPER)
        login = reloaded.login("learner@example.test", password, "198.51.100.11")
        status = reloaded.account_status(reloaded.authenticate(login["session_token"]))
        self.assertEqual(status["demo_used"], 1)
        self.assertIn(qid, status["used_question_ids"])
        self.assertEqual(status["demo_remaining"], 4)

    def test_guest_account_cannot_claim_unrecoverable_premium_token(self):
        guest = self.store.authenticate(self.store.start_anonymous_demo("198.51.100.46")["session_token"])
        admin = self.admin()
        code = self.store.create_tokens(admin, 1, None)[0]["code"]
        with self.assertRaises(api.ApiError) as rejected:
            self.store.activate_token(guest, code, "198.51.100.46")
        self.assertEqual(rejected.exception.code, "ACCOUNT_REQUIRED")
        self.assertEqual(self.store.admin_summary(admin)["available"], 1)

    def test_first_claim_binds_token_to_account_and_returns_premium(self):
        user, _, _ = self.user()
        code = self.token()
        result = self.store.activate_token(user, code, "198.51.100.10")
        self.assertTrue(result["activated"])
        self.assertFalse(result["already_yours"])
        status = self.store.account_status(user)
        self.assertTrue(status["premium"])
        self.assertEqual(status["access_level"], "premium")
        self.assertEqual(status["demo_remaining"], 5)

    def test_second_account_cannot_claim_same_token(self):
        first, _, _ = self.user("first@example.test", ip="198.51.100.10")
        second, _, _ = self.user("second@example.test", ip="198.51.100.11")
        code = self.token()
        self.store.activate_token(first, code, "198.51.100.10")
        with self.assertRaises(api.ApiError) as caught:
            self.store.activate_token(second, code, "198.51.100.11")
        self.assertEqual(caught.exception.code, "TOKEN_ALREADY_USED")
        self.assertFalse(self.store.account_status(second)["premium"])

    def test_concurrent_claims_are_atomic(self):
        first, _, _ = self.user("one@example.test", ip="198.51.100.10")
        second, _, _ = self.user("two@example.test", ip="198.51.100.11")
        code = self.token()
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()

        def claim(user, ip):
            barrier.wait()
            try:
                result = self.store.activate_token(user, code, ip)
                value = ("ok", result)
            except api.ApiError as exc:
                value = ("error", exc.code)
            with lock:
                results.append(value)

        threads = [threading.Thread(target=claim, args=(first, "198.51.100.10")), threading.Thread(target=claim, args=(second, "198.51.100.11"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sum(1 for kind, _ in results if kind == "ok"), 1)
        self.assertEqual(sum(1 for kind, value in results if kind == "error" and value == "TOKEN_ALREADY_USED"), 1)

    def test_invalid_expired_and_revoked_tokens_are_denied(self):
        user, _, _ = self.user()
        with self.assertRaises(api.ApiError) as malformed:
            self.store.activate_token(user, "not-a-token", "198.51.100.10")
        self.assertEqual(malformed.exception.code, "INVALID_TOKEN")
        with self.assertRaises(api.ApiError) as unknown:
            self.store.activate_token(user, "EQP-" + "A" * 48, "198.51.100.10")
        self.assertEqual(unknown.exception.code, "INVALID_TOKEN")
        admin = self.admin()
        item = self.store.create_tokens(admin, 1, None)[0]
        self.store.revoke_token(admin, item["token_id"])
        with self.assertRaises(api.ApiError) as revoked:
            self.store.activate_token(user, item["code"], "198.51.100.10")
        self.assertEqual(revoked.exception.code, "TOKEN_REVOKED")
        expired = self.store.create_tokens(admin, 1, api.iso(api.utc_now() + api.timedelta(days=1)))[0]
        with self.store.connection() as db:
            db.execute("UPDATE premium_tokens SET expires_at=? WHERE token_id=?", (api.iso(api.utc_now() - api.timedelta(seconds=1)), expired["token_id"]))
        with self.assertRaises(api.ApiError) as expired_error:
            self.store.activate_token(user, expired["code"], "198.51.100.10")
        self.assertEqual(expired_error.exception.code, "TOKEN_EXPIRED")

    def test_premium_follows_account_after_logout_and_new_login(self):
        user, session, password = self.user()
        self.store.activate_token(user, self.token(), "198.51.100.10")
        self.store.logout(session)
        # New Store instance simulates a server restart; local app data is not needed for entitlement.
        reloaded = api.Store(self.db, PEPPER)
        login = reloaded.login("learner@example.test", password, "198.51.100.12")
        reauthenticated = reloaded.authenticate(login["session_token"])
        self.assertTrue(reloaded.account_status(reauthenticated)["premium"])
        self.assertNotEqual(session, login["session_token"])

    def test_admin_token_plaintext_is_not_in_listing_or_database(self):
        admin = self.admin()
        created = self.store.create_tokens(admin, 2, None)
        for item in created:
            self.assertTrue(item["code"].startswith("EQP-"))
        listing = self.store.admin_tokens(admin)
        self.assertEqual(len(listing), 2)
        self.assertNotIn("code", listing[0])
        with self.store.connection() as db:
            rows = db.execute("SELECT token_hash,status FROM premium_tokens").fetchall()
        for item in created:
            self.assertNotIn(item["code"], json.dumps([dict(row) for row in rows]))
        account, _, _ = self.user()
        self.store.activate_token(account, created[0]["code"], "198.51.100.10")
        used = self.store.admin_tokens(admin, status="used")
        self.assertEqual([item["token_id"] for item in used], [created[0]["token_id"]])
        self.assertEqual(used[0]["status"], "used")
        self.assertEqual(self.store.admin_summary(admin)["used"], 1)
        self.store.revoke_token(admin, created[0]["token_id"])
        statuses = {t["token_id"]: t["status"] for t in self.store.admin_tokens(admin)}
        self.assertEqual(statuses[created[0]["token_id"]], "revoked")

    def test_http_register_demo_api_and_answer_binding(self):
        old = os.environ.get("EQ_ALLOW_INSECURE_DEV")
        old_origins = os.environ.get("EQ_ALLOWED_ORIGINS")
        os.environ["EQ_ALLOW_INSECURE_DEV"] = "1"
        os.environ["EQ_ALLOWED_ORIGINS"] = "https://appassets.androidplatform.net"
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        server.store = self.store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            preflight = urllib.request.Request(base + "/api/demo/consume", method="OPTIONS", headers={"Origin":"https://appassets.androidplatform.net","Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"authorization,content-type"})
            cors = urllib.request.urlopen(preflight, timeout=5)
            self.assertEqual(cors.headers.get("Access-Control-Allow-Origin"), "https://appassets.androidplatform.net")
            self.assertIn("POST", cors.headers.get("Access-Control-Allow-Methods", ""))
            self.assertIn("Authorization", cors.headers.get("Access-Control-Allow-Headers", ""))
            home = urllib.request.urlopen(base + "/", timeout=5)
            self.assertIn(b'id="app"', home.read())
            asset = urllib.request.urlopen(base + "/app.js", timeout=5)
            self.assertIn(b"Coba Demo Gratis", asset.read())
            client_asset = urllib.request.urlopen(base + "/access-client.js", timeout=5)
            self.assertIn(b"getApiBaseUrl", client_asset.read())
            guest_start = urllib.request.Request(base + "/api/demo/start", data=b"{}", headers={"Content-Type":"application/json"}, method="POST")
            guest = json.loads(urllib.request.urlopen(guest_start, timeout=5).read())
            self.assertTrue(guest["ok"])
            guest_me = urllib.request.Request(base + "/api/me", headers={"Authorization":f"Bearer {guest['session_token']}"})
            guest_status = json.loads(urllib.request.urlopen(guest_me, timeout=5).read())
            self.assertTrue(guest_status["account"]["anonymous"])
            self.assertEqual(guest_status["account"]["demo_remaining"], 5)
            register = urllib.request.Request(base + "/api/register", data=json.dumps({"email":"api-user@example.test","password":"a-long-api-password-42"}).encode(), headers={"Content-Type":"application/json"}, method="POST")
            response = json.loads(urllib.request.urlopen(register, timeout=10).read())
            self.assertTrue(response["ok"])
            token = response["session_token"]
            me = urllib.request.Request(base + "/api/me", headers={"Authorization":f"Bearer {token}"})
            status = json.loads(urllib.request.urlopen(me, timeout=5).read())
            self.assertEqual(status["account"]["demo_remaining"], 5)
            qid = sorted(api.DEMO_QUESTION_IDS)[0]
            payload = json.dumps({"question_id":qid,"answer":"Good morning"}).encode()
            first = urllib.request.Request(base + "/api/demo/consume", data=payload, headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"}, method="POST")
            result = json.loads(urllib.request.urlopen(first, timeout=5).read())
            self.assertEqual(result["demo_used"], 1)
            changed = urllib.request.Request(base + "/api/demo/consume", data=json.dumps({"question_id":qid,"answer":"Good night"}).encode(), headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(changed, timeout=5)
            self.assertEqual(caught.exception.code, 409)
            self.assertEqual(json.loads(caught.exception.read())["code"], "DEMO_ANSWER_MISMATCH")
            sync = urllib.request.Request(base + "/api/demo/sync", data=json.dumps({"question_ids":[sorted(api.DEMO_QUESTION_IDS)[1]]}).encode(), headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"}, method="POST")
            sync_result = json.loads(urllib.request.urlopen(sync, timeout=5).read())
            self.assertEqual(sync_result["imported"], 1)
            self.assertEqual(sync_result["demo_used"], 2)
            me_after_sync = urllib.request.Request(base + "/api/me", headers={"Authorization":f"Bearer {token}"})
            status_after_sync = json.loads(urllib.request.urlopen(me_after_sync, timeout=5).read())["account"]
            self.assertEqual(status_after_sync["demo_remaining"], 3)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            if old is None:
                os.environ.pop("EQ_ALLOW_INSECURE_DEV", None)
            else:
                os.environ["EQ_ALLOW_INSECURE_DEV"] = old
            if old_origins is None:
                os.environ.pop("EQ_ALLOWED_ORIGINS", None)
            else:
                os.environ["EQ_ALLOWED_ORIGINS"] = old_origins

    def test_http_admin_endpoint_rejects_regular_account(self):
        user, session, _ = self.user()
        old = os.environ.get("EQ_ALLOW_INSECURE_DEV")
        os.environ["EQ_ALLOW_INSECURE_DEV"] = "1"
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        server.store = self.store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/admin/summary", headers={"Authorization": f"Bearer {session}"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            body = json.loads(caught.exception.read())
            self.assertEqual(caught.exception.code, 403)
            self.assertEqual(body["code"], "ADMIN_REQUIRED")
            for private_path in ("/backend/server.py", "/backend/manage.py", "/README.md"):
                private_request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{private_path}")
                with self.assertRaises(urllib.error.HTTPError) as private_error:
                    urllib.request.urlopen(private_request, timeout=5)
                self.assertEqual(private_error.exception.code, 404)
                head_request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{private_path}", method="HEAD")
                with self.assertRaises(urllib.error.HTTPError) as head_error:
                    urllib.request.urlopen(head_request, timeout=5)
                self.assertEqual(head_error.exception.code, 404)
            admin_page = urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/admin", timeout=5)
            self.assertIn(b"Token operations", admin_page.read())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            if old is None:
                os.environ.pop("EQ_ALLOW_INSECURE_DEV", None)
            else:
                os.environ["EQ_ALLOW_INSECURE_DEV"] = old


if __name__ == "__main__":
    unittest.main()
