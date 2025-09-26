import os
import logging
import json
import asyncio
import hmac
import hashlib
import tempfile
from typing import Dict, Optional
from aiohttp import web, ClientSession
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, ContentType, BufferedInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiofiles

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_telegram_bot_token_here")
KIE_AI_API_KEY = os.getenv("KIE_AI_API_KEY", "your_kie_ai_api_key_here")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "your_payment_provider_token")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-repl-url.replit.dev")
CALLBACK_SECRET = os.getenv("CALLBACK_SECRET", "your_callback_secret_key_here")  # New security key

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Global HTTP client session
http_session: Optional[ClientSession] = None

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

def verify_callback_signature(payload: bytes, signature: str) -> bool:
    """Verify HMAC signature for callback authentication"""
    try:
        expected_signature = hmac.new(
            CALLBACK_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logger.error(f"Error verifying callback signature: {e}")
        return False

async def download_telegram_file(file_id: str) -> Optional[bytes]:
    """Download file from Telegram and return its content"""
    try:
        file_info = await bot.get_file(file_id)
        if not file_info.file_path:
            logger.error(f"No file path for file_id: {file_id}")
            return None
            
        # Download file using aiohttp
        global http_session
        if not http_session:
            http_session = ClientSession()
            
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        
        async with http_session.get(file_url) as response:
            if response.status == 200:
                content = await response.read()
                logger.info(f"Successfully downloaded file {file_id}, size: {len(content)} bytes")
                return content
            else:
                logger.error(f"Failed to download file {file_id}: HTTP {response.status}")
                return None
                
    except Exception as e:
        logger.error(f"Error downloading Telegram file {file_id}: {e}")
        return None

async def upload_image_to_temporary_storage(image_content: bytes, filename: str) -> Optional[str]:
    """Upload image to temporary storage and return accessible URL/path"""
    try:
        # Create temporary file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"telegram_image_{filename}")
        
        # Write image content to temporary file
        async with aiofiles.open(temp_path, 'wb') as f:
            await f.write(image_content)
        
        logger.info(f"Image saved to temporary storage: {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"Error uploading image to temporary storage: {e}")
        return None

async def send_to_kie_api(prompt: str, model: str, image_path: Optional[str] = None) -> str:
    """Send request to KIE.ai API using aiohttp"""
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
    
    # If image is provided, include it in the request
    if image_path:
        # In a real implementation, you might need to upload the image first
        # and get a URL or ID to include in the request
        data["image_path"] = image_path
    
    try:
        global http_session
        if not http_session:
            http_session = ClientSession()
            
        async with http_session.post(api_url, headers=headers, json=data) as response:
            if response.status == 200:
                result = await response.json()
                return result.get("generation_id", "unknown")
            else:
                error_text = await response.text()
                raise Exception(f"KIE API error: HTTP {response.status} - {error_text}")
                
    except Exception as e:
        logger.error(f"Error sending to KIE API: {e}")
        raise

# Bot command handlers
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command"""
    if not message.from_user:
        return
        
    try:
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
        
    except Exception as e:
        logger.error(f"Error in cmd_start: {e}")
        await message.answer("❌ An error occurred. Please try again later.")

@dp.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext):
    """Handle /generate command"""
    if not message.from_user:
        return
        
    try:
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
        
    except Exception as e:
        logger.error(f"Error in cmd_generate: {e}")
        await message.answer("❌ An error occurred. Please try again later.")

@dp.callback_query(F.data.startswith("model_"))
async def process_model_selection(callback: CallbackQuery, state: FSMContext):
    """Handle model selection"""
    if not callback.from_user or not callback.data:
        return
        
    try:
        user_id = callback.from_user.id
        model_key = callback.data.split("_", 1)[1]
        
        if model_key in AVAILABLE_MODELS:
            user_models[user_id] = model_key
            model_name = AVAILABLE_MODELS[model_key]
            
            if callback.message and hasattr(callback.message, 'edit_text'):
                try:
                    await callback.message.edit_text(  # type: ignore
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
        
    except Exception as e:
        logger.error(f"Error in process_model_selection: {e}")
        await callback.answer("❌ An error occurred. Please try again later.")

@dp.message(GenerationStates.waiting_for_prompt)
async def process_prompt(message: Message, state: FSMContext):
    """Handle text prompt input"""
    try:
        if not message.text:
            await message.answer("Please provide a text prompt.")
            return
            
        prompt = message.text
        await state.update_data(prompt=prompt)
        
        await message.answer(
            "🖼️ You can now upload an image (optional) or type 'skip' to proceed without an image:"
        )
        await state.set_state(GenerationStates.waiting_for_image)
        
    except Exception as e:
        logger.error(f"Error in process_prompt: {e}")
        await message.answer("❌ An error occurred. Please try again later.")
        await state.clear()

@dp.message(GenerationStates.waiting_for_image)
async def process_image_or_skip(message: Message, state: FSMContext):
    """Handle image upload or skip"""
    if not message.from_user:
        return
        
    try:
        user_id = message.from_user.id
        data = await state.get_data()
        prompt = data.get("prompt")
        
        if not prompt:
            await message.answer("Error: No prompt found. Please start over with /generate")
            await state.clear()
            return
            
        model = user_models.get(user_id, "veo3_fast")
        image_path = None
        
        if message.text and message.text.lower() == "skip":
            # No image, proceed with generation
            pass
        elif message.photo:
            # User uploaded an image - properly download it
            try:
                photo = message.photo[-1]  # Get the highest resolution
                await message.answer("⏳ Downloading image...")
                
                # Download the image from Telegram
                image_content = await download_telegram_file(photo.file_id)
                if image_content:
                    # Upload to temporary storage
                    filename = f"{photo.file_id}.jpg"
                    image_path = await upload_image_to_temporary_storage(image_content, filename)
                    if image_path:
                        await message.answer("✅ Image downloaded and processed successfully!")
                    else:
                        await message.answer("❌ Failed to process image. Proceeding without image.")
                else:
                    await message.answer("❌ Failed to download image. Proceeding without image.")
                    
            except Exception as e:
                logger.error(f"Error processing image: {e}")
                await message.answer("❌ Error processing image. Proceeding without image.")
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
            generation_id = await send_to_kie_api(prompt, model, image_path)
            
            # Store pending generation
            pending_generations[generation_id] = {
                "user_id": user_id,
                "prompt": prompt,
                "model": model,
                "image_path": image_path
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
        
    except Exception as e:
        logger.error(f"Error in process_image_or_skip: {e}")
        await message.answer("❌ An error occurred. Please try again later.")
        await state.clear()

@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    """Handle /buy command - Telegram Stars payment"""
    if not message.from_user:
        return
        
    try:
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
        
    except Exception as e:
        logger.error(f"Error in cmd_buy: {e}")
        await message.answer("❌ An error occurred. Please try again later.")

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Handle pre-checkout query"""
    try:
        if pre_checkout_query.id:
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    except Exception as e:
        logger.error(f"Error in process_pre_checkout: {e}")

@dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: Message):
    """Handle successful payment"""
    if not message.from_user or not message.successful_payment:
        return
        
    try:
        user_id = message.from_user.id
        payment = message.successful_payment
        
        if payment.invoice_payload == "credit_purchase_1":
            add_credits(user_id, 1)
            await message.answer(
                "✅ Payment successful!\n"
                f"1 credit added to your account.\n"
                f"Total credits: {get_user_credits(user_id)}"
            )
            
    except Exception as e:
        logger.error(f"Error in process_successful_payment: {e}")
        await message.answer("❌ An error occurred processing your payment. Please contact support.")

# aiohttp web handlers
async def kie_callback(request):
    """Handle KIE.ai API callbacks with HMAC authentication"""
    try:
        # Get request body and signature
        body = await request.read()
        signature = request.headers.get('X-Signature', '')
        
        # Verify signature for security
        if not verify_callback_signature(body, signature):
            logger.warning("Invalid callback signature")
            return web.json_response({"error": "Invalid signature"}, status=401)
        
        # Parse JSON data
        try:
            data = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in callback: {e}")
            return web.json_response({"error": "Invalid JSON"}, status=400)
        
        generation_id = data.get('generation_id')
        status = data.get('status')
        video_url = data.get('video_url')
        
        if not generation_id:
            logger.warning("No generation_id in callback")
            return web.json_response({"error": "Missing generation_id"}, status=400)
        
        if generation_id not in pending_generations:
            logger.warning(f"Unknown generation_id: {generation_id}")
            return web.json_response({"error": "Unknown generation_id"}, status=400)
        
        generation_info = pending_generations[generation_id]
        user_id = generation_info['user_id']
        
        if status == 'completed' and video_url:
            # Send video to user
            await send_video_to_user(user_id, video_url, generation_id)
        elif status == 'failed':
            # Refund credits
            add_credits(user_id, 1)
            await send_failure_message(user_id, generation_id)
        else:
            logger.warning(f"Unknown callback status: {status}")
        
        # Clean up
        if generation_id in pending_generations:
            # Clean up temporary image file if it exists
            image_path = generation_info.get('image_path')
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    logger.info(f"Cleaned up temporary image file: {image_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up image file {image_path}: {e}")
            
            del pending_generations[generation_id]
        
        return web.json_response({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Error processing callback: {e}")
        return web.json_response({"error": "Internal server error"}, status=500)

async def send_video_to_user(user_id: int, video_url: str, generation_id: str):
    """Send completed video to user using aiohttp"""
    try:
        global http_session
        if not http_session:
            http_session = ClientSession()
            
        # Download video using aiohttp
        async with http_session.get(video_url) as response:
            if response.status == 200:
                video_content = await response.read()
                
                # Create video file for sending
                video_file = BufferedInputFile(video_content, filename=f"video_{generation_id}.mp4")
                
                await bot.send_video(
                    chat_id=user_id,
                    video=video_file,
                    caption="🎬 Your video is ready!"
                )
                logger.info(f"Successfully sent video to user {user_id}")
            else:
                # If download fails, send the URL instead
                await bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Video generated successfully!\nDownload: {video_url}"
                )
                logger.warning(f"Could not download video from {video_url}, sent URL instead")
                
    except Exception as e:
        logger.error(f"Error sending video to user {user_id}: {e}")
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ Video generated successfully!\nDownload: {video_url}"
            )
        except Exception as send_error:
            logger.error(f"Error sending fallback message to user {user_id}: {send_error}")

async def send_failure_message(user_id: int, generation_id: str):
    """Send failure message to user"""
    try:
        await bot.send_message(
            chat_id=user_id,
            text="❌ Video generation failed. Your credit has been refunded."
        )
        logger.info(f"Sent failure message to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending failure message to user {user_id}: {e}")

async def index_handler(request):
    """Basic health check"""
    return web.json_response({
        "status": "Bot is running", 
        "webhook_url": f"{WEBHOOK_URL}/kie_callback",
        "pending_generations": len(pending_generations)
    })

async def health_handler(request):
    """Health check endpoint"""
    return web.json_response({
        "status": "healthy", 
        "pending_generations": len(pending_generations),
        "user_count": len(user_credits)
    })

async def create_web_app():
    """Create aiohttp web application"""
    app = web.Application()
    
    # Add routes
    app.router.add_post('/kie_callback', kie_callback)
    app.router.add_get('/', index_handler)
    app.router.add_get('/health', health_handler)
    
    return app

async def init_http_session():
    """Initialize global HTTP session"""
    global http_session
    http_session = ClientSession()
    logger.info("HTTP session initialized")

async def cleanup_http_session():
    """Cleanup global HTTP session"""
    global http_session
    if http_session:
        await http_session.close()
        logger.info("HTTP session closed")

async def main():
    """Main function to run the bot and web server in the same event loop"""
    try:
        # Delete any existing webhook to enable polling
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Deleted existing webhook to enable polling")
        except Exception as e:
            logger.warning(f"Error deleting webhook (may not exist): {e}")
        
        # Initialize HTTP session
        await init_http_session()
        
        # Create web app
        web_app = await create_web_app()
        
        # Create web runner
        runner = web.AppRunner(web_app)
        await runner.setup()
        
        # Create site and start server
        site = web.TCPSite(runner, '0.0.0.0', 5000)
        await site.start()
        
        logger.info("aiohttp server started on http://0.0.0.0:5000")
        logger.info(f"Callback URL: {WEBHOOK_URL}/kie_callback")
        logger.info("Bot starting...")
        
        try:
            # Start bot polling - this will run indefinitely
            await dp.start_polling(bot, skip_updates=True)
        finally:
            # Cleanup on shutdown
            await cleanup_http_session()
            await runner.cleanup()
            
    except Exception as e:
        logger.error(f"Error in main: {e}")
        await cleanup_http_session()
        raise

if __name__ == "__main__":
    # Run the bot and web server in the same event loop
    asyncio.run(main())