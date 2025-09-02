import os
import requests
import telebot

# 🔑 Tokens from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
API_URL = "https://leakosintapi.com/"

bot = telebot.TeleBot(BOT_TOKEN)

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
        for db, details in response["List"].items():
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

print("🤖 Bot is running...")
bot.infinity_polling()
