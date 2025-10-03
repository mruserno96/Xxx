import json
import requests
import logging
import os
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext
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
async def start(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Help", callback_data='help'), 
             InlineKeyboardButton("Owner", callback_data='owner')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text('Hello! Welcome to BlackEye Number To Information Bot', reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Sorry, an error occurred while processing your request.")

# Function to show the help message when Help button is clicked
async def help_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Back", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "Use /num <your_phone_number> to search for number information.\nFor example: /num 9239595956",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in help_button callback: {e}")

# Function to show the owner information when Owner button is clicked
async def owner_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Back", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "Owner of Bot:\n@GodAlexMM\n@WinTheBetWithMe",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in owner_button callback: {e}")

# Function to handle the back button to return to the main menu
async def back_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [InlineKeyboardButton("Help", callback_data='help'),
             InlineKeyboardButton("Owner", callback_data='owner')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            'Hello! Welcome to BlackEye Number To Information Bot', reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in back_button callback: {e}")

# Function to handle /num command and fetch number information
async def num(update: Update, context: CallbackContext) -> None:
    try:
        if len(context.args) > 0:
            phone_number = context.args[0]
            url = f"https://yahu.site/api/?number={phone_number}&key=The_ajay"
            response = requests.get(url)
            
            if response.status_code == 200:
                data = response.json()
                result = json.dumps(data, indent=4)
                await update.message.reply_text(f"Number Information:\n{result}")
            else:
                await update.message.reply_text("Failed to fetch data. Please try again later.")
        else:
            await update.message.reply_text("Please provide a number. Example: /num 9239595956")
    except Exception as e:
        logger.error(f"Error in num command: {e}")
        await update.message.reply_text("Sorry, an error occurred while processing your request.")

# Function to handle any unknown commands
async def unknown(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Sorry, I didn't understand that command.")

# Set up the webhook handler
@app.route(f'/{TOKEN}', methods=['POST'])
async def webhook():
    try:
        json_str = request.get_data().decode('UTF-8')
        update = Update.de_json(json_str, application.bot)
        application.process_update(update)
        return 'ok'
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return 'fail'

# Set up the bot with dispatcher for webhook
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

# Main function to set up the bot with webhook
def main():
    global application
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("num", num))
    application.add_handler(CallbackQueryHandler(help_button, pattern='help'))
    application.add_handler(CallbackQueryHandler(owner_button, pattern='owner'))
    application.add_handler(CallbackQueryHandler(back_button, pattern='back'))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Set webhook on start
    set_webhook()

    # Start the Flask app (used to handle the webhook)
    app.run(host="0.0.0.0", port=5000)

if __name__ == '__main__':
    main()
