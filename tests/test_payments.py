"""Premium purchase tests: demo limit, orders, webhook verification, token issuance, redemption.

These tests cover the business rules agreed for the one-off Premium purchase:

* Demo is capped at DEMO_LIMIT (5) unique Easy questions.
* Premium costs Rp5.000, once, and unlocks access permanently (no subscription).
* A verified successful payment issues exactly one Premium token, automatically.
* Pending / failed / expired / unrecognised / mismatched payments issue nothing.
* A token is validated by the backend and cannot be redeemed twice.
* Nothing settles without a cryptographically verified provider notification.

Run with:  python3 -m unittest tests.test_payments
No credentials, tokens or secrets are printed by this module.
"""
import base64
import hashlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from backend import payments, server as api


PEPPER = "payments-pepper-not-for-production-0123456789abcdef"
SANDBOX_SECRET = "sandbox-webhook-secret-0123456789abcdefghijklmnop"
DANA_PATH = "/api/payments/webhook"

try:  # The DANA adapter needs a real RSA implementation; the rest of the suite does not.
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAVE_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - environment without the optional dependency
    HAVE_CRYPTOGRAPHY = False


def sandbox_env(**overrides):
    env = {
        "EQ_PAYMENT_PROVIDER": "sandbox",
        "EQ_PAYMENT_WEBHOOK_SECRET": SANDBOX_SECRET,
        "EQ_ALLOW_INSECURE_DEV": "1",
    }
    env.update(overrides)
    return env


class PaymentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "payments.sqlite3"
        self.env = patch.dict(os.environ, sandbox_env(), clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = api.Store(self.db, PEPPER)

    def tearDown(self):
        self.tmp.cleanup()

    # --------------------------------------------------------------- helpers
    def account(self, email="buyer@example.test", password="correct-horse-47", ip="198.51.100.10"):
        response = self.store.register(email, password, ip)
        return self.store.authenticate(response["session_token"])

    def order(self, user, ip="198.51.100.10"):
        return self.store.create_payment_order(user, ip)

    def notification(self, order_id, status="SUCCESS", amount=5000, event_id="evt-1", currency="IDR"):
        body = json.dumps(
            {"order_id": order_id, "amount": amount, "currency": currency, "status": status},
            separators=(",", ":"),
        ).encode("utf-8")
        provider = payments.provider_from_env()
        return provider, provider.verify_and_parse(
            {"X-EQ-Signature": provider.sign(body), "X-EQ-Event-Id": event_id}, body
        )

    def settle(self, order_id, status="SUCCESS", amount=5000, event_id="evt-1"):
        provider, note = self.notification(order_id, status, amount, event_id)
        return self.store.settle_payment(provider.name, note)

    def issued_tokens(self, order_id):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT token_id,status,claimed_by_user_id FROM premium_tokens WHERE issued_for_order_id=?",
                (order_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def order_status(self, order_id):
        with self.store.connection() as db:
            return dict(db.execute("SELECT * FROM payment_orders WHERE order_id=?", (order_id,)).fetchone())

    # ------------------------------------------------------- business rules
    def test_demo_is_capped_at_five_unique_easy_questions(self):
        self.assertEqual(api.DEMO_LIMIT, 5, "the public business rule is a five-question demo")
        self.assertEqual(len(api.DEMO_QUESTION_IDS), 5, "the demo bank holds exactly five question IDs")
        self.assertTrue(
            all(qid.startswith("easy-greetings-q") for qid in api.DEMO_QUESTION_IDS),
            "the demo bank is a single complete Easy mission",
        )
        user = self.account()
        status = self.store.account_status(user)
        self.assertEqual(status["demo_limit"], 5)
        self.assertEqual(status["demo_remaining"], 5)
        for qid in sorted(api.DEMO_QUESTION_IDS):
            self.store.consume_demo_question(user, qid, "198.51.100.10")
        exhausted = self.store.account_status(user)
        self.assertEqual(exhausted["demo_used"], 5)
        self.assertEqual(exhausted["demo_remaining"], 0)
        self.assertEqual(exhausted["access_level"], "demo", "finishing the demo alone never grants Premium")

    def test_premium_price_is_a_one_off_five_thousand_rupiah(self):
        user = self.account()
        order = self.order(user)
        self.assertEqual(order["amount"], api.PREMIUM_PRICE_IDR)
        self.assertEqual(api.PREMIUM_PRICE_IDR, 5_000)
        self.assertEqual(order["currency"], "IDR")
        self.assertEqual(order["price_label"], "Rp5.000")
        self.assertEqual(order["status"], "created")
        self.assertFalse(order["paid"])
        self.assertIsNone(order["paid_at"])

    def test_customer_with_an_open_order_reuses_it_instead_of_stacking_orders(self):
        user = self.account()
        first = self.order(user)
        second = self.order(user)
        self.assertEqual(first["order_id"], second["order_id"])
        with self.store.connection() as db:
            count = db.execute("SELECT COUNT(*) n FROM payment_orders").fetchone()["n"]
        self.assertEqual(count, 1)

    def test_guest_demo_session_cannot_buy_premium(self):
        guest = self.store.authenticate(self.store.start_anonymous_demo("198.51.100.31")["session_token"])
        with self.assertRaises(api.ApiError) as caught:
            self.order(guest)
        self.assertEqual(caught.exception.code, "ACCOUNT_REQUIRED")
        self.assertEqual(caught.exception.status, 403)

    # ------------------------------------------------- fail closed on config
    def test_payments_are_refused_when_no_provider_is_configured(self):
        with patch.dict(os.environ, {"EQ_PAYMENT_PROVIDER": "", "EQ_PAYMENT_WEBHOOK_SECRET": ""}):
            self.assertIsNone(payments.provider_from_env())
            user = self.account()
            with self.assertRaises(api.ApiError) as caught:
                self.order(user)
            self.assertEqual(caught.exception.code, "PAYMENTS_NOT_CONFIGURED")
            self.assertEqual(caught.exception.status, 503)

    def test_sandbox_provider_requires_an_explicit_development_opt_in(self):
        with patch.dict(os.environ, {"EQ_ALLOW_INSECURE_DEV": "0", "EQ_PAYMENT_PROVIDER": "sandbox"}):
            provider = payments.provider_from_env()
            self.assertIsNotNone(provider)
            self.assertFalse(provider.configured(), "the simulator must never run outside local development")

    def test_sandbox_provider_rejects_a_short_secret(self):
        with patch.dict(os.environ, {"EQ_PAYMENT_WEBHOOK_SECRET": "too-short"}):
            self.assertFalse(payments.provider_from_env().configured())

    # --------------------------------------------- signature verification
    def test_webhook_without_a_valid_signature_never_settles(self):
        user = self.account()
        order = self.order(user)
        provider = payments.provider_from_env()
        body = json.dumps(
            {"order_id": order["order_id"], "amount": 5000, "currency": "IDR", "status": "SUCCESS"},
            separators=(",", ":"),
        ).encode("utf-8")
        for headers in (
            {},
            {"X-EQ-Signature": "sha256=deadbeef", "X-EQ-Event-Id": "forged-1"},
            {"X-EQ-Signature": provider.sign(body[:10]), "X-EQ-Event-Id": "forged-2"},
            {"X-EQ-Signature": provider.sign(body).replace("sha256=", ""), "X-EQ-Event-Id": "forged-3"},
        ):
            with self.assertRaises(payments.PaymentError) as caught:
                provider.verify_and_parse(headers, body)
            self.assertEqual(caught.exception.status, 401)
            self.assertEqual(caught.exception.code, "INVALID_SIGNATURE")
        # A tampered body invalidates a signature that was valid for the original bytes.
        good = provider.sign(body)
        with self.assertRaises(payments.PaymentError):
            provider.verify_and_parse({"X-EQ-Signature": good, "X-EQ-Event-Id": "forged-4"}, body.replace(b"5000", b"1000"))
        self.assertEqual(self.order_status(order["order_id"])["status"], "created")
        self.assertEqual(self.issued_tokens(order["order_id"]), [])

    def test_forged_notification_cannot_be_settled_through_the_store(self):
        user = self.account()
        order = self.order(user)
        with self.assertRaises(payments.PaymentError):
            payments.provider_from_env().verify_and_parse(
                {"X-EQ-Signature": "sha256=" + "0" * 64, "X-EQ-Event-Id": "forged-5"},
                json.dumps({"order_id": order["order_id"], "amount": 5000, "currency": "IDR", "status": "SUCCESS"}).encode(),
            )
        self.assertEqual(self.order_status(order["order_id"])["status"], "created")
        self.assertEqual(self.issued_tokens(order["order_id"]), [])

    # --------------------------------------- only verified success pays out
    def test_pending_failed_and_unknown_states_issue_no_token(self):
        user = self.account()
        order = self.order(user)
        reference = order["order_id"]
        cases = [
            ("PENDING", payments.STATUS_PENDING),
            ("FAILED", payments.STATUS_FAILED),
            ("GATEWAY_SAYS_MAYBE", payments.STATUS_UNVERIFIED),
        ]
        for status, expected_state in cases:
            event_id = "evt-" + status.lower()
            outcome = self.settle(reference, status=status, event_id=event_id)
            self.assertEqual(outcome["state"], expected_state, f"{status} mapped to the wrong internal state")
            self.assertFalse(outcome["issued"], f"{status} must never issue a token")
            self.assertEqual(self.issued_tokens(reference), [], f"{status} created a token")
        self.assertNotEqual(self.order_status(reference)["status"], "paid")
        self.assertEqual(self.store.account_status(user)["access_level"], "demo")
        # A buyer who retries and pays inside the window still gets a token automatically.
        success = self.settle(reference, event_id="evt-success-after-failure")
        self.assertTrue(success["issued"])
        self.assertEqual(len(self.issued_tokens(reference)), 1)

    def test_provider_expiry_then_late_success_is_reconciled_by_hand(self):
        user = self.account()
        order = self.order(user)
        expired = self.settle(order["order_id"], status="EXPIRED", event_id="evt-provider-expiry")
        self.assertEqual(expired["state"], payments.STATUS_EXPIRED)
        self.assertFalse(expired["issued"])
        late = self.settle(order["order_id"], event_id="evt-late-success")
        self.assertFalse(late["issued"])
        self.assertTrue(late["review"])
        self.assertEqual(late["reason"], "late_payment")
        self.assertEqual(self.issued_tokens(order["order_id"]), [])
        self.assertEqual(self.store.account_status(user)["access_level"], "demo")

    def test_amount_mismatch_goes_to_manual_review_without_a_token(self):
        user = self.account()
        order = self.order(user)
        outcome = self.settle(order["order_id"], amount=1000, event_id="evt-underpaid")
        self.assertFalse(outcome["issued"])
        self.assertTrue(outcome["review"])
        self.assertEqual(outcome["reason"], "amount_mismatch")
        self.assertEqual(self.order_status(order["order_id"])["status"], "review")
        self.assertEqual(self.issued_tokens(order["order_id"]), [])

    def test_unknown_order_reference_is_recorded_but_grants_nothing(self):
        outcome = self.settle("EQO-DOESNOTEXIST", event_id="evt-orphan")
        self.assertFalse(outcome["matched"])
        self.assertFalse(outcome["issued"])
        with self.store.connection() as db:
            event = db.execute("SELECT order_id,state FROM payment_events WHERE event_key='sandbox:evt-orphan'").fetchone()
        self.assertIsNone(event["order_id"])
        self.assertEqual(event["state"], payments.STATUS_PAID)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) n FROM premium_tokens").fetchone()["n"], 0)

    def test_payment_arriving_after_the_order_window_needs_a_human(self):
        user = self.account()
        order = self.order(user)
        with patch.object(api, "PAYMENT_ORDER_TTL_SECONDS", 0):
            with self.store.connection() as db:
                self.store._expire_tokens(db)
            self.assertEqual(self.order_status(order["order_id"])["status"], "expired")
            outcome = self.settle(order["order_id"], event_id="evt-late")
        self.assertFalse(outcome["issued"])
        self.assertTrue(outcome["review"])
        self.assertEqual(outcome["reason"], "late_payment")
        self.assertEqual(self.order_status(order["order_id"])["status"], "review")
        self.assertEqual(self.issued_tokens(order["order_id"]), [])

    # -------------------------------------- token issuance and redemption
    def test_verified_payment_issues_exactly_one_token_automatically(self):
        user = self.account()
        api.create_admin("admin@example.test", "admin-password-123", self.store)
        order = self.order(user)
        outcome = self.settle(order["order_id"], event_id="evt-paid-1")
        self.assertTrue(outcome["issued"])
        tokens = self.issued_tokens(order["order_id"])
        self.assertEqual(len(tokens), 1, "a verified payment issues one token and no administrator approval is involved")
        self.assertEqual(tokens[0]["status"], "available")
        self.assertIsNone(tokens[0]["claimed_by_user_id"], "the buyer still has to activate the token")
        status = self.order_status(order["order_id"])
        self.assertEqual(status["status"], "paid")
        self.assertEqual(status["token_id"], tokens[0]["token_id"])
        self.assertIsNotNone(status["paid_at"])
        # The entitlement shows up in the operator summary as a paid order, not as manual work.
        admin_row = self.store.authenticate(self.store.login("admin@example.test", "admin-password-123", "198.51.100.60")["session_token"])
        self.assertEqual(self.store.admin_summary(admin_row)["orders_paid"], 1)
        self.assertEqual(self.store.admin_summary(admin_row)["revenue_idr"], 5000)
        self.assertEqual(self.store.admin_summary(admin_row)["orders_review"], 0)
        self.assertEqual(self.store.admin_summary(admin_row)["payments_unsettled"], 0)
        self.assertEqual(self.store.admin_summary(admin_row)["available"], 1)

    def test_redelivered_or_repeated_success_notifications_never_stack_tokens(self):
        user = self.account()
        order = self.order(user)
        first = self.settle(order["order_id"], event_id="evt-dupe")
        self.assertTrue(first["issued"])
        again = self.settle(order["order_id"], event_id="evt-dupe")
        self.assertTrue(again["duplicate"])
        self.assertFalse(again["issued"])
        second_event = self.settle(order["order_id"], event_id="evt-second-success")
        self.assertFalse(second_event["issued"])
        self.assertEqual(second_event["reason"], "already_paid")
        self.assertEqual(len(self.issued_tokens(order["order_id"])), 1)

    def test_concurrent_duplicate_successes_still_issue_only_one_token(self):
        user = self.account()
        order = self.order(user)
        barrier = threading.Barrier(2)
        outcomes = []

        def worker(event_id):
            provider, note = self.notification(order["order_id"], event_id=event_id)
            barrier.wait()
            outcomes.append(self.store.settle_payment(provider.name, note))

        threads = [threading.Thread(target=worker, args=(f"evt-race-{index}",)) for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(self.issued_tokens(order["order_id"])), 1, "the unique order->token index must hold under concurrency")
        self.assertEqual(sum(1 for outcome in outcomes if outcome["issued"]), 1)

    def test_buyer_receives_a_token_that_the_backend_validates(self):
        user = self.account()
        order = self.order(user)
        # Before payment the buyer gets nothing: no token, and the claim is refused.
        with self.assertRaises(api.ApiError) as too_early:
            self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")
        self.assertEqual(too_early.exception.code, "ORDER_NOT_PAID")
        self.assertEqual(too_early.exception.status, 409)
        self.assertFalse(self.store.account_status(user)["premium"])
        self.assertTrue(self.settle(order["order_id"], event_id="evt-happy")["issued"])
        claim = self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")
        self.assertTrue(claim["token"].startswith("EQP-"))
        self.assertEqual(len(claim["token"]), 52)
        activated = self.store.activate_token(user, claim["token"], "198.51.100.10")
        self.assertTrue(activated["activated"])
        status = self.store.account_status(user)
        self.assertTrue(status["premium"])
        self.assertEqual(status["access_level"], "premium")
        self.assertIsNone(status["premium_expires_at"], "Premium is permanent: no expiry is stored")

    def test_plaintext_token_is_never_written_to_disk(self):
        user = self.account()
        order = self.order(user)
        self.settle(order["order_id"], event_id="evt-secret")
        code = self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")["token"]
        raw = self.db.read_bytes()
        self.assertNotIn(code.encode("ascii"), raw, "the plaintext token must never be stored")
        with self.store.connection() as db:
            stored = db.execute("SELECT token_hash FROM premium_tokens WHERE issued_for_order_id=?", (order["order_id"],)).fetchone()["token_hash"]
        self.assertEqual(stored, self.store.token_hash(code))
        self.assertNotEqual(stored, code)

    def test_redelivery_rotates_the_code_and_the_old_one_stops_working(self):
        user = self.account()
        order = self.order(user)
        self.settle(order["order_id"], event_id="evt-rotate")
        first = self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")["token"]
        second = self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")["token"]
        self.assertNotEqual(first, second, "each delivery hands out a fresh code")
        with self.assertRaises(api.ApiError) as stale:
            self.store.activate_token(user, first, "198.51.100.10")
        self.assertIn(stale.exception.code, ("INVALID_TOKEN", "TOKEN_ALREADY_USED"))
        activated = self.store.activate_token(user, second, "198.51.100.10")
        self.assertTrue(activated["activated"])
        with self.assertRaises(api.ApiError) as finished:
            self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")
        self.assertEqual(finished.exception.code, "TOKEN_ALREADY_ACTIVATED")

    def test_token_cannot_be_redeemed_twice_or_by_another_account(self):
        owner = self.account("owner@example.test", ip="198.51.100.10")
        other = self.account("other@example.test", ip="198.51.100.11")
        order = self.order(owner)
        self.settle(order["order_id"], event_id="evt-owner")
        code = self.store.claim_payment_token(owner, order["order_id"], "198.51.100.10")["token"]
        self.assertTrue(self.store.activate_token(owner, code, "198.51.100.10")["activated"])
        repeat = self.store.activate_token(owner, code, "198.51.100.10")
        self.assertTrue(repeat["already_yours"], "re-activating your own token is idempotent, not a new grant")
        with self.assertRaises(api.ApiError) as stolen:
            self.store.activate_token(other, code, "198.51.100.11")
        self.assertEqual(stolen.exception.code, "TOKEN_ALREADY_USED")
        self.assertFalse(self.store.account_status(other)["premium"])

    def test_another_account_cannot_inspect_or_claim_someone_elses_order(self):
        owner = self.account("owner@example.test", ip="198.51.100.10")
        other = self.account("other@example.test", ip="198.51.100.11")
        order = self.order(owner)
        for action in (
            lambda: self.store.payment_order_status(other, order["order_id"]),
            lambda: self.store.claim_payment_token(other, order["order_id"], "198.51.100.11"),
        ):
            with self.assertRaises(api.ApiError) as caught:
                action()
            self.assertEqual(caught.exception.code, "ORDER_NOT_FOUND")
            self.assertEqual(caught.exception.status, 404)

    def test_premium_remains_after_the_session_ends_and_the_store_restarts(self):
        user = self.account(password="correct-horse-47")
        order = self.order(user)
        self.settle(order["order_id"], event_id="evt-permanent")
        code = self.store.claim_payment_token(user, order["order_id"], "198.51.100.10")["token"]
        self.store.activate_token(user, code, "198.51.100.10")
        reopened = api.Store(self.db, PEPPER)
        login = reopened.login("buyer@example.test", "correct-horse-47", "198.51.100.99")
        status = reopened.account_status(reopened.authenticate(login["session_token"]))
        self.assertTrue(status["premium"], "Premium follows the account, not the device or the order")
        self.assertEqual(status["used_question_ids"], [], "activating Premium does not wipe demo history")

    def test_activation_keeps_the_legacy_token_path_working(self):
        user = self.account()
        api.create_admin("admin@example.test", "admin-password-123", self.store)
        admin_row = self.store.authenticate(self.store.login("admin@example.test", "admin-password-123", "198.51.100.61")["session_token"])
        legacy = self.store.create_tokens(admin_row, 1, None)[0]["code"]
        self.assertTrue(self.store.activate_token(user, legacy, "198.51.100.10")["activated"])
        self.assertTrue(self.store.account_status(user)["premium"])

    # ----------------------------------------------------- DANA adapter
    @unittest.skipUnless(HAVE_CRYPTOGRAPHY, "cryptography is not installed")
    def test_dana_notifications_are_verified_against_danas_public_key(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key_file = Path(self.tmp.name) / "dana_public.pem"
        key_file.write_bytes(pem)
        user = self.account()
        order = self.order(user)
        body = json.dumps({
            "originalPartnerReferenceNo": order["order_id"],
            "latestTransactionStatus": "SUCCESS",
            "amount": {"value": "5000.00", "currency": "IDR"},
        }, separators=(",", ":")).encode("utf-8")
        timestamp = "2026-10-15T10:00:00+07:00"
        digest = hashlib.sha256(body).hexdigest().lower()
        string_to_sign = f"POST:{DANA_PATH}:{digest}:{timestamp}".encode("utf-8")
        signature = private_key.sign(string_to_sign, padding.PKCS1v15(), hashes.SHA256())

        with patch.dict(os.environ, {
            "EQ_PAYMENT_PROVIDER": "dana",
            "EQ_DANA_PUBLIC_KEY_PATH": str(key_file),
            "EQ_DANA_NOTIFY_PATH": DANA_PATH,
        }):
            provider = payments.provider_from_env()
            self.assertIsInstance(provider, payments.DanaProvider)
            self.assertTrue(provider.configured())
            note = provider.verify_and_parse(
                {"X-SIGNATURE": base64.b64encode(signature).decode(), "X-TIMESTAMP": timestamp}, body
            )
            self.assertEqual(note.order_reference, order["order_id"])
            self.assertEqual(note.amount, 5000, "a decimal string amount is normalised to whole rupiah")
            self.assertEqual(note.state, payments.STATUS_PAID)
            self.assertTrue(self.store.settle_payment(provider.name, note)["issued"])
            # A signature from a different key must be refused outright.
            attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            forged = attacker.sign(string_to_sign, padding.PKCS1v15(), hashes.SHA256())
            with self.assertRaises(payments.PaymentError) as caught:
                provider.verify_and_parse(
                    {"X-SIGNATURE": base64.b64encode(forged).decode(), "X-TIMESTAMP": timestamp}, body
                )
            self.assertEqual(caught.exception.code, "INVALID_SIGNATURE")
            # An authentic notification with a status we do not positively recognise pays nothing.
            odd_body = json.dumps({
                "originalPartnerReferenceNo": order["order_id"],
                "latestTransactionStatus": "PENDING_REVIEW",
                "amount": {"value": "5000.00", "currency": "IDR"},
            }, separators=(",", ":")).encode("utf-8")
            odd_digest = hashlib.sha256(odd_body).hexdigest().lower()
            odd_signature = private_key.sign(
                f"POST:{DANA_PATH}:{odd_digest}:{timestamp}".encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
            )
            odd_note = provider.verify_and_parse(
                {"X-SIGNATURE": base64.b64encode(odd_signature).decode(), "X-TIMESTAMP": timestamp}, odd_body
            )
            self.assertEqual(odd_note.state, payments.STATUS_UNVERIFIED)
            self.assertFalse(self.store.settle_payment(provider.name, odd_note)["issued"])

    def test_dana_adapter_refuses_to_invent_a_checkout_without_merchant_credentials(self):
        user = self.account()
        with patch.dict(os.environ, {
            "EQ_PAYMENT_PROVIDER": "dana",
            "EQ_DANA_PUBLIC_KEY_PATH": "",
            "EQ_DANA_PUBLIC_KEY": "",
            "EQ_DANA_PARTNER_ID": "",
        }):
            provider = payments.provider_from_env()
            self.assertFalse(provider.configured(), "an unconfigured DANA adapter must not look ready")
            with self.assertRaises(api.ApiError) as caught:
                self.order(user)
            self.assertEqual(caught.exception.code, "PAYMENTS_NOT_CONFIGURED")
        with patch.dict(os.environ, {
            "EQ_PAYMENT_PROVIDER": "dana",
            "EQ_DANA_PARTNER_ID": "partner-id-from-merchant-portal",
            "EQ_DANA_PRIVATE_KEY_PATH": str(Path(self.tmp.name) / "missing-private-key.pem"),
            "EQ_DANA_CLIENT_SECRET": "client-secret-from-merchant-portal",
            # A placeholder public key only satisfies the "can we verify notifications" check;
            # it is never used to sign anything and no real key material lives in this repo.
            "EQ_DANA_PUBLIC_KEY_PATH": "",
            "EQ_DANA_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----placeholder-not-a-real-dana-key-----END PUBLIC KEY-----",
        }):
            # Merchant credentials are named, but no endpoint or URL is hard-coded anywhere in
            # this repository, so checkout creation is refused until a reviewed integration
            # provides them (and the private key file actually exists).
            adapter = payments.DanaProvider()
            with self.assertRaises(payments.PaymentError) as refused:
                adapter.create_checkout(order_id="EQO-NOT-A-REAL-ORDER", amount=5000, currency="IDR")
            self.assertEqual(refused.exception.code, "PROVIDER_NOT_READY")
            self.assertEqual(refused.exception.status, 503)
            self.assertIn("belum aktif", refused.exception.message)
            with self.assertRaises(api.ApiError) as caught:
                self.order(user)
            self.assertEqual(caught.exception.code, "PROVIDER_NOT_READY")
            self.assertEqual(caught.exception.status, 503)

    def test_unrecognised_provider_name_disables_payments_entirely(self):
        with patch.dict(os.environ, {"EQ_PAYMENT_PROVIDER": "not-a-real-provider"}):
            self.assertIsNone(payments.provider_from_env())


class PaymentHttpTests(unittest.TestCase):
    """The webhook and order endpoints as a client (and a provider) actually sees them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = api.Store(Path(self.tmp.name) / "http.sqlite3", PEPPER)
        self.env = patch.dict(os.environ, sandbox_env(), clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.server.store = self.store
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, data=None, headers=None, timeout=10):
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def json_call(self, method, path, payload, session=None):
        headers = {"Content-Type": "application/json"}
        if session:
            headers["Authorization"] = "Bearer " + session
        return self.call(method, path, json.dumps(payload).encode("utf-8"), headers)

    def register(self, email="http-buyer@example.test"):
        status, payload = self.json_call("POST", "/api/register", {"email": email, "password": "correct-horse-47"})
        self.assertEqual(status, 201, payload)
        return payload["session_token"]

    def signed_notification(self, order_id, status_word="SUCCESS", event_id="http-evt-1", amount=5000):
        body = json.dumps(
            {"order_id": order_id, "amount": amount, "currency": "IDR", "status": status_word},
            separators=(",", ":"),
        ).encode("utf-8")
        provider = payments.provider_from_env()
        return body, {"Content-Type": "application/json", "X-EQ-Signature": provider.sign(body), "X-EQ-Event-Id": event_id}

    def test_provider_webhook_settles_a_paid_order_and_the_buyer_activates_the_token(self):
        session = self.register()
        status, order = self.json_call("POST", "/api/payments/order", {}, session)
        self.assertEqual(status, 201, order)
        body, headers = self.signed_notification(order["order_id"])
        status, result = self.call("POST", "/api/payments/webhook", body, headers)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["issued"])
        # The provider retries the same event: accepted, nothing new issued.
        status, repeated = self.call("POST", "/api/payments/webhook", body, headers)
        self.assertEqual(status, 200)
        self.assertTrue(repeated["duplicate"])
        self.assertFalse(repeated["issued"])
        status, fetched = self.call("GET", f"/api/payments/order?order_id={order['order_id']}", headers={"Authorization": "Bearer " + session})
        self.assertEqual(status, 200, fetched)
        self.assertTrue(fetched["paid"])
        self.assertTrue(fetched["token_pending"])
        status, claim = self.json_call("POST", "/api/payments/claim", {"order_id": order["order_id"]}, session)
        self.assertEqual(status, 200, claim)
        status, activated = self.json_call("POST", "/api/premium/activate", {"token": claim["token"]}, session)
        self.assertEqual(status, 200, activated)
        self.assertTrue(activated["activated"])
        status, me = self.call("GET", "/api/me", headers={"Authorization": "Bearer " + session})
        self.assertTrue(me["account"]["premium"])
        self.assertEqual(me["account"]["demo_limit"], 5)

    def test_unsigned_webhook_is_rejected_over_http(self):
        session = self.register("http-unsigned@example.test")
        _, order = self.json_call("POST", "/api/payments/order", {}, session)
        body = json.dumps({"order_id": order["order_id"], "amount": 5000, "currency": "IDR", "status": "SUCCESS"}).encode()
        status, payload = self.call("POST", "/api/payments/webhook", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 401, payload)
        self.assertEqual(payload["code"], "INVALID_SIGNATURE")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) n FROM premium_tokens").fetchone()["n"], 0)

    def test_webhook_is_unavailable_when_no_provider_is_configured(self):
        body = json.dumps({"order_id": "EQO-X", "status": "SUCCESS"}).encode()
        with patch.dict(os.environ, {"EQ_PAYMENT_PROVIDER": ""}):
            status, payload = self.call("POST", "/api/payments/webhook", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 503, payload)
        self.assertEqual(payload["code"], "PAYMENTS_NOT_CONFIGURED")

    def test_local_simulator_only_works_for_the_orders_owner_and_only_in_dev(self):
        session = self.register("http-simulator@example.test")
        _, order = self.json_call("POST", "/api/payments/order", {}, session)
        status, result = self.json_call("POST", "/api/payments/sandbox/settle", {"order_id": order["order_id"]}, session)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["issued"], "the simulator replays the real verification path")
        other = self.register("http-other@example.test")
        _, other_order = self.json_call("POST", "/api/payments/order", {}, other)
        status, denied = self.json_call("POST", "/api/payments/sandbox/settle", {"order_id": other_order["order_id"]}, session)
        self.assertEqual(status, 404, denied)
        with patch.dict(os.environ, {"EQ_PAYMENT_PROVIDER": "dana"}):
            status, hidden = self.json_call("POST", "/api/payments/sandbox/settle", {"order_id": order["order_id"]}, session)
        self.assertEqual(status, 404, hidden)

    def test_config_endpoint_publishes_the_price_without_leaking_configuration(self):
        status, payload = self.call("GET", "/api/payments/config", None, {"Content-Type": "application/json"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["price_idr"], 5000)
        self.assertEqual(payload["currency"], "IDR")
        self.assertEqual(payload["provider"], "sandbox")
        self.assertTrue(payload["available"])
        self.assertNotIn(SANDBOX_SECRET, json.dumps(payload))

    def test_admin_can_review_orders_but_cannot_see_secrets(self):
        session = self.register("http-admin-buyer@example.test")
        _, order = self.json_call("POST", "/api/payments/order", {}, session)
        body, headers = self.signed_notification(order["order_id"], event_id="http-admin-evt")
        self.call("POST", "/api/payments/webhook", body, headers)
        api.create_admin("http-admin@example.test", "admin-password-123", self.store)
        login = self.store.login("http-admin@example.test", "admin-password-123", "198.51.100.70")
        status, payload = self.call("GET", "/api/admin/payments", None, {"Authorization": "Bearer " + login["session_token"]})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(payload["orders"]), 1)
        self.assertEqual(payload["orders"][0]["status"], "paid")
        self.assertEqual(payload["orders"][0]["user_email"], "http-admin-buyer@example.test")
        self.assertNotIn(SANDBOX_SECRET, json.dumps(payload))
        self.assertNotIn("token_hash", json.dumps(payload))

    def test_unauthenticated_caller_cannot_create_orders(self):
        status, payload = self.json_call("POST", "/api/payments/order", {})
        self.assertEqual(status, 401, payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
