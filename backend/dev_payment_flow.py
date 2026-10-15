#!/usr/bin/env python3
"""Local end-to-end exercise of the Premium payment path (no real money, no real provider).

What it does
------------
Starts an isolated API instance on 127.0.0.1 with the sandbox payment provider and a temporary
database, then walks the whole flow as two clients would:

    register -> create order (Rp5.000) -> provider notification -> order status
             -> token delivery -> activation -> Premium access -> sixth demo answer refused
             -> token reuse refused

The notification is signed with `SandboxProvider`, i.e. it goes through exactly the same
signature-verification code a real provider webhook would, so this is a rehearsal of the real
path — not a substitute for it. It does **not** talk to any bank, e-wallet, or provider endpoint,
and it never touches production data.

Usage
-----
    python3 backend/dev_payment_flow.py

Requires only the Python standard library. No secret is printed; generated tokens are masked.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SANDBOX_SECRET = secrets.token_urlsafe(36)  # dev-only, created for this run and never printed


def mask(value: str) -> str:
    text = str(value or "")
    return f"{text[:8]}…{text[-4:]}" if len(text) > 14 else "…"


def call(base: str, method: str, path: str, payload=None, session: str | None = None,
         extra_headers: dict[str, str] | None = None, raw: bytes | None = None):
    headers = {"Accept": "application/json"}
    data = raw
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if session:
        headers["Authorization"] = "Bearer " + session
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health(base: str, deadline: float = 20.0) -> None:
    end = time.time() + deadline
    while time.time() < end:
        try:
            status, payload = call(base, "GET", "/api/health")
            if status == 200 and payload.get("ok"):
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit("Server did not become healthy in time.")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        port = 8099
        env = dict(os.environ)
        env.update({
            "EQ_DB_PATH": str(Path(tmp) / "demo.sqlite3"),
            "EQ_TOKEN_PEPPER": secrets.token_urlsafe(48),
            "EQ_ALLOW_INSECURE_DEV": "1",
            "EQ_HOST": "127.0.0.1",
            "EQ_PORT": str(port),
            "EQ_PAYMENT_PROVIDER": "sandbox",
            "EQ_PAYMENT_WEBHOOK_SECRET": SANDBOX_SECRET,
            "PYTHONPATH": str(ROOT),
        })
        server = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            wait_for_health(base)
            sys.path.insert(0, str(ROOT))
            # The parent process signs the notification with the same simulator the server uses,
            # so the server verifies a genuine signature produced by provider code.
            os.environ.update({"EQ_PAYMENT_PROVIDER": "sandbox",
                               "EQ_PAYMENT_WEBHOOK_SECRET": SANDBOX_SECRET,
                               "EQ_ALLOW_INSECURE_DEV": "1"})
            from backend.payments import provider_from_env  # noqa: WPS433 (import after env is set)

            provider = provider_from_env()
            print(f"1. server siap di {base} dengan provider '{provider.name}' (simulator lokal)")

            status, config = call(base, "GET", "/api/payments/config")
            print(f"2. GET /api/payments/config -> {status} harga={config['price_idr']} {config['currency']} "
                  f"provider={config['provider']!r} tersedia={config['available']}")

            email = f"buyer-{secrets.token_hex(4)}@example.test"
            status, registered = call(base, "POST", "/api/register", {"email": email, "password": "correct-horse-47"})
            session = registered["session_token"]
            print(f"3. register -> {status} akun={email} (token sesi tidak dicetak)")

            status, account = call(base, "GET", "/api/me", session=session)
            print(f"4. GET /api/me -> {status} level={account['account']['access_level']} "
                  f"batas demo={account['account']['demo_limit']} soal")

            # The demo cap is checked before the purchase, so the quota maths is visible.
            accepted = 0
            for index in range(1, 7):
                status, consumed = call(base, "POST", "/api/demo/consume",
                                        {"question_id": f"easy-greetings-q{index}", "answer": "Good morning"},
                                        session=session)
                if status == 200 and consumed.get("consumed"):
                    accepted += 1
                else:
                    print(f"      soal ke-{index} ditolak -> {status} kode={consumed.get('code')}")
                    break
            status, account = call(base, "GET", "/api/me", session=session)
            usage = account["account"]
            print(f"5. jawaban demo diterima={accepted} dari bank {len(usage['allowed_demo_question_ids'])} ID unik; "
                  f"terpakai={usage['demo_used']}/{usage['demo_limit']} sisa={usage['demo_remaining']}")

            status, extra = call(base, "POST", "/api/demo/consume",
                                 {"question_id": "easy-alphabet-spelling-q1", "answer": "A"}, session=session)
            print(f"6. soal misi Easy kedua (sekarang materi Premium) -> {status} kode={extra.get('code')}")

            status, order = call(base, "POST", "/api/payments/order", {}, session=session)
            print(f"7. POST /api/payments/order -> {status} pesanan={order['order_id']} "
                  f"harga={order['price_label']} status={order['status']}")

            notification = json.dumps({
                "order_id": order["order_id"], "amount": order["amount"],
                "currency": order["currency"], "status": "SUCCESS",
            }, separators=(",", ":")).encode("utf-8")
            headers = {"Content-Type": "application/json", "X-EQ-Signature": provider.sign(notification),
                       "X-EQ-Event-Id": "local-demo-event-1"}
            status, settled = call(base, "POST", "/api/payments/webhook", raw=notification, extra_headers=headers)
            print(f"8. webhook bertanda tangan (SUCCESS) -> {status} token_diterbitkan={settled.get('issued')} "
                  f"alasan={settled.get('reason')}")

            status, replay = call(base, "POST", "/api/payments/webhook", raw=notification, extra_headers=headers)
            print(f"9. webhook yang sama diulang -> {status} duplikat={replay.get('duplicate')} "
                  f"token_diterbitkan={replay.get('issued')}")

            status, unsigned = call(base, "POST", "/api/payments/webhook", raw=notification,
                                    extra_headers={"Content-Type": "application/json"})
            print(f"10. webhook TANPA tanda tangan -> {status} kode={unsigned.get('code')}")

            status, pending = call(base, "POST", "/api/payments/webhook", raw=json.dumps({
                "order_id": order["order_id"], "amount": 5000, "currency": "IDR", "status": "PENDING",
            }, separators=(",", ":")).encode("utf-8"), extra_headers={
                "Content-Type": "application/json",
                "X-EQ-Signature": provider.sign(json.dumps({
                    "order_id": order["order_id"], "amount": 5000, "currency": "IDR", "status": "PENDING",
                }, separators=(",", ":")).encode("utf-8")),
                "X-EQ-Event-Id": "local-demo-event-2",
            })
            print(f"11. webhook status PENDING -> {status} token_diterbitkan={pending.get('issued')} "
                  f"(pending tidak pernah menerbitkan token)")

            status, current = call(base, "GET", f"/api/payments/order?order_id={order['order_id']}", session=session)
            print(f"12. GET /api/payments/order -> {status} status={current['status']} lunas={current['paid']} "
                  f"token_siap={current['token_pending']}")

            status, claim = call(base, "POST", "/api/payments/claim", {"order_id": order["order_id"]}, session=session)
            token = claim["token"]
            print(f"13. POST /api/payments/claim -> {status} token={mask(token)} "
                  f"(plaintext tidak pernah disimpan server)")

            status, activated = call(base, "POST", "/api/premium/activate", {"token": token}, session=session)
            print(f"14. POST /api/premium/activate -> {status} aktif={activated.get('activated')}")

            status, account = call(base, "GET", "/api/me", session=session)
            profile = account["account"]
            print(f"15. GET /api/me -> {status} level={profile['access_level']} premium={profile['premium']} "
                  f"kedaluwarsa={profile['premium_expires_at']} (akses permanen)")

            status, replay_token = call(base, "POST", "/api/premium/activate", {"token": token}, session=session)
            print(f"16. token sama dipakai ULANG oleh pemiliknya -> {status} "
                  f"idempoten={replay_token.get('already_yours')} (tidak menambah hak baru)")

            status, other = call(base, "POST", "/api/register",
                                 {"email": f"other-{secrets.token_hex(4)}@example.test", "password": "correct-horse-47"})
            status, denied = call(base, "POST", "/api/premium/activate", {"token": token},
                                  session=other["session_token"])
            print(f"17. akun lain memakai token yang sama -> {status} kode={denied.get('code')}")

            status, material = call(base, "GET", "/api/me", session=session)
            print(f"18. materi/progres tidak dihapus: level materi lama & XP tetap di klien; "
                  f"kuota demo akun tetap {material['account']['demo_limit']} soal")
            print("\nSemua langkah selesai. Ini pengujian lokal tanpa uang sungguhan dan tanpa provider resmi.")
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
