import json
import os
from datetime import timedelta

from .db import connect
from .security import digest, expires, parse, stamp, token, utcnow


def register_user(telegram_id: int, private_chat_id: int | None = None):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES(?)", (telegram_id,))
        if private_chat_id == telegram_id:
            db.execute("UPDATE users SET private_chat_id=? WHERE telegram_id=?", (private_chat_id, telegram_id))
        user = db.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        db.execute("INSERT OR IGNORE INTO subscriptions(user_id,status,ends_at) VALUES(?,?,?)",
                   (user["id"], "trial", stamp(utcnow() + timedelta(days=7))))
        return user["id"]


def make_link_code(user_id):
    code = token(18)
    with connect() as db:
        db.execute("INSERT INTO link_codes VALUES(?,?,?,NULL)", (digest(code), user_id, expires(10)))
    return code


def link_account(code, login, server, account_type, currency, trade_allowed):
    if not login or not server or not account_type or not currency or not trade_allowed:
        raise ValueError("Incomplete MT5 account proof")
    if account_type.lower() != "demo":
        raise ValueError("Only MT5 demo accounts are supported")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM link_codes WHERE code_hash=?", (digest(code),)).fetchone()
        if not row or row["used_at"] or parse(row["expires_at"]) <= utcnow():
            raise ValueError("Link code invalid, expired, or already used")
        if db.execute("SELECT 1 FROM accounts WHERE login=? AND server=?", (login, server)).fetchone():
            raise ValueError("Account already linked")
        ea_token = token()
        cur = db.execute("INSERT INTO accounts(user_id,login,server,account_type,currency,ea_token_hash) VALUES(?,?,?,?,?,?)",
                         (row["user_id"], login, server, "demo", currency, digest(ea_token)))
        account_id = cur.lastrowid
        db.execute("INSERT INTO risk_settings(account_id) VALUES(?)", (account_id,))
        db.execute("UPDATE link_codes SET used_at=? WHERE code_hash=?", (stamp(utcnow()), digest(code)))
        event(db, row["user_id"], account_id, "linked", "MT5 demo account linked")
        alert(db, row["user_id"], "تم ربط حساب MT5 التجريبي بنجاح. التداول متوقف حتى تفعّله من الإعدادات.")
        return account_id, ea_token


def event(db, user_id, account_id, kind, message):
    db.execute("INSERT INTO events(user_id,account_id,kind,message) VALUES(?,?,?,?)", (user_id, account_id, kind, message))


def alert(db, user_id, message):
    db.execute("INSERT INTO alerts(user_id,message) VALUES(?,?)", (user_id, message))


def account_for_token(raw):
    if not raw:
        return None
    with connect() as db:
        return db.execute("SELECT * FROM accounts WHERE ea_token_hash=?", (digest(raw),)).fetchone()


def subscription_ok(db, user_id):
    s = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    return bool(s and s["status"] in ("trial", "active") and
                (not s["ends_at"] or parse(s["ends_at"]) > utcnow()))


def stop_account(user_id, account_id):
    with connect() as db:
        cur = db.execute("UPDATE risk_settings SET enabled=0 WHERE account_id=? AND EXISTS (SELECT 1 FROM accounts WHERE id=? AND user_id=?)",
                         (account_id, account_id, user_id))
        if not cur.rowcount:
            raise ValueError("Account not found")
        event(db, user_id, account_id, "stopped", "New entries disabled")


def make_web_code(user_id):
    code = token(24)
    with connect() as db:
        db.execute("INSERT INTO web_codes VALUES(?,?,?,NULL)", (digest(code), user_id, expires(5)))
    return code


def redeem_web_code(code):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM web_codes WHERE code_hash=?", (digest(code),)).fetchone()
        if not row or row["used_at"] or parse(row["expires_at"]) <= utcnow():
            raise ValueError("Web login expired")
        raw = token()
        db.execute("UPDATE web_codes SET used_at=? WHERE code_hash=?", (stamp(utcnow()), digest(code)))
        db.execute("INSERT INTO web_sessions VALUES(?,?,?)", (digest(raw), row["user_id"], expires(60 * 12)))
        return raw


def session_user(raw):
    if not raw:
        return None
    with connect() as db:
        row = db.execute("SELECT user_id,expires_at FROM web_sessions WHERE token_hash=?", (digest(raw),)).fetchone()
        return row["user_id"] if row and parse(row["expires_at"]) > utcnow() else None


def is_admin(db, user_id):
    ids = {int(x.strip()) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().isdigit()}
    row = db.execute("SELECT telegram_id FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["telegram_id"] in ids)


def risk_payload(db, account):
    r = db.execute("SELECT * FROM risk_settings WHERE account_id=?", (account["id"],)).fetchone()
    return {"enabled": bool(r["enabled"]) and subscription_ok(db, account["user_id"]),
            "lot": r["lot"], "allowed_symbols": r["allowed_symbols"],
            "max_positions": r["max_positions"], "daily_loss_limit": r["daily_loss_limit"],
            "max_spread_points": r["max_spread_points"], "demo_only": True}
