"""Failure-path regression tests for the English Quest API.

These tests cover behaviour that only shows up when something goes wrong: failed
authentication attempts, hostile or malformed requests, expired sessions, stalled
sockets, and housekeeping. They complement test_backend.py, which covers the
success paths, and they deliberately assert the *fixed* behaviour so the bugs they
reproduce cannot come back silently.

No credentials, tokens or secrets are printed by this module.
"""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from backend import server as api


PEPPER = "failure-path-pepper-not-for-production-0123456789"

# Every helper below uses a unique email per test so the IP-scoped registration limit
# of the previous test can never leak into the next one.
EMAIL_SEQ = iter(range(1, 10000))


def next_email(prefix="failure"):
    return f"{prefix}-{next(EMAIL_SEQ)}@example.test"


class FailurePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = api.Store(Path(self.tmp.name) / "failure.sqlite3", PEPPER)
        self._saved_env = os.environ.get("EQ_ALLOW_INSECURE_DEV")
        os.environ["EQ_ALLOW_INSECURE_DEV"] = "1"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.server.store = self.store
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self._saved_env is None:
            os.environ.pop("EQ_ALLOW_INSECURE_DEV", None)
        else:
            os.environ["EQ_ALLOW_INSECURE_DEV"] = self._saved_env
        self.tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def call(self, method, path, data=None, headers=None, timeout=10):
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post_json(self, path, payload, extra_headers=None):
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers or {})
        return self.call("POST", path, json.dumps(payload).encode(), headers)

    def rate_limit_row(self, scope):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT requests FROM rate_limits WHERE bucket LIKE ?",
                (scope + ":%",)).fetchall()
        return [row["requests"] for row in rows]

    # ------------------------------------------------- failed auth must count
    def test_failed_logins_consume_the_rate_limit_and_are_cut_off(self):
        """Regression: the counter used to live inside the rolled-back transaction.

        A rejected login raises ApiError, and the caller's rollback also discarded the
        increment, so 20 failed logins produced *no* rate_limits row at all and the
        12-attempts-per-15-minutes limit never triggered — unlimited password guessing.
        """
        statuses = [self.post_json("/api/login", {
            "email": "nobody@example.test", "password": "wrong-password-1"})[0]
            for _ in range(12)]
        self.assertEqual(set(statuses), {401}, "wrong credentials must be rejected")
        self.assertTrue(self.rate_limit_row("login"), "failed attempts must be counted")
        self.assertGreaterEqual(max(self.rate_limit_row("login")), 12)

        status, body = self.post_json("/api/login", {
            "email": "nobody@example.test", "password": "wrong-password-1"})
        self.assertEqual(status, 429, "the limit must cut off further guessing")
        self.assertEqual(json.loads(body)["code"], "RATE_LIMITED")

    def test_failed_logins_are_limited_at_the_store_level_too(self):
        for _ in range(12):
            with self.assertRaises(api.ApiError) as raised:
                self.store.login("nobody@example.test", "wrong-password-1", "203.0.113.77")
            self.assertEqual(raised.exception.status, 401)
        with self.assertRaises(api.ApiError) as raised:
            self.store.login("nobody@example.test", "wrong-password-1", "203.0.113.77")
        self.assertEqual(raised.exception.status, 429)

    def test_rejected_registrations_consume_the_rate_limit(self):
        email = next_email("dup")
        self.assertEqual(self.post_json("/api/register", {
            "email": email, "password": "a-long-api-password-42"})[0], 201)
        statuses = [self.post_json("/api/register", {
            "email": email, "password": "a-long-api-password-42"})[0] for _ in range(4)]
        self.assertEqual(set(statuses), {409}, "duplicate account must be refused")
        self.assertEqual(self.post_json("/api/register", {
            "email": email, "password": "a-long-api-password-42"})[0], 429)

    def test_failed_token_claims_consume_the_rate_limit(self):
        """A refused claim must still cost an attempt (8 per 15 minutes)."""
        email = next_email("claimer")
        self.assertEqual(self.post_json("/api/register", {
            "email": email, "password": "a-long-api-password-42"})[0], 201)
        session = self.store.login(email, "a-long-api-password-42", "203.0.113.2")["session_token"]
        auth = {"Authorization": f"Bearer {session}"}
        unknown = "EQP-" + "A" * 48
        statuses = [self.post_json("/api/premium/activate", {"token": unknown}, auth)[0]
                    for _ in range(8)]
        self.assertEqual(set(statuses), {404}, "an unknown token must be refused")
        self.assertEqual(self.post_json("/api/premium/activate", {"token": unknown}, auth)[0], 429)
        # The refused claims must not have granted Premium as a side effect.
        account = self.store.account_status(self.store.authenticate(session))
        self.assertFalse(account["premium"])
        self.assertEqual(account["access_level"], "demo")

    # --------------------------------------------------- malformed / hostile
    def test_non_json_content_type_with_body_is_rejected(self):
        """A cross-site HTML form can post text/plain without a CORS preflight."""
        status, body = self.call(
            "POST", "/api/register",
            json.dumps({"email": next_email("ctype"), "password": "a-long-api-password-42"}).encode(),
            {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(json.loads(body)["code"], "UNSUPPORTED_MEDIA_TYPE")
        status, _ = self.call(
            "POST", "/api/login", b"email=a%40b.test&password=x",
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_body_larger_than_the_limit_is_rejected(self):
        status, body = self.post_json("/api/register", {"email": "a" * 40000})
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["code"], "BODY_TOO_LARGE")

    def test_invalid_json_and_empty_body_are_rejected(self):
        for payload in (b"{not json", b"", b"[]", b"null"):
            status, body = self.call("POST", "/api/register", payload,
                                     {"Content-Type": "application/json"})
            self.assertEqual(status, 400, f"payload {payload!r} must be refused")
            self.assertEqual(json.loads(body)["code"], "INVALID_JSON")

    def test_unknown_api_path_does_not_leak_which_endpoints_exist(self):
        """By design: anonymous callers get the same 401 for unknown and known paths.

        Returning 404 for unknown routes would let an unauthenticated attacker map the
        API surface. An authenticated caller gets an honest 404.
        """
        self.assertEqual(self.call("GET", "/api/does-not-exist")[0], 401)
        self.assertEqual(self.call("GET", "/api/admin/tokens")[0], 401)
        email = next_email("mapper")
        self.post_json("/api/register", {"email": email, "password": "a-long-api-password-42"})
        session = self.store.login(email, "a-long-api-password-42", "203.0.113.3")["session_token"]
        status, body = self.call("GET", "/api/does-not-exist",
                                 headers={"Authorization": f"Bearer {session}"})
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["code"], "NOT_FOUND")

    # ----------------------------------------------------------- sessions
    def test_expired_session_is_rejected(self):
        email = next_email("expired")
        self.post_json("/api/register", {"email": email, "password": "a-long-api-password-42"})
        session = self.store.login(email, "a-long-api-password-42", "203.0.113.4")["session_token"]
        with self.store.connection() as db:
            db.execute("UPDATE sessions SET expires_at=?",
                       (api.iso(api.utc_now() - api.timedelta(days=1)),))
        status, body = self.call("GET", "/api/me",
                                 headers={"Authorization": f"Bearer {session}"})
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["code"], "SESSION_EXPIRED")

    def test_revoked_session_is_rejected(self):
        email = next_email("revoked")
        self.post_json("/api/register", {"email": email, "password": "a-long-api-password-42"})
        session = self.store.login(email, "a-long-api-password-42", "203.0.113.5")["session_token"]
        auth = {"Authorization": f"Bearer {session}"}
        self.assertEqual(self.call("POST", "/api/logout", b"{}",
                                   {**auth, "Content-Type": "application/json"})[0], 200)
        status, body = self.call("GET", "/api/me", headers=auth)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["code"], "SESSION_EXPIRED")

    def test_pruning_removes_dead_sessions_only(self):
        """Housekeeping must never touch a session that can still authenticate."""
        email = next_email("prune")
        self.post_json("/api/register", {"email": email, "password": "a-long-api-password-42"})
        live = self.store.login(email, "a-long-api-password-42", "203.0.113.6")["session_token"]
        dead = self.store.login(email, "a-long-api-password-42", "203.0.113.6")["session_token"]
        old = api.iso(api.utc_now() - api.timedelta(days=api.SESSION_RETENTION_DAYS + 1))
        with self.store.connection() as db:
            db.execute("UPDATE sessions SET expires_at=? WHERE session_hash=?",
                       (old, api.Store.session_hash(dead)))
            db.execute("INSERT INTO rate_limits(bucket,window_start,requests) VALUES('stale:bucket',1,9)")
            remaining_before = db.execute("SELECT COUNT(*) n FROM sessions").fetchone()["n"]
        self.assertGreaterEqual(remaining_before, 2)

        # Any authenticated call runs the expiry + pruning pass.
        self.store.account_status(self.store.authenticate(live))

        with self.store.connection() as db:
            hashes = {row["session_hash"] for row in
                      db.execute("SELECT session_hash FROM sessions").fetchall()}
            stale_buckets = db.execute(
                "SELECT COUNT(*) n FROM rate_limits WHERE bucket='stale:bucket'").fetchone()["n"]
        self.assertIn(api.Store.session_hash(live), hashes, "live session must survive pruning")
        self.assertNotIn(api.Store.session_hash(dead), hashes, "long-dead session must be pruned")
        self.assertEqual(stale_buckets, 0, "stale rate-limit buckets must be pruned")
        # The live session still authenticates after the pruning pass.
        self.assertEqual(self.store.authenticate(live)["email"], email)

    # ------------------------------------------------------- HTTP surface
    def test_head_uses_the_same_allowlist_as_get(self):
        request = urllib.request.Request(self.base + "/", method="HEAD")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"", "HEAD must not return a body")
            self.assertGreater(int(response.headers.get("Content-Length", "0")), 0)
        self.assertEqual(self.call("HEAD", "/backend/server.py")[0], 404)
        self.assertEqual(self.call("HEAD", "/tests/test_backend.py")[0], 404)

    def test_stalled_connection_is_dropped_by_the_socket_timeout(self):
        """A peer that connects and never sends a request must not hold a worker forever."""
        class QuickTimeoutHandler(api.Handler):
            timeout = 1

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuickTimeoutHandler)
        server.store = self.store
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = socket.create_connection(server.server_address, timeout=10)
            client.settimeout(10)
            started = time.monotonic()
            try:
                # The handler gives up after its 1s idle timeout and closes the connection.
                data = client.recv(64)
            except (ConnectionResetError, socket.timeout):
                data = b""
            elapsed = time.monotonic() - started
            self.assertEqual(data, b"", "stalled connection must be closed, not answered")
            self.assertLess(elapsed, 8, "the drop must happen promptly, not after a long wait")
            client.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_default_handler_timeout_is_configured(self):
        self.assertIsNotNone(getattr(api.Handler, "timeout", None))
        self.assertLessEqual(api.Handler.timeout, 120)


if __name__ == "__main__":
    unittest.main()
