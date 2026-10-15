"""Provider-agnostic payment layer for English Quest Premium.

Design rules (all of them are enforced by the tests in tests/test_payments.py):

1. Only a *cryptographically verified* provider notification can settle an order.
2. No adapter ever invents an endpoint, credential or URL. Every adapter reads its
   configuration from the environment and reports ``configured() == False`` when that
   configuration is incomplete, so an unconfigured deployment cannot take payments at all.
3. Nothing here grants Premium. Settling an order only marks the order paid; the entitlement
   is issued by ``Store.settle_payment`` inside one transaction, at most once per order.
4. Two providers ship in this repository:
   * ``sandbox`` — a local simulator for development and tests. It is only reachable when
     EQ_PAYMENT_PROVIDER=sandbox *and* EQ_ALLOW_INSECURE_DEV=1, and its notifications are
     signed with HMAC-SHA256 like a real webhook, so the code path under test is the same one
     a real provider would hit.
   * ``dana`` — the DANA Gapura (BI-SNAP) adapter. It verifies the official
     ``X-SIGNATURE``/``X-TIMESTAMP`` headers against DANA's public key. It cannot create
     checkout sessions until merchant credentials from the DANA Merchant Portal are supplied,
     and it fails closed (no settlement) for any status it does not positively recognise.

Nothing in this module prints or logs secrets.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Internal, provider-independent payment states.
STATUS_PAID = "paid"
STATUS_PENDING = "pending"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_UNVERIFIED = "unverified"

# Status words that a provider may report. Anything not listed here stays unverified and can
# never settle an order (fail closed).
_PAID_WORDS = frozenset({"success", "succeeded", "settled", "capture", "captured", "paid", "completed", "paid_offline"})
_PENDING_WORDS = frozenset({"pending", "initiated", "authorized", "authorised", "processing", "created", "waiting", "open"})
_FAILED_WORDS = frozenset({"failed", "failure", "deny", "denied", "cancel", "cancelled", "canceled", "refunded", "refund", "chargeback", "reversed", "aborted", "error"})
_EXPIRED_WORDS = frozenset({"expire", "expired", "timeout", "timed_out", "closed"})

DANA_NOTIFY_PATH = "/v1.0/debit/notify"


class PaymentError(Exception):
    """A payment failure that is safe to show to the caller."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class PaymentNotification:
    """A provider notification that has already passed signature verification."""

    provider: str
    event_id: str
    order_reference: str
    amount: int | None
    currency: str
    state: str
    raw_status: str
    payload: dict[str, Any] = field(default_factory=dict)


def classify_status(raw_status: str) -> str:
    """Map a provider status word onto an internal state, defaulting to ``unverified``."""
    word = str(raw_status or "").strip().lower()
    if word in _PAID_WORDS:
        return STATUS_PAID
    if word in _PENDING_WORDS:
        return STATUS_PENDING
    if word in _FAILED_WORDS:
        return STATUS_FAILED
    if word in _EXPIRED_WORDS:
        return STATUS_EXPIRED
    return STATUS_UNVERIFIED


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _as_amount(value: Any) -> int | None:
    """Normalise an amount to whole rupiah. Returns None when it is not a usable number."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0:
        return None
    return int(round(number))


class PaymentProvider:
    """Interface every provider adapter implements."""

    name = ""

    def configured(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def create_checkout(self, *, order_id: str, amount: int, currency: str) -> dict[str, Any]:
        raise NotImplementedError

    def verify_and_parse(self, headers: Any, raw_body: bytes) -> PaymentNotification:
        raise NotImplementedError

    # -- helpers shared by adapters -------------------------------------------------
    @staticmethod
    def _header(headers: Any, name: str) -> str:
        try:
            return str(headers.get(name, "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _parse_json(raw_body: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PaymentError(400, "INVALID_JSON", "Notifikasi pembayaran tidak dapat dibaca.")
        if not isinstance(parsed, dict):
            raise PaymentError(400, "INVALID_JSON", "Notifikasi pembayaran tidak dapat dibaca.")
        return parsed


class SandboxProvider(PaymentProvider):
    """Local simulator. Development and automated tests only.

    Notifications are signed with HMAC-SHA256 over the raw body:
        X-EQ-Signature: sha256=<hex hmac(secret, raw_body)>
        X-EQ-Event-Id:  <unique event id>
    The secret comes from EQ_PAYMENT_WEBHOOK_SECRET (at least 32 bytes). Because the simulator
    goes through exactly the same verification code as a real provider, the tested path is the
    production path.
    """

    name = "sandbox"
    MIN_SECRET_BYTES = 32

    def __init__(self) -> None:
        self.secret = _env("EQ_PAYMENT_WEBHOOK_SECRET").encode("utf-8", "replace")
        self.dev_enabled = _env("EQ_ALLOW_INSECURE_DEV") == "1"

    def configured(self) -> bool:
        return self.dev_enabled and len(self.secret) >= self.MIN_SECRET_BYTES

    def create_checkout(self, *, order_id: str, amount: int, currency: str) -> dict[str, Any]:
        if not self.configured():
            raise PaymentError(503, "PAYMENTS_NOT_CONFIGURED", "Simulator pembayaran lokal belum diaktifkan.")
        # No hosted page exists for the simulator, and this deliberately does not pretend to be
        # one: it documents the exact request a tester must send to finish a local payment.
        return {
            "checkout_url": "",
            "sandbox": True,
            "simulate": {
                "method": "POST",
                "path": "/api/payments/sandbox/settle",
                "body": {"order_id": order_id, "amount": amount, "currency": currency, "status": "SUCCESS"},
                "note": "Endpoint simulator lokal; hanya tersedia saat EQ_PAYMENT_PROVIDER=sandbox dan EQ_ALLOW_INSECURE_DEV=1.",
            },
        }

    def sign(self, raw_body: bytes) -> str:
        """Produce the signature a simulator notification must carry (used by tests/dev tooling)."""
        return "sha256=" + hmac.new(self.secret, raw_body, hashlib.sha256).hexdigest()

    def verify_and_parse(self, headers: Any, raw_body: bytes) -> PaymentNotification:
        if not self.configured():
            raise PaymentError(503, "PAYMENTS_NOT_CONFIGURED", "Simulator pembayaran lokal belum diaktifkan.")
        signature = self._header(headers, "X-EQ-Signature")
        if not signature.startswith("sha256="):
            raise PaymentError(401, "INVALID_SIGNATURE", "Tanda tangan notifikasi tidak valid.")
        expected = self.sign(raw_body)
        if not hmac.compare_digest(signature, expected):
            raise PaymentError(401, "INVALID_SIGNATURE", "Tanda tangan notifikasi tidak valid.")
        payload = self._parse_json(raw_body)
        event_id = self._header(headers, "X-EQ-Event-Id") or str(payload.get("event_id", "") or "")
        if not event_id:
            raise PaymentError(400, "INVALID_EVENT", "Notifikasi tanpa ID peristiwa.")
        order_reference = str(payload.get("order_id", "") or "")
        if not order_reference:
            raise PaymentError(400, "INVALID_EVENT", "Notifikasi tanpa referensi pesanan.")
        raw_status = str(payload.get("status", "") or "")
        return PaymentNotification(
            provider=self.name,
            event_id=event_id[:120],
            order_reference=order_reference[:120],
            amount=_as_amount(payload.get("amount")),
            currency=str(payload.get("currency", "IDR") or "IDR").upper(),
            state=classify_status(raw_status),
            raw_status=raw_status[:40],
            payload={"link_id": str(payload.get("link_id", "") or "")[:120]},
        )


class DanaProvider(PaymentProvider):
    """DANA Gapura (BI-SNAP) adapter.

    Verified notification contract (BI-SNAP):
        X-SIGNATURE  = base64( SHA256withRSA( DANA private key, stringToSign ) )
        X-TIMESTAMP  = ISO-8601 timestamp also used inside stringToSign
        stringToSign = HTTPMethod + ":" + EndpointUrl + ":" + lowercase(hex(SHA256(body))) + ":" + timestamp
    Verification uses DANA's *public* key (EQ_DANA_PUBLIC_KEY_PATH / EQ_DANA_PUBLIC_KEY).

    Two body canonicalisations are accepted because the SNAP specification says
    ``minify(RequestBody)`` while some integrations hash the raw bytes: the minified body is
    tried first, then the raw body. This only affects which byte string is hashed — an attacker
    still needs DANA's private key to produce a signature that verifies, so security is not
    weakened either way.

    Checkout creation is intentionally not implemented: it requires merchant credentials
    (partner id, private key, client secret) issued by the DANA Merchant Portal, which this
    repository must never contain. Until those exist, ``create_checkout`` refuses.
    """

    name = "dana"

    def __init__(self) -> None:
        self.public_key_pem = _env("EQ_DANA_PUBLIC_KEY")
        self.public_key_path = _env("EQ_DANA_PUBLIC_KEY_PATH")
        self.notify_path = _env("EQ_DANA_NOTIFY_PATH", DANA_NOTIFY_PATH)
        self.partner_id = _env("EQ_DANA_PARTNER_ID")

    def _public_key(self) -> bytes:
        if self.public_key_path:
            path = Path(self.public_key_path)
            if not path.is_file():
                return b""
            try:
                return path.read_bytes()
            except OSError:
                return b""
        return self.public_key_pem.encode("utf-8") if self.public_key_pem else b""

    def configured(self) -> bool:
        return bool(self._public_key())

    def create_checkout(self, *, order_id: str, amount: int, currency: str) -> dict[str, Any]:
        missing = [key for key, value in (
            ("EQ_DANA_PARTNER_ID", self.partner_id),
            ("EQ_DANA_PRIVATE_KEY_PATH", _env("EQ_DANA_PRIVATE_KEY_PATH")),
            ("EQ_DANA_CLIENT_SECRET", _env("EQ_DANA_CLIENT_SECRET")),
        ) if not value]
        raise PaymentError(
            503,
            "PROVIDER_NOT_READY",
            "Checkout DANA belum aktif. Kredensial merchant DANA belum tersedia di server"
            + (f" (kurang: {', '.join(missing)})." if missing else "."),
        )

    @staticmethod
    def _string_to_sign(method: str, endpoint: str, body_digest_hex: str, timestamp: str) -> str:
        return f"{method}:{endpoint}:{body_digest_hex}:{timestamp}"

    def _verify_signature(self, signature: str, method: str, endpoint: str, raw_body: bytes, timestamp: str) -> bool:
        pem = self._public_key()
        if not pem or not signature or not timestamp:
            return False
        try:
            signature_bytes = base64.b64decode(signature, validate=True)
        except Exception:
            return False
        try:
            from cryptography.exceptions import InvalidSignature  # type: ignore
            from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
            from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
        except Exception:
            # The cryptography package is optional. Without it DANA notifications cannot be
            # verified, and an unverifiable notification must never be trusted.
            return False
        try:
            public_key = serialization.load_pem_public_key(pem)
        except Exception:
            return False
        variants = []
        try:
            minified = json.dumps(json.loads(raw_body.decode("utf-8")), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            variants.append(minified)
        except Exception:
            pass
        variants.append(raw_body)
        for candidate in variants:
            digest = hashlib.sha256(candidate).hexdigest().lower()
            message = self._string_to_sign(method, endpoint, digest, timestamp).encode("utf-8")
            try:
                public_key.verify(signature_bytes, message, padding.PKCS1v15(), hashes.SHA256())
                return True
            except InvalidSignature:
                continue
            except Exception:
                continue
        return False

    def verify_and_parse(self, headers: Any, raw_body: bytes) -> PaymentNotification:
        if not self.configured():
            raise PaymentError(503, "PAYMENTS_NOT_CONFIGURED", "Verifikasi DANA belum dikonfigurasi (public key belum diset).")
        signature = self._header(headers, "X-SIGNATURE")
        timestamp = self._header(headers, "X-TIMESTAMP")
        if not self._verify_signature(signature, "POST", self.notify_path, raw_body, timestamp):
            raise PaymentError(401, "INVALID_SIGNATURE", "Tanda tangan notifikasi DANA tidak valid.")
        payload = self._parse_json(raw_body)
        order_reference = str(
            payload.get("originalPartnerReferenceNo")
            or payload.get("originalReferenceNo")
            or payload.get("partnerReferenceNo")
            or ""
        )
        if not order_reference:
            raise PaymentError(400, "INVALID_EVENT", "Notifikasi DANA tanpa referensi pesanan.")
        amount_block = payload.get("amount") if isinstance(payload.get("amount"), dict) else {}
        raw_status = str(payload.get("latestTransactionStatus") or payload.get("transactionStatus") or "")
        # DANA does not always send a dedicated event id; derive a stable one so a re-delivered
        # notification is recognised as the same event instead of a new one.
        event_id = str(
            payload.get("eventId")
            or payload.get("originalExternalId")
            or payload.get("originalReferenceNo")
            or (hashlib.sha256(raw_body).hexdigest()[:40])
        )
        return PaymentNotification(
            provider=self.name,
            event_id=event_id[:120],
            order_reference=order_reference[:120],
            amount=_as_amount(amount_block.get("value")),
            currency=str(amount_block.get("currency", "IDR") or "IDR").upper(),
            state=classify_status(raw_status),
            raw_status=raw_status[:40],
            payload={"charge_type": str(payload.get("chargeType", "") or "")[:40]},
        )


def provider_from_env() -> PaymentProvider | None:
    """Return the configured provider, or None when payments are switched off."""
    name = _env("EQ_PAYMENT_PROVIDER").lower()
    if name == "sandbox":
        return SandboxProvider()
    if name in ("dana", "dana_gapura", "gapura"):
        return DanaProvider()
    return None


def parse_query_path(path: str) -> str:
    """Unquote a request path (used when checking the SNAP notify path)."""
    return urllib.parse.unquote(urllib.parse.urlsplit(path).path)
