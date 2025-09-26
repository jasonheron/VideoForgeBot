import os
import logging
import json
import asyncio
from typing import Dict, Optional
from flask import Flask, request, jsonify
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, ContentType, BufferedInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import requests
from threading import Thread

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_telegram_bot_token_here")
KIE_AI_API_KEY = os.getenv("KIE_AI_API_KEY", "your_kie_ai_api_key_here")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "your_payment_provider_token")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-repl-url.replit.dev")

# Initialize Flask app
app = Flask(__name__)

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Credit tracking (in memory - in production, use a database)
user_credits: Dict[int, int] = {}
user_models: Dict[int, str] = {}  # Store selected model per user
pending_generations: Dict[str, dict] = {}  # Track pending generations

# Available models
AVAILABLE_MODELS = {
    "veo3_fast": "Veo 3 Fast - Quick generation",
    "kling_v2.1": "Kling v2.1 - High quality"
}

# FSM States
class GenerationStates(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_image = State()

# Helper functions
def get_user_credits(user_id: int) -> int:
    """Get user credits"""
    return user_credits.get(user_id, 0)

def add_credits(user_id: int, amount: int):
    """Add credits to user"""
    if user_id not in user_credits:
        user_credits[user_id] = 0
    user_credits[user_id] += amount
    logger.info(f"Added {amount} credits to user {user_id}. Total: {user_credits[user_id]}")

def deduct_credits(user_id: int, amount: int) -> bool:
    """Deduct credits from user. Returns True if successful."""
    current_credits = get_user_credits(user_id)
    if current_credits >= amount:
        user_credits[user_id] = current_credits - amount
        logger.info(f"Deducted {amount} credits from user {user_id}. Remaining: {user_credits[user_id]}")
        return True
    return False

def create_model_selection_keyboard():
    """Create inline keyboard for model selection"""
    keyboard = []
    for model_key, model_name in AVAILABLE_MODELS.items():
        keyboard.append([InlineKeyboardButton(
            text=model_name, 
            callback_data=f"model_{model_key}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def send_to_kie_api(prompt: str, model: str, image_file=None) -> str:
    """Send request to KIE.ai API"""
    api_url = "https://api.kie.ai/v1/video/generate"  # Example URL - adjust based on actual API
    
    headers = {
        "Authorization": f"Bearer {KIE_AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "prompt": prompt,
        "model": model,
        "callback_url": f"{WEBHOOK_URL}/kie_callback"
    }
    
    # If image is provided, handle image upload
    if image_file:
        # In a real implementation, you'd upload the image first
        # and get a URL or ID to include in the request
        data["image"] = image_file
    
    try:
        response = requests.post(api_url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        return result.get("generation_id", "unknown")
    except Exception as e:
        logger.error(f"Error sending to KIE API: {e}")
        raise

# Bot command handlers
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command"""
    if not message.from_user:
        return
    user_id = message.from_user.id
    credits = get_user_credits(user_id)
    
    welcome_text = f"""
🎬 Welcome to the AI Video Generator Bot!

Your current credits: {credits}

Available commands:
/start - Show this message
/generate - Generate a video
/buy - Purchase credits

Each video generation costs 1 credit ($1.30 equivalent).
"""
    
    await message.answer(welcome_text)

@dp.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext):
    """Handle /generate command"""
    if not message.from_user:
        return
    user_id = message.from_user.id
    credits = get_user_credits(user_id)
    
    if credits < 1:
        await message.answer(
            "❌ You don't have enough credits to generate a video.\n"
            "Use /buy to purchase credits."
        )
        return
    
    # Check if user has a selected model
    if user_id not in user_models:
        keyboard = create_model_selection_keyboard()
        await message.answer(
            "🤖 Please select a model for video generation:",
            reply_markup=keyboard
        )
        return
    
    # User has a model, ask for prompt
    model_name = AVAILABLE_MODELS[user_models[user_id]]
    await message.answer(
        f"✨ Selected model: {model_name}\n\n"
        "📝 Please enter your text prompt for video generation:"
    )
    await state.set_state(GenerationStates.waiting_for_prompt)

@dp.callback_query(F.data.startswith("model_"))
async def process_model_selection(callback: CallbackQuery, state: FSMContext):
    """Handle model selection"""
    if not callback.from_user or not callback.data:
        return
        
    user_id = callback.from_user.id
    model_key = callback.data.split("_", 1)[1]
    
    if model_key in AVAILABLE_MODELS:
        user_models[user_id] = model_key
        model_name = AVAILABLE_MODELS[model_key]
        
        if callback.message and hasattr(callback.message, 'edit_text'):
            try:
                await callback.message.edit_text(
                    f"✅ Model selected: {model_name}\n\n"
                    "📝 Please enter your text prompt for video generation:"
                )
            except Exception as e:
                # If editing fails, send a new message
                await bot.send_message(
                    callback.from_user.id,
                    f"✅ Model selected: {model_name}\n\n"
                    "📝 Please enter your text prompt for video generation:"
                )
        elif callback.message:
            # Message exists but doesn't support editing, send new message
            await bot.send_message(
                callback.from_user.id,
                f"✅ Model selected: {model_name}\n\n"
                "📝 Please enter your text prompt for video generation:"
            )
        await state.set_state(GenerationStates.waiting_for_prompt)
    
    await callback.answer()

@dp.message(GenerationStates.waiting_for_prompt)
async def process_prompt(message: Message, state: FSMContext):
    """Handle text prompt input"""
    if not message.text:
        await message.answer("Please provide a text prompt.")
        return
    prompt = message.text
    await state.update_data(prompt=prompt)
    
    await message.answer(
        "🖼️ You can now upload an image (optional) or type 'skip' to proceed without an image:"
    )
    await state.set_state(GenerationStates.waiting_for_image)

@dp.message(GenerationStates.waiting_for_image)
async def process_image_or_skip(message: Message, state: FSMContext):
    """Handle image upload or skip"""
    if not message.from_user:
        return
        
    user_id = message.from_user.id
    data = await state.get_data()
    prompt = data.get("prompt")
    
    if not prompt:
        await message.answer("Error: No prompt found. Please start over with /generate")
        await state.clear()
        return
    model = user_models.get(user_id, "veo3_fast")
    
    image_file = None
    
    if message.text and message.text.lower() == "skip":
        # No image, proceed with generation
        pass
    elif message.photo:
        # User uploaded an image
        photo = message.photo[-1]  # Get the highest resolution
        file = await bot.get_file(photo.file_id)
        # In a real implementation, you'd download and process the image
        image_file = file.file_path
    else:
        await message.answer("❌ Please upload an image or type 'skip' to proceed.")
        return
    
    # Deduct credits
    if not deduct_credits(user_id, 1):
        await message.answer("❌ Insufficient credits!")
        await state.clear()
        return
    
    # Send to KIE.ai API
    try:
        await message.answer("🎬 Starting video generation... This may take a few minutes.")
        generation_id = await send_to_kie_api(prompt, model, image_file)
        
        # Store pending generation
        pending_generations[generation_id] = {
            "user_id": user_id,
            "prompt": prompt,
            "model": model
        }
        
        await message.answer(
            f"✅ Video generation started!\n"
            f"Generation ID: {generation_id}\n"
            f"You'll receive the video when it's ready."
        )
        
    except Exception as e:
        # Refund credits on error
        add_credits(user_id, 1)
        await message.answer(f"❌ Error starting generation: {str(e)}\nCredits refunded.")
        logger.error(f"Generation error for user {user_id}: {e}")
    
    await state.clear()

@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    """Handle /buy command - Telegram Stars payment"""
    if not message.from_user:
        return
    user_id = message.from_user.id
    
    # Create invoice for 1 credit = $1.30 equivalent in XTR
    # 1 Star = $0.013, so $1.30 = 100 Stars
    price = LabeledPrice(label="1 Video Credit", amount=100)  # Amount in Stars
    
    await bot.send_invoice(
        chat_id=user_id,
        title="Video Generation Credit",
        description="Purchase 1 credit to generate a video ($1.30 equivalent)",
        payload="credit_purchase_1",
        provider_token="",  # Empty for Telegram Stars (XTR)
        currency="XTR",  # Telegram Stars
        prices=[price]
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Handle pre-checkout query"""
    if pre_checkout_query.id:
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: Message):
    """Handle successful payment"""
    if not message.from_user or not message.successful_payment:
        return
        
    user_id = message.from_user.id
    payment = message.successful_payment
    
    if payment.invoice_payload == "credit_purchase_1":
        add_credits(user_id, 1)
        await message.answer(
            "✅ Payment successful!\n"
            f"1 credit added to your account.\n"
            f"Total credits: {get_user_credits(user_id)}"
        )

# Flask routes
@app.route('/kie_callback', methods=['POST'])
def kie_callback():
    """Handle KIE.ai API callbacks"""
    try:
        data = request.get_json()
        generation_id = data.get('generation_id')
        status = data.get('status')
        video_url = data.get('video_url')
        
        if generation_id not in pending_generations:
            logger.warning(f"Unknown generation_id: {generation_id}")
            return jsonify({"error": "Unknown generation_id"}), 400
        
        generation_info = pending_generations[generation_id]
        user_id = generation_info['user_id']
        
        if status == 'completed' and video_url:
            # Send video to user
            asyncio.create_task(send_video_to_user(user_id, video_url, generation_id))
        elif status == 'failed':
            # Refund credits
            add_credits(user_id, 1)
            asyncio.create_task(send_failure_message(user_id, generation_id))
        
        # Clean up
        if generation_id in pending_generations:
            del pending_generations[generation_id]
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        logger.error(f"Error processing callback: {e}")
        return jsonify({"error": str(e)}), 500

async def send_video_to_user(user_id: int, video_url: str, generation_id: str):
    """Send completed video to user"""
    try:
        # Download video and send to user
        response = requests.get(video_url)
        response.raise_for_status()
        
        video_file = BufferedInputFile(response.content, filename=f"video_{generation_id}.mp4")
        
        await bot.send_video(
            chat_id=user_id,
            video=video_file,
            caption="🎬 Your video is ready!"
        )
    except Exception as e:
        logger.error(f"Error sending video to user {user_id}: {e}")
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Video generated successfully!\nDownload: {video_url}"
        )

async def send_failure_message(user_id: int, generation_id: str):
    """Send failure message to user"""
    try:
        await bot.send_message(
            chat_id=user_id,
            text="❌ Video generation failed. Your credit has been refunded."
        )
    except Exception as e:
        logger.error(f"Error sending failure message to user {user_id}: {e}")

@app.route('/')
def index():
    """Basic health check"""
    return jsonify({"status": "Bot is running", "webhook_url": f"{WEBHOOK_URL}/kie_callback"})

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "pending_generations": len(pending_generations)})

def run_flask():
    """Run Flask app"""
    app.run(host='0.0.0.0', port=5000, debug=False)

async def main():
    """Main function to run the bot"""
    # Set webhook URL for Telegram
    webhook_url = f"{WEBHOOK_URL}/webhook"
    
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logger.info("Bot starting...")
    logger.info(f"Webhook URL: {webhook_url}")
    logger.info(f"Callback URL: {WEBHOOK_URL}/kie_callback")
    
    # Start polling instead of webhook for development
    # In production, you might want to use webhooks
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())