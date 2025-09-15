import os
import json
import requests
import telebot
from flask import Flask, request

# GitHub Gist ID और Personal Access Token
GIST_ID = "40289f54f8e2c1eb3ba2894ab477f5cd"
GITHUB_TOKEN = "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"
API_URL = "https://leakosintapi.com/"

# Telegram Bot Token और Admin ID
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8356178010

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Gist URL
GIST_URL = f"https://api.github.com/gists/{GIST_ID}"

# Gist से API Token लोड करें
def load_token():
    response = requests.get(GIST_URL)
    data = response.json()
    content = data['files']['local.json']['content']
    return json.loads(content)['API_TOKEN']

# Gist में API Token सेव करें
def save_token(new_token):
    content = json.dumps({"API_TOKEN": new_token}, indent=2)
    payload = {
        "files": {
            "local.json": {
                "content": content
            }
        }
    }
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    response = requests.patch(GIST_URL, headers=headers, json=payload)
    return response.status_code

# Webhook सेट करें
@app.route("/setwebhook", methods=["GET", "POST"])
def set_webhook():
    bot.remove_webhook()
    url = os.getenv("WEBHOOK_URL", "https://xxx-etbu.onrender.com")
    bot.set_webhook(url=f"{url}/{BOT_TOKEN}")
    return "Webhook set", 200

# Telegram से आने वाले संदेशों को हैंडल करें
@app.route('/' + BOT_TOKEN, methods=['POST'])
def getMessage():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# /start कमांड हैंडलर
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "👋 Send a phone number or email — I'll check leaked databases.")

# /settoken कमांड हैंडलर (केवल Admin के लिए)
@bot.message_handler(commands=['settoken'])
def set_token(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Not authorized")
        return

    try:
        new_token = message.text.split(maxsplit=1)[1]
    except IndexError:
        bot.reply_to(message, "⚠️ Usage: /settoken NEW_API_TOKEN")
        return

    status_code = save_token(new_token)
    if status_code == 200:
        bot.reply_to(message, "✅ API token updated and saved in Gist")
    else:
        bot.reply_to(message, f"❌ Failed to update token: {status_code}")

# सामान्य संदेश हैंडलर
@bot.message_handler(func=lambda m: True)
def handle_query(message):
    query = message.text.strip()
    if not query:
        bot.reply_to(message, "⚠️ Please send a phone number or email.")
        return

    waiting_msg = bot.reply_to(message, f"🔎 Searching for *{query}* … please wait")
    bot.send_chat_action(message.chat.id, 'typing')

    def call_api(limit):
        payload = {"token": load_token(), "request": query, "limit": limit, "lang": "en"}
        resp = requests.post(API_URL, json=payload)
        try:
            return resp.json()
        except Exception:
            return {"Error code": "Invalid JSON response from API"}

    # First try with default limit
    resp = call_api(100)

    # If API returns a limit-related error, retry with 100
    if isinstance(resp, dict) and "Error code" in resp:
        err_text = str(resp["Error code"]).lower()
        if "limit" in err_text or "100" in err_text and "10000" in err_text:
            resp = call_api(100)  # retry with minimum acceptable value
            if isinstance(resp, dict) and "Error code" in resp:
                bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                                      text=f"❌ API Error: {resp['Error code']}")
                return
        else:
            bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                                  text=f"❌ API Error: {resp['Error code']}")
            return

    # No list found
    if not resp.get("List"):
        bot.edit_message_text(chat_id=message.chat.id, message_id=waiting_msg.message_id,
                              text=f"✅ No leaks found for *{query}*")
        return

    # Build reply (show first 5 records per DB)
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
