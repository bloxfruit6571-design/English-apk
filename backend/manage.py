#!/usr/bin/env python3
"""One-time operator commands for the English Quest API."""
import argparse
import getpass
import os
import sys
from pathlib import Path

from server import Store, create_admin


def main() -> int:
    parser = argparse.ArgumentParser(description="English Quest server administration")
    parser.add_argument("command", choices=["create-admin"])
    parser.add_argument("--email", required=True)
    parser.add_argument("--db", default=os.getenv("EQ_DB_PATH", str(Path(__file__).parent / "data" / "english-quest.sqlite3")))
    args = parser.parse_args()
    pepper = os.getenv("EQ_TOKEN_PEPPER", "")
    if len(pepper.encode("utf-8")) < 32:
        print("Set EQ_TOKEN_PEPPER to a strong random secret (at least 32 bytes) before running this command.", file=sys.stderr)
        return 2
    password = getpass.getpass("Admin password (min 12 characters): ")
    confirm = getpass.getpass("Confirm admin password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        user_id = create_admin(args.email, password, Store(args.db, pepper))
    except Exception as exc:
        print(f"Could not create administrator: {exc}", file=sys.stderr)
        return 2
    print(f"Administrator created: {args.email.strip().lower()} (id {user_id})")
    print("Store the credentials in your organization's password manager. Do not place them in the APK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
