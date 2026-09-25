import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def path():
    return Path(os.getenv("DATABASE_PATH", "./data/platform.db"))


@contextmanager
def connect():
    db_path = path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def migrate():
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, telegram_id INTEGER NOT NULL UNIQUE,
          private_chat_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
          user_id INTEGER PRIMARY KEY REFERENCES users(id), status TEXT NOT NULL
            CHECK(status IN ('trial','active','expired','suspended')),
          ends_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS link_codes (
          code_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at TEXT NOT NULL, used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          login INTEGER NOT NULL, server TEXT NOT NULL, account_type TEXT NOT NULL,
          currency TEXT NOT NULL, ea_token_hash TEXT NOT NULL UNIQUE,
          symbol TEXT, contract_size REAL, tick_size REAL, tick_value REAL,
          volume_min REAL, volume_step REAL, volume_max REAL,
          last_seen TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(login,server)
        );
        CREATE TABLE IF NOT EXISTS risk_settings (
          account_id INTEGER PRIMARY KEY REFERENCES accounts(id), enabled INTEGER NOT NULL DEFAULT 0,
          lot REAL NOT NULL DEFAULT 0.01, allowed_symbols TEXT NOT NULL DEFAULT 'XAU',
          max_positions INTEGER NOT NULL DEFAULT 1, daily_loss_limit REAL NOT NULL DEFAULT 20,
          max_spread_points INTEGER NOT NULL DEFAULT 50
        );
        CREATE TABLE IF NOT EXISTS signals (
          id TEXT PRIMARY KEY, provider TEXT NOT NULL CHECK(provider='DEMO_ONLY'),
          symbol_base TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
          stop_loss REAL NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
          account_id INTEGER NOT NULL REFERENCES accounts(id), signal_id TEXT NOT NULL REFERENCES signals(id),
          state TEXT NOT NULL CHECK(state IN ('offered','claimed','rejected','submitted','filled','closed')),
          reason TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(account_id,signal_id)
        );
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
          signal_id TEXT NOT NULL REFERENCES signals(id), request_id TEXT NOT NULL UNIQUE,
          broker_order INTEGER, broker_deal INTEGER, retcode INTEGER,
          state TEXT NOT NULL, fill_price REAL, volume REAL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(account_id,signal_id)
        );
        CREATE TABLE IF NOT EXISTS trades (
          id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
          signal_id TEXT NOT NULL REFERENCES signals(id), position_ticket INTEGER NOT NULL,
          symbol TEXT NOT NULL, side TEXT NOT NULL, volume REAL NOT NULL,
          open_price REAL NOT NULL, stop_loss REAL NOT NULL, opened_at TEXT NOT NULL,
          close_price REAL, profit REAL, closed_at TEXT,
          UNIQUE(account_id,position_ticket)
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(id),
          user_id INTEGER REFERENCES users(id), kind TEXT NOT NULL,
          message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alerts (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          message TEXT NOT NULL, sent_at TEXT, error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS web_codes (
          code_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at TEXT NOT NULL, used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS web_sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);
        CREATE INDEX IF NOT EXISTS idx_signals_expiry ON signals(expires_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_pending ON alerts(sent_at);
        """)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)")
