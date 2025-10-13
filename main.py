# app.py
import os
import json
import logging
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Config from env
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL1_INVITE_LINK = os.environ.get("CHANNEL1_INVITE_LINK", "")  # invite link for private channel
CHANNEL1_CHAT_ID = os.environ.get("CHANNEL1_CHAT_ID", "")  # optional; recommended for getChatMember
CHANNEL2_CHAT = os.environ.get("CHANNEL2_CHAT_ID_OR_USERNAME", "")  # can be @username or numeric chat_id
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "") or "default-secret"  # optional extra path part

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"

# Helper: send message
def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    r = requests.post(f"{TELEGRAM_API}/sendMessage", data=payload, timeout=10)
    logging.info("send_message: %s %s", r.status_code, r.text)
    return r.json()

# Helper: answer callback queries
def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    return requests.post(f"{TELEGRAM_API}/answerCallbackQuery", data=payload, timeout=10).json()

# Check membership for a single channel/chat
def is_member(user_id, chat_identifier):
    """
    chat_identifier: numeric chat_id (like -100...) or @username
    Returns True/False or None if unknown/error
    """
    try:
        r = requests.get(f"{TELEGRAM_API}/getChatMember", params={"chat_id": chat_identifier, "user_id": user_id}, timeout=10)
        data = r.json()
        if not data.get("ok"):
            logging.warning("getChatMember failed: %s", data)
            return None
        status = data["result"]["status"]
        # statuses: "creator","administrator","member","restricted","left","kicked"
        return status in ("creator", "administrator", "member", "restricted")
    except Exception as e:
        logging.exception("is_member error")
        return None

def build_join_keyboard(remaining):
    """
    remaining: list of dicts like {"label": "Join Channel 1", "url": "..."}
    always add Try Again button as callback
    """
    buttons = []
    for ch in remaining:
        buttons.append([{"text": ch["label"], "url": ch["url"]}])
    # Try Again
    buttons.append([{"text": "✅ Try Again", "callback_data": "try_again"}])
    return {"inline_keyboard": buttons}

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    logging.info("update: %s", update.keys())

    # handle message
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            return handle_start(chat_id, user_id)
        if text.startswith("/help"):
            return handle_help(chat_id)
        if text.startswith("/num"):
            parts = text.strip().split()
            if len(parts) < 2:
                send_message(chat_id, "Usage: /num <10-digit-number>\nExample: /num 9235895648")
                return jsonify(ok=True)
            number = parts[1].strip()
            return handle_num(chat_id, number)
        # other messages: ignore or send hint
        return jsonify(ok=True)

    # handle callback_query
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data")
        user_id = cb["from"]["id"]
        callback_id = cb["id"]
        message = cb.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        if data == "try_again":
            answer_callback(callback_id, text="Checking membership...")
            return handle_start(chat_id, user_id, from_callback=True)
        return jsonify(ok=True)

    return jsonify(ok=True)

def handle_start(chat_id, user_id, from_callback=False):
    # Define channels config (use env placeholders)
    # For join buttons, use invite links (for private) and t.me links (for public)
    ch1_url = CHANNEL1_INVITE_LINK or ""
    ch2_url = f"https://t.me/{CHANNEL2_CHAT.lstrip('@')}" if CHANNEL2_CHAT and CHANNEL2_CHAT.startswith("@") else None
    if CHANNEL2_CHAT and CHANNEL2_CHAT.startswith("-"):
        # numeric id - no direct t.me link; user should provide public username ideally
        ch2_url = None

    # Determine identifiers to pass to getChatMember
    ch1_check_id = CHANNEL1_CHAT_ID if CHANNEL1_CHAT_ID else CHANNEL1_INVITE_LINK or CHANNEL1_CHAT_ID or CHANNEL1_INVITE_LINK
    ch2_check_id = CHANNEL2_CHAT if CHANNEL2_CHAT else CHANNEL2_CHAT

    # Try to check membership for both channels
    mem1 = is_member(user_id, ch1_check_id) if ch1_check_id else None
    mem2 = is_member(user_id, ch2_check_id) if ch2_check_id else None

    # If any check returned None (unknown), we will still provide join buttons and let user try again.
    not_joined = []
    if mem1 is not True:
        not_joined.append({"label": "Join Channel 1", "url": ch1_url or CHANNEL1_INVITE_LINK})
    if mem2 is not True:
        # Prefer t.me link if available
        not_joined.append({"label": "Join Channel 2", "url": ch2_url or (f"https://t.me/{CHANNEL2_CHAT.lstrip('@')}" if CHANNEL2_CHAT else "")})

    if not_joined:
        text = "Please join the following channel(s) to use this bot, then press Try Again:"
        keyboard = build_join_keyboard(not_joined)
        send_message(chat_id, text, reply_markup=keyboard)
        return jsonify(ok=True)

    # both joined
    welcome = "Hello Buddy Welcome To Own Number To Information Bot. click /help how to use bot!!!"
    send_message(chat_id, welcome)
    return jsonify(ok=True)

def handle_help(chat_id):
    help_text = (
        "Commands:\n"
        "/num <10-digit-number>\n\n"
        "Example: `/num 9235895648`\n\n"
        "Notes:\n"
        "- Only 10-digit numbers accepted. If you send /num with 11 digits it will be rejected.\n"
        "- The bot will call the external API `https://yahu.site/api/?number=<number>&key=The_ajay` and forward the JSON response.\n"
    )
    send_message(chat_id, help_text)
    return jsonify(ok=True)

def handle_num(chat_id, number):
    # Validate 10-digit numeric only
    if not number.isdigit() or len(number) != 10:
        send_message(chat_id, "❌ /num accepts only 10 digit numbers. Example: /num 9235895648")
        return jsonify(ok=True)
    # Call external API
    api_url = f"https://yahu.site/api/?number={number}&key=The_ajay"
    try:
        r = requests.get(api_url, timeout=15)
        r.raise_for_status()
        data = r.json()
        # send JSON as pretty text (limit length)
        pretty = json.dumps(data, indent=2)
        if len(pretty) > 3500:
            # too large for single message: send truncated
            pretty = pretty[:3500] + "\n\n[truncated]"
        send_message(chat_id, f"<pre>{pretty}</pre>", parse_mode="HTML")
    except Exception as e:
        logging.exception("API call failed")
        send_message(chat_id, "❌ Failed to fetch data from remote API. Try again later.")
    return jsonify(ok=True)

# utility route to set webhook manually (not production-safe)
@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        return "Set WEBHOOK_URL env var first", 400
    r = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": webhook_url}, timeout=10)
    return jsonify(r.json())

if __name__ == "__main__":
    # local dev
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
