#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NumberInfo Telegram Bot (Flask) — Production Ready
==================================================

Highlights
----------
- Reply keyboards (bottom) for **owner**, **admin**, and **user** — NO inline admin controls.
- Membership gate: checks user is a member of your group/channel(s); shows inline URL buttons only for join links.
- Broadcast: supports **text**, **photo+caption**, **video+caption**, **document+caption**. Uses original file_id to forward.
- Supabase persistence: users, admin roles, simple sessions (for pending actions), broadcast logs.
- Live stats: total users and today's active users.
- Robust HTTP session with retries, webhook route, keepalive ping thread.
- Safe for redeploy/restart — data stored in Supabase.
- Clean structure; verbose logging; helpful comments.

Environment Variables
---------------------
TELEGRAM_TOKEN=...
WEBHOOK_URL=https://your-domain.com/webhook/<secret>  (you set <secret> as WEBHOOK_SECRET below)
WEBHOOK_SECRET=some-secret
CHANNEL1_INVITE_LINK=https://t.me/+abcdef (Join Group URL)
CHANNEL1_CHAT_ID=-1001234567890         (Group/Channel numeric id for gate check)
CHANNEL2_CHAT_ID_OR_USERNAME=@yourchan  (Second channel username or id for gate check)

SUPABASE_URL=...
SUPABASE_SERVICE_ROLE=... (recommended)  OR  SUPABASE_ANON_KEY=... (limited)
OWNER_ID=123456789 (Your Telegram user id — gets owner privileges automatically)

Notes
-----
- Reply keyboards are persistent for private chats only.
- Join gate uses an inline keyboard because reply keyboards cannot include URL buttons.
- Sessions are intentionally simple: if a deploy occurs while you are in a "waiting for input"
  state (e.g., broadcast), you may need to re-initiate the action. All persistent user/admin
  data remains intact.

"""

import os
import json
import logging
import threading
import time
from datetime import datetime, timezone, date
from typing import Dict, Any, Optional, List, Tuple

from flask import Flask, request, jsonify
import requests
from requests.adapters import HTTPAdapter, Retry

# ----- Supabase -----
from supabase import create_client, Client

# ---------------------------------------------------------------------
# Flask & Logging
# ---------------------------------------------------------------------
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default-secret")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
SELF_URL = os.getenv("WEBHOOK_URL", "").rsplit("/webhook", 1)[0] or "https://example.com"

# Channels / Groups gate (set any of them you need)
CHANNEL1_INVITE_LINK = os.getenv("CHANNEL1_INVITE_LINK", "")
CHANNEL1_CHAT_ID = os.getenv("CHANNEL1_CHAT_ID", "")
CHANNEL2_CHAT = os.getenv("CHANNEL2_CHAT_ID_OR_USERNAME", "")

# Admin owner (bootstrap): this user_id is always treated as owner/admin
OWNER_ID = os.getenv("OWNER_ID", "")  # e.g. "8356178010"

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE", os.getenv("SUPABASE_ANON_KEY", ""))  # prefer service role

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.info("✅ Supabase client initialized")
    except Exception as e:
        logging.exception("❌ Supabase init failed: %s", e)
else:
    logging.warning("⚠️ Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_ROLE/ANON. Persistence disabled.")

# ---------------------------------------------------------------------
# Robust HTTP session (Telegram API) with retries
# ---------------------------------------------------------------------
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# ---------------------------------------------------------------------
# Keyboards — Reply (bottom) only for commands; Inline only for join URLs
# ---------------------------------------------------------------------
def keyboard_user() -> Dict[str, Any]:
    """Keyboard for normal users."""
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "selective": True
    }

def keyboard_admin() -> Dict[str, Any]:
    """Keyboard for admins (limited)."""
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
            [{"text": "📊 Live Stats"}, {"text": "📢 Broadcast"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "selective": True
    }

def keyboard_owner() -> Dict[str, Any]:
    """Keyboard for owner (full control)."""
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
            [{"text": "📊 Live Stats"}, {"text": "📢 Broadcast"}],
            [{"text": "👑 List Admins"}, {"text": "➕ Add Admin"}, {"text": "➖ Remove Admin"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "selective": True
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
# Telegram helpers
# ---------------------------------------------------------------------
def tg(method: str, data: Dict[str, Any], timeout: int = 20) -> Optional[Dict[str, Any]]:
    """Low-level Telegram call with logging."""
    try:
        resp = session.post(f"{TELEGRAM_API}/{method}", data=data, timeout=timeout)
        logging.info("%s: %s %s", method, resp.status_code, resp.text[:500])
        return resp.json()
    except Exception as e:
        logging.exception("%s failed: %s", method, e)
        return None

def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None,
                 parse_mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendMessage", payload)

def edit_message(chat_id: int, message_id: int, text: str,
                 reply_markup: Optional[Dict[str, Any]] = None, parse_mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("editMessageText", payload)

def send_photo(chat_id: int, file_id: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    payload = {"chat_id": chat_id, "photo": file_id}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendPhoto", payload)

def send_video(chat_id: int, file_id: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    payload = {"chat_id": chat_id, "video": file_id}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendVideo", payload)

def send_document(chat_id: int, file_id: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    payload = {"chat_id": chat_id, "document": file_id}
    if caption:
        payload["caption"] = caption
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg("sendDocument", payload)

def answer_callback(callback_id: str, text: Optional[str] = None, show_alert: bool = False):
    payload = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    tg("answerCallbackQuery", payload, timeout=10)

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
            logging.warning("getChatMember failed: %s", data)
            return None
        status = data["result"]["status"]
        return status in ("creator", "administrator", "member")
    except Exception as e:
        logging.exception("is_member error: %s", e)
        return None

# ---------------------------------------------------------------------
# Supabase persistence helpers
# ---------------------------------------------------------------------
def db_upsert_user(user: Dict[str, Any]):
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
        supabase.table("users").upsert(row).execute()
    except Exception as e:
        logging.exception("db_upsert_user failed: %s", e)

def db_mark_admin(user_id: int, is_admin: bool) -> bool:
    if not supabase:
        return False
    try:
        supabase.table("users").upsert({"id": user_id, "is_admin": is_admin}).execute()
        return True
    except Exception as e:
        logging.exception("db_mark_admin failed: %s", e)
        return False

def db_is_admin(user_id: int) -> bool:
    """Owner is always admin (bootstrap)."""
    if OWNER_ID and str(user_id) == str(OWNER_ID):
        return True
    if not supabase:
        return False
    try:
        res = supabase.table("users").select("is_admin").eq("id", user_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            return bool(res.data[0].get("is_admin", False))
        return False
    except Exception as e:
        logging.exception("db_is_admin failed: %s", e)
        return False

def db_list_admins() -> List[Dict[str, Any]]:
    if not supabase:
        return []
    try:
        res = supabase.table("users").select("id,username,first_name,last_name,is_admin").eq("is_admin", True).execute()
        return res.data or []
    except Exception as e:
        logging.exception("db_list_admins failed: %s", e)
        return []

def db_all_user_ids() -> List[int]:
    if not supabase:
        return []
    try:
        res = supabase.table("users").select("id").execute()
        return [row["id"] for row in (res.data or [])]
    except Exception as e:
        logging.exception("db_all_user_ids failed: %s", e)
        return []

def db_set_session(user_id: int, action: Optional[str] = None, payload: Optional[Dict[str, Any]] = None):
    """Store pending action for admin (e.g., broadcast, add_admin, remove_admin)."""
    if not supabase:
        return
    try:
        supabase.table("sessions").upsert({
            "user_id": user_id,
            "action": action,
            "payload": json.dumps(payload or {})
        }).execute()
    except Exception as e:
        logging.exception("db_set_session failed: %s", e)

def db_get_session(user_id: int) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    try:
        res = supabase.table("sessions").select("*").eq("user_id", user_id).limit(1).execute()
        if res.data:
            row = res.data[0]
            payload = {}
            try:
                payload = json.loads(row.get("payload") or "{}")
            except Exception:
                payload = {}
            return {"action": row.get("action"), "payload": payload}
        return None
    except Exception as e:
        logging.exception("db_get_session failed: %s", e)
        return None

def db_clear_session(user_id: int):
    if not supabase:
        return
    try:
        supabase.table("sessions").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logging.exception("db_clear_session failed: %s", e)

def db_log_broadcast(desc: str, total: int, success: int, failed: int):
    if not supabase:
        return
    try:
        supabase.table("broadcasts").insert({
            "text": desc,
            "total": total,
            "success": success,
            "failed": failed
        }).execute()
    except Exception as e:
        logging.exception("db_log_broadcast failed: %s", e)

def db_stats_counts() -> Tuple[int, int]:
    """Return total users and today's active users (by last_seen date)."""
    if not supabase:
        return 0, 0
    try:
        res = supabase.table("users").select("id,last_seen").execute()
        rows = res.data or []
        total = len(rows)
        today_str = date.today().isoformat()
        active_today = 0
        for r in rows:
            ls = r.get("last_seen")
            if ls and str(ls)[:10] == today_str:
                active_today += 1
        return total, active_today
    except Exception as e:
        logging.exception("db_stats_counts failed: %s", e)
        return 0, 0

# ---------------------------------------------------------------------
# Join Gate
# ---------------------------------------------------------------------
def check_membership_and_prompt(chat_id: int, user_id: int) -> bool:
    """Checks membership; if not, shows inline URLs to join and a Try Again button."""
    ch1_url = CHANNEL1_INVITE_LINK
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
from flask import Flask, request, jsonify

@app.route("/", methods=["GET", "POST"])
def home():
    return jsonify(ok=True, message="Bot is alive")

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True)
    if not update:
        return jsonify(ok=False, error="no update")

    logging.info("Incoming update keys: %s", list(update.keys()))

    # Track user whenever possible
    if "message" in update:
        ufrom = update["message"].get("from", {})
        if ufrom:
            db_upsert_user(ufrom)
            if OWNER_ID and str(ufrom.get("id")) == str(OWNER_ID):
                db_mark_admin(int(OWNER_ID), True)

    if "callback_query" in update:
        ufrom = update["callback_query"].get("from", {})
        if ufrom:
            db_upsert_user(ufrom)
            if OWNER_ID and str(ufrom.get("id")) == str(OWNER_ID):
                db_mark_admin(int(OWNER_ID), True)

    # ----- Handle user messages -----
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        user = msg.get("from", {})
        user_id = user.get("id")
        text = msg.get("text", "")
        chat_type = chat.get("type")

        # ignore groups/channels
        if chat_type != "private":
            logging.info(f"Ignored non-private chat: {chat_type}")
            return jsonify(ok=True)

        # Map bottom keyboard button presses to commands
        if text == "🏠 Home":
            text = "/start"
        elif text == "ℹ️ Help":
            text = "/help"
        elif text == "📊 Live Stats":
            text = "/stats"
        elif text == "📢 Broadcast":
            text = "/broadcast"
        elif text == "👑 List Admins":
            text = "/list_admins"
        elif text == "➕ Add Admin":
            text = "/add_admin"
        elif text == "➖ Remove Admin":
            text = "/remove_admin"

        # check if admin session is waiting for input
        sess = db_get_session(user_id)
        if sess and db_is_admin(user_id):
            action = sess.get("action")
            if action == "broadcast_wait_message":
                db_clear_session(user_id)
                run_broadcast(user_id, chat_id, msg)
                return jsonify(ok=True)
            elif action == "add_admin_wait_id":
                if text.strip().isdigit():
                    uid = int(text.strip())
                    ok = db_mark_admin(uid, True)
                    send_message(chat_id, f"✅ Promoted {uid} to admin.", reply_markup=keyboard_for(user_id)) if ok                         else send_message(chat_id, "❌ Failed to promote.", reply_markup=keyboard_for(user_id))
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.", reply_markup=keyboard_for(user_id))
                db_clear_session(user_id)
                return jsonify(ok=True)
            elif action == "remove_admin_wait_id":
                if text.strip().isdigit():
                    uid = int(text.strip())
                    ok = db_mark_admin(uid, False)
                    send_message(chat_id, f"✅ Removed admin {uid}.", reply_markup=keyboard_for(user_id)) if ok                         else send_message(chat_id, "❌ Failed to remove.", reply_markup=keyboard_for(user_id))
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.", reply_markup=keyboard_for(user_id))
                db_clear_session(user_id)
                return jsonify(ok=True)

        # membership gating for all commands and text
        if text.startswith("/"):
            cmd = text.split()[0].lower()
            if cmd in ("/start", "/help"):
                pass
            else:
                if not check_membership_and_prompt(chat_id, user_id):
                    return jsonify(ok=True)

        if text.startswith("/start"):
            handle_start(chat_id, user_id)
        elif text.startswith("/help"):
            handle_help(chat_id, user_id)
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
        elif text.startswith("/num"):
            parts = text.split()
            if len(parts) < 2:
                send_message(chat_id,
                             "Usage: /num <10-digit-number>\nExample: /num 9235895648",
                             reply_markup=keyboard_for(user_id))
            else:
                handle_num(chat_id, parts[1], user_id)
        else:
            if not check_membership_and_prompt(chat_id, user_id):
                return jsonify(ok=True)
            send_message(chat_id, "Use /help to see commands.", reply_markup=keyboard_for(user_id))
        return jsonify(ok=True)

    # ----- Handle callbacks (only join retry) -----
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        user_id = cb["from"]["id"]
        callback_id = cb["id"]
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        if data == "try_again":
            answer_callback(callback_id, text="Rechecking your join status...")
            handle_start(chat_id, user_id)
        else:
            answer_callback(callback_id, text="OK")

        return jsonify(ok=True)

    return jsonify(ok=True)

# ---------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------
def handle_start(chat_id: int, user_id: int):
    if not check_membership_and_prompt(chat_id, user_id):
        return
    try:
        r = session.get(f"{TELEGRAM_API}/getChat", params={"chat_id": chat_id}, timeout=10)
        user_data = r.json().get("result", {})
        first_name = user_data.get("first_name", "Buddy")
    except Exception:
        first_name = "Buddy"

    welcome = (
        f"👋 Hello {first_name}!\n"
        "Welcome to *Our Number Info Bot!* 🤖\n\n"
        "📘 Type /help to learn how to use this bot.\n"
        "📘 बोट का उपयोग करने के लिए /help लिखें।"
    )
    send_message(chat_id, welcome, parse_mode="Markdown", reply_markup=keyboard_for(user_id))

def handle_help(chat_id: int, user_id: Optional[int] = None):
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return
    help_text = (
        "📘 *How To Use This Bot* / 📘 *बोट का उपयोग कैसे करें*\n\n"
        "➡️ `/num <10-digit-number>`\n"
        "💡 *Example / उदाहरण:* `/num 9235895648`\n\n"
        "📌 *Rules / नियम:*\n"
        "• Only 10-digit Indian numbers accepted (without +91).\n"
        "• केवल 10 अंकों वाले भारतीय नंबर स्वीकार किए जाएंगे (बिना +91 के)।\n\n"
        "• If you enter 11 digits or letters, it will be rejected.\n"
        "• यदि आप 11 अंक या अक्षर दर्ज करते हैं, तो इसे अस्वीकार कर दिया जाएगा।\n\n"
        "• Reply will contain information about the given number.\n"
        "• जवाब में दिए गए नंबर की जानकारी शामिल होगी।\n"
    )
    send_message(chat_id, help_text, parse_mode="Markdown", reply_markup=keyboard_for(user_id or 0))

def handle_stats(chat_id: int, user_id: int):
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

def handle_list_admins(chat_id: int, user_id: int):
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

def handle_add_admin(chat_id: int, user_id: int):
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can add admins.", reply_markup=keyboard_for(user_id))
        return
    db_set_session(user_id, "add_admin_wait_id")
    send_message(chat_id, "👑 Send the Telegram *user_id* to promote as admin:", parse_mode="Markdown", reply_markup=keyboard_for(user_id))

def handle_remove_admin(chat_id: int, user_id: int):
    if role_for(user_id) != "owner":
        send_message(chat_id, "❌ Only owner can remove admins.", reply_markup=keyboard_for(user_id))
        return
    db_set_session(user_id, "remove_admin_wait_id")
    send_message(chat_id, "🗑️ Send the Telegram *user_id* to remove from admin:", parse_mode="Markdown", reply_markup=keyboard_for(user_id))

def handle_broadcast(chat_id: int, user_id: int):
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

def handle_num(chat_id: int, number: str, user_id: Optional[int] = None):
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return

    if not number.isdigit() or len(number) != 10:
        send_message(chat_id, "❌ Only 10-digit numbers allowed. Example: /num 9235895648",
                     reply_markup=keyboard_for(user_id or 0))
        return

    # Step 1: send initial message and fetch message_id safely
    init_resp = tg("sendMessage", {
        "chat_id": chat_id,
        "text": "🔍 Searching number info... 0%",
        "reply_markup": json.dumps(keyboard_for(user_id or 0))
    })
    message_id = None
    try:
        message_id = init_resp.get("result", {}).get("message_id")
    except Exception:
        message_id = None

    # Step 2: update same message with progress bar
    if message_id:
        for p in [15, 42, 68, 90, 100]:
            try:
                time.sleep(0.5)
                tg("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"🔍 Searching number info... {p}%"
                })
            except Exception as e:
                logging.warning("edit progress failed: %s", e)
    else:
        # fallback (if message_id missing)
        send_message(chat_id, "🔍 Searching number info... Please wait...")

    # Step 3: call external API
    api_url = f"https://yahu.site/api/?number={number}&key=The_ajay"
    try:
        r = session.get(api_url, timeout=20)
        r.raise_for_status()
        data = r.json()

        # Step 4: Handle empty data
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) == 0:
            if message_id:
                edit_message(chat_id, message_id, "✅ Search Complete! Here's your result ↓")
            bilingual_msg = (
                "⚠️ *Number Data Not Available !!!*\n"
                "⚠️ *नंबर का डेटा उपलब्ध नहीं है !!!*"
            )
            send_message(chat_id, bilingual_msg, parse_mode="Markdown", reply_markup=keyboard_for(user_id or 0))
            return

        # Step 5: Show formatted result
        pretty_json = json.dumps(data, indent=2, ensure_ascii=False)
        if len(pretty_json) > 3900:
            pretty_json = pretty_json[:3900] + "\n\n[truncated due to size limit]"

        if message_id:
            edit_message(chat_id, message_id, "✅ Search Complete! Here's your result ↓")
        send_message(chat_id, f"<pre>{pretty_json}</pre>", parse_mode="HTML", reply_markup=keyboard_for(user_id or 0))

    except Exception as e:
        logging.exception("API fetch failed: %s", e)
        if message_id:
            edit_message(chat_id, message_id, "⚠️ Failed to fetch data. Try again later.")


# ---------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------
def run_broadcast(admin_user_id: int, chat_id: int, message_obj: Dict[str, Any]):
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
            res = None
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
                res = {"ok": False}

            if res and res.get("ok"):
                success += 1
            else:
                failed += 1
        except Exception as e:
            logging.warning("broadcast send failed to %s: %s", uid, e)
            failed += 1
        time.sleep(0.03)

    kind = "photo" if photo else "video" if video else "document" if document else "text"
    db_log_broadcast(f"{kind} broadcast", total, success, failed)
    send_message(chat_id, f"✅ Broadcast complete!\nTotal: {total}\nDelivered: {success}\nFailed: {failed}",
                 reply_markup=keyboard_for(admin_user_id))

# ---------------------------------------------------------------------
# Webhook setup & Keepalive
# ---------------------------------------------------------------------
@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    url = os.getenv("WEBHOOK_URL")
    if not url:
        return jsonify(ok=False, error="WEBHOOK_URL not set"), 400
    r = session.get(f"{TELEGRAM_API}/setWebhook", params={"url": url}, timeout=10)
    return jsonify(r.json())

def auto_ping():
    while True:
        try:
            ping_url = SELF_URL + "/"
            session.get(ping_url, timeout=5)
            logging.info("Auto-pinged %s", ping_url)
        except Exception as e:
            logging.warning("Auto-ping failed: %s", e)
        time.sleep(300)

threading.Thread(target=auto_ping, daemon=True).start()

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
