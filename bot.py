import os
import requests
import telebot
from flask import Flask, request

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
API_URL = "https://leakosintapi.com/"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
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
    bot.set_webhook(url="https://xxx-etbu.onrender.com/" + BOT_TOKEN)
    return "Webhook set", 200

# --- Bot Commands ---
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "👋 *Welcome!*\nSend me a phone number or email, and I’ll search leaked databases.")

# --- Handle Queries ---
@bot.message_handler(func=lambda msg: True)
def handle_query(message):
    query = message.text.strip()
    if not query:
        bot.reply_to(message, "⚠️ Please send a phone number or email.")
        return

    # Step 1: Send "Searching…" message
    waiting_msg = bot.reply_to(message, f"🔎 Searching for *{query}* … please wait")

    data = {"token": API_TOKEN, "request": query, "limit": 50, "lang": "en"}

    try:
        response = requests.post(API_URL, json=data).json()

        if "Error code" in response:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=waiting_msg.message_id,
                text=f"❌ API Error: {response['Error code']}"
            )
            return

        if not response.get("List"):
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=waiting_msg.message_id,
                text=f"✅ No leaks found for *{query}*"
            )
            return

        reply_parts = []
        for db, details in response["List"].items():
            section = f"*📂 {db}*\n_{details['InfoLeak']}_\n"
            for record in details["Data"][:5]:  # show only first 5 per DB
                for field, value in record.items():
                    section += f"- `{field}`: {value}\n"
            reply_parts.append(section)

        final_reply = "\n\n".join(reply_parts)

        if len(final_reply) > 4000:
            final_reply = final_reply[:3900] + "\n\n…truncated."

        # Step 2: Replace "Searching…" with results
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=waiting_msg.message_id,
            text=final_reply,
            parse_mode="Markdown"
        )

    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=waiting_msg.message_id,
            text=f"❌ Internal Error: {str(e)}"
        )

# --- Run Flask ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
