import os
from contextlib import asynccontextmanager
from html import escape

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import core
from .db import connect, migrate
from .security import parse, stamp, utcnow
from .strategy import ManualDemoProvider

@asynccontextmanager
async def lifespan(_app):
    migrate()
    yield


app = FastAPI(title="MT5 Demo Coordinator", docs_url=None, redoc_url=None, lifespan=lifespan)


class LinkIn(BaseModel):
    code: str
    login: int = Field(gt=0)
    server: str = Field(min_length=1, max_length=100)
    account_type: str
    currency: str
    trade_allowed: bool


class Heartbeat(BaseModel):
    login: int
    server: str
    account_type: str
    trade_allowed: bool
    symbol: str = Field(min_length=1, max_length=40)
    contract_size: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    tick_value: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    volume_max: float = Field(gt=0)


class Report(BaseModel):
    signal_id: str
    state: str
    reason: str = ""
    request_id: str = ""
    broker_order: int = 0
    broker_deal: int = 0
    position_ticket: int = 0
    retcode: int = 0
    symbol: str = ""
    side: str = ""
    volume: float = 0
    price: float = 0
    stop_loss: float = 0
    profit: float = 0


class SignalIn(BaseModel):
    symbol_base: str = "XAU"
    side: str
    stop_loss: float


def ea_account(authorization: str | None = Header(default=None)):
    raw = authorization.removeprefix("Bearer ") if authorization else ""
    account = core.account_for_token(raw)
    if not account:
        raise HTTPException(401, "Invalid EA token")
    return account


def web_user(session: str | None = Cookie(default=None)):
    user_id = core.session_user(session)
    if not user_id:
        raise HTTPException(401, "Sign in through the private Telegram bot")
    return user_id


def ensure_demo(account):
    if account["account_type"] != "demo":
        raise HTTPException(403, "Demo accounts only")


@app.get("/health")
def health():
    return {"status": "ok", "real_trading": False}


@app.post("/api/ea/link")
def link(body: LinkIn):
    try:
        account_id, ea_token = core.link_account(**body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"account_id": account_id, "ea_token": ea_token, "demo_only": True}


@app.post("/api/ea/heartbeat")
def heartbeat(body: Heartbeat, account=Depends(ea_account)):
    ensure_demo(account)
    if body.account_type.lower() != "demo" or body.login != account["login"] or body.server != account["server"]:
        raise HTTPException(403, "MT5 identity changed")
    with connect() as db:
        db.execute("UPDATE accounts SET last_seen=?,symbol=?,contract_size=?,tick_size=?,tick_value=?,volume_min=?,volume_step=?,volume_max=? WHERE id=?",
                   (stamp(utcnow()), body.symbol, body.contract_size, body.tick_size, body.tick_value,
                    body.volume_min, body.volume_step, body.volume_max, account["id"]))
        config = core.risk_payload(db, account)
    config["enabled"] = config["enabled"] and body.trade_allowed
    return config


@app.get("/api/ea/signals")
def signals(account=Depends(ea_account)):
    ensure_demo(account)
    with connect() as db:
        risk = core.risk_payload(db, account)
        if not risk["enabled"]:
            return {"signals": []}
        rows = db.execute("""SELECT s.* FROM signals s WHERE s.expires_at>?
          AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.account_id=? AND d.signal_id=s.id)
          ORDER BY s.created_at LIMIT 10""", (stamp(utcnow()), account["id"])).fetchall()
        return {"signals": [dict(r) for r in rows]}


@app.get("/api/ea/open-trades")
def open_trades(account=Depends(ea_account)):
    ensure_demo(account)
    with connect() as db:
        rows = db.execute("SELECT signal_id,position_ticket FROM trades WHERE account_id=? AND closed_at IS NULL", (account["id"],)).fetchall()
        return {"trades": [dict(r) for r in rows]}


@app.post("/api/ea/report")
def report(body: Report, account=Depends(ea_account)):
    ensure_demo(account)
    if body.state not in ("rejected", "submitted", "filled", "closed"):
        raise HTTPException(400, "Invalid report state")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        sig = db.execute("SELECT * FROM signals WHERE id=? AND provider='DEMO_ONLY'", (body.signal_id,)).fetchone()
        if not sig:
            raise HTTPException(404, "Signal not found")
        prior = db.execute("SELECT state FROM deliveries WHERE account_id=? AND signal_id=?", (account["id"], body.signal_id)).fetchone()
        if prior and prior["state"] in ("rejected", "closed"):
            return {"accepted": True, "duplicate": True}
        if prior and prior["state"] == "filled" and body.state != "closed":
            return {"accepted": True, "duplicate": True}
        if prior and prior["state"] == "submitted" and body.state == "submitted":
            return {"accepted": True, "duplicate": True}
        if body.state == "filled" and (not body.position_ticket or not body.broker_deal or body.price <= 0 or body.volume <= 0 or body.stop_loss <= 0):
            raise HTTPException(400, "Broker fill proof incomplete")
        if body.state == "closed" and (not body.position_ticket or not prior or prior["state"] != "filled"):
            raise HTTPException(400, "Unknown open platform position")
        if body.state == "submitted" and not body.request_id:
            raise HTTPException(400, "Missing request id")
        db.execute("""INSERT INTO deliveries(account_id,signal_id,state,reason) VALUES(?,?,?,?)
          ON CONFLICT(account_id,signal_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,updated_at=CURRENT_TIMESTAMP""",
                   (account["id"], body.signal_id, body.state, body.reason[:500]))
        if body.state in ("submitted", "filled"):
            db.execute("""INSERT INTO orders(account_id,signal_id,request_id,broker_order,broker_deal,retcode,state,fill_price,volume)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,signal_id) DO UPDATE SET
              broker_order=excluded.broker_order,broker_deal=excluded.broker_deal,retcode=excluded.retcode,
              state=excluded.state,fill_price=excluded.fill_price,volume=excluded.volume""",
                       (account["id"], body.signal_id, body.request_id or body.signal_id,
                        body.broker_order, body.broker_deal, body.retcode, body.state, body.price, body.volume))
        if body.state == "filled":
            db.execute("""INSERT OR IGNORE INTO trades(account_id,signal_id,position_ticket,symbol,side,volume,open_price,stop_loss,opened_at)
              VALUES(?,?,?,?,?,?,?,?,?)""", (account["id"], body.signal_id, body.position_ticket,
                                               body.symbol, body.side, body.volume, body.price, body.stop_loss, stamp(utcnow())))
            if not prior or prior["state"] != "filled":
                core.alert(db, account["user_id"], f"صفقة Demo نُفذت: {body.symbol} {body.side} {body.volume} @ {body.price}; SL {body.stop_loss}; ticket {body.position_ticket}")
        elif body.state == "closed":
            cur = db.execute("UPDATE trades SET close_price=?,profit=?,closed_at=? WHERE account_id=? AND position_ticket=? AND closed_at IS NULL",
                             (body.price, body.profit, stamp(utcnow()), account["id"], body.position_ticket))
            if not cur.rowcount:
                raise HTTPException(400, "Position already closed or unknown")
            core.alert(db, account["user_id"], f"صفقة Demo أُغلقت: ticket {body.position_ticket}; result {body.profit}")
        elif body.state == "rejected":
            core.alert(db, account["user_id"], f"امتنع EA عن تنفيذ إشارة Demo: {body.reason[:300]}")
        core.event(db, account["user_id"], account["id"], body.state, body.reason[:500] or body.signal_id)
    return {"accepted": True, "duplicate": False}


@app.get("/login/{code}")
def login(code: str):
    try:
        session = core.redeem_web_code(code)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("session", session, httponly=True, secure=os.getenv("BASE_URL", "").startswith("https://"), samesite="strict", max_age=43200)
    return response


def page(title, body):
    return HTMLResponse(f"<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>body{{font:16px system-ui;max-width:900px;margin:40px auto;padding:0 20px;background:#f7f8fa;color:#18212f}}section{{background:white;padding:20px;margin:15px 0;border-radius:12px;box-shadow:0 2px 10px #ddd}}input{{margin:5px;padding:8px}}button{{padding:10px;background:#164c7e;color:white;border:0;border-radius:6px}}table{{width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd}}</style><h1>{escape(title)}</h1>{body}</html>")


@app.get("/dashboard")
def dashboard(user_id=Depends(web_user)):
    with connect() as db:
        accounts = db.execute("SELECT a.*,r.* FROM accounts a JOIN risk_settings r ON r.account_id=a.id WHERE a.user_id=?", (user_id,)).fetchall()
        events = db.execute("SELECT kind,message,created_at FROM events WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,)).fetchall()
        sub = db.execute("SELECT status,ends_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        out = f"<section>الاشتراك: {escape(sub['status']) if sub else 'غير معروف'}؛ حتى {escape(str(sub['ends_at'])) if sub else ''}</section>"
        for a in accounts:
            out += f"<section><h2>حساب {a['login']} — {escape(a['server'])}</h2><p>EA: {escape(str(a['last_seen'] or 'غير متصل'))} | الرمز: {escape(str(a['symbol'] or 'غير معروف'))}</p><form method='post' action='/dashboard/settings/{a['id']}'><label>Lot <input name='lot' type='number' min='0.01' step='0.01' value='{a['lot']}'></label><label>الأدوات <input name='allowed_symbols' value='{escape(a['allowed_symbols'])}'></label><label>أقصى صفقات <input name='max_positions' type='number' min='1' max='20' value='{a['max_positions']}'></label><label>حد خسارة يومي <input name='daily_loss_limit' type='number' min='1' step='0.01' value='{a['daily_loss_limit']}'></label><label>حد سبريد بالنقاط <input name='max_spread_points' type='number' min='1' value='{a['max_spread_points']}'></label><label>تشغيل <input name='enabled' type='checkbox' value='1' {'checked' if a['enabled'] else ''}></label><button>حفظ</button></form></section>"
        out += "<section><h2>الأحداث</h2><table><tr><th>الوقت</th><th>النوع</th><th>الوصف</th></tr>" + "".join(f"<tr><td>{escape(e['created_at'])}</td><td>{escape(e['kind'])}</td><td>{escape(e['message'])}</td></tr>" for e in events) + "</table></section>"
        return page("لوحة Demo", out)


@app.post("/dashboard/settings/{account_id}")
async def settings(account_id: int, request: Request, user_id=Depends(web_user)):
    form = await request.form()
    try:
        lot = float(form.get("lot", 0))
        symbols = str(form.get("allowed_symbols", "")).upper()
        max_positions = int(form.get("max_positions", 0))
        daily_loss = float(form.get("daily_loss_limit", 0))
        spread = int(form.get("max_spread_points", 0))
    except ValueError as e:
        raise HTTPException(400, "Invalid settings") from e
    if not (0.01 <= lot <= 10 and symbols == "XAU" and 1 <= max_positions <= 20 and 1 <= daily_loss <= 100000 and 1 <= spread <= 10000):
        raise HTTPException(400, "Settings out of range; demo XAU only")
    with connect() as db:
        account = db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?", (account_id, user_id)).fetchone()
        if not account:
            raise HTTPException(404, "Account not found")
        db.execute("UPDATE risk_settings SET lot=?,allowed_symbols=?,max_positions=?,daily_loss_limit=?,max_spread_points=?,enabled=? WHERE account_id=?",
                   (lot, symbols, max_positions, daily_loss, spread, int(form.get("enabled") == "1"), account_id))
        core.event(db, user_id, account_id, "settings", "Demo risk settings updated")
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/admin")
def admin(user_id=Depends(web_user)):
    with connect() as db:
        if not core.is_admin(db, user_id):
            raise HTTPException(403, "Admin only")
        accounts = db.execute("SELECT a.id,a.user_id,a.login,a.server,a.last_seen,s.status FROM accounts a JOIN subscriptions s ON s.user_id=a.user_id ORDER BY a.id DESC LIMIT 100").fetchall()
        errors = db.execute("SELECT created_at,kind,message FROM events WHERE kind IN ('rejected','error') ORDER BY id DESC LIMIT 50").fetchall()
        body = "<section><h2>الحسابات</h2><table><tr><th>ID</th><th>MT5</th><th>Server</th><th>EA</th><th>Subscription</th></tr>" + "".join(f"<tr><td>{a['id']}</td><td>{a['login']}</td><td>{escape(a['server'])}</td><td>{escape(str(a['last_seen']))}</td><td>{escape(a['status'])}</td></tr>" for a in accounts) + "</table></section>"
        body += "<section><h2>حالة الاشتراك</h2><form method='post' action='/admin/subscription'><label>User ID <input name='user_id' type='number' min='1'></label><select name='status'><option>trial</option><option>active</option><option>expired</option><option>suspended</option></select><label>نهاية UTC ISO <input name='ends_at' placeholder='2026-12-31T23:59:00+00:00'></label><button>حفظ</button></form></section>"
        body += "<section><h2>إشارة اختبار Demo</h2><form method='post' action='/admin/demo-signal'><select name='side'><option>BUY</option><option>SELL</option></select><label>وقف الخسارة بسعر مطلق <input name='stop_loss' type='number' step='0.01' min='0.01'></label><button>إنشاء إشارة تنتهي خلال دقيقتين</button></form></section>"
        body += "<section><h2>الأخطاء</h2>" + "".join(f"<p>{escape(e['created_at'])}: {escape(e['kind'])} — {escape(e['message'])}</p>" for e in errors) + "</section>"
        return page("إدارة Demo", body)


@app.post("/admin/subscription")
async def admin_subscription(request: Request, user_id=Depends(web_user)):
    form = await request.form()
    with connect() as db:
        if not core.is_admin(db, user_id):
            raise HTTPException(403, "Admin only")
        try:
            target = int(form.get("user_id", 0))
            status = str(form.get("status", ""))
            ends_at = str(form.get("ends_at", "")) or None
            if status not in ("trial", "active", "expired", "suspended") or (ends_at and parse(ends_at) <= utcnow() and status in ("trial", "active")):
                raise ValueError()
            if ends_at:
                parse(ends_at)
        except ValueError as e:
            raise HTTPException(400, "Invalid subscription") from e
        cur = db.execute("UPDATE subscriptions SET status=?,ends_at=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (status, ends_at, target))
        if not cur.rowcount:
            raise HTTPException(404, "User not found")
        core.event(db, target, None, "subscription", f"Subscription changed to {status} by admin {user_id}")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/demo-signal")
async def admin_demo_signal(request: Request, user_id=Depends(web_user)):
    form = await request.form()
    try:
        signal = SignalIn(side=str(form.get("side", "")), stop_loss=float(form.get("stop_loss", 0)))
    except ValueError as e:
        raise HTTPException(400, "Invalid demo signal") from e
    demo_signal(signal, user_id)
    return RedirectResponse("/admin", status_code=303)


@app.post("/api/admin/demo-signal")
def demo_signal(body: SignalIn, user_id=Depends(web_user)):
    with connect() as db:
        if not core.is_admin(db, user_id):
            raise HTTPException(403, "Admin only")
        try:
            s = ManualDemoProvider().generate(**body.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        db.execute("INSERT INTO signals VALUES(?,?,?,?,?,?,?)", tuple(s.__dict__.values()))
        return s.__dict__
