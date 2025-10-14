import os
import json
import logging
import threading
import time
from flask import Flask, request, jsonify
import requests
from requests.adapters import HTTPAdapter, Retry

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ===== CONFIG =====
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL1_INVITE_LINK = os.getenv("CHANNEL1_INVITE_LINK", "")
CHANNEL1_CHAT_ID = os.getenv("CHANNEL1_CHAT_ID", "")
CHANNEL2_CHAT = os.getenv("CHANNEL2_CHAT_ID_OR_USERNAME", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default-secret")
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
SELF_URL = os.getenv("WEBHOOK_URL", "").rsplit("/webhook", 1)[0] or "https://blackeye-89da.onrender.com"

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

# ===== HELPERS =====
def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    """Send message safely to Telegram."""
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = session.post(f"{TELEGRAM_API}/sendMessage", data=payload, timeout=10)
        logging.info("send_message: %s %s", r.status_code, r.text)
    except Exception as e:
        logging.exception("send_message failed: %s", e)

def answer_callback(callback_id, text=None, show_alert=False):
    try:
        payload = {"callback_query_id": callback_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        session.post(f"{TELEGRAM_API}/answerCallbackQuery", data=payload, timeout=10)
    except Exception as e:
        logging.exception("answer_callback failed: %s", e)

def is_member(user_id, chat_identifier):
    """Check if user is member/admin in a channel."""
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

def check_membership_and_prompt(chat_id, user_id):
    """Checks if user joined both channels. Returns True if joined, False otherwise."""
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

    # ===== Handle user messages =====
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        # 🚫 Ignore all messages that are not from private chat
        chat_type = msg["chat"].get("type", "")
        if chat_type != "private":
            logging.info(f"Ignored message from non-private chat: {chat_type}")
            return jsonify(ok=True)

        if text.startswith("/start"):
            handle_start(chat_id, user_id)
        elif text.startswith("/help"):
            handle_help(chat_id, user_id)
        elif text.startswith("/num"):
            parts = text.split()
            if len(parts) < 2:
                send_message(chat_id, "Usage: /num <10-digit-number>\nExample: /num 9235895648")
            else:
                handle_num(chat_id, parts[1], user_id)
        else:
            # Check membership before responding to any normal text
            if not check_membership_and_prompt(chat_id, user_id):
                return jsonify(ok=True)
            send_message(chat_id, "Use /help to see commands.")
        return jsonify(ok=True)

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
            answer_callback(callback_id, text="Unknown action.")
        return jsonify(ok=True)

    return jsonify(ok=True)

# ===== COMMAND HANDLERS =====
def handle_start(chat_id, user_id):
    # Check membership first
    if not check_membership_and_prompt(chat_id, user_id):
        return

    # Personalized bilingual welcome message
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
    )
    send_message(chat_id, welcome, parse_mode="Markdown")

def handle_help(chat_id, user_id=None):
    # Check membership first
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
    send_message(chat_id, help_text, parse_mode="Markdown")

def handle_num(chat_id, number, user_id=None):
    # Check membership first
    if user_id and not check_membership_and_prompt(chat_id, user_id):
        return

    if not number.isdigit() or len(number) != 10:
        send_message(chat_id, "❌ Only 10-digit numbers allowed. Example: /num 9235895648")
        return

    # Step 1: send initial message
    msg = session.post(f"{TELEGRAM_API}/sendMessage", data={
        "chat_id": chat_id,
        "text": "🔍 Searching number info... 0%"
    }).json()
    message_id = msg.get("result", {}).get("message_id")

    # Step 2: update same message with progress
    for p in [15, 42, 68, 90, 100]:
        try:
            time.sleep(0.5)
            session.post(f"{TELEGRAM_API}/editMessageText", data={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"🔍 Searching number info... {p}%"
            })
        except Exception as e:
            logging.warning("edit progress failed: %s", e)

    # Step 3: Call API
    api_url = f"https://yahu.site/api/?number={number}&key=The_ajay"
    try:
        r = session.get(api_url, timeout=15)
        r.raise_for_status()
        data = r.json()

    # Step 4: Check if "data" exists but empty
if "data" in data and isinstance(data["data"], list) and len(data["data"]) == 0:
    session.post(f"{TELEGRAM_API}/editMessageText", data={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": "✅ Search Complete! Here's your result ↓"
    })
    
    bilingual_msg = (
        "⚠️ *Number Data Not Available !!!*\n"
        "⚠️ *नंबर का डेटा उपलब्ध नहीं है !!!*"
    )
    send_message(chat_id, bilingual_msg, parse_mode="Markdown")
    return

        pretty_json = json.dumps(data, indent=2, ensure_ascii=False)
        if len(pretty_json) > 3900:
            pretty_json = pretty_json[:3900] + "\n\n[truncated due to size limit]"

        session.post(f"{TELEGRAM_API}/editMessageText", data={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "✅ Search Complete! Here's your result ↓"
        })

        send_message(chat_id, f"<pre>{pretty_json}</pre>", parse_mode="HTML")

    except Exception as e:
        logging.exception("API fetch failed: %s", e)
        session.post(f"{TELEGRAM_API}/editMessageText", data={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "⚠️ Failed to fetch data. Try again later."
        })

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
