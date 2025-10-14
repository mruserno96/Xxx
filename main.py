import os
import json
import logging
import threading
import time
from datetime import datetime, timezone, date

from flask import Flask, request, jsonify
import requests
from requests.adapters import HTTPAdapter, Retry

# ----- Supabase -----
from supabase import create_client, Client

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ===== CONFIG =====
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL1_INVITE_LINK = os.getenv("CHANNEL1_INVITE_LINK", "")
CHANNEL1_CHAT_ID = os.getenv("CHANNEL1_CHAT_ID", "")
CHANNEL2_CHAT = os.getenv("CHANNEL2_CHAT_ID_OR_USERNAME", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default-secret")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
SELF_URL = os.getenv("WEBHOOK_URL", "").rsplit("/webhook", 1)[0] or "https://example.com"

# Admin owner (bootstrap): this user_id will automatically be admin on first run
OWNER_ID = os.getenv("OWNER_ID", "")  # e.g. "8356178010"

# ===== Supabase =====
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE", os.getenv("SUPABASE_ANON_KEY", ""))  # service key preferred

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    logging.warning("Supabase not configured! Set SUPABASE_URL and SUPABASE_SERVICE_ROLE/ANON.")

# ===== HTTP SESSION WITH RETRIES =====
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# ===== REPLY KEYBOARD (BOTTOM KEYBOARD) =====
def get_reply_keyboard():
    """Permanent bottom keyboard for private chats."""
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "ℹ️ Help"}],
            [{"text": "👑 Admin Panel"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

def remove_reply_keyboard():
    return {"remove_keyboard": True}

# ===== HELPERS: Telegram =====
def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = session.post(f"{TELEGRAM_API}/sendMessage", data=payload, timeout=20)
        logging.info("send_message: %s %s", r.status_code, r.text)
        return r.json()
    except Exception as e:
        logging.exception("send_message failed: %s", e)
        return None

def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = session.post(f"{TELEGRAM_API}/editMessageText", data=payload, timeout=20)
        logging.info("edit_message: %s %s", r.status_code, r.text)
        return r.json()
    except Exception as e:
        logging.exception("edit_message failed: %s", e)
        return None

def answer_callback(callback_id, text=None, show_alert=False):
    try:
        payload = {"callback_query_id": callback_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        session.post(f"{TELEGRAM_API}/answerCallbackQuery", data=payload, timeout=10)
    except Exception as e:
        logging.exception("answer_callback failed: %s", e)

def is_member(user_id, chat_identifier):
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

def build_join_keyboard(channels):
    buttons = [[{"text": ch["label"], "url": ch["url"]}] for ch in channels]
    buttons.append([{"text": "✅ Try Again", "callback_data": "try_again"}])
    return {"inline_keyboard": buttons}

# ===== HELPERS: Supabase persistence =====
def db_upsert_user(user):
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

def db_mark_admin(user_id: int, is_admin: bool):
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

def db_list_admins():
    if not supabase:
        return []
    try:
        res = supabase.table("users").select("id,username,first_name,last_name").eq("is_admin", True).execute()
        return res.data or []
    except Exception as e:
        logging.exception("db_list_admins failed: %s", e)
        return []

def db_all_user_ids():
    if not supabase:
        return []
    try:
        res = supabase.table("users").select("id").execute()
        return [row["id"] for row in (res.data or [])]
    except Exception as e:
        logging.exception("db_all_user_ids failed: %s", e)
        return []

def db_set_session(user_id, action=None, payload=None):
    """Store pending action for admin (e.g., broadcast, add_admin, remove_admin)"""
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

def db_get_session(user_id):
    if not supabase:
        return None
    try:
        res = supabase.table("sessions").select("*").eq("user_id", user_id).limit(1).execute()
        if res.data:
            row = res.data[0]
            payload = {}
            try:
                payload = json.loads(row.get("payload") or "{}")
            except:
                payload = {}
            return {"action": row.get("action"), "payload": payload}
        return None
    except Exception as e:
        logging.exception("db_get_session failed: %s", e)
        return None

def db_clear_session(user_id):
    if not supabase:
        return
    try:
        supabase.table("sessions").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logging.exception("db_clear_session failed: %s", e)

def db_log_broadcast(desc, total, success, failed):
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

def db_stats_counts():
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
            if ls and ls[:10] == today_str:
                active_today += 1
        return total, active_today
    except Exception as e:
        logging.exception("db_stats_counts failed: %s", e)
        return 0, 0

# ===== MEMBERSHIP CHECK =====
def check_membership_and_prompt(chat_id, user_id):
    ch1_url = CHANNEL1_INVITE_LINK
    ch2_url = f"https://t.me/{CHANNEL2_CHAT.lstrip('@')}" if CHANNEL2_CHAT else None

    mem1 = is_member(user_id, CHANNEL1_CHAT_ID) if CHANNEL1_CHAT_ID else None
    mem2 = is_member(user_id, CHANNEL2_CHAT) if CHANNEL2_CHAT else None

    not_joined = []
    if mem1 is not True:
        not_joined.append({"label": "Join Group", "url": ch1_url})
    if mem2 is not True:
        not_joined.append({"label": "Join Channel", "url": ch2_url})

    if not_joined:
        send_message(
            chat_id,
            "🚫 You must join both channels below before using this bot 👇",
            reply_markup=build_join_keyboard(not_joined)
        )
        return False
    return True

# ===== ROUTES =====
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

    # ===== Handle user messages =====
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
        elif text == "👑 Admin Panel":
            text = "/admin"

        # check if admin session is waiting for input
        sess = db_get_session(user_id)
        if sess and db_is_admin(user_id):
            action = sess.get("action")
            if action == "broadcast_wait_text":
                db_clear_session(user_id)
                # Pass the FULL message so media can be broadcast too
                run_broadcast(user_id, chat_id, msg)
                return jsonify(ok=True)
            elif action == "add_admin_wait_id":
                if text.strip().isdigit():
                    uid = int(text.strip())
                    ok = db_mark_admin(uid, True)
                    send_message(chat_id, f"✅ Promoted {uid} to admin.", reply_markup=get_reply_keyboard()) if ok \
                        else send_message(chat_id, "❌ Failed to promote.", reply_markup=get_reply_keyboard())
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.", reply_markup=get_reply_keyboard())
                db_clear_session(user_id)
                return jsonify(ok=True)
            elif action == "remove_admin_wait_id":
                if text.strip().isdigit():
                    uid = int(text.strip())
                    ok = db_mark_admin(uid, False)
                    send_message(chat_id, f"✅ Removed admin {uid}.", reply_markup=get_reply_keyboard()) if ok \
                        else send_message(chat_id, "❌ Failed to remove.", reply_markup=get_reply_keyboard())
                else:
                    send_message(chat_id, "❌ Send a numeric Telegram user ID.", reply_markup=get_reply_keyboard())
                db_clear_session(user_id)
                return jsonify(ok=True)

        # membership gating for all commands and text
        if text.startswith("/start"):
            handle_start(chat_id, user_id)
        elif text.startswith("/help"):
            handle_help(chat_id, user_id)
        elif text.startswith("/admin"):
            handle_admin_panel(chat_id, user_id)
        elif text.startswith("/num"):
            parts = text.split()
            if len(parts) < 2:
                send_message(chat_id,
                             "Usage: /num <10-digit-number>\nExample: /num 9235895648",
                             reply_markup=get_reply_keyboard())
            else:
                handle_num(chat_id, parts[1], user_id)
        else:
            if not check_membership_and_prompt(chat_id, user_id):
                return jsonify(ok=True)
            send_message(chat_id, "Use /help to see commands.", reply_markup=get_reply_keyboard())
        return jsonify(ok=True)

    # ===== Handle callbacks =====
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        user_id = cb["from"]["id"]
        callback_id = cb["id"]
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        if data == "try_again":
            answer_callback(callback_id, text="Rechecking your join status...")
            handle_start(chat_id, user_id)

        # Admin callbacks
        elif data == "admin_stats":
            if not db_is_admin(user_id):
                answer_callback(callback_id, "Not authorized")
            else:
                total, today = db_stats_counts()
                txt = (
                    "📊 *Live Stats*\n\n"
                    f"• Total Users: *{total}*\n"
                    f"• Active Today: *{today}*\n"
                )
                answer_callback(callback_id, "Stats updated")
                send_message(chat_id, txt, parse_mode="Markdown", reply_markup=get_reply_keyboard())

        elif data == "admin_broadcast":
            if not db_is_admin(user_id):
                answer_callback(callback_id, "Not authorized")
            else:
                db_set_session(user_id, "broadcast_wait_text")
                answer_callback(callback_id, "Send the message to broadcast")
                send_message(
                    chat_id,
                    "📣 Send the message you want to broadcast to all users.\n"
                    "• Text: just send text\n"
                    "• Photo/Video/Document: send the media (with optional caption)\n",
                    reply_markup=get_reply_keyboard()
                )

        elif data == "admin_list_admins":
            if not db_is_admin(user_id):
                answer_callback(callback_id, "Not authorized")
            else:
                admins = db_list_admins()
                if not admins:
                    send_message(chat_id, "No admins yet.", reply_markup=get_reply_keyboard())
                else:
                    lines = []
                    for a in admins:
                        nm = a.get("first_name") or ""
                        un = a.get("username")
                        if un:
                            nm = f"{nm} (@{un})"
                        lines.append(f"• {nm} — `{a['id']}`")
                    send_message(chat_id, "👑 *Admins:*\n" + "\n".join(lines),
                                 parse_mode="Markdown", reply_markup=get_reply_keyboard())

        elif data == "admin_add":
            if not db_is_admin(user_id):
                answer_callback(callback_id, "Not authorized")
            else:
                db_set_session(user_id, "add_admin_wait_id")
                answer_callback(callback_id, "Send user ID to promote")
                send_message(chat_id, "👑 Send the Telegram *user_id* to promote as admin:",
                             parse_mode="Markdown", reply_markup=get_reply_keyboard())

        elif data == "admin_remove":
            if not db_is_admin(user_id):
                answer_callback(callback_id, "Not authorized")
            else:
                db_set_session(user_id, "remove_admin_wait_id")
                answer_callback(callback_id, "Send user ID to remove")
                send_message(chat_id, "🗑️ Send the Telegram *user_id* to remove from admin:",
                             parse_mode="Markdown", reply_markup=get_reply_keyboard())

        elif data == "admin_refresh":
            if not db_is_admin(user_id):
                answer_callback(callback_id, "Not authorized")
            else:
                answer_callback(callback_id, "Refreshed")
                handle_admin_panel(chat_id, user_id)

        else:
            answer_callback(callback_id, text="Unknown action.")

        return jsonify(ok=True)

    return jsonify(ok=True)

# ===== COMMAND HANDLERS =====
def handle_start(chat_id, user_id):
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
    send_message(chat_id, welcome, parse_mode="Markdown", reply_markup=get_reply_keyboard())

def handle_help(chat_id, user_id=None):
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
    send_message(chat_id, help_text, parse_mode="Markdown", reply_markup=get_reply_keyboard())

def handle_num(chat_id, number, user_id=None):
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return

    if not number.isdigit() or len(number) != 10:
        send_message(chat_id, "❌ Only 10-digit numbers allowed. Example: /num 9235895648",
                     reply_markup=get_reply_keyboard())
        return

    # progress message
    msg = send_message(chat_id, "🔍 Searching number info... 0%", reply_markup=get_reply_keyboard())
    message_id = (msg or {}).get("result", {}).get("message_id")

    for p in [15, 42, 68, 90, 100]:
        try:
            time.sleep(0.5)
            if message_id:
                edit_message(chat_id, message_id, f"🔍 Searching number info... {p}%")
        except Exception as e:
            logging.warning("edit progress failed: %s", e)

    api_url = f"https://yahu.site/api/?number={number}&key=The_ajay"
    try:
        r = session.get(api_url, timeout=20)
        r.raise_for_status()
        data = r.json()

        if "data" in data and isinstance(data["data"], list) and len(data["data"]) == 0:
            if message_id:
                edit_message(chat_id, message_id, "✅ Search Complete! Here's your result ↓")
            bilingual_msg = (
                "⚠️ *Number Data Not Available !!!*\n"
                "⚠️ *नंबर का डेटा उपलब्ध नहीं है !!!*"
            )
            send_message(chat_id, bilingual_msg, parse_mode="Markdown", reply_markup=get_reply_keyboard())
            return

        pretty_json = json.dumps(data, indent=2, ensure_ascii=False)
        if len(pretty_json) > 3900:
            pretty_json = pretty_json[:3900] + "\n\n[truncated due to size limit]"

        if message_id:
            edit_message(chat_id, message_id, "✅ Search Complete! Here's your result ↓")
        send_message(chat_id, f"<pre>{pretty_json}</pre>", parse_mode="HTML", reply_markup=get_reply_keyboard())

    except Exception as e:
        logging.exception("API fetch failed: %s", e)
        if message_id:
            edit_message(chat_id, message_id, "⚠️ Failed to fetch data. Try again later.")

# ===== ADMIN PANEL =====
def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Live Stats", "callback_data": "admin_stats"},
                {"text": "📢 Broadcast", "callback_data": "admin_broadcast"}
            ],
            [
                {"text": "👑 Admins", "callback_data": "admin_list_admins"},
                {"text": "➕ Add Admin", "callback_data": "admin_add"},
                {"text": "➖ Remove Admin", "callback_data": "admin_remove"}
            ],
            [{"text": "🔄 Refresh", "callback_data": "admin_refresh"}]
        ]
    }

def handle_admin_panel(chat_id, user_id):
    if not db_is_admin(user_id):
        send_message(chat_id, "❌ You are not authorized to use admin panel.", reply_markup=get_reply_keyboard())
        return
    total, today = db_stats_counts()
    text = (
        "🛠️ *Admin Panel*\n\n"
        f"• Total Users: *{total}*\n"
        f"• Active Today: *{today}*\n\n"
        "Choose an action:"
    )
    send_message(chat_id, text, reply_markup=admin_keyboard(), parse_mode="Markdown")

def run_broadcast(admin_user_id, chat_id, message_obj):
    if not db_is_admin(admin_user_id):
        send_message(chat_id, "❌ Not authorized.", reply_markup=get_reply_keyboard())
        return

    user_ids = db_all_user_ids()
    total = len(user_ids)
    success = 0
    failed = 0
    send_message(chat_id, f"📣 Broadcast started to {total} users...", reply_markup=get_reply_keyboard())

    # message_obj is the full Telegram message (to support media)
    text = message_obj.get("text")
    photo = message_obj.get("photo")
    video = message_obj.get("video")
    document = message_obj.get("document")
    caption = message_obj.get("caption", "")

    for uid in user_ids:
        try:
            if photo:
                file_id = photo[-1]["file_id"]
                session.post(f"{TELEGRAM_API}/sendPhoto", data={"chat_id": uid, "photo": file_id, "caption": caption})
            elif video:
                file_id = video["file_id"]
                session.post(f"{TELEGRAM_API}/sendVideo", data={"chat_id": uid, "video": file_id, "caption": caption})
            elif document:
                file_id = document["file_id"]
                session.post(f"{TELEGRAM_API}/sendDocument", data={"chat_id": uid, "document": file_id, "caption": caption})
            elif text:
                session.post(f"{TELEGRAM_API}/sendMessage", data={"chat_id": uid, "text": text})
            else:
                # nothing detected, skip
                pass
            success += 1
        except Exception:
            failed += 1
        time.sleep(0.05)

    kind = "photo" if photo else "video" if video else "document" if document else "text"
    db_log_broadcast(f"{kind} broadcast", total, success, failed)
    send_message(chat_id, f"✅ Broadcast complete!\nTotal: {total}\nDelivered: {success}\nFailed: {failed}",
                 reply_markup=get_reply_keyboard())

# ===== WEBHOOK SETUP =====
@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    url = os.getenv("WEBHOOK_URL")
    if not url:
        return jsonify(ok=False, error="WEBHOOK_URL not set"), 400
    r = session.get(f"{TELEGRAM_API}/setWebhook", params={"url": url}, timeout=10)
    return jsonify(r.json())

# ===== AUTO PING (KEEP-ALIVE THREAD) =====
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

# ===== MAIN =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
