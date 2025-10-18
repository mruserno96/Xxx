#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NumberInfo Telegram Bot (Flask) — Production Ready
==================================================

Highlights
----------
- Reply keyboards (bottom) for owner/admin/user — now includes **📱 Number Info** for easy lookups (no /num needed).
- Membership gate: checks user is a member of your group/channel(s); shows join links with inline URL buttons.
- Broadcast: supports text, photo+caption, video+caption, document+caption (uses original file_id to forward).
- Supabase persistence: users, admin roles, sessions (pending actions, including number-entry), broadcast logs.
- Live stats: total users and today's active users.
- Robust HTTP session with retries; webhook route; optional keepalive ping thread.
- Safe for redeploy/restart — data stored in Supabase.
- Clean structure; structured logging; helpful comments.
- Render/Gunicorn friendly: no double-run, optional ping thread, health endpoints.

Environment Variables
---------------------
TELEGRAM_TOKEN=...
WEBHOOK_URL=https://your-domain.com/webhook/<secret>
WEBHOOK_SECRET=<secret>
CHANNEL1_INVITE_LINK=https://t.me/+abcdef
CHANNEL1_CHAT_ID=-1001234567890
CHANNEL2_CHAT_ID_OR_USERNAME=@yourchan

SUPABASE_URL=...
SUPABASE_SERVICE_ROLE=...  (recommended)  OR  SUPABASE_ANON_KEY=... (limited)
OWNER_ID=123456789

# Optional:
LOG_LEVEL=INFO              # DEBUG|INFO|WARNING|ERROR
DISABLE_PING=1              # set to 1 to disable keepalive ping thread
PING_INTERVAL_SECONDS=300   # default 300
REQUEST_TIMEOUT_SECONDS=20  # default 20
"""

from __future__ import annotations

import os
import json
import logging
import threading
import time


from datetime import datetime, timezone, date
from typing import Dict, Any, Optional, List, Tuple

from flask import Flask, request, jsonify, abort
import requests
from requests.adapters import HTTPAdapter, Retry

# ----- Supabase -----


from supabase import create_client, Client

# ---------------------------------------------------------------------
# Logging (configurable via LOG_LEVEL)
# ---------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("numberinfo-bot")

# ---------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------
app = Flask(__name__)

# ---------------------------------------------------------------------
# Config (env)
# ---------------------------------------------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
if not TOKEN:
    log.warning("TELEGRAM_TOKEN is empty! Telegram calls will fail.")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()


SELF_URL = WEBHOOK_URL.rsplit("/webhook", 1)[0] if "/webhook" in WEBHOOK_URL else (
    os.getenv("SELF_URL", "").strip() or "https://example.com"
)

KUKUPAY_API_KEY = os.getenv("KUKUPAY_API_KEY", "axMSq3oSEEhrYvWNjXeCavGQisdxaY1U")
KUKUPAY_WEBHOOK_URL = os.getenv("KUKUPAY_WEBHOOK_URL", f"{SELF_URL}/kukupay_webhook")
KUKUPAY_RETURN_URL = os.getenv("KUKUPAY_RETURN_URL", "https://t.me/YourBotUsername")

print("DEBUG_KUKUPAY_KEY =", os.getenv("KUKUPAY_API_KEY"))
print("DEBUG_KUKUPAY_WEBHOOK =", os.getenv("KUKUPAY_WEBHOOK_URL"))
print("DEBUG_KUKUPAY_RETURN =", os.getenv("KUKUPAY_RETURN_URL"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default-secret").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
SELF_URL = WEBHOOK_URL.rsplit("/webhook", 1)[0] if "/webhook" in WEBHOOK_URL else (os.getenv("SELF_URL", "").strip() or "https://example.com")
UPI_ID = os.getenv("UPI_ID", "2xclubwinsharma@fam")
QR_IMAGE_URL = os.getenv("QR_IMAGE_URL", "https://alexcoder.shop/qer.jpg")
RUPEES_PER_POINT = int(os.getenv("RUPEES_PER_POINT", "10"))  # e.g. 10 rupees = 1 point
MANUAL_AMOUNTS = [10, 50, 100, 200, 500]  # rupees
# Channels / Groups gate (set the ones you need)
CHANNEL1_INVITE_LINK = os.getenv("CHANNEL1_INVITE_LINK", "").strip()
CHANNEL1_CHAT_ID = os.getenv("CHANNEL1_CHAT_ID", "").strip()
CHANNEL2_CHAT = os.getenv("CHANNEL2_CHAT_ID_OR_USERNAME", "").strip()

# Admin owner (bootstrap): this user_id is always treated as owner/admin
OWNER_ID = os.getenv("OWNER_ID", "").strip()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE", os.getenv("SUPABASE_ANON_KEY", "")).strip()

try:
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase initialized successfully!")
except Exception as e:
    sb = None
    logging.exception("❌ Supabase init failed: %s", e)

# Requests / Telegram session with retries
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
session = requests.Session()
retries = Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
    respect_retry_after_header=True,
)
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# ---------------------------------------------------------------------
# Keyboards — Reply (bottom) only for commands; Inline only for join URLs
# ---------------------------------------------------------------------
def keyboard_user() -> Dict[str, Any]:
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
            [{"text": "📱 Number Info"}],
            [{"text": "💰 My Balance"}, {"text": "🎁 Refer & Earn"}],
            [{"text": "💳 Deposit Points"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "selective": True,
    }


def keyboard_admin() -> Dict[str, Any]:
    """Keyboard for admins (limited)."""
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
            [{"text": "📱 Number Info"}, {"text": "📊 Live Stats"}, {"text": "📢 Broadcast"}],
            [{"text": "💰 My Balance"}, {"text": "🎁 Refer & Earn"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "selective": True,
    }



def keyboard_owner() -> Dict[str, Any]:
    """Keyboard for owner (full control)."""
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
            [{"text": "📱 Number Info"}, {"text": "📊 Live Stats"}, {"text": "📢 Broadcast"}],
            [{"text": "👑 List Admins"}, {"text": "➕ Add Admin"}, {"text": "➖ Remove Admin"}],
            [{"text": "💰 My Balance"}, {"text": "🎁 Refer & Earn"}],
            [{"text": "💎 Add Points to User"}],  # 🆕 new
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "selective": True,
    }




def keyboard_none() -> Dict[str, Any]:
    return {"remove_keyboard": True}


def membership_join_inline(channels: List[Dict[str, str]]) -> Dict[str, Any]:
    """Inline keyboard for join links (reply keyboards can't have URLs)."""
    buttons = [[{"text": ch["label"], "url": ch["url"]}] for ch in channels if ch.get("url")]
    if not buttons:
        buttons = [[{"text": "❗️No Join Link Configured", "callback_data": "noop"}]]
    buttons.append([{"text": "✅ Try Again", "callback_data": "try_again"}])
    return {"inline_keyboard": buttons}


def db_is_admin(user_id: int) -> bool:
    """Owner is always admin (bootstrap)."""
    if OWNER_ID and str(user_id) == str(OWNER_ID):
        return True
    if not sb:
        return False
    try:
        res = sb.table("users").select("is_admin").eq("id", user_id).limit(1).execute()  # type: ignore
        if getattr(res, "data", None) and len(res.data) > 0:  # type: ignore
            return bool(res.data[0].get("is_admin", False))  # type: ignore
        return False
    except Exception as e:
        log.exception("db_is_admin failed: %s", e)
        return False


def role_for(user_id: int) -> str:
    """Return 'owner' | 'admin' | 'user'."""
    if OWNER_ID and str(user_id) == str(OWNER_ID):
        return "owner"
    if db_is_admin(user_id):
        return "admin"
    return "user"


def keyboard_for(user_id: int) -> Dict[str, Any]:
    r = role_for(user_id)
    if r == "owner":
        return keyboard_owner()
    if r == "admin":
        return keyboard_admin()
    return keyboard_user()

# ---------------------------------------------------------------------
# Telegram helpers (FIXED)
# ---------------------------------------------------------------------
def tg(method: str, data: Dict[str, Any], timeout: int = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """
    Low-level Telegram call with logging.
    Always return a dict. On error, return {"ok": False, "error": "..."} so callers can branch safely.
    """
    try:
        resp = session.post(f"{TELEGRAM_API}/{method}", data=data, timeout=timeout)
        text = (resp.text or "")[:800]
        log.debug("TG %s -> %s %s", method, resp.status_code, text)
        if resp.status_code == 200:
            try:
                return resp.json()  # type: ignore
            except Exception:
                return {"ok": False, "error": "invalid json from telegram"}
        return {"ok": False, "error": text or f"status {resp.status_code}"}
    except Exception as e:
        log.exception("TG %s failed: %s", method, e)
        return {"ok": False, "error": str(e)}


def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None,
                 parse_mode: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendMessage", payload)


def edit_message(chat_id: int, message_id: int, text: str,
                 reply_markup: Optional[Dict[str, Any]] = None, parse_mode: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("editMessageText", payload)


def send_photo(chat_id: int, file_id: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "photo": file_id}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendPhoto", payload)


def send_video(chat_id: int, file_id: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "video": file_id}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendVideo", payload)


def send_document(chat_id: int, file_id: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "document": file_id}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendDocument", payload)


def answer_callback(callback_id: str, text: Optional[str] = None, show_alert: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    return tg("answerCallbackQuery", payload, timeout=10)


def is_member(user_id: int, chat_identifier: str) -> Optional[bool]:
    """Return True if user is member/admin/creator; False if not; None if error."""
    if not chat_identifier:
        return None
    try:
        r = session.get(f"{TELEGRAM_API}/getChatMember",
                        params={"chat_id": chat_identifier, "user_id": user_id},
                        timeout=10)
        data = r.json()
        if not data.get("ok"):
            log.warning("getChatMember failed: %s", data)
            return None
        status = data["result"]["status"]
        return status in ("creator", "administrator", "member")
    except Exception as e:
        log.exception("is_member error: %s", e)
        return None

# ---------------------------------------------------------------------
# Supabase persistence helpers
# ---------------------------------------------------------------------







# ---------------------------------------------------------------------
# Points + Referral helpers
# ---------------------------------------------------------------------

def db_get_points(user_id: int) -> int:
    if not sb:
        return 0
    try:
        res = sb.table("points").select("points").eq("user_id", user_id).limit(1).execute()
        if res.data:
            return int(res.data[0].get("points", 0))
        return 0
    except Exception as e:
        log.exception("db_get_points failed: %s", e)
        return 0


def db_add_points(user_id: int, amount: int) -> None:
    """Add (or subtract if negative) user points."""
    if not sb:
        return
    try:
        current = db_get_points(user_id)
        newval = max(current + amount, 0)
        sb.table("points").upsert({"user_id": user_id, "points": newval}).execute()
    except Exception as e:
        log.exception("db_add_points failed: %s", e)


def db_init_points_if_new(user_id: int, referred_by: Optional[int] = None) -> None:
    """Give 5 points to new user on first start."""
    if not sb:
        return
    try:
        res = sb.table("points").select("user_id").eq("user_id", user_id).execute()
        if not res.data:
            sb.table("points").insert({
                "user_id": user_id,
                "points": 5,
                "referred_by": referred_by
            }).execute()
    except Exception as e:
        log.exception("db_init_points_if_new failed: %s", e)




def _progress_bar(points: int, total: int = 100) -> str:
    """10-slot bar, scales to 'total' (default 100)."""
    pct = min(max(points, 0), total) / total
    filled_slots = int(round(pct * 10))
    return "▰" * filled_slots + "▱" * (10 - filled_slots)






def db_upsert_user(user: Dict[str, Any]) -> None:
    """Upsert user in 'users' table; user is dict with Telegram fields."""
    if not sb:
        return
    try:
        row = {
            "id": user["id"],
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "username": user.get("username"),
            "language_code": user.get("language_code"),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        sb.table("users").upsert(row).execute()  # type: ignore
    except Exception as e:
        log.exception("db_upsert_user failed: %s", e)


def db_mark_admin(user_id: int, is_admin: bool) -> bool:
    if not sb:
        return False
    try:
        sb.table("users").upsert({"id": user_id, "is_admin": is_admin}).execute()  # type: ignore
        return True
    except Exception as e:
        log.exception("db_mark_admin failed: %s", e)
        return False


def db_list_admins() -> List[Dict[str, Any]]:
    if not sb:
        return []
    try:
        res = sb.table("users").select("id,username,first_name,last_name,is_admin").eq("is_admin", True).execute()  # type: ignore
        return res.data or []  # type: ignore
    except Exception as e:
        log.exception("db_list_admins failed: %s", e)
        return []


def db_all_user_ids() -> List[int]:
    if not sb:
        return []
    try:
        res = sb.table("users").select("id").execute()  # type: ignore
        return [row["id"] for row in (res.data or [])]  # type: ignore
    except Exception as e:
        log.exception("db_all_user_ids failed: %s", e)
        return []


def db_set_session(user_id: int, action: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    """Store pending action for admin/user (e.g., broadcast, add_admin, remove_admin, await_number)."""
    if not sb:
        return
    try:
        sb.table("sessions").upsert({
            "user_id": user_id,
            "action": action,
            "payload": json.dumps(payload or {})
        }).execute()  # type: ignore
    except Exception as e:
        log.exception("db_set_session failed: %s", e)


def db_get_session(user_id: int) -> Optional[Dict[str, Any]]:
    if not sb:
        return None
    try:
        res = sb.table("sessions").select("*").eq("user_id", user_id).limit(1).execute()  # type: ignore
        if res.data:  # type: ignore
            row = res.data[0]  # type: ignore
            payload = {}
            try:
                payload = json.loads(row.get("payload") or "{}")
            except Exception:
                payload = {}
            return {"action": row.get("action"), "payload": payload}
        return None
    except Exception as e:
        log.exception("db_get_session failed: %s", e)
        return None


def db_clear_session(user_id: int) -> None:
    if not sb:
        return
    try:
        sb.table("sessions").delete().eq("user_id", user_id).execute()  # type: ignore
    except Exception as e:
        log.exception("db_clear_session failed: %s", e)


def db_log_broadcast(desc: str, total: int, success: int, failed: int) -> None:
    if not sb:
        return
    try:
        sb.table("broadcasts").insert({
            "text": desc,
            "total": total,
            "success": success,
            "failed": failed
        }).execute()  # type: ignore
    except Exception as e:
        log.exception("db_log_broadcast failed: %s", e)


def db_stats_counts() -> Tuple[int, int]:
    """Return total users and today's active users (by last_seen date)."""
    if not sb:
        return 0, 0
    try:
        res = sb.table("users").select("id,last_seen").execute()  # type: ignore
        rows = res.data or []  # type: ignore
        total = len(rows)
        today_str = date.today().isoformat()
        active_today = 0
        for r in rows:
            ls = r.get("last_seen")
            if ls and str(ls)[:10] == today_str:
                active_today += 1
        return total, active_today
    except Exception as e:
        log.exception("db_stats_counts failed: %s", e)
        return 0, 0

# ---------------------------------------------------------------------
# Join Gate
# ---------------------------------------------------------------------
def check_membership_and_prompt(chat_id: int, user_id: int) -> bool:
    """Check channel membership. If not joined, prompt with join buttons.
    If user has joined, also complete pending referrals (once)."""
    ch1_url = CHANNEL1_INVITE_LINK or None
    ch2_url = f"https://t.me/{CHANNEL2_CHAT.lstrip('@')}" if CHANNEL2_CHAT else None

    mem1 = is_member(user_id, CHANNEL1_CHAT_ID) if CHANNEL1_CHAT_ID else True
    mem2 = is_member(user_id, CHANNEL2_CHAT) if CHANNEL2_CHAT else True

    not_joined = []
    if mem1 is not True:
        not_joined.append({"label": "Join Group", "url": ch1_url})
    if mem2 is not True:
        not_joined.append({"label": "Join Channel", "url": ch2_url})

    if not_joined:
        send_message(
            chat_id,
            "🚫 You must join both channels below before using this bot 👇\n"
            "Please join and then tap *Try Again*.",
            reply_markup=membership_join_inline(not_joined),
            parse_mode="Markdown",
        )
        return False

    # ✅ User is now a member — complete any pending referral
    try:
        if sb:
            res = (
                sb.table("referrals")
                .select("id, referrer_id")
                .eq("referred_id", user_id)
                .eq("status", "pending")
                .execute()
            )
            for ref in res.data or []:
                sb.table("referrals").update({"status": "completed"}).eq("id", ref["id"]).execute()
                referrer = ref["referrer_id"]
                db_add_points(referrer, 2)
                db_add_points(user_id, 2)
                send_message(referrer, "🎉 Your referral joined both channels! +2 points added.")
                send_message(user_id, "🎁 You earned +2 welcome points for joining! 🎉")
    except Exception as e:
        log.warning("Referral completion check failed: %s", e)

    return True
# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route("/", methods=["GET"])
def home() -> Any:
    return jsonify(ok=True, message="Bot is alive", ts=datetime.now(timezone.utc).isoformat())


@app.route("/health", methods=["GET"])
def health() -> Any:
    return jsonify(status="ok", time=datetime.now(timezone.utc).isoformat())


@app.route("/version", methods=["GET"])
def version() -> Any:
    return jsonify(
        name="numberinfo-bot",
        version="1.1.0",
        webhook_url=WEBHOOK_URL,
        webhook_secret=WEBHOOK_SECRET[:4] + "***" if WEBHOOK_SECRET else "",
        sb=bool(sb),
    )


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook() -> Any:
    update = request.get_json(force=True, silent=True)
    if not update:
        return jsonify(ok=False, error="no update")

    log.info("Incoming update keys: %s", list(update.keys()))

    # Track user whenever possible
    if "message" in update:
        ufrom = update["message"].get("from", {})
        if ufrom:
            db_upsert_user(ufrom)
            if OWNER_ID and str(ufrom.get("id")) == str(OWNER_ID):
                try:
                    db_mark_admin(int(OWNER_ID), True)
                except Exception:
                    pass

    if "callback_query" in update:
        ufrom = update["callback_query"].get("from", {})
        if ufrom:
            db_upsert_user(ufrom)
            if OWNER_ID and str(ufrom.get("id")) == str(OWNER_ID):
                try:
                    db_mark_admin(int(OWNER_ID), True)
                except Exception:
                    pass

    # ----- Handle user messages -----
       # ----- Handle user messages -----
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        user = msg.get("from", {})
        user_id = user.get("id")
        chat_type = chat.get("type")

        # --- 📸 Check if awaiting manual screenshot upload ---
        sess_for_photo = db_get_session(user_id)
        if sess_for_photo and sess_for_photo.get("action") == "await_manual_screenshot":
            # user must send a photo
            if "photo" not in msg:
                send_message(
                    chat_id,
                    "📸 Please upload your <b>payment screenshot</b> as a photo (not file).",
                    parse_mode="HTML"
                )
                return jsonify(ok=True)

            try:
                amount = int(sess_for_photo.get("payload", {}).get("amount", 0))
            except Exception:
                amount = 0

            if amount <= 0:
                db_clear_session(user_id)
                send_message(chat_id, "⚠️ Amount missing. Please pick a plan again with /deposit.")
                return jsonify(ok=True)

            # largest size photo entry
            file_id = msg["photo"][-1]["file_id"]
            order_id = f"MAN-{user_id}-{int(time.time())}"
            points = amount // RUPEES_PER_POINT

            # insert a pending row into existing 'payments' table
            # reuse 'link_id' to store screenshot file_id (no schema change needed)
            if sb:
                try:
                    res = sb.table("payments").insert({
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "amount": amount,
                        "points": points,
                        "order_id": order_id,
                        "status": "manual_submitted",   # pending owner review
                        "link_id": file_id,             # store screenshot file_id here
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }).execute()
                    row = (res.data or [{}])[0]
                    pid = row.get("id")
                except Exception as e:
                    log.exception("Insert manual payment failed: %s", e)
                    pid = None
            else:
                pid = None

            db_clear_session(user_id)
            send_message(
                chat_id,
                "🧾 Thanks! Your screenshot has been submitted for review.\n"
                "⏳ You’ll be notified after approval.",
                reply_markup=keyboard_for(user_id)
            )

            # notify owner if set
            if OWNER_ID:
                try:
                    pid_txt = f"#{pid}" if pid else order_id
                    send_message(
                        int(OWNER_ID),
                        f"🆕 Manual deposit pending {pid_txt}\n"
                        f"• User: <code>{user_id}</code>\n"
                        f"• Amount: ₹{amount} → +{points} pts\n"
                        f"• Order: {order_id}",
                        parse_mode="HTML"
                    )
                    send_photo(int(OWNER_ID), file_id, caption=f"Manual deposit proof {pid_txt}")
                except Exception as e:
                    log.warning("Notify owner failed: %s", e)

            return jsonify(ok=True)

        # --- now handle text messages ---
        text = (msg.get("text") or "").strip()

        # ignore groups/channels
        if chat_type != "private":
            log.info("Ignored non-private chat: %s", chat_type)
            return jsonify(ok=True)

        # Map bottom keyboard button presses to commands
        mapping = {
            "🏠 Home": "/start",
            "🏠 Home": "/home",
            "ℹ️ Help": "/help",
            "📊 Live Stats": "/stats",
            "📢 Broadcast": "/broadcast",
            "👑 List Admins": "/list_admins",
            "➕ Add Admin": "/add_admin",
            "💳 Deposit Points": "/deposit",
            "➖ Remove Admin": "/remove_admin",
            "📱 Number Info": "/numberinfo",
            "💰 My Balance": "/balance",
            "💎 Add Points to User": "/add_points",
            "🎁 Refer & Earn": "/refer",
        }

        if text in mapping:
            text = mapping[text]

        # check if admin session is waiting for input OR number-entry mode
        sess = db_get_session(user_id)
        if sess:
            action = sess.get("action")

            if action in ("await_add_points_user", "await_add_points_value"):
                handle_add_points_process(chat_id, user_id, text)
                return jsonify(ok=True)

            # ----- Broadcast pending -----
            if action == "broadcast_wait_message" and db_is_admin(user_id):
                db_clear_session(user_id)
                run_broadcast(user_id, chat_id, msg)
                return jsonify(ok=True)

            # ----- Add/Remove Admin pending -----
            if action == "add_admin_wait_id" and db_is_admin(user_id):
                if text.isdigit():
                    uid = int(text)
                    ok = db_mark_admin(uid, True)
                    if ok:
                        send_message(chat_id, f"✅ Promoted {uid} to admin.", reply_markup=keyboard_for(user_id))
                    else:
                        send_message(chat_id, "❌ Failed to promote.", reply_markup=keyboard_for(user_id))
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.", reply_markup=keyboard_for(user_id))
                db_clear_session(user_id)
                return jsonify(ok=True)

            if action == "remove_admin_wait_id" and db_is_admin(user_id):
                if text.isdigit():
                    uid = int(text)
                    ok = db_mark_admin(uid, False)
                    if ok:
                        send_message(chat_id, f"✅ Removed admin {uid}.", reply_markup=keyboard_for(user_id))
                    else:
                        send_message(chat_id, "❌ Failed to remove.", reply_markup=keyboard_for(user_id))
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.", reply_markup=keyboard_for(user_id))
                db_clear_session(user_id)
                return jsonify(ok=True)

            if action == "await_number":
                mapped_buttons = {"🏠 Home": "/start", "ℹ️ Help": "/help"}
                if text in mapped_buttons or text.startswith("/"):
                    db_clear_session(user_id)
                    cmd = mapped_buttons.get(text, text)
                    if cmd == "/start":
                        handle_start(chat_id, user_id)
                    elif cmd == "/help":
                        handle_help(chat_id, user_id)
                    return jsonify(ok=True)

                num = "".join(ch for ch in text if ch.isdigit())
                if len(num) != 10:
                    send_message(
                        chat_id,
                        "❌ Only 10-digit numbers allowed.\n"
                        "✅ Example: 9235895648\n\n"
                        "कृपया केवल 10 अंकों का नंबर भेजें।\n"
                        "उदाहरण: 9235895648",
                        reply_markup=keyboard_for(user_id),
                    )
                    return jsonify(ok=True)

                db_clear_session(user_id)
                handle_num(chat_id, num, user_id)
                return jsonify(ok=True)

        # membership gating
        if text.startswith("/"):
            cmd = text.split()[0].lower()
            if cmd not in ("/start", "/help"):
                if not check_membership_and_prompt(chat_id, user_id):
                    return jsonify(ok=True)

        # command routing
        if text.startswith("/start"):
            handle_start(chat_id, user_id)
        elif text.startswith("/balance"):
            handle_balance(chat_id, user_id)
        elif text.startswith("/add_points"):
            handle_add_points_start(chat_id, user_id)
        elif text.startswith("/deposit"):
            handle_deposit(chat_id, user_id)
        elif text.startswith("/refer"):
            handle_refer(chat_id, user_id)
        elif text.startswith("/help"):
            handle_help(chat_id, user_id)
        elif text.startswith("/home"):
            handle_home(chat_id, user_id)
        elif text.startswith("/stats"):
            handle_stats(chat_id, user_id)
        elif text.startswith("/list_admins"):
            handle_list_admins(chat_id, user_id)
        elif text.startswith("/add_admin"):
            handle_add_admin(chat_id, user_id)
        elif text.startswith("/remove_admin"):
            handle_remove_admin(chat_id, user_id)
        elif text.startswith("/broadcast"):
            handle_broadcast(chat_id, user_id)
        elif text.startswith("/numberinfo"):
            handle_numberinfo(chat_id, user_id)
        elif text.startswith("/num"):
            parts = text.split()
            if len(parts) < 2:
                send_message(
                    chat_id,
                    "Usage: /num <10-digit-number>\nExample: /num 9235895648",
                    reply_markup=keyboard_for(user_id),
                )
            else:
                handle_num(chat_id, parts[1], user_id)
        else:
            if not check_membership_and_prompt(chat_id, user_id):
                return jsonify(ok=True)
            send_message(chat_id, "Use the 📱 Number Info button or type /help.", reply_markup=keyboard_for(user_id))

        return jsonify(ok=True)

    # ----- Handle callbacks (only join retry) -----
    # ----- Handle callbacks (only join retry) -----






    # ----- Handle callback queries -----

    # --- Generic callbacks
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        user_id = cb["from"]["id"]
        callback_id = cb["id"]
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        # --- Generic callbacks ---
        if data == "try_again":
            answer_callback(callback_id, text="Rechecking your join status...")
            if check_membership_and_prompt(chat_id, user_id):
                handle_home(chat_id, user_id)
            return jsonify(ok=True)

        elif data == "balance_refresh":
            pts = db_get_points(user_id)
            msg = (
                f"💰 *Your Current Balance*\n\n"
                f"🏅 Points: *{pts}*\n\n"
                f"Use /deposit to add more or /refer to earn free points!"
            )
            answer_callback(callback_id, text="Balance updated!", show_alert=False)
            send_message(chat_id, msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id))
            return jsonify(ok=True)

        elif data == "home_num":
            handle_numberinfo(chat_id, user_id)
            return jsonify(ok=True)

        elif data == "home_balance":
            handle_balance(chat_id, user_id)
            return jsonify(ok=True)

        elif data == "home_refer":
            handle_refer(chat_id, user_id)
            return jsonify(ok=True)

        elif data == "home_deposit":
            handle_deposit(chat_id, user_id)
            return jsonify(ok=True)

        elif data == "home_help":
            handle_help(chat_id, user_id)
            return jsonify(ok=True)

        # --- Referral related ---
        elif data.startswith("copy_link_"):
            answer_callback(callback_id, text="✅ Link copied! Share it with your friends.", show_alert=True)
            return jsonify(ok=True)


        # --- Owner approve manual ---
        elif data.startswith("approve_"):
            if role_for(user_id) != "owner":
                answer_callback(callback_id, "Not allowed.", show_alert=True)
                return jsonify(ok=True)
            try:
                pid = int(data.split("_", 1)[1])
            except Exception:
                answer_callback(callback_id, "Invalid ID.", show_alert=True)
                return jsonify(ok=True)

            if not sb:
                answer_callback(callback_id, "DB unavailable.", show_alert=True)
                return jsonify(ok=True)

            try:
                # fetch row
                res = sb.table("payments").select("*").eq("id", pid).limit(1).execute()
                row = (res.data or [None])[0]
                if not row:
                    answer_callback(callback_id, "Not found.", show_alert=True)
                    return jsonify(ok=True)

                if row.get("status") != "manual_submitted":
                    answer_callback(callback_id, "Already processed.", show_alert=True)
                    return jsonify(ok=True)

                uid = row["user_id"]
                pts = int(row["points"])

                # add points
                db_add_points(uid, pts)
                # update status
                sb.table("payments").update({"status": "manual_approved"}).eq("id", pid).execute()

                # notify user
                try:
                    send_message(uid, f"🎉 Manual deposit approved! +{pts} points added.", parse_mode="Markdown")
                except Exception as e:
                    log.warning("Notify user approve failed: %s", e)

                answer_callback(callback_id, "Approved ✅", show_alert=False)
                send_message(chat_id, f"✅ Approved deposit #{pid}.", reply_markup=keyboard_for(user_id))
            except Exception as e:
                log.exception("Approve failed: %s", e)
                answer_callback(callback_id, "Error approving.", show_alert=True)
            return jsonify(ok=True)

        # --- Owner reject manual ---
        elif data.startswith("reject_"):
            if role_for(user_id) != "owner":
                answer_callback(callback_id, "Not allowed.", show_alert=True)
                return jsonify(ok=True)
            try:
                pid = int(data.split("_", 1)[1])
            except Exception:
                answer_callback(callback_id, "Invalid ID.", show_alert=True)
                return jsonify(ok=True)

            if not sb:
                answer_callback(callback_id, "DB unavailable.", show_alert=True)
                return jsonify(ok=True)

            try:
                res = sb.table("payments").select("*").eq("id", pid).limit(1).execute()
                row = (res.data or [None])[0]
                if not row:
                    answer_callback(callback_id, "Not found.", show_alert=True)
                    return jsonify(ok=True)

                if row.get("status") != "manual_submitted":
                    answer_callback(callback_id, "Already processed.", show_alert=True)
                    return jsonify(ok=True)

                uid = row["user_id"]

                sb.table("payments").update({"status": "manual_rejected"}).eq("id", pid).execute()

                try:
                    send_message(uid, "❌ Manual deposit rejected. Please contact support if you believe this is a mistake. @GodAlexMM")
                except Exception as e:
                    log.warning("Notify user reject failed: %s", e)

                answer_callback(callback_id, "Rejected ❌", show_alert=False)
                send_message(chat_id, f"❌ Rejected deposit #{pid}.", reply_markup=keyboard_for(user_id))
            except Exception as e:
                log.exception("Reject failed: %s", e)
                answer_callback(callback_id, "Error rejecting.", show_alert=True)
            return jsonify(ok=True)



        elif data.startswith("my_refs_"):
            try:
                res = sb.table("referrals").select("*").eq("referrer_id", user_id).execute()
                refs = res.data or []
                total = len(refs)
                completed = len([r for r in refs if r.get("status") in ("joined", "completed")])
                pending = total - completed
                msg = (
                    f"🎯 *My Referrals*\n\n"
                    f"👥 Total Invited: *{total}*\n"
                    f"✅ Joined: *{completed}*\n"
                    f"🕓 Pending: *{pending}*\n\n"
                    f"💰 Earned approx: *{completed * 2} points!*"
                )
                send_message(chat_id, msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id))
            except Exception as e:
                log.exception("Failed to fetch referrals: %s", e)
                send_message(chat_id, "⚠️ Unable to fetch referral data. Try again later.")
            return jsonify(ok=True)



      

        # --- Manual deposit: choose amount ---
  

        # --- Manual deposit: choose amount ---
        elif data.startswith("manual_"):
            try:
                amount = int(data.split("_", 1)[1])
            except Exception:
                answer_callback(callback_id, "Invalid amount.", show_alert=True)
                return jsonify(ok=True)  # only return early if invalid

            # ₹10 = 1 point (example) → points are amount // RUPEES_PER_POINT
            points = amount // RUPEES_PER_POINT

            # remember we're waiting for a screenshot for this amount
            db_set_session(user_id, "await_manual_screenshot", {"amount": amount})

            caption = (
                f"💳Deposit Details\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"💰 Amount to Pay:₹{amount}\n"
                f"🏅 You’ll Receive:{points} points\n\n"
                f"📱 Pay to this UPI ID:\n{UPI_ID}\n\n"
                f"🧾 After payment, upload your payment screenshot here.\n"
                f"Make sure the transaction ID is visible."
            )

            # Try sending QR as photo (URL is fine). If that fails, send plain text.
            try:
                res = send_photo(chat_id, QR_IMAGE_URL, caption=caption)
                if not res.get("ok"):
                    send_message(chat_id, caption, parse_mode="HTML")
            except Exception:
                send_message(chat_id, caption, parse_mode="HTML")

            answer_callback(callback_id, text="UPI details sent!")
            return jsonify(ok=True)
# ---------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------
def handle_start(chat_id: int, user_id: int) -> None:
    # Step 1: membership gate
    if not check_membership_and_prompt(chat_id, user_id):
        return

    # Step 2: parse referral param (if any)
    referred_by = None
    try:
        text = request.get_json(force=True).get("message", {}).get("text", "") or ""
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            referred_by = int(parts[1])
    except Exception:
        referred_by = None

    # Step 3: always try to init points (only inserts if user not in points table)
    db_init_points_if_new(user_id, referred_by)

    # Step 4: Create referral record only if new (prevent duplicates)
    if referred_by and referred_by != user_id and sb:
        try:
            existing = (
                sb.table("referrals")
                .select("id, status")
                .eq("referrer_id", referred_by)
                .eq("referred_id", user_id)
                .limit(1)
                .execute()
            )

            if not existing.data:
                sb.table("referrals").insert({
                    "referrer_id": referred_by,
                    "referred_id": user_id,
                    "status": "pending"
                }).execute()
                log.info("Referral recorded: %s referred %s", referred_by, user_id)
            else:
                log.info("Referral already exists, skipping duplicate.")
        except Exception as e:
            log.warning("Referral insert failed: %s", e)

    # Step 5: If user has now joined both channels, complete pending referral and reward
    try:
        if sb:
            res = (
                sb.table("referrals")
                .select("id, referrer_id")
                .eq("referred_id", user_id)
                .eq("status", "pending")
                .execute()
            )
            for ref in res.data or []:
                sb.table("referrals").update({"status": "completed"}).eq("id", ref["id"]).execute()
                referrer = ref["referrer_id"]
                db_add_points(referrer, 2)
                db_add_points(user_id, 2)
                send_message(referrer, "🎉 Your referral joined both channels! +2 points added.")
                send_message(user_id, "🎁 You earned +2 welcome points for joining! 🎉")
    except Exception as e:
        log.warning("Referral completion check failed: %s", e)

    # Step 6: Welcome message
    first_name = "Buddy"
    welcome = (
        f"👋 Hello {first_name}!\n"
        "Welcome to *Our Number Info Bot!* 🤖\n\n"
        "Tap *📱 Number Info* to search a number, or type /help.\n"
        "📘 बोट का उपयोग करने के लिए *📱 Number Info* दबाएं या /help लिखें।"
    )
    send_message(chat_id, welcome, parse_mode="Markdown", reply_markup=keyboard_for(user_id))

def handle_review_manual(chat_id: int, user_id: int):
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can review deposits.", reply_markup=keyboard_for(user_id))
        return

    if not sb:
        send_message(chat_id, "⚠️ Supabase not available.")
        return

    try:
        res = (
            sb.table("payments")
            .select("*")
            .eq("status", "manual_submitted")
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        log.exception("Fetch manual_submitted failed: %s", e)
        rows = []

    if not rows:
        send_message(chat_id, "📭 No pending manual deposits.", reply_markup=keyboard_for(user_id))
        return

    for r in rows:
        pid = r.get("id")
        uid = r.get("user_id")
        amt = r.get("amount")
        pts = r.get("points")
        oid = r.get("order_id")
        ss  = r.get("link_id")  # screenshot file_id stored here

        cap = (
            f"🧾 <b>Pending Manual Deposit</b>\n"
            f"• ID: <code>{pid}</code>\n"
            f"• User: <code>{uid}</code>\n"
            f"• Amount: ₹{amt}\n"
            f"• Points: +{pts}\n"
            f"• Order: {oid}\n\n"
            f"Approve or reject below."
        )

        kb = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve_{pid}"},
                    {"text": "❌ Reject",  "callback_data": f"reject_{pid}"},
                ]
            ]
        }

        # try sending screenshot too
        try:
            if ss:
                send_photo(chat_id, ss, caption=cap, reply_markup=kb)
            else:
                send_message(chat_id, cap, parse_mode="HTML", reply_markup=kb)
        except Exception:
            send_message(chat_id, cap, parse_mode="HTML", reply_markup=kb)

def handle_help(chat_id: int, user_id: Optional[int] = None) -> None:
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return

    bot_username = "OfficialBlackEyeBot"  # 🟢 your bot username
    owner_contact = "@GodAlexMM"          # 🟢 your Telegram handle

    help_text = (
        "📘 <b>Help & Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 <b>Quick Guide:</b>\n"
        "• Tap <b>📱 Number Info</b> → Send any <code>10-digit</code> Indian number.\n"
        "• Each search costs <b>1 point</b>.\n"
        "• Earn <b>+2 points</b> per referral via <b>🎁 Refer</b>.\n"
        "• Add more points with <b>💳 Deposit</b>.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 <b>Need Help?</b>\n"
        f"Contact: {owner_contact}\n"
        f"Bot: <a href='https://t.me/{bot_username}'>@{bot_username}</a>\n\n"
        "❤️ <i>Developed by God Alex — stay awesome!</i>\n"
        "🌐 <i>Fast • Secure • Reliable</i>"
    )

    inline_buttons = {
        "inline_keyboard": [
            [
                {"text": "📱 Try Number Info", "callback_data": "home_num"},
                {"text": "💰 Check Balance", "callback_data": "home_balance"},
            ],
            [
                {"text": "🎁 Refer Now", "callback_data": "home_refer"},
                {"text": "💳 Deposit Points", "callback_data": "home_deposit"},
            ],
            [
                {"text": "🏠 Back to Home", "callback_data": "try_again"}
            ]
        ]
    }

    send_message(chat_id, help_text, parse_mode="HTML", reply_markup=inline_buttons)

def handle_balance(chat_id: int, user_id: int):
    """Show fancy balance screen with progress bar and referral info."""
    pts = db_get_points(user_id)

    # Progress bar (out of 20 points = full)
    total_bar = 20
    filled = int((pts / total_bar) * 10)
    filled = min(filled, 10)
    bar = "🟩" * filled + "⬜️" * (10 - filled)

    # Get referrals count (optional if you have 'referrals' table)
    ref_count = 0
    try:
        if sb:
            res = sb.table("referrals").select("id").eq("referrer_id", user_id).execute()
            ref_count = len(res.data or [])
    except Exception:
        ref_count = 0

    msg = (
        f"💰 *My Balance*\n\n"
        f"🏅 Points: *{pts}*\n"
        f"{bar}\n\n"
        f"📞 Searches left: *{pts}*\n"
        f"👥 Referrals: *{ref_count}*\n\n"
        f"⚡ Each search costs *1 point*\n"
        f"🎁 Earn +2 points per referral using /refer\n"
        f"💳 Tap On Deposit Points To Get More Points "
    )

    send_message(chat_id, msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id))





def handle_home(chat_id: int, user_id: int):
    if not check_membership_and_prompt(chat_id, user_id):
        return

    pts = db_get_points(user_id)
    # You can treat 'total' as a soft milestone to visualize progress
    milestone = 100  # change to 20/50/100 if you prefer a different target
    bar = _progress_bar(pts, milestone)

    # Small dynamic tip (rotates by simple modulo)
    tips = [
        "💡 Tip: Tap <b>📱 Number Info</b> to start a fresh lookup.",
        "💡 Tip: Earn <b>+2 points</b> per referral via <b>🎁 Refer</b>.",
        "💡 Tip: Each search costs <b>1 point</b> — keep an eye on balance!",
        "💡 Tip: Use <b>🔁 Refresh</b> to update your points instantly.",
    ]
    tip = tips[pts % len(tips)]

    # Clean, bilingual, HTML-styled card
    msg = (
        "🏠 <b>Home</b>\n"
        f"———————————————\n"
        f"💰 <b>Points:</b> <code>{pts}</code>\n"
        f"🏁 <b>Progress:</b> {bar}  <i>{min(pts, milestone)}/{milestone}</i>\n"
        f"🔎 <b>Searches Left:</b> <code>{pts}</code>\n"
        "———————————————\n"
        f"{tip}\n\n"
        "🇮🇳 <b>हिंदी:</b> <i>पॉइंट्स बढ़ाने के लिए रेफ़रल करें या डिपॉज़िट करें।</i>\n"
        "🇬🇧 <b>English:</b> <i>Use Refer or Deposit to boost your balance.</i>"
    )

    # Inline quick actions (callbacks handled below)
    inline = {
        "inline_keyboard": [
            [
                {"text": "📱 Number Info", "callback_data": "home_num"},
                {"text": "💰 Balance", "callback_data": "home_balance"},
            ],
            [
                {"text": "🎁 Refer", "callback_data": "home_refer"},
                {"text": "💳 Deposit", "callback_data": "home_deposit"},
            ],
            [
                {"text": "ℹ️ Help", "callback_data": "home_help"},
                {"text": "🔁 Refresh", "callback_data": "balance_refresh"},
            ],
        ]
    }

    # Send as HTML (keeps monospace/strong/italics crisp)
    send_message(chat_id, msg, parse_mode="HTML", reply_markup=inline)

def handle_add_points_start(chat_id: int, user_id: int):
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can add points.", reply_markup=keyboard_for(user_id))
        return
    db_set_session(user_id, "await_add_points_user")
    send_message(chat_id, "💎 Send the *user_id* to whom you want to add points:", parse_mode="Markdown")


def handle_add_points_process(chat_id: int, owner_id: int, text: str):
    sess = db_get_session(owner_id)
    if not sess:
        return

    action = sess.get("action")
    payload = sess.get("payload", {})

    # Step 1: expect user_id
    if action == "await_add_points_user":
        if not text.isdigit():
            send_message(chat_id, "❌ Please send a valid numeric user_id.")
            return
        db_set_session(owner_id, "await_add_points_value", {"target_user": int(text)})
        send_message(chat_id, "✅ User ID received.\nNow send the *number of points* to add:", parse_mode="Markdown")
        return

    # Step 2: expect amount
    if action == "await_add_points_value":
        if not text.isdigit():
            send_message(chat_id, "❌ Please send a valid number.")
            return
        points = int(text)
        target_user = payload.get("target_user")
        if not target_user:
            send_message(chat_id, "⚠️ Missing target user, start again with /add_points.")
            db_clear_session(owner_id)
            return

        db_add_points(target_user, points)
        send_message(chat_id, f"✅ Added *{points} points* to user `{target_user}`.", parse_mode="Markdown")
        send_message(target_user, f"💎 You have received *+{points} points!* from the owner 🎉", parse_mode="Markdown")
        db_clear_session(owner_id)
        return





def handle_refer(chat_id: int, user_id: int):
    """Fancy referral card with share/copy buttons."""
    bot_username = "OfficialBlackEyeBot"  # 🟢 Replace this with your real bot username (without @)
    link = f"https://t.me/{bot_username}?start={user_id}"

    msg = (
        "🎁 *Refer & Earn Points!* 🎁\n\n"
        "💡 Invite your friends to use this bot and earn *+2 points* per referral.\n\n"
        "📱 When your friend joins both channels and starts the bot, "
        "you both get rewarded automatically!\n\n"
        "🔗 *Your Referral Link:*\n"
        f"`{link}`\n\n"
        "👇 Share it now and grow your balance!"
    )

    inline_buttons = {
        "inline_keyboard": [
            [
                {"text": "📋 Copy Link", "callback_data": f"copy_link_{user_id}"},
                {"text": "📤 Share to Friends", "url": f"https://t.me/share/url?url={link}&text=🎁%20Join%20this%20NumberInfo%20Bot%20and%20get%20Free%20Points!"},
            ],
            [
                {"text": "🎯 My Referrals", "callback_data": f"my_refs_{user_id}"}
            ]
        ]
    }
    send_message(chat_id, msg, parse_mode="Markdown", reply_markup=inline_buttons)


def handle_stats(chat_id: int, user_id: int) -> None:
    if role_for(user_id) not in ("owner", "admin"):
        send_message(chat_id, "❌ Not authorized.", reply_markup=keyboard_for(user_id))
        return
    total, today = db_stats_counts()
    txt = (
        "📊 *Live Stats*\n\n"
        f"• Total Users: *{total}*\n"
        f"• Active Today: *{today}*"
    )
    send_message(chat_id, txt, parse_mode="Markdown", reply_markup=keyboard_for(user_id))

# ---- CLEANED UP DEPOSIT FLOW ----
def handle_deposit(chat_id: int, user_id: int):
    """
    Clean deposit flow:
    1️⃣ Shows amount options.
    2️⃣ On click -> shows exact payable amount, points, QR & UPI.
    """
    buttons = [
        [
            {"text": f"₹{amt}", "callback_data": f"manual_{amt}"}
        ]
        for amt in MANUAL_AMOUNTS
    ]

    msg = (
    "💳 <b>Deposit Points</b>\n"
    "━━━━━━━━━━━━━━━\n\n"
    "Select the amount you wish to deposit 👇\n\n"
    f"Conversion Rate: <b>₹{RUPEES_PER_POINT} = 1 Point</b>\n"
    f"Example: ₹{RUPEES_PER_POINT * 10} → 10 Points\n\n"
    "After payment, upload your screenshot proof here."
)


    send_message(chat_id, msg, parse_mode="HTML", reply_markup={"inline_keyboard": buttons})



def handle_list_admins(chat_id: int, user_id: int) -> None:
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can list admins.", reply_markup=keyboard_for(user_id))
        return
    admins = db_list_admins()
    if not admins:
        send_message(chat_id, "No admins yet.", reply_markup=keyboard_for(user_id))
    else:
        lines = []
        for a in admins:
            nm = a.get("first_name") or ""
            un = a.get("username")
            if un:
                nm = f"{nm} (@{un})"
            lines.append(f"• {nm} — `{a['id']}`")
        send_message(chat_id, "👑 *Admins:*\n" + "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard_for(user_id))


def handle_add_admin(chat_id: int, user_id: int) -> None:
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can add admins.", reply_markup=keyboard_for(user_id))
        return
    db_set_session(user_id, "add_admin_wait_id")
    send_message(chat_id, "👑 Send the Telegram *user_id* to promote as admin:", parse_mode="Markdown", reply_markup=keyboard_for(user_id))


def handle_remove_admin(chat_id: int, user_id: int) -> None:
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can remove admins.", reply_markup=keyboard_for(user_id))
        return
    db_set_session(user_id, "remove_admin_wait_id")
    send_message(chat_id, "🗑️ Send the Telegram *user_id* to remove from admin:", parse_mode="Markdown", reply_markup=keyboard_for(user_id))


def handle_broadcast(chat_id: int, user_id: int) -> None:
    if role_for(user_id) not in ("owner", "admin"):
        send_message(chat_id, "❌ Only owner/admin can broadcast.", reply_markup=keyboard_for(user_id))
        return
    db_set_session(user_id, "broadcast_wait_message")
    send_message(
        chat_id,
        "📣 Send the message you want to broadcast to all users.\n"
        "• Text: just send text\n"
        "• Photo/Video/Document: send the media (with optional caption)\n",
        reply_markup=keyboard_for(user_id)
    )


def handle_numberinfo(chat_id: int, user_id: int) -> None:
    """Elegant bilingual prompt for number lookup."""
    if not check_membership_and_prompt(chat_id, user_id):
        return

    db_set_session(user_id, "await_number")

    msg = (
        "📱 <b>Number Info Lookup</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧮 <b>Enter any 10-digit Indian mobile number</b> (without +91).\n"
        "💡 Example: <code>9235895648</code>\n\n"
        "🇮🇳 <b>हिंदी:</b> कृपया <b>+91 के बिना</b> कोई भी <b>10 अंकों का मोबाइल नंबर</b> भेजें।\n"
        "💡 उदाहरण: <code>9235895648</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔎 <i>We’ll fetch detailed info instantly once you send the number.</i>"
    )

    inline_buttons = {
        "inline_keyboard": [
            [
                {"text": "🏠 Back to Home", "callback_data": "try_again"},
                {"text": "ℹ️ Help", "callback_data": "home_help"}
            ]
        ]
    }

    send_message(chat_id, msg, parse_mode="HTML", reply_markup=inline_buttons)

def handle_payments(chat_id: int, user_id: int):
    if not sb:
        send_message(chat_id, "⚠️ Payments history not available.")
        return
    res = sb.table("payments").select("*").eq("user_id", user_id).order("id", desc=True).limit(5).execute()
    if not res.data:
        send_message(chat_id, "📭 No payments yet.")
        return
    lines = [f"₹{r['amount']} → +{r['points']} pts — *{r['status'].capitalize()}*" for r in res.data]
    send_message(chat_id, "💳 *Recent Deposits:*\n\n" + "\n".join(lines), parse_mode="Markdown")

def handle_num(chat_id: int, number: str, user_id: Optional[int] = None) -> None:
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return

    # Normalize: extract digits only
    number = "".join(ch for ch in number if ch.isdigit())

    if not number.isdigit() or len(number) != 10:
        send_message(
            chat_id,
            "❌ Only 10-digit numbers allowed. Example: 9235895648\n"
            "कृपया केवल 10 अंकों का नंबर भेजें। उदाहरण: 9235895648",
            reply_markup=keyboard_for(user_id or 0),
        )
        return 
    # ✅ Step: Check balance before search
    if user_id:
        pts = db_get_points(user_id)
        if pts <= 0:
            msg = (
                "⚠️ *You have 0 points left!* ⚠️\n\n"
                "💡 Each number search costs *1 point*.\n"
                "🎁 Use /refer to invite friends and earn *+2 points* each!\n"
                "💳 Tap On Deposit Points To Get More Points "
            )
            send_message(chat_id, msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id))
            return

     # Step 1: Send initial message safely
    # Do not attach reply_markup to make the message editable
    init_resp = send_message(
        chat_id,
        "🔍 Searching number info… Please wait"
    )




    # Safer extraction of message_id
    message_id = init_resp.get("result", {}).get("message_id") if init_resp and init_resp.get("ok") else None

  # Step 2: Update progress (FAST — fewer, bigger jumps)
    if message_id:
        # small delay before first edit to avoid Telegram edit race
        time.sleep(0.3)
        # faster and simpler steps
        steps = [22, 44, 66, 88, 100]
        for p in steps:
            try:
                time.sleep(0.35)  # faster animation
                resp = edit_message(
                    chat_id,
                    message_id,
                    f"🔍 Searching number info… {p}%"
                )
                if not resp.get("ok"):
                    log.warning("editMessage failed at %d%%: %s", p, resp.get("error"))
            except Exception as e:
                log.warning("edit progress failed at %d%%: %s", p, e)
        edit_message(chat_id, message_id, "✅ Search complete! Here's your result ↓")
    else:
        send_message(chat_id, "🔍 Searching number info…", reply_markup=keyboard_for(user_id or 0))



    # Step 3: Fetch data from API
    api_url = f"https://yahu.site/api/?number={number}&key=The_ajay"
    try:
        r = session.get(api_url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        # Step 4: Handle empty data
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) == 0:
            if message_id:
                edit_message(chat_id, message_id, "✅ Search complete! Here's your result ↓")
            bilingual_msg = (
                "⚠️ *Number Data Not Available !!!*\n"
                "⚠️ *नंबर का डेटा उपलब्ध नहीं है !!!*"
            )
            send_message(chat_id, bilingual_msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id or 0))
            return

        # Step 5: Show formatted result (truncate if needed)
        pretty_json = json.dumps(data, indent=2, ensure_ascii=False)
        if len(pretty_json) > 3800:
            pretty_json = pretty_json[:3800] + "\n\n[truncated due to size limit]"

        if message_id:
            edit_message(chat_id, message_id, "✅ Search complete! Here's your result ↓")
        send_message(chat_id, f"<pre>{pretty_json}</pre>", parse_mode="HTML", reply_markup=keyboard_for(user_id or 0))
          
     # ✅ Deduct 1 point after successful lookup
        if user_id:
            db_add_points(user_id, -1)

    except Exception as e:
        log.exception("API fetch failed: %s", e)
        if message_id:
            edit_message(chat_id, message_id, "⚠️ Failed to fetch data. Try again later.")
        else:
            send_message(chat_id, "⚠️ Failed to fetch data. Try again later.")

# ---------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------
def run_broadcast(admin_user_id: int, chat_id: int, message_obj: Dict[str, Any]) -> None:
    if role_for(admin_user_id) not in ("owner", "admin"):
        send_message(chat_id, "❌ Not authorized.", reply_markup=keyboard_for(admin_user_id))
        return

    user_ids = db_all_user_ids()
    total = len(user_ids)
    success = 0
    failed = 0
    send_message(chat_id, f"📣 Broadcast started to {total} users...", reply_markup=keyboard_for(admin_user_id))

    text = message_obj.get("text")
    photo = message_obj.get("photo")
    video = message_obj.get("video")
    document = message_obj.get("document")
    caption = message_obj.get("caption", "")

    for uid in user_ids:
        try:
            res: Dict[str, Any]
            if photo:
                file_id = photo[-1]["file_id"]
                res = send_photo(uid, file_id, caption=caption)
            elif video:
                file_id = video["file_id"]
                res = send_video(uid, file_id, caption=caption)
            elif document:
                file_id = document["file_id"]
                res = send_document(uid, file_id, caption=caption)
            elif text:
                res = send_message(uid, text)
            else:
                res = {"ok": False, "error": "no content"}

            if res.get("ok"):
                success += 1
            else:
                failed += 1
        except Exception as e:
            log.warning("broadcast send failed to %s: %s", uid, e)
            failed += 1
        time.sleep(0.03)  # avoid hitting flood limits

    kind = "photo" if photo else "video" if video else "document" if document else "text"
    db_log_broadcast(f"{kind} broadcast", total, success, failed)
    send_message(
        chat_id,
        f"✅ Broadcast complete!\nTotal: {total}\nDelivered: {success}\nFailed: {failed}",
        reply_markup=keyboard_for(admin_user_id),
    )

# ---------------------------------------------------------------------
# Webhook setup & Keepalive
# ---------------------------------------------------------------------
@app.route("/set_webhook", methods=["GET"])
def set_webhook() -> Any:
    url = WEBHOOK_URL
    if not url:
        return jsonify(ok=False, error="WEBHOOK_URL not set"), 400
    r = session.get(f"{TELEGRAM_API}/setWebhook", params={"url": url}, timeout=10)
    try:
        return jsonify(r.json())
    except Exception:
        return jsonify(ok=False, status=r.status_code, text=r.text), r.status_code


def auto_ping() -> None:
    """
    Periodically ping SELF_URL to keep the Render dyno warm.
    Logs only at DEBUG to avoid spam.

    Disable by setting DISABLE_PING=1 in env.
    """
    interval = int(os.getenv("PING_INTERVAL_SECONDS", "300"))
    ping_url = (SELF_URL.rstrip("/") + "/") if SELF_URL else None
    if not ping_url:
        log.warning("auto_ping disabled: SELF_URL empty")
        return

    while True:
        try:
            session.get(ping_url, timeout=5)
            log.debug("Auto-pinged %s", ping_url)
        except Exception as e:
            log.warning("Auto-ping failed: %s", e)
        time.sleep(interval)

# Start keepalive thread only if enabled (and avoid double start under gunicorn preload)
if os.getenv("DISABLE_PING", "").strip() not in ("1", "true", "True"):
    try:
        threading.Thread(target=auto_ping, daemon=True).start()
        log.info("Keepalive ping thread started.")
    except Exception as e:
        log.warning("Failed to start keepalive thread: %s", e)
else:
    log.info("Keepalive ping thread disabled by env.")


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook() -> Any:
    try:
        update = request.get_json(force=True, silent=True)
        if not update:
            return jsonify(ok=False, error="no update")
        log.info("Incoming update keys: %s", list(update.keys()))
        ...
        # (your existing logic here)
        return jsonify(ok=True)
    except Exception as e:
        log.exception("Webhook crashed: %s", e)
        return jsonify(ok=False, error=str(e)), 200



# ---------------------------------------------------------------------
# Main (for local dev). On Render/Gunicorn use: gunicorn app:app
# ---------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    # For local dev only; in production use gunicorn
    app.run(host="0.0.0.0", port=port)
