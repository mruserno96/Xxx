import json
import requests
import logging
import os
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext
from dotenv import load_dotenv
from time import sleep
import threading

# ---------------- Config ----------------
load_dotenv()

TOKEN = os.getenv('BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# Flask app
app = Flask(__name__)

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Handlers ----------------

def start(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Help", callback_data='help'), 
             InlineKeyboardButton("Owner", callback_data='owner')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Hello! Welcome to BlackEye Number To Information Bot', reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        update.message.reply_text("Sorry, an error occurred while processing your request.")

def help_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Back", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.callback_query.answer()
        update.callback_query.message.edit_text(
            "Use /num <your_phone_number> to search for number information.\nFor example: /num 9239595956",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in help_button callback: {e}")

def owner_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Back", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.callback_query.answer()
        update.callback_query.message.edit_text(
            "Owner of Bot:\n@GodAlexMM\n@WinTheBetWithMe",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in owner_button callback: {e}")

def back_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Help", callback_data='help'),
             InlineKeyboardButton("Owner", callback_data='owner')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.callback_query.answer()
        update.callback_query.message.edit_text(
            'Hello! Welcome to BlackEye Number To Information Bot', reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in back_button callback: {e}")

def num(update: Update, context: CallbackContext) -> None:
    try:
        if len(context.args) > 0:
            phone_number = context.args[0]
            url = f"https://yahu.site/api/?number={phone_number}&key=The_ajay"
            response = requests.get(url)
            
            if response.status_code == 200:
                data = response.json()
                result = json.dumps(data, indent=4)
                update.message.reply_text(f"Number Information:\n{result}")
            else:
                update.message.reply_text("Failed to fetch data. Please try again later.")
        else:
            update.message.reply_text("Please provide a number. Example: /num 9239595956")
    except Exception as e:
        logger.error(f"Error in num command: {e}")
        update.message.reply_text("Sorry, an error occurred while processing your request.")

def unknown(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Sorry, I didn't understand that command.")

# ---------------- Webhook Routes ----------------
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('UTF-8')
        update = Update.de_json(json_str, application.bot)
        application.process_update(update)  # Process the update via the Application instance
        return 'OK', 200
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return 'fail', 500

# ---------------- Set Webhook ----------------
def set_webhook():
    try:
        url = f'https://api.telegram.org/bot{TOKEN}/setWebhook?url={WEBHOOK_URL}/{TOKEN}'
        response = requests.get(url)
        if response.status_code == 200:
            print("Webhook set successfully!")
        else:
            print(f"Failed to set webhook. Status code: {response.status_code}")
    except Exception as e:
        logger.error(f"Error in setting webhook: {e}")

# ---------------- Auto-ping and Retry Backoff ----------------
def auto_ping():
    while True:
        try:
            response = requests.get(f'{WEBHOOK_URL}/{TOKEN}')
            if response.status_code != 200:
                logger.error(f"Failed to ping webhook. Status code: {response.status_code}")
            else:
                logger.info("Webhook is alive.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error pinging webhook: {e}")
        sleep(300)  # Auto ping every 5 minutes

# ---------------- Main Function ----------------
def main():
    global application
    # Set up the Application object (bot instance)
    application = Application.builder().token(TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("num", num))
    application.add_handler(CallbackQueryHandler(help_button, pattern='help'))
    application.add_handler(CallbackQueryHandler(owner_button, pattern='owner'))
    application.add_handler(CallbackQueryHandler(back_button, pattern='back'))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Set webhook on start
    set_webhook()

    # Start the Flask app on port 5000 (fixed port)
    app.run(host="0.0.0.0", port=5000)

    # Start auto ping in a separate thread
    threading.Thread(target=auto_ping, daemon=True).start()

if __name__ == '__main__':
    main()
