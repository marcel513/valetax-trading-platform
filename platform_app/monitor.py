"""Low-volume operational alerts, safe to call repeatedly from the bot worker."""
from datetime import timedelta

from .core import alert, event
from .db import connect
from .security import parse, utcnow


def inspect():
    now = utcnow()
    with connect() as db:
        for a in db.execute("SELECT id,user_id,last_seen FROM accounts WHERE last_seen IS NOT NULL").fetchall():
            if parse(a["last_seen"]) > now - timedelta(minutes=2):
                continue
            prior = db.execute("SELECT created_at FROM events WHERE account_id=? AND kind='ea_offline' ORDER BY id DESC LIMIT 1", (a["id"],)).fetchone()
            if prior and parse(prior["created_at"] + "+00:00") > parse(a["last_seen"]):
                continue
            event(db, a["user_id"], a["id"], "ea_offline", "EA heartbeat overdue")
            alert(db, a["user_id"], f"تنبيه: انقطع اتصال EA للحساب #{a['id']} لأكثر من دقيقتين.")
        day = (now - timedelta(days=1)).date().isoformat()
        for u in db.execute("SELECT id FROM users WHERE private_chat_id IS NOT NULL").fetchall():
            key = f"daily:{day}"
            if db.execute("SELECT 1 FROM events WHERE user_id=? AND kind=?", (u["id"], key)).fetchone():
                continue
            row = db.execute("SELECT COUNT(*) n,COALESCE(SUM(t.profit),0) p FROM trades t JOIN accounts a ON a.id=t.account_id WHERE a.user_id=? AND date(t.closed_at)=?", (u["id"], day)).fetchone()
            event(db, u["id"], None, key, "Daily demo summary queued")
            alert(db, u["id"], f"ملخص Demo ليوم {day}: {row['n']} صفقة مغلقة، المحصلة {row['p']}.")
