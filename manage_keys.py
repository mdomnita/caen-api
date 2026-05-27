"""
CLI for managing API keys stored in caen.db.

Commands:
    list              – show all keys and their status
    create <name>     – generate a new key and store its hash
    revoke <name>     – deactivate a key (does not delete it)

Usage:
    python manage_keys.py list
    python manage_keys.py create "My App"
    python manage_keys.py revoke "My App"

The DB path is read from the DB_PATH env var (default: caen.db).
Run python init_db.py first to ensure the api_keys table exists.
"""
import hashlib
import os
import secrets
import sqlite3
import sys

DB_PATH = os.getenv("DB_PATH", "caen.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def cmd_list() -> None:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, created_at, is_active FROM api_keys ORDER BY created_at"
        ).fetchall()
    if not rows:
        print("No API keys found.")
        return
    print(f"{'NAME':<30}  {'CREATED':<20}  STATUS")
    print("-" * 62)
    for r in rows:
        status = "active" if r["is_active"] else "revoked"
        print(f"{r['name']:<30}  {r['created_at']:<20}  {status}")


def cmd_create(name: str) -> None:
    key = "caen_sk_" + secrets.token_hex(24)
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO api_keys (key_hash, name) VALUES (?, ?)", (_hash(key), name)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            print("Error: hash collision — this is astronomically unlikely; retry.")
            sys.exit(1)
    print(f"Key created for '{name}':\n\n  {key}\n")
    print("Save this now — the plaintext is not stored and cannot be recovered.")


def cmd_revoke(name: str) -> None:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE name = ? AND is_active = 1", (name,)
        )
        conn.commit()
    if cur.rowcount == 0:
        print(f"No active key found for '{name}'.")
    else:
        print(f"Key '{name}' revoked.")


_COMMANDS = {"list": cmd_list, "create": cmd_create, "revoke": cmd_revoke}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(__doc__)
        sys.exit(1)
    cmd = args[0]
    if cmd in ("create", "revoke"):
        if len(args) < 2:
            print(f"Usage: python manage_keys.py {cmd} <name>")
            sys.exit(1)
        _COMMANDS[cmd](args[1])
    else:
        _COMMANDS[cmd]()
