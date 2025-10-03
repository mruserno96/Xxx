import json
import requests
import logging
import os
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext, Dispatcher
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get the BOT_TOKEN and WEBHOOK_URL from environment variables
TOKEN = os.getenv('BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# Flask App for Webhook
app = Flask(__name__)

# Set up logging to get error logs
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Function to send the start message and inline keyboard
def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Help", callback_data='help'), 
         InlineKeyboardButton("Owner", callback_data='owner')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text('Hello! Welcome to BlackEye Number To Information Bot', reply_markup=reply_markup)

# Function to show the help message when Help button is clicked
def help_button(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Back", callback_data='back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.callback_query.answer()
    update.callback_query.message.edit_text(
        "Use /num <your_phone_number> to search for number information.\nFor example: /num 9239595956",
        reply_markup=reply_markup
    )

# Function to show the owner information when Owner button is clicked
def owner_button(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Back", callback_data='back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.callback_query.answer()
    update.callback_query.message.edit_text(
        "Owner of Bot:\n@GodAlexMM\n@WinTheBetWithMe",
        reply_markup=reply_markup
    )

# Function to handle the back button to return to the main menu
def back_button(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("Help", callback_data='help'),
         InlineKeyboardButton("Owner", callback_data='owner')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.callback_query.answer()
    update.callback_query.message.edit_text(
        'Hello! Welcome to BlackEye Number To Information Bot', reply_markup=reply_markup
    )

# Function to handle /num command and fetch number information
def num(update: Update, context: CallbackContext) -> None:
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

# Function to handle any unknown commands
def unknown(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("Sorry, I didn't understand that command.")

# Set up the webhook handler
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = Update.de_json(json_str, dispatcher.bot)
    dispatcher.process_update(update)
    return 'ok'

# Set up the bot with dispatcher for webhook
def set_webhook():
    url = f'https://api.telegram.org/bot{TOKEN}/setWebhook?url={WEBHOOK_URL}/{TOKEN}'
    response = requests.get(url)
    if response.status_code == 200:
        print("Webhook set successfully!")
    else:
        print(f"Failed to set webhook. Status code: {response.status_code}")

# Main function to set up the bot with webhook
def main():
    global dispatcher
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("num", num))
    dispatcher.add_handler(CallbackQueryHandler(help_button, pattern='help'))
    dispatcher.add_handler(CallbackQueryHandler(owner_button, pattern='owner'))
    dispatcher.add_handler(CallbackQueryHandler(back_button, pattern='back'))
    dispatcher.add_handler(MessageHandler(Filters.command, unknown))

    # Set webhook on start
    set_webhook()

    # Start the Flask app (used to handle the webhook)
    app.run(host="0.0.0.0", port=5000)

if __name__ == '__main__':
    main()
