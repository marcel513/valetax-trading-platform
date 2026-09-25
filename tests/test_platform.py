from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from platform_app import core
from platform_app.api import app
from platform_app.db import connect, migrate
from platform_app.security import digest, stamp, utcnow
from platform_app.strategy import ManualDemoProvider
from platform_app.bot import response_for


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "1001")
    migrate()


def link(user_id, login=123, server="Valetax-Demo"):
    code = core.make_link_code(user_id)
    return core.link_account(code, login, server, "demo", "USD", True)


def test_link_is_one_time_and_rejects_live():
    user = core.register_user(1001, 1001)
    code = core.make_link_code(user)
    with pytest.raises(ValueError, match="demo"):
        core.link_account(code, 123, "S", "real", "USD", True)
    account_id, token = core.link_account(code, 123, "S", "demo", "USD", True)
    assert core.account_for_token(token)["id"] == account_id
    with pytest.raises(ValueError, match="already used"):
        core.link_account(code, 124, "S", "demo", "USD", True)


def test_expired_code_and_duplicate_account():
    u1 = core.register_user(1001, 1001)
    u2 = core.register_user(1002, 1002)
    old = core.make_link_code(u1)
    with connect() as db:
        db.execute("UPDATE link_codes SET expires_at=? WHERE code_hash=?", (stamp(utcnow() - timedelta(seconds=1)), digest(old)))
    with pytest.raises(ValueError, match="expired"):
        core.link_account(old, 123, "S", "demo", "USD", True)
    link(u1, 123, "S")
    with pytest.raises(ValueError, match="already linked"):
        link(u2, 123, "S")


def test_account_isolation_and_admin():
    u1 = core.register_user(1001, 1001)
    u2 = core.register_user(1002, 1002)
    account_id, _ = link(u1)
    with pytest.raises(ValueError, match="not found"):
        core.stop_account(u2, account_id)
    with connect() as db:
        assert core.is_admin(db, u1)
        assert not core.is_admin(db, u2)
    client = TestClient(app)
    s2 = core.redeem_web_code(core.make_web_code(u2))
    response = client.post(f"/dashboard/settings/{account_id}", cookies={"session": s2}, data={"lot": "0.01", "allowed_symbols": "XAU", "max_positions": "1", "daily_loss_limit": "20", "max_spread_points": "50"}, follow_redirects=False)
    assert response.status_code == 404
    assert client.get("/admin", cookies={"session": s2}).status_code == 403


def test_subscription_and_default_pause():
    u = core.register_user(1001, 1001)
    account_id, token = link(u)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/ea/signals", headers=headers).json() == {"signals": []}
    with connect() as db:
        db.execute("UPDATE risk_settings SET enabled=1 WHERE account_id=?", (account_id,))
        db.execute("UPDATE subscriptions SET status='expired' WHERE user_id=?", (u,))
    assert client.get("/api/ea/signals", headers=headers).json() == {"signals": []}


def test_signal_deduplication_and_broker_fill_reporting():
    u = core.register_user(1001, 1001)
    account_id, token = link(u)
    with connect() as db:
        db.execute("UPDATE risk_settings SET enabled=1 WHERE account_id=?", (account_id,))
        s = ManualDemoProvider().generate("XAU", "BUY", 2000)
        db.execute("INSERT INTO signals VALUES(?,?,?,?,?,?,?)", tuple(s.__dict__.values()))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    assert len(client.get("/api/ea/signals", headers=headers).json()["signals"]) == 1
    bad = client.post("/api/ea/report", headers=headers, json={"signal_id": s.id, "state": "filled"})
    assert bad.status_code == 400
    body = {"signal_id": s.id, "state": "filled", "request_id": s.id,
            "broker_deal": 700, "broker_order": 600, "position_ticket": 500,
            "price": 2100, "volume": 0.01, "stop_loss": 2000, "symbol": "XAUUSD.c", "side": "BUY"}
    assert client.post("/api/ea/report", headers=headers, json=body).status_code == 200
    assert client.get("/api/ea/signals", headers=headers).json() == {"signals": []}
    client.post("/api/ea/report", headers=headers, json=body)
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM alerts WHERE message LIKE 'صفقة Demo نُفذت:%'").fetchone()[0] == 1


def test_heartbeat_rejects_identity_change():
    u = core.register_user(1001, 1001)
    _, token = link(u)
    client = TestClient(app)
    payload = {"login": 123, "server": "Valetax-Demo", "account_type": "demo", "trade_allowed": True,
               "symbol": "XAUUSD.c", "contract_size": 100, "tick_size": 0.01, "tick_value": 1,
               "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100}
    assert client.post("/api/ea/heartbeat", headers={"Authorization": f"Bearer {token}"}, json=payload).status_code == 200
    payload["server"] = "Other"
    assert client.post("/api/ea/heartbeat", headers={"Authorization": f"Bearer {token}"}, json=payload).status_code == 403


def test_private_bot_and_close_transition():
    assert "الخاصة" in response_for(1001, -100, "/trades")
    u = core.register_user(1001, 1001)
    account_id, token = link(u)
    with connect() as db:
        sig = ManualDemoProvider().generate("XAU", "BUY", 2000)
        db.execute("INSERT INTO signals VALUES(?,?,?,?,?,?,?)", tuple(sig.__dict__.values()))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    premature = client.post("/api/ea/report", headers=headers, json={"signal_id": sig.id, "state": "closed", "position_ticket": 55})
    assert premature.status_code == 400
    fill = {"signal_id": sig.id, "state": "filled", "request_id": sig.id, "broker_deal": 77,
            "position_ticket": 55, "price": 2100, "volume": 0.01, "stop_loss": 2000,
            "symbol": "XAUUSD", "side": "BUY"}
    assert client.post("/api/ea/report", headers=headers, json=fill).status_code == 200
    close = {"signal_id": sig.id, "state": "closed", "position_ticket": 55, "price": 2105, "profit": 5}
    assert client.post("/api/ea/report", headers=headers, json=close).status_code == 200
    assert client.post("/api/ea/report", headers=headers, json=close).json()["duplicate"]
    with connect() as db:
        assert db.execute("SELECT profit FROM trades WHERE account_id=?", (account_id,)).fetchone()[0] == 5


def test_bot_start_is_idempotent_and_stop_is_owned():
    assert "Demo" in response_for(1001, 1001, "/start")
    assert "Demo" in response_for(1001, 1001, "/start")
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users WHERE telegram_id=1001").fetchone()[0] == 1
    u = core.register_user(1001, 1001)
    account_id, _ = link(u)
    with connect() as db:
        db.execute("UPDATE risk_settings SET enabled=1 WHERE account_id=?", (account_id,))
    assert "إيقاف" in response_for(1001, 1001, "/stop")
    with connect() as db:
        assert db.execute("SELECT enabled FROM risk_settings WHERE account_id=?", (account_id,)).fetchone()[0] == 0
