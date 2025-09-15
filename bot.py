import os
import json
import requests
import telebot
from flask import Flask, request

BOT_TOKEN = os.getenv("BOT_TOKEN")  # सिर्फ़ Telegram bot का token env से लेंगे
API_URL = "https://leakosintapi.com/"
ADMIN_ID = int(os.getenv("ADMIN_ID", "8356178010"))  # अपना Telegram user id डालें

# ---- Load API_TOKEN from local.json ----
CONFIG_FILE = "local.json"

def load_token():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            return data.get("API_TOKEN")
    return None

def save_token(token):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"API_TOKEN": token}, f)

API_TOKEN = load_token()  # startup पर load होगा

# default limit (must be 100..10000)
DEFAULT_LIMIT = int(os.getenv("DEFAULT_LIMIT", "100"))
if DEFAULT_LIMIT < 100:
    DEFAULT_LIMIT = 100
elif DEFAULT_LIMIT > 10000:
    DEFAULT_LIMIT = 10000

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route('/' + BOT_TOKEN, methods=['POST'])
def getMessage():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def webhook():
    bot.remove_webhook()
    url = os.getenv("WEBHOOK_URL", "https://xxx-etbu.onrender.com")
    bot.set_webhook(url=f"{url}/{BOT_TOKEN}")
    return "Webhook set", 200

@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "👋 Send a phone number or email — I'll check leaked databases.")

# ✅ Admin command: set new API token
@bot.message_handler(commands=['settoken'])
def set_token(message):
    global API_TOKEN
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ Not authorized")
        return

    try:
        new_token = message.text.split(maxsplit=1)[1]
    except IndexError:
        bot.reply_to(message, "⚠️ Usage: /settoken NEW_API_TOKEN")
        return

    API_TOKEN = new_token
    save_token(API_TOKEN)
    bot.reply_to(message, f"✅ API token updated & saved:\n`{API_TOKEN}`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def handle_query(message):
    global API_TOKEN
    query = message.text.strip()
    if not query:
        bot.reply_to(message, "⚠️ Please send a phone number or email.")
        return

    waiting_msg = bot.reply_to(message, f"🔎 Searching for *{query}* … please wait")
    bot.send_chat_action(message.chat.id, 'typing')

    def call_api(limit):
        payload = {"token": API_TOKEN, "request": query, "limit": limit, "lang": "en"}
        resp = requests.post(API_URL, json=payload)
        try:
            return resp.json()
        except Exception:
            return {"Error code": "Invalid JSON response from API"}

    resp = call_api(DEFAULT_LIMIT)
    if isinstance(resp, dict) and "Error code" in resp:
        err_text = str(resp["Error code"]).lower()
        if "limit" in err_text or "100" in err_text and "10000" in err_text:
            resp = call_api(100)
            if isinstance(resp, dict) and "Error code" in resp:
                bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                                      text=f"❌ API Error: {resp['Error code']}")
                return
        else:
            bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                                  text=f"❌ API Error: {resp['Error code']}")
            return

    if not resp.get("List"):
        bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                              text=f"✅ No leaks found for *{query}*")
        return

    parts = []
    for db, details in resp.get("List", {}).items():
        parts.append(f"*📂 {db}*\n_{details.get('InfoLeak','')}_\n")
        for record in details.get("Data", [])[:5]:
            for field, value in record.items():
                parts.append(f"`{field}`: {value}")
        parts.append("")

    final = "\n".join(parts)
    if len(final) > 4000:
        final = final[:3900] + "\n\n…truncated."

    bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                          text=final, parse_mode="Markdown")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
