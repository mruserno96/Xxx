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
import razorpay

from datetime import datetime, timezone, date
from typing import Dict, Any, Optional, List, Tuple
from cashfree_pg.api_client import Cashfree
from cashfree_pg.models.create_order_request import CreateOrderRequest
from cashfree_pg.models.customer_details import CustomerDetails
from cashfree_pg.models.order_meta import OrderMeta
from flask import Flask, request, jsonify, abort
import requests
from requests.adapters import HTTPAdapter, Retry

# ----- Supabase -----
try:
    from supabase import create_client, Client  # type: ignore
except Exception:  # pragma: no cover
    create_client = None
    Client = object  # type: ignore

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



CASHFREE_CLIENT_ID = os.getenv("CASHFREE_CLIENT_ID", "")
CASHFREE_CLIENT_SECRET = os.getenv("CASHFREE_CLIENT_SECRET", "")
CASHFREE_ENV = os.getenv("CASHFREE_ENV", "TEST").upper()
CASHFREE_WEBHOOK_SECRET = os.getenv("CASHFREE_WEBHOOK_SECRET", "")

CASHFREE_API_VERSION = None
if CASHFREE_CLIENT_ID and CASHFREE_CLIENT_SECRET:
    # Configure global SDK settings (no object constructor args)
    Cashfree.XClientId = CASHFREE_CLIENT_ID
    Cashfree.XClientSecret = CASHFREE_CLIENT_SECRET
    Cashfree.XEnvironment = Cashfree.SANDBOX if CASHFREE_ENV == "TEST" else Cashfree.PRODUCTION
    CASHFREE_API_VERSION = "2023-08-01"  # per Cashfree docs
else:
    log.warning("Cashfree credentials missing — deposit will be disabled.")


WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default-secret").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
SELF_URL = WEBHOOK_URL.rsplit("/webhook", 1)[0] if "/webhook" in WEBHOOK_URL else (os.getenv("SELF_URL", "").strip() or "https://example.com")

# Channels / Groups gate (set the ones you need)
CHANNEL1_INVITE_LINK = os.getenv("CHANNEL1_INVITE_LINK", "").strip()
CHANNEL1_CHAT_ID = os.getenv("CHANNEL1_CHAT_ID", "").strip()
CHANNEL2_CHAT = os.getenv("CHANNEL2_CHAT_ID_OR_USERNAME", "").strip()

# Admin owner (bootstrap): this user_id is always treated as owner/admin
OWNER_ID = os.getenv("OWNER_ID", "").strip()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE", os.getenv("SUPABASE_ANON_KEY", "")).strip()

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)  # type: ignore
        log.info("✅ Supabase client initialized")
    except Exception as e:
        log.exception("❌ Supabase init failed: %s", e)
else:
    log.warning("⚠️ Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_ROLE/ANON. Persistence disabled.")

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
    if not supabase:
        return False
    try:
        res = supabase.table("users").select("is_admin").eq("id", user_id).limit(1).execute()  # type: ignore
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
    if not supabase:
        return 0
    try:
        res = supabase.table("points").select("points").eq("user_id", user_id).limit(1).execute()
        if res.data:
            return int(res.data[0].get("points", 0))
        return 0
    except Exception as e:
        log.exception("db_get_points failed: %s", e)
        return 0


def db_add_points(user_id: int, amount: int) -> None:
    """Add (or subtract if negative) user points."""
    if not supabase:
        return
    try:
        current = db_get_points(user_id)
        newval = max(current + amount, 0)
        supabase.table("points").upsert({"user_id": user_id, "points": newval}).execute()
    except Exception as e:
        log.exception("db_add_points failed: %s", e)


def db_init_points_if_new(user_id: int, referred_by: Optional[int] = None) -> None:
    """Give 5 points to new user on first start."""
    if not supabase:
        return
    try:
        res = supabase.table("points").select("user_id").eq("user_id", user_id).execute()
        if not res.data:
            supabase.table("points").insert({
                "user_id": user_id,
                "points": 5,
                "referred_by": referred_by
            }).execute()
    except Exception as e:
        log.exception("db_init_points_if_new failed: %s", e)











def db_upsert_user(user: Dict[str, Any]) -> None:
    """Upsert user in 'users' table; user is dict with Telegram fields."""
    if not supabase:
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
        supabase.table("users").upsert(row).execute()  # type: ignore
    except Exception as e:
        log.exception("db_upsert_user failed: %s", e)


def db_mark_admin(user_id: int, is_admin: bool) -> bool:
    if not supabase:
        return False
    try:
        supabase.table("users").upsert({"id": user_id, "is_admin": is_admin}).execute()  # type: ignore
        return True
    except Exception as e:
        log.exception("db_mark_admin failed: %s", e)
        return False


def db_list_admins() -> List[Dict[str, Any]]:
    if not supabase:
        return []
    try:
        res = supabase.table("users").select("id,username,first_name,last_name,is_admin").eq("is_admin", True).execute()  # type: ignore
        return res.data or []  # type: ignore
    except Exception as e:
        log.exception("db_list_admins failed: %s", e)
        return []


def db_all_user_ids() -> List[int]:
    if not supabase:
        return []
    try:
        res = supabase.table("users").select("id").execute()  # type: ignore
        return [row["id"] for row in (res.data or [])]  # type: ignore
    except Exception as e:
        log.exception("db_all_user_ids failed: %s", e)
        return []


def db_set_session(user_id: int, action: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    """Store pending action for admin/user (e.g., broadcast, add_admin, remove_admin, await_number)."""
    if not supabase:
        return
    try:
        supabase.table("sessions").upsert({
            "user_id": user_id,
            "action": action,
            "payload": json.dumps(payload or {})
        }).execute()  # type: ignore
    except Exception as e:
        log.exception("db_set_session failed: %s", e)


def db_get_session(user_id: int) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    try:
        res = supabase.table("sessions").select("*").eq("user_id", user_id).limit(1).execute()  # type: ignore
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
    if not supabase:
        return
    try:
        supabase.table("sessions").delete().eq("user_id", user_id).execute()  # type: ignore
    except Exception as e:
        log.exception("db_clear_session failed: %s", e)


def db_log_broadcast(desc: str, total: int, success: int, failed: int) -> None:
    if not supabase:
        return
    try:
        supabase.table("broadcasts").insert({
            "text": desc,
            "total": total,
            "success": success,
            "failed": failed
        }).execute()  # type: ignore
    except Exception as e:
        log.exception("db_log_broadcast failed: %s", e)


def db_stats_counts() -> Tuple[int, int]:
    """Return total users and today's active users (by last_seen date)."""
    if not supabase:
        return 0, 0
    try:
        res = supabase.table("users").select("id,last_seen").execute()  # type: ignore
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
    """Checks membership; if not, shows inline URLs to join and a Try Again button."""
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
            parse_mode="Markdown"
        )
        return False
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
        supabase=bool(supabase),
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
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        user = msg.get("from", {})
        user_id = user.get("id")
        text = (msg.get("text") or "").strip()
        chat_type = chat.get("type")

        # Ignore group messages
        if chat_type != "private":
            log.info("Ignored non-private chat: %s", chat_type)
            return jsonify(ok=True)

        # Map buttons → commands
        mapping = {
            "🏠 Home": "/home",
            "ℹ️ Help": "/help",
            "📊 Live Stats": "/stats",
            "📢 Broadcast": "/broadcast",
            "👑 List Admins": "/list_admins",
            "➕ Add Admin": "/add_admin",
            "➖ Remove Admin": "/remove_admin",
            "📱 Number Info": "/numberinfo",
            "💰 My Balance": "/balance",
            "💎 Add Points to User": "/add_points",
            "🎁 Refer & Earn": "/refer",
            "💳 Deposit Points": "/deposit",
        }
        if text in mapping:
            text = mapping[text]

        # Handle active session (admin actions, number entry, etc.)
        sess = db_get_session(user_id)
        if sess:
            action = sess.get("action")

            if action in ("await_add_points_user", "await_add_points_value"):
                handle_add_points_process(chat_id, user_id, text)
                return jsonify(ok=True)

            if action == "broadcast_wait_message" and db_is_admin(user_id):
                db_clear_session(user_id)
                run_broadcast(user_id, chat_id, msg)
                return jsonify(ok=True)

            if action == "add_admin_wait_id" and db_is_admin(user_id):
                if text.isdigit():
                    uid = int(text)
                    ok = db_mark_admin(uid, True)
                    send_message(chat_id,
                                 f"✅ Promoted {uid} to admin." if ok else "❌ Failed to promote.",
                                 reply_markup=keyboard_for(user_id))
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.",
                                 reply_markup=keyboard_for(user_id))
                db_clear_session(user_id)
                return jsonify(ok=True)

            if action == "remove_admin_wait_id" and db_is_admin(user_id):
                if text.isdigit():
                    uid = int(text)
                    ok = db_mark_admin(uid, False)
                    send_message(chat_id,
                                 f"✅ Removed admin {uid}." if ok else "❌ Failed to remove.",
                                 reply_markup=keyboard_for(user_id))
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.",
                                 reply_markup=keyboard_for(user_id))
                db_clear_session(user_id)
                return jsonify(ok=True)

            if action == "await_number":
                num = "".join(ch for ch in text if ch.isdigit())
                if len(num) != 10:
                    send_message(chat_id,
                                 "❌ Only 10-digit numbers allowed.\n✅ Example: 9235895648",
                                 reply_markup=keyboard_for(user_id))
                    return jsonify(ok=True)
                db_clear_session(user_id)
                handle_num(chat_id, num, user_id)
                return jsonify(ok=True)

        # Membership check for commands
        if text.startswith("/"):
            cmd = text.split()[0].lower()
            if cmd not in ("/start", "/help") and not check_membership_and_prompt(chat_id, user_id):
                return jsonify(ok=True)

        # ----- Command routing -----
        if text.startswith("/start"):
            handle_start(chat_id, user_id)
        elif text.startswith("/help"):
            handle_help(chat_id, user_id)
        elif text.startswith("/home"):
            handle_home(chat_id, user_id)
        elif text.startswith("/balance"):
            handle_balance(chat_id, user_id)
        elif text.startswith("/add_points"):
            handle_add_points_start(chat_id, user_id)
        elif text.startswith("/deposit"):
            handle_deposit(chat_id, user_id)
        elif text.startswith("/refer"):
            handle_refer(chat_id, user_id)
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
                send_message(chat_id, "Usage: /num <10-digit-number>",
                             reply_markup=keyboard_for(user_id))
            else:
                handle_num(chat_id, parts[1], user_id)
        else:
            send_message(chat_id, "Use the 📱 Number Info button or type /help.",
                         reply_markup=keyboard_for(user_id))
        return jsonify(ok=True)

      # ----- Handle callback queries -----
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        user_id = cb["from"]["id"]
        callback_id = cb["id"]
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        if data == "try_again":
            answer_callback(callback_id, text="Rechecking your join status...")
            if check_membership_and_prompt(chat_id, user_id):
                handle_home(chat_id, user_id)
            return jsonify(ok=True)

        elif data == "balance_refresh":
            pts = db_get_points(user_id)
            msg = f"💰 *Your Balance*\n🏅 Points: *{pts}*"
            send_message(chat_id, msg, parse_mode="Markdown",
                         reply_markup=keyboard_for(user_id))
            return jsonify(ok=True)

        elif data.startswith("copy_link_"):
            answer_callback(callback_id, text="✅ Link copied!", show_alert=True)
            return jsonify(ok=True)

        elif data.startswith("my_refs_"):
            try:
                res = supabase.table("referrals").select("*").eq("referrer_id", user_id).execute()
                refs = res.data or []
                total = len(refs)
                completed = len([r for r in refs if r.get("status") in ("joined", "completed")])
                msg = (
                    f"🎯 *My Referrals*\n\n"
                    f"👥 Total Invited: *{total}*\n"
                    f"✅ Joined: *{completed}*\n"
                    f"🕓 Pending: *{total - completed}*\n"
                )
                send_message(chat_id, msg, parse_mode="Markdown",
                             reply_markup=keyboard_for(user_id))
            except Exception as e:
                log.exception("Failed to fetch referrals: %s", e)
                send_message(chat_id, "⚠️ Unable to fetch referral data.")
            return jsonify(ok=True)

        # ✅ Cashfree Deposit (fixed indentation)
        elif data.startswith("deposit_"):
            amount = int(data.split("_")[1])
            points = amount // 10

            if not CASHFREE_API_VERSION:
                send_message(chat_id, "⚠️ Payment system not configured.")
                return jsonify(ok=True)

            try:
                order_id = f"order_{int(time.time())}_{user_id}"
                customer = CustomerDetails(
                    customer_id=str(user_id),
                    customer_phone="9999999999",
                    customer_name=f"user_{user_id}",
                    customer_email="bot@telegram.com",
                )
                order_meta = OrderMeta(
                    return_url=f"{SELF_URL}/payment-return?order_id={{order_id}}"
                )
                req = CreateOrderRequest(
                    order_id=order_id,
                    order_amount=float(amount),
                    order_currency="INR",
                    customer_details=customer,
                    order_meta=order_meta,
                )

                api = Cashfree()
                api_resp = api.PGCreateOrder(CASHFREE_API_VERSION, req, None, None)

                payment_link = None
                session_id = getattr(api_resp.data, "payment_session_id", None)

                if session_id:
                    payment_link = f"https://payments.cashfree.com/order/#/{session_id}"

                if not payment_link:
                    log.error("Cashfree order failed: %s", api_resp)
                    send_message(chat_id, "⚠️ Payment link not received. Try again later.")
                    return jsonify(ok=True)

                msg = (
                    f"💸 *Deposit Request Initiated!*\n\n"
                    f"💰 Amount: ₹{amount}\n"
                    f"🏅 You’ll earn: +{points} points\n\n"
                    f"🔗 Tap below to complete payment 👇"
                )
                inline_buttons = {
                    "inline_keyboard": [
                        [{"text": "💳 Pay Now", "url": payment_link}],
                        [{"text": "🔁 Refresh Status", "callback_data": f"check_cashfree_{order_id}"}],
                    ]
                }
                send_message(chat_id, msg, parse_mode="Markdown", reply_markup=inline_buttons)

                if supabase:
                    supabase.table("payments").insert({
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "order_id": order_id,
                        "amount": amount,
                        "points": points,
                        "status": "pending",
                    }).execute()

            except Exception as e:
                log.exception("Cashfree order creation failed: %s", e)
                send_message(chat_id, "⚠️ Unable to create payment. Try again later.")
            return jsonify(ok=True)

        elif data.startswith("check_cashfree_"):
            order_id = data.split("_", 2)[2]
            if not CASHFREE_API_VERSION:
                send_message(chat_id, "⚠️ Payment system not configured.")
                return jsonify(ok=True)
            try:
                api = Cashfree()
                api_resp = api.PGFetchOrder(CASHFREE_API_VERSION, order_id, None)
                info = getattr(api_resp, "data", {}) or {}
                status = info.get("order_status")
                amount = info.get("order_amount")

                if status == "PAID":
                    amount_int = int(float(amount))
                    points = amount_int // 10
                    db_add_points(user_id, points)
                    send_message(chat_id,
                                 f"✅ Payment of ₹{amount_int} confirmed!\n🎯 +{points} points credited.",
                                 parse_mode="Markdown",
                                 reply_markup=keyboard_for(user_id))
                    if supabase:
                        supabase.table("payments").update(
                            {"status": "paid"}).eq("order_id", order_id).execute()
                elif status in ("ACTIVE", "PENDING"):
                    send_message(chat_id, "⏳ Payment pending. Please complete it.")
                else:
                    send_message(chat_id, f"⚠️ Payment Status: {status or 'UNKNOWN'}")
            except Exception as e:
                log.exception("Cashfree status check failed: %s", e)
                send_message(chat_id, "⚠️ Unable to check payment status.")
            return jsonify(ok=True)

        # End of callback_query
        return jsonify(ok=True)

    # Default response
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

    # Step 4: create referral record only if not present
    if referred_by and referred_by != user_id and supabase:
        try:
            exist = supabase.table("referrals").select("id").eq("referrer_id", referred_by).eq("referred_id", user_id).limit(1).execute()
            if not (exist.data or []):
                supabase.table("referrals").insert({
                    "referrer_id": referred_by,
                    "referred_id": user_id,
                    "status": "pending"
                }).execute()
        except Exception as e:
            log.exception("Referral insert failed: %s", e)

    # Step 5: if any pending referral now meets membership, complete + reward
    try:
        if supabase:
            res = supabase.table("referrals").select("id, referrer_id").eq("referred_id", user_id).eq("status", "pending").execute()
            for ref in res.data or []:
                if check_membership_and_prompt(chat_id, user_id):
                    supabase.table("referrals").update({"status": "completed"}).eq("id", ref["id"]).execute()
                    db_add_points(ref["referrer_id"], 2)
                    send_message(ref["referrer_id"], "🎉 Your referral joined successfully! You earned +2 points.")
    except Exception as e:
        log.exception("Referral completion failed: %s", e)

    # Step 6: welcome
    first_name = "Buddy"
    welcome = (
        f"👋 Hello {first_name}!\n"
        "Welcome to *Our Number Info Bot!* 🤖\n\n"
        "Tap *📱 Number Info* to search a number, or type /help.\n"
        "📘 बोट का उपयोग करने के लिए *📱 Number Info* दबाएं या /help लिखें।"
    )
    send_message(chat_id, welcome, parse_mode="Markdown", reply_markup=keyboard_for(user_id))


def handle_help(chat_id: int, user_id: Optional[int] = None) -> None:
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return
    help_text = (
        "📘 *How To Use This Bot* / 📘 *बोट का उपयोग कैसे करें*\n\n"
        "➡️ Tap *📱 Number Info* and then send a 10-digit number.\n"
        "➡️ Or use the command:\n"
        "`/num <10-digit-number>`\n"
        "💡 *Example / उदाहरण:* `/num 9235895648`\n\n"
        "📌 *Rules / नियम:*\n"
        "• Only 10-digit Indian numbers accepted (without +91).\n"
        "• केवल 10 अंकों वाले भारतीय नंबर स्वीकार किए जाएंगे (बिना +91 के)।\n"
        "• If you enter letters or not 10 digits, it will be rejected.\n"
        "• यदि आप 10 अंकों से अलग या अक्षर दर्ज करते हैं, तो यह अस्वीकार हो जाएगा।\n"
    )
    send_message(chat_id, help_text, parse_mode="Markdown", reply_markup=keyboard_for(user_id or 0))

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
        if supabase:
            res = supabase.table("referrals").select("id").eq("referrer_id", user_id).execute()
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
        f"💳 Deposit feature coming soon!"
    )

    send_message(chat_id, msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id))




def handle_home(chat_id: int, user_id: int):
    if not check_membership_and_prompt(chat_id, user_id):
        return
    pts = db_get_points(user_id)
    msg = (
        "🏠 *Home*\n"
        f"💰 Points: *{pts}*\n\n"
        "Use the buttons below."
    )
    send_message(chat_id, msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id))


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


def handle_deposit(chat_id: int, user_id: int):
    """Show deposit options and generate Razorpay link."""
    amounts = [
        {"label": "₹10 → +1 Point", "value": 10},
        {"label": "₹50 → +5 Points", "value": 50},
        {"label": "₹100 → +10 Points", "value": 100},
        {"label": "₹200 → +20 Points", "value": 200},
    ]
    buttons = [
        [{"text": a["label"], "callback_data": f"deposit_{a['value']}"}] for a in amounts
    ]
    send_message(
        chat_id,
        "💳 *Deposit Points*\n\nSelect an amount to add points:",
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": buttons},
    )


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
    """NEW: Prompt user to enter a 10-digit number (bilingual), store session."""
    if not check_membership_and_prompt(chat_id, user_id):
        return
    db_set_session(user_id, "await_number")
    send_message(
        chat_id,
        "🧮 Please enter a *10-digit Indian phone number* without +91.\n"
        "✅ Example: `9235895648`\n\n"
        "🧮 कृपया *+91 के बिना 10 अंकों का भारतीय मोबाइल नंबर* भेजें।\n"
        "✅ उदाहरण: `9235895648`",
        parse_mode="Markdown",
        reply_markup=keyboard_for(user_id),
    )

def handle_payments(chat_id: int, user_id: int):
    if not supabase:
        send_message(chat_id, "⚠️ Payments history not available.")
        return
    res = supabase.table("payments").select("*").eq("user_id", user_id).order("id", desc=True).limit(5).execute()
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
                "💳 Deposit option coming soon!"
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




# ---------------------------------------------------------------------
# Razorpay Webhook — auto-credit points + status notification
# ---------------------------------------------------------------------
@app.route("/cashfree_webhook", methods=["POST"])
def cashfree_webhook():
    data = None

    import hmac, hashlib

    payload = request.data.decode("utf-8")
    signature = request.headers.get("x-webhook-signature", "")
    expected_sig = hmac.new(
        bytes(CASHFREE_WEBHOOK_SECRET, "utf-8"),
        msg=bytes(payload, "utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        log.warning("Invalid Cashfree webhook signature.")
        return abort(400)

    data = request.get_json()
    event = data.get("event")
    order_id = data.get("data", {}).get("order", {}).get("order_id")
    status = data.get("data", {}).get("order", {}).get("order_status")

    # lookup DB
    if supabase:
        res = supabase.table("payments").select("user_id, chat_id").eq("order_id", order_id).limit(1).execute()
        if res.data:
            user_id = res.data[0]["user_id"]
            chat_id = res.data[0]["chat_id"]
        else:
            user_id, chat_id = None, None

    if event == "ORDER_PAID" or status == "PAID":
        points = int(data["data"]["order"]["order_amount"]) // 10
        db_add_points(user_id, points)
        send_message(chat_id, f"✅ Payment of ₹{data['data']['order']['order_amount']} confirmed! +{points} points added.", parse_mode="Markdown")
        if supabase:
            supabase.table("payments").update({"status": "paid"}).eq("order_id", order_id).execute()

    return jsonify(ok=True)








# ---------------------------------------------------------------------
# Main (for local dev). On Render/Gunicorn use: gunicorn app:app
# ---------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    # For local dev only; in production use gunicorn
    app.run(host="0.0.0.0", port=port)
