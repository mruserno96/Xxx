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

    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            handle_start(chat_id, user_id)
        elif text.startswith("/help"):
            handle_help(chat_id)
        elif text.startswith("/num"):
            parts = text.split()
            if len(parts) < 2:
                send_message(chat_id, "Usage: /num <10-digit-number>\nExample: /num 9235895648")
            else:
                handle_num(chat_id, parts[1])
        else:
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
            "Please join both channels below to use this bot, then press Try Again 👇",
            reply_markup=build_join_keyboard(not_joined)
        )
    else:
        send_message(chat_id, "Hello Buddy 👋 Welcome to Our Number To Information Bot.\nClick /help to learn how to use the bot!!!")

def handle_help(chat_id):
    help_text = (
        "📘 *How To Use This Bot*\n\n"
        "➡️ `/num <10-digit-number>` — Example: `/num 9235895648`\n\n"
        "📌 Rules:\n"
        "• Only 10-digit Indian numbers accepted (without +91).\n"
        "• If you enter 11 digits or letters, it will be rejected.\n"
        "• Reply will contain information about the given number.\n"
    )
    send_message(chat_id, help_text, parse_mode="Markdown")

def handle_num(chat_id, number):
    if not number.isdigit() or len(number) != 10:
        send_message(chat_id, "❌ Only 10-digit numbers allowed. Example: /num 9235895648")
        return

    api_url = f"https://yahu.site/api/?number={number}&key=The_ajay"
    try:
        r = session.get(api_url, timeout=15)
        r.raise_for_status()
        data = r.json()
        pretty = json.dumps(data, indent=2)
        if len(pretty) > 3500:
            pretty = pretty[:3500] + "\n\n[truncated]"
        # Send the data neatly as text, not code block to avoid confusion
        send_message(chat_id, pretty)
    except Exception as e:
        logging.exception("API fetch failed: %s", e)
        send_message(chat_id, "⚠️ Failed to fetch data. Try again later.")

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
