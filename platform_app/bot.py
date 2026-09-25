"""Long polling Telegram bot. Run separately from the web server."""
import asyncio
import logging
import os

import httpx
from dotenv import load_dotenv

from . import core
from .db import connect, migrate
from .monitor import inspect
from .security import parse, utcnow

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token):
        self.client = httpx.AsyncClient(base_url=f"https://api.telegram.org/bot{token}/", timeout=40)

    async def call(self, method, payload):
        response = await self.client.post(method, json=payload)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram error"))
        return result["result"]

    async def send(self, chat_id, text):
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text,
                                               "disable_web_page_preview": True})


HELP = ("الأوامر: /start /register /subscribe /link /accounts /ea /settings /trades "
        "/today /subscription /stop /dashboard /help\n"
        "هذه نسخة Demo فقط؛ لا ترسل كلمات مرور أو أموالاً إلى البوت.")


def response_for(telegram_id, chat_id, text):
    if telegram_id != chat_id:
        return "استخدم المحادثة الخاصة مع البوت لحماية بيانات حسابك."
    user_id = core.register_user(telegram_id, chat_id)
    command = text.split()[0].split("@")[0].lower() if text else ""
    if command in ("/start", "/register", "/help"):
        return HELP
    if command == "/link":
        code = core.make_link_code(user_id)
        return f"رمز الربط المؤقت (10 دقائق، مرة واحدة): {code}\nأدخله في إعداد EA داخل MT5 Demo. لا تشاركه."
    if command == "/dashboard":
        code = core.make_web_code(user_id)
        base = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        return f"رابط دخول لمرة واحدة (5 دقائق): {base}/login/{code}"
    with connect() as db:
        accounts = db.execute("SELECT a.*,r.enabled FROM accounts a JOIN risk_settings r ON r.account_id=a.id WHERE a.user_id=? ORDER BY a.id", (user_id,)).fetchall()
        s = db.execute("SELECT status,ends_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        if command in ("/subscribe", "/subscription"):
            return f"الاشتراك: {s['status']} حتى {s['ends_at'] or 'دون تاريخ'}. الدفع غير مفعّل في هذه النسخة."
        if command == "/accounts":
            return "\n".join(f"#{a['id']} MT5 {a['login']} / {a['server']} / Demo" for a in accounts) or "لا يوجد حساب مربوط. استخدم /link"
        if command == "/ea":
            return "\n".join(f"#{a['id']}: آخر اتصال {a['last_seen'] or 'لم يتصل'}" for a in accounts) or "لا يوجد EA مربوط."
        if command == "/settings":
            return "استخدم /dashboard لضبط اللوت والمخاطرة والأدوات. التداول متوقف افتراضيًا."
        if command == "/stop":
            for a in accounts:
                core.stop_account(user_id, a["id"])
            return "تم إيقاف فتح صفقات جديدة لكل حساباتك."
        if command == "/trades":
            rows = db.execute("SELECT t.* FROM trades t JOIN accounts a ON a.id=t.account_id WHERE a.user_id=? ORDER BY t.id DESC LIMIT 10", (user_id,)).fetchall()
            return "\n".join(f"{r['symbol']} {r['side']} {r['volume']} ticket {r['position_ticket']} — {'مفتوحة' if not r['closed_at'] else r['profit']}" for r in rows) or "لا توجد صفقات Demo."
        if command == "/today":
            row = db.execute("SELECT COUNT(*) n,COALESCE(SUM(t.profit),0) p FROM trades t JOIN accounts a ON a.id=t.account_id WHERE a.user_id=? AND date(t.closed_at)=date('now')", (user_id,)).fetchone()
            return f"نتائج Demo اليوم: {row['n']} صفقة مغلقة، المحصلة {row['p']}."
    return HELP


async def flush_alerts(tg):
    with connect() as db:
        pending = db.execute("SELECT al.id,al.message,u.private_chat_id FROM alerts al JOIN users u ON u.id=al.user_id WHERE al.sent_at IS NULL ORDER BY al.id LIMIT 30").fetchall()
    for a in pending:
        if not a["private_chat_id"]:
            continue
        try:
            await tg.send(a["private_chat_id"], a["message"])
            with connect() as db:
                db.execute("UPDATE alerts SET sent_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?", (a["id"],))
        except Exception as e:
            log.warning("Alert delivery failed for alert %s: %s", a["id"], type(e).__name__)
            with connect() as db:
                db.execute("UPDATE alerts SET error=? WHERE id=?", (type(e).__name__, a["id"]))


async def run():
    load_dotenv()
    migrate()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "replace-locally":
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in local .env")
    tg = Telegram(token)
    offset = None
    while True:
        try:
            updates = await tg.call("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message"]})
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                sender = msg.get("from", {}).get("id")
                chat = msg.get("chat", {}).get("id")
                if not sender or not chat or not msg.get("text"):
                    continue
                try:
                    reply = response_for(sender, chat, msg["text"])
                    await tg.send(chat, reply)
                except Exception:
                    log.exception("Bot command failed; update %s", update["update_id"])
            await flush_alerts(tg)
            inspect()
        except Exception:
            log.exception("Polling or alert delivery failed")
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
