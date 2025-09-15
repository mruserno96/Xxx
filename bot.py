import os
import json
import time
import threading
import requests
import telebot
from flask import Flask, request
from supabase import create_client, Client

# ------------------ ENV ------------------ #
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OWNER_ID = int(os.getenv("ADMIN_ID", 0))  # Bot owner

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY or not OWNER_ID:
    raise ValueError("All required env variables must be set!")

# ------------------ SUPABASE ------------------ #
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------- OWNER / ADMIN FUNCTIONS ----------- #
def get_owner_token() -> str:
    result = supabase.table("owner").select("api_token").eq("user_id", OWNER_ID).limit(1).execute()
    if result.data and "api_token" in result.data[0]:
        return result.data[0]["api_token"]
    raise ValueError("Owner API token not set!")

def set_owner_token(new_token: str, propagate_to_admins: bool = False):
    # Update owner table
    supabase.table("owner").update({"api_token": new_token}).eq("user_id", OWNER_ID).execute()
    # Optionally update all admin tokens
    if propagate_to_admins:
        supabase.table("admins").update({"api_token": new_token}).execute()

def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    data = supabase.table("admins").select("user_id").eq("user_id", user_id).execute()
    return bool(data.data)

def get_admin_token(user_id: int) -> str:
    if user_id == OWNER_ID:
        return get_owner_token()
    result = supabase.table("admins").select("api_token").eq("user_id", user_id).limit(1).execute()
    if result.data and "api_token" in result.data[0]:
        return result.data[0]["api_token"]
    raise ValueError("API token not found for this admin!")

def add_admin(user_id: int, api_token: str):
    supabase.table("admins").insert({"user_id": user_id, "api_token": api_token}).execute()

def remove_admin(user_id: int):
    supabase.table("admins").delete().eq("user_id", user_id).execute()

# ------------------ TELEGRAM SETUP ------------------ #
API_URL = "https://leakosintapi.com/"
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ------------------ AUTO PING ------------------ #
def auto_ping():
    delay = 60
    max_delay = 3600
    while True:
        try:
            token = get_owner_token()
            payload = {"token": token, "request": "ping", "limit": 1, "lang": "en"}
            resp = requests.post(API_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                delay = 60
        except requests.exceptions.RequestException:
            delay = min(delay * 2, max_delay)
        time.sleep(delay)

threading.Thread(target=auto_ping, daemon=True).start()

# ------------------ WEBHOOK ------------------ #
@app.route("/setwebhook", methods=["GET", "POST"])
def set_webhook():
    bot.remove_webhook()
    url = os.getenv("WEBHOOK_URL", "https://xxx-etbu.onrender.com")
    bot.set_webhook(url=f"{url}/{BOT_TOKEN}")
    return "Webhook set", 200

@app.route('/' + BOT_TOKEN, methods=['POST'])
def get_message():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# ------------------ TELEGRAM HANDLERS ------------------ #
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    if user_id == OWNER_ID:
        bot.reply_to(message, "👑 Welcome Owner! Full access granted.")
    elif is_admin(user_id):
        bot.reply_to(message, "👋 Welcome Admin! You can search numbers, emails, and names.")
    else:
        bot.reply_to(
            message,
            "✨ Hello! This is **Number To Information Bot**.\n\n"
            "🔒 Premium Feature: To access search, please contact Admin @WinTheBetWithMe",
            parse_mode="Markdown"
        )

@bot.message_handler(commands=['settoken'])
def settoken_cmd(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        args = message.text.split()
        new_token = args[1]
        propagate = args[2].lower() == "true" if len(args) > 2 else False
        set_owner_token(new_token, propagate_to_admins=propagate)
        bot.reply_to(message, f"✅ API token updated successfully. Propagate to admins: {propagate}")
    except IndexError:
        bot.reply_to(message, "⚠️ Usage: /settoken NEW_API_TOKEN [true|false]")

@bot.message_handler(commands=['help'])
def help_cmd(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "ℹ️ Only the bot owner can see help.")
        return
    bot.reply_to(
        message,
        "🛠 Owner Commands:\n"
        "/help - Show this help\n"
        "/settoken <token> [true|false] - Update owner token and optionally all admins\n"
        "/addadmin <user_id> <api_token> - Add admin\n"
        "/removeadmin <user_id> - Remove admin\n"
        "Admins and owner can search numbers, emails, names"
    )

@bot.message_handler(commands=['addadmin'])
def addadmin_cmd(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        new_admin_id = int(message.text.split()[1])
        api_token = message.text.split()[2]
        add_admin(new_admin_id, api_token)
        bot.reply_to(message, f"✅ User {new_admin_id} added as admin.")
    except Exception:
        bot.reply_to(message, "⚠️ Usage: /addadmin <user_id> <api_token>")

@bot.message_handler(commands=['removeadmin'])
def removeadmin_cmd(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        remove_id = int(message.text.split()[1])
        remove_admin(remove_id)
        bot.reply_to(message, f"✅ User {remove_id} removed from admins.")
    except Exception:
        bot.reply_to(message, "⚠️ Usage: /removeadmin <user_id>")

@bot.message_handler(func=lambda m: True)
def handle_query(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.reply_to(
            message,
            "⛔ Sorry! Only **Premium Users** can use this feature.\n"
            "🔒 Contact Admin @WinTheBetWithMe",
            parse_mode="Markdown"
        )
        return

    query = message.text.strip()
    waiting_msg = bot.reply_to(message, f"🔎 Searching for *{query}* … please wait", parse_mode="Markdown")
    bot.send_chat_action(message.chat.id, 'typing')

    def call_api_with_retry(payload, max_retries=5, backoff_factor=2):
        delay = 1
        for attempt in range(max_retries):
            try:
                resp = requests.post(API_URL, json=payload, timeout=15)
                return resp.json()
            except (requests.exceptions.RequestException, json.JSONDecodeError):
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= backoff_factor
                else:
                    return {"Error code": "API request failed after retries"}

    api_token = get_admin_token(user_id)
    payload = {"token": api_token, "request": query, "limit": 100, "lang": "en"}
    resp = call_api_with_retry(payload)

    if isinstance(resp, dict) and "Error code" in resp:
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

# ------------------ RUN ------------------ #
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
