import os
import requests
import telebot
from flask import Flask, request

# 🔑 Tokens from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
API_URL = "https://leakosintapi.com/"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- Flask Routes for Webhook ---
@app.route('/' + BOT_TOKEN, methods=['POST'])
def getMessage():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def webhook():
    bot.remove_webhook()
    bot.set_webhook(url="https://xxx-etbu.onrender.com/" + BOT_TOKEN)
    return "Webhook set", 200

# --- Bot Handlers ---
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "👋 Send me a phone number or email, I’ll check the database.")

@bot.message_handler(func=lambda msg: True)
def handle_query(message):
    query = message.text.strip()
    data = {"token": API_TOKEN, "request": query, "limit": 100, "lang": "ru"}

    try:
        response = requests.post(API_URL, json=data).json()

        if "Error code" in response:
            bot.reply_to(message, f"⚠️ Error: {response['Error code']}")
            return

        result_text = []
        for db, details in response.get("List", {}).items():
            result_text.append(f"📂 {db}")
            result_text.append(details["InfoLeak"])
            for record in details["Data"]:
                for field, value in record.items():
                    result_text.append(f"{field}: {value}")
            result_text.append("\n")

        reply = "\n".join(result_text)
        if len(reply) > 4000:  # Telegram message limit
            reply = reply[:4000] + "\n\n...Truncated"

        bot.reply_to(message, reply, parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# --- Run Flask App ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
