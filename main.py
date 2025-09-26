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
BRS_AI_API_KEY = os.getenv("BRS_AI_API_KEY", "your_brs_ai_api_key_here")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "your_payment_provider_token")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-repl-url.replit.dev")
CALLBACK_SECRET = os.getenv("CALLBACK_SECRET", "your_callback_secret_key_here")  # New security key

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Global HTTP client session
http_session: Optional[ClientSession] = None

# Credit tracking (persistent storage)
CREDITS_FILE = "user_credits.json"
user_models: Dict[int, str] = {}  # Store selected model per user
pending_generations: Dict[str, dict] = {}  # Track pending generations

def load_user_credits() -> Dict[int, int]:
    """Load user credits from persistent storage"""
    try:
        if os.path.exists(CREDITS_FILE):
            with open(CREDITS_FILE, 'r') as f:
                # JSON keys are strings, convert back to int
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        return {}
    except Exception as e:
        logger.error(f"Error loading credits: {e}")
        return {}

def save_user_credits():
    """Save user credits to persistent storage"""
    try:
        with open(CREDITS_FILE, 'w') as f:
            json.dump(user_credits, f, indent=2)
        logger.info("Credits saved to persistent storage")
    except Exception as e:
        logger.error(f"Error saving credits: {e}")

# Load existing credits on startup
user_credits: Dict[int, int] = load_user_credits()
logger.info(f"Loaded {len(user_credits)} user credit accounts from storage")

# Simplified available models - streamlined selection
AVAILABLE_MODELS = {
    "veo3_fast": "⚡ Veo 3 Fast - Quick generation with images", 
    "runway_gen3": "🚀 Runway Gen-3 - Advanced video generation",
    "wan_2_2_t2v": "📝 Wan 2.2 - Text to video",
    "wan_2_2_i2v": "🖼️ Wan 2.2 - Image to video",
    "kling_standard": "💰 Kling 2.1 - Image to video (720p)"
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
    save_user_credits()  # Save to persistent storage
    logger.info(f"Added {amount} credits to user {user_id}. Total: {user_credits[user_id]}")

def deduct_credits(user_id: int, amount: int) -> bool:
    """Deduct credits from user. Returns True if successful."""
    current_credits = get_user_credits(user_id)
    if current_credits >= amount:
        user_credits[user_id] = current_credits - amount
        save_user_credits()  # Save to persistent storage
        logger.info(f"Deducted {amount} credits from user {user_id}. Remaining: {user_credits[user_id]}")
        return True
    return False

def create_model_selection_keyboard():
    """Create simplified full-width keyboard for model selection"""
    keyboard = []
    
    # Add each model as a full-width button for better readability
    for model_key, model_name in AVAILABLE_MODELS.items():
        keyboard.append([InlineKeyboardButton(
            text=model_name,
            callback_data=f"model_{model_key}"
        )])
    
    # Add action buttons in pairs
    keyboard.append([
        InlineKeyboardButton(text="🔄 Reset Selection", callback_data="reset_model"),
        InlineKeyboardButton(text="💰 Buy Credits", callback_data="buy_credits")
    ])
    keyboard.append([
        InlineKeyboardButton(text="❓ Help", callback_data="help_models"),
        InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_main")
    ])
    
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

async def safe_edit_message(callback: CallbackQuery, text: str, reply_markup=None, parse_mode: Optional[str] = None) -> bool:
    """
    Safely edit callback message with proper type checking.
    Returns True if message was successfully edited, False if sent as new message.
    """
    try:
        if callback.message and isinstance(callback.message, types.Message):
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return True
        elif callback.from_user:
            await bot.send_message(callback.from_user.id, text, reply_markup=reply_markup, parse_mode=parse_mode)
            return False
        else:
            logger.warning("Unable to edit message or send new message - no user info")
            return False
    except Exception as e:
        logger.error(f"Error in safe_edit_message: {e}")
        # Fallback: try to send as new message
        if callback.from_user:
            try:
                await bot.send_message(callback.from_user.id, text, reply_markup=reply_markup, parse_mode=parse_mode)
                return False
            except Exception as fallback_error:
                logger.error(f"Fallback message send also failed: {fallback_error}")
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

async def send_to_brs_api(prompt: str, model: str, image_path: Optional[str] = None) -> str:
    """Send request to BRS AI API using aiohttp"""
    headers = {
        "Authorization": f"Bearer {BRS_AI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Different endpoints and parameters for different models
    if model.startswith("veo3"):
        api_url = "https://api.kie.ai/api/v1/veo/generate"
        data = {
            "prompt": prompt,
            "model": model,
            "aspectRatio": "16:9",
            "enableFallback": False,
            "enableTranslation": True,
            "callBackUrl": f"{WEBHOOK_URL}/kie_callback"
        }
        # Add image URLs for Veo3 if provided
        if image_path:
            # Serve image through our web server
            image_url = f"{WEBHOOK_URL}/images/{os.path.basename(image_path)}"
            data["imageUrls"] = [image_url]
            logger.info(f"Added image URL for Veo3: {image_url}")
            
    elif model == "runway_gen3":
        api_url = "https://api.kie.ai/api/v1/runway/generate"
        data = {
            "prompt": prompt,
            "duration": 5,
            "quality": "720p",
            "aspectRatio": "16:9",
            "callBackUrl": f"{WEBHOOK_URL}/kie_callback"
        }
        # Add image URL for Runway if provided
        if image_path:
            # Serve image through our web server
            image_url = f"{WEBHOOK_URL}/images/{os.path.basename(image_path)}"
            data["imageUrl"] = image_url
            logger.info(f"Added image URL for Runway: {image_url}")
            
    elif model.startswith("wan_2_2"):
        api_url = "https://api.kie.ai/api/v1/jobs/createTask"
        
        # Determine the specific Wan 2.2 model variant
        if model == "wan_2_2_t2v":
            model_name = "wan/2-2-a14b-text-to-video-turbo"
            input_data = {
                "prompt": prompt,
                "resolution": "720p",
                "aspect_ratio": "16:9",
                "enable_prompt_expansion": False,
                "acceleration": "none"
            }
        elif model == "wan_2_2_i2v":
            model_name = "wan/2-2-a14b-image-to-video-turbo"
            input_data = {
                "prompt": prompt,
                "resolution": "720p", 
                "aspect_ratio": "auto",
                "enable_prompt_expansion": False,
                "acceleration": "none"
            }
            # Add image URL if provided
            if image_path:
                # Serve image through our web server
                image_url = f"{WEBHOOK_URL}/images/{os.path.basename(image_path)}"
                input_data["image_url"] = image_url
                logger.info(f"Added image URL for Wan 2.2 I2V: {image_url}")
        else:
            raise Exception(f"Unknown Wan 2.2 variant: {model}")
            
        data = {
            "model": model_name,
            "callBackUrl": f"{WEBHOOK_URL}/brs_callback",
            "input": input_data
        }
        
    elif model.startswith("kling"):
        api_url = "https://api.kie.ai/api/v1/jobs/createTask"
        
        # Determine the specific Kling model variant
        if model == "kling_standard":
            model_name = "kling/v2-1-standard"
        elif model == "kling_pro":
            model_name = "kling/v2-1-pro"
        elif model == "kling_master_i2v":
            model_name = "kling/v2-1-master-image-to-video"
        elif model == "kling_master_t2v":
            model_name = "kling/v2-1-master-text-to-video"
        else:
            raise Exception(f"Unknown Kling variant: {model}")
            
        # Build input data for Kling models
        input_data = {
            "prompt": prompt,
            "duration": "5",  # 5 seconds default
            "aspect_ratio": "16:9",
            "negative_prompt": "blur, distort, and low quality",
            "cfg_scale": 0.5
        }
        
        # Add image URL for image-to-video models
        if model.endswith("_i2v") or model == "kling_standard" or model == "kling_pro":
            if image_path:
                # Serve image through our web server
                image_url = f"{WEBHOOK_URL}/images/{os.path.basename(image_path)}"
                input_data["image_url"] = image_url
                logger.info(f"Added image URL for Kling: {image_url}")
                
        data = {
            "model": model_name,
            "callBackUrl": f"{WEBHOOK_URL}/brs_callback",
            "input": input_data
        }
    else:
        raise Exception(f"Unsupported model: {model}")
    
    try:
        global http_session
        if not http_session:
            http_session = ClientSession()
            
        async with http_session.post(api_url, headers=headers, json=data) as response:
            if response.status == 200:
                result = await response.json()
                # BRS AI returns format: {"code": 200, "msg": "success", "data": {"taskId": "..."}}
                if result.get("code") == 200 and "data" in result:
                    return result["data"].get("taskId", "unknown")
                else:
                    raise Exception(f"BRS Error: {result.get('msg', 'Unknown error')}")
            else:
                error_text = await response.text()
                raise Exception(f"BRS Error: HTTP {response.status} - {error_text}")
                
    except Exception as e:
        logger.error(f"Error sending to BRS API: {e}")
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
🎬 **Welcome to AI Video Generator Bot!**

👋 **Hello {message.from_user.first_name or 'there'}!**

💳 **Your Credits:** {credits} {'credit' if credits == 1 else 'credits'}

🚀 **Quick Start:**
• Use /generate to create amazing videos
• Need credits? Try /buy for great packages
• Get help anytime with /help

🎯 **Available Models:** 5 AI models including Veo 3, Runway Gen-3, and Kling 2.1

💰 **Pricing:** 1 credit per video, bulk discounts available

⚠️ **This bot is in BETA - not everything works yet - we are updating daily!**

✨ Ready to create something amazing?
"""
        
        # Create welcome keyboard with quick actions
        welcome_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Generate Video", callback_data="quick_generate")],
            [InlineKeyboardButton(text="💳 Buy Credits", callback_data="show_packages"),
             InlineKeyboardButton(text="❓ Help & Guide", callback_data="help_main")],
            [InlineKeyboardButton(text="📊 My Stats", callback_data="user_stats")]
        ])
        
        await message.answer(welcome_text, reply_markup=welcome_keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in cmd_start: {e}")
        await message.answer("❌ An error occurred. Please try again later.")

@dp.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext):
    """Handle /reset command to clear model selection and state"""
    if not message.from_user:
        return
        
    try:
        user_id = message.from_user.id
        
        # Clear selected model
        if user_id in user_models:
            del user_models[user_id]
            
        # Clear FSM state
        await state.clear()
        
        credits = get_user_credits(user_id)
        
        await message.answer(
            "🔄 **Reset Complete!**\n\n"
            f"💳 **Your Balance:** `{credits}` credits\n\n"
            "✅ **Cleared:**\n"
            "• Selected AI model\n"
            "• Any pending inputs\n"
            "• Generation state\n\n"
            "🎬 **Ready for a fresh start!**\n"
            "Use /generate to begin creating videos",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error in cmd_reset: {e}")
        await message.answer("❌ An error occurred during reset. Please try again.")

@dp.message(Command("video"))
async def cmd_send_video(message: Message):
    """Manually send the last generated video that wasn't delivered"""
    if not message.from_user:
        return
        
    try:
        user_id = message.from_user.id
        
        # Send the video that was generated but not delivered
        video_url = "https://tempfile.aiquickdraw.com/p/3fc297d0f7ad7c3c0680d94dc3ae5ee8_1758925534.mp4"
        
        await bot.send_message(
            chat_id=user_id,
            text=f"🎬 **Your Previous Video is Ready!**\n\n"
                 f"📹 **Video URL:** {video_url}\n\n"
                 f"🦝 *The raccoon in a suit turns to the camera and says \"BRS Studio is now live on Telegram!\"*\n\n"
                 f"💡 This was the video that was successfully generated but not delivered due to a callback parsing issue (now fixed).",
            parse_mode="Markdown"
        )
        
        # Try to send as actual video file too
        try:
            global http_session
            if not http_session:
                http_session = ClientSession()
                
            async with http_session.get(video_url) as response:
                if response.status == 200:
                    video_content = await response.read()
                    video_file = BufferedInputFile(video_content, filename="raccoon_brs_studio.mp4")
                    
                    await bot.send_video(
                        chat_id=user_id,
                        video=video_file,
                        caption="🎬 Your video is ready! (Previously generated)"
                    )
        except Exception as video_error:
            logger.error(f"Could not send video file: {video_error}")
        
    except Exception as e:
        logger.error(f"Error in cmd_send_video: {e}")
        await message.answer("❌ Could not retrieve the previous video.")

@dp.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext):
    """Handle /generate command"""
    if not message.from_user:
        return
        
    try:
        user_id = message.from_user.id
        credits = get_user_credits(user_id)
        
        if credits < 1:
            no_credits_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Buy Credits", callback_data="buy_credits")],
                [InlineKeyboardButton(text="📚 Learn More", callback_data="help_credits")]
            ])
            await message.answer(
                "💸 **Insufficient Credits!**\n\n"
                f"💳 **Current Balance:** `{credits}` credits\n\n"
                "🎬 **Required:** `1` credit for video generation\n\n"
                "💡 **Quick Solutions:**\n"
                "• Buy credits with Telegram Stars (⭐)\n"
                "• 100 Stars = 1 Credit (≈ $1.30)\n\n"
                "👆 **Tap below to get started!**",
                reply_markup=no_credits_keyboard,
                parse_mode="Markdown"
            )
            return
        
        # Check if user has a selected model
        if user_id not in user_models:
            keyboard = create_model_selection_keyboard()
            await message.answer(
                "🤖 **Choose Your AI Model**\n\n"
                f"💳 **Your Balance:** `{credits}` credits\n\n"
                "🎯 **Select the perfect model for your video:**\n\n"
                "⚡ **Fast:** Quick generation (1-2 min)\n"
                "🎵 **Audio:** High quality with sound\n"
                "🚀 **Advanced:** Premium features\n"
                "💰 **Affordable:** Budget-friendly options\n\n"
                "👆 **Tap a model below to continue:**",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            return
        
        # User has a model, ask for prompt
        selected_model = user_models[user_id]
        model_name = AVAILABLE_MODELS[selected_model]
        
        # Check if model supports image-to-video
        image_models = ["wan_2_2_i2v", "kling_standard", "kling_pro", "kling_master_i2v", "veo3_fast", "runway_gen3"]
        supports_images = selected_model in image_models
        
        prompt_hint = " - you can add an image in step 2" if supports_images else ""
        
        await message.answer(
            f"✨ **Model Selected:** {model_name}\n"
            "🔄 `/reset` to start over\n\n"
            f"💳 **Your Balance:** `{credits}` credits\n\n"
            f"📝 **Step 1:** Enter your creative prompt{prompt_hint}\n\n"
            "💡 **Pro Tips:**\n"
            "• Be specific and descriptive\n"
            "• Mention camera angles, lighting, mood\n"
            "• Keep it under 500 characters\n\n"
            "🎬 **Example:** *A majestic eagle soaring over snow-capped mountains at sunset*\n\n"
            "✍️ **Your turn - type your prompt below:**",
            parse_mode="Markdown"
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

# New comprehensive callback handlers for enhanced UI
@dp.callback_query(F.data == "quick_generate")
async def quick_generate_callback(callback: CallbackQuery, state: FSMContext):
    """Handle quick generate button from welcome and other menus"""
    if not callback.from_user:
        return
    
    try:
        user_id = callback.from_user.id
        credits = get_user_credits(user_id)
        
        if credits < 1:
            no_credits_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Buy Credits", callback_data="buy_credits")],
                [InlineKeyboardButton(text="📚 Learn More", callback_data="help_credits")]
            ])
            await safe_edit_message(
                callback,
                "💸 **Insufficient Credits!**\n\n"
                f"💳 **Current Balance:** `{credits}` credits\n\n"
                "🎬 **Required:** `1` credit for video generation\n\n"
                "💡 **Quick Solutions:**\n"
                "• Buy credits with Telegram Stars (⭐)\n"
                "• 100 Stars = 1 Credit (≈ $1.30)\n\n"
                "👆 **Tap below to get started!**",
                no_credits_keyboard
            )
            await callback.answer()
            return
        
        # Check if user has a selected model
        if user_id not in user_models:
            keyboard = create_model_selection_keyboard()
            await safe_edit_message(
                callback,
                "🤖 **Choose Your AI Model**\n\n"
                f"💳 **Your Balance:** `{credits}` credits\n\n"
                "🎯 **Select the perfect model for your video:**\n\n"
                "⚡ **Fast:** Quick generation (1-2 min)\n"
                "🎵 **Audio:** High quality with sound\n"
                "🚀 **Advanced:** Premium features\n"
                "💰 **Affordable:** Budget-friendly options\n\n"
                "👆 **Tap a model below to continue:**",
                keyboard
            )
            await callback.answer()
            return
        
        # User has a model, ask for prompt
        selected_model = user_models[user_id]
        model_name = AVAILABLE_MODELS[selected_model]
        
        # Check if model supports image-to-video
        image_models = ["wan_2_2_i2v", "kling_standard", "kling_pro", "kling_master_i2v", "veo3_fast", "runway_gen3"]
        supports_images = selected_model in image_models
        
        prompt_hint = " - you can add an image in step 2" if supports_images else ""
        
        await safe_edit_message(
            callback,
            f"✨ **Model Selected:** {model_name}\n"
            "🔄 `/reset` to start over\n\n"
            f"💳 **Your Balance:** `{credits}` credits\n\n"
            f"📝 **Step 1:** Enter your creative prompt{prompt_hint}\n\n"
            "💡 **Pro Tips:**\n"
            "• Be specific and descriptive\n"
            "• Mention camera angles, lighting, mood\n"
            "• Keep it under 500 characters\n\n"
            "🎬 **Example:** *A majestic eagle soaring over snow-capped mountains at sunset*\n\n"
            "✍️ **Your turn - type your prompt below:"
        )
        await state.set_state(GenerationStates.waiting_for_prompt)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in quick_generate_callback: {e}")
        await callback.answer("❌ Error starting generation. Please try again.")

@dp.callback_query(F.data == "buy_credits")
async def buy_credits_callback(callback: CallbackQuery):
    """Handle buy credits button"""
    if not callback.from_user:
        return
        
    try:
        user_id = callback.from_user.id
        credits = get_user_credits(user_id)
        
        # Create enhanced buy menu
        buy_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Buy 1 Credit (100 Stars)", callback_data="buy_1")],
            [InlineKeyboardButton(text="📊 Credit Packages", callback_data="buy_packages")],
            [InlineKeyboardButton(text="💡 How Stars Work", callback_data="help_stars")],
            [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_main")]
        ])
        
        buy_text = (
            "💳 **Credit Store**\n\n"
            f"💰 **Current Balance:** `{credits}` {'credit' if credits == 1 else 'credits'}\n\n"
            "⭐ **Telegram Stars Pricing:**\n"
            "• 1 Credit = 100 Stars (≈ $1.30)\n"
            "• Instant delivery\n"
            "• Secure Telegram payment\n\n"
            "🎬 **What you get:**\n"
            "• Generate 1 high-quality AI video\n"
            "• Choice of 5 premium models\n"
            "• Image-to-video support\n"
            "• Direct delivery to Telegram\n\n"
            "💡 **Tip:** Credits never expire, bulk discounts available!"
        )
        
        await safe_edit_message(callback, buy_text, reply_markup=buy_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in buy_credits_callback: {e}")
        await callback.answer("❌ Error loading credit store.")

@dp.callback_query(F.data == "buy_1")
async def buy_one_credit_callback(callback: CallbackQuery):
    """Handle buying 1 credit"""
    if not callback.from_user:
        return
        
    try:
        # Simulate the /buy command
        if callback.message:
            await cmd_buy(callback.message)
        await callback.answer("🛒 Opening payment...")
        
    except Exception as e:
        logger.error(f"Error in buy_one_credit_callback: {e}")
        await callback.answer("❌ Error processing purchase.")

@dp.callback_query(F.data == "user_stats")
async def user_stats_callback(callback: CallbackQuery):
    """Show user statistics and account info"""
    if not callback.from_user:
        return
        
    try:
        user_id = callback.from_user.id
        credits = get_user_credits(user_id)
        user_name = callback.from_user.first_name or "User"
        
        # Count pending generations for this user
        user_pending = sum(1 for gen in pending_generations.values() if gen.get('user_id') == user_id)
        
        stats_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Generate Video", callback_data="quick_generate")],
            [InlineKeyboardButton(text="💳 Buy Credits", callback_data="buy_credits")],
            [InlineKeyboardButton(text="🔙 Main Menu", callback_data="back_main")]
        ])
        
        stats_text = (
            f"📊 **Account Statistics**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **ID:** `{user_id}`\n\n"
            f"💳 **Credits:** `{credits}` {'credit' if credits == 1 else 'credits'}\n"
            f"⏳ **Pending:** `{user_pending}` {'generation' if user_pending == 1 else 'generations'}\n\n"
            "🎯 **Usage Tips:**\n"
            "• Each video costs 1 credit\n"
            "• Credits never expire\n"
            "• Try different models for variety\n\n"
            "🚀 **Ready for your next creation?**"
        )
        
        await safe_edit_message(callback, stats_text, reply_markup=stats_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in user_stats_callback: {e}")
        await callback.answer("❌ Error loading stats.")

@dp.callback_query(F.data == "help_main")
async def help_main_callback(callback: CallbackQuery):
    """Show main help menu"""
    if not callback.from_user:
        return
        
    try:
        help_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Video Generation", callback_data="help_generate")],
            [InlineKeyboardButton(text="💳 Credits & Payment", callback_data="help_credits")],
            [InlineKeyboardButton(text="🤖 AI Models Guide", callback_data="help_models")],
            [InlineKeyboardButton(text="🖼️ Image Upload Tips", callback_data="help_image")],
            [InlineKeyboardButton(text="🛠️ Troubleshooting", callback_data="help_troubleshoot")],
            [InlineKeyboardButton(text="🔙 Main Menu", callback_data="back_main")]
        ])
        
        help_text = (
            "❓ **Help & Support Center**\n\n"
            "Welcome to the comprehensive help system! Choose a topic below to get detailed assistance:\n\n"
            "🎬 **Video Generation** - Learn how to create videos\n"
            "💳 **Credits & Payment** - Understand the credit system\n"
            "🤖 **AI Models** - Compare different models\n"
            "🖼️ **Image Tips** - Optimize your image uploads\n"
            "🛠️ **Troubleshooting** - Fix common issues\n\n"
            "💬 **Need more help?** Contact @niftysolsol"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=help_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_main_callback: {e}")
        await callback.answer("❌ Error loading help.")

@dp.callback_query(F.data == "skip_image")
async def skip_image_callback(callback: CallbackQuery, state: FSMContext):
    """Handle skip image button during generation"""
    if not callback.from_user:
        return
        
    try:
        # Simulate typing 'skip'
        data = await state.get_data()
        if data.get('prompt'):
            # Create a mock message with 'skip' text
            class MockMessage:
                def __init__(self, user, text):
                    self.from_user = user
                    self.text = text
                    self.photo = None
                
                async def answer(self, text, **kwargs):
                    # Send message through callback query instead
                    try:
                        if callback.message:
                            await callback.message.answer(text, **kwargs)
                        else:
                            logger.error("Callback message is None")
                    except Exception as e:
                        logger.error(f"Failed to send mock message answer: {e}")
            
            mock_message = MockMessage(callback.from_user, 'skip')
            await process_image_or_skip(mock_message, state)
        
        await callback.answer("⏭️ Skipping image upload...")
        
    except Exception as e:
        logger.error(f"Error in skip_image_callback: {e}")
        await callback.answer("❌ Error processing skip.")

# Additional help system callbacks
@dp.callback_query(F.data == "help_generate")
async def help_generate_callback(callback: CallbackQuery):
    """Show video generation help"""
    if not callback.from_user:
        return
    
    try:
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Try Now", callback_data="quick_generate")],
            [InlineKeyboardButton(text="🤖 Model Guide", callback_data="help_models")],
            [InlineKeyboardButton(text="🔙 Help Menu", callback_data="help_main")]
        ])
        
        help_text = (
            "🎬 **Video Generation Guide**\n\n"
            "🚀 **Getting Started:**\n"
            "1️⃣ Use `/generate` or tap Generate Video\n"
            "2️⃣ Choose from 9 AI models\n"
            "3️⃣ Write a creative prompt (be specific!)\n"
            "4️⃣ Upload image (optional)\n"
            "5️⃣ Wait 2-5 minutes for your video\n\n"
            "✍️ **Writing Great Prompts:**\n"
            "• Be specific and descriptive\n"
            "• Include camera angles, lighting, mood\n"
            "• Mention colors, movement, style\n"
            "• Keep under 500 characters\n\n"
            "🌟 **Example Prompts:**\n"
            "_\"A majestic golden eagle soaring over snow-capped mountains at sunset, cinematic wide shot\"_\n\n"
            "_\"Close-up of raindrops on a car window, neon city lights blurred in background, moody lighting\"_\n\n"
            "💰 **Cost:** 1 credit per video (≈ $1.30)"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=back_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_generate_callback: {e}")
        await callback.answer("❌ Error loading help.")

@dp.callback_query(F.data == "help_credits")
async def help_credits_callback(callback: CallbackQuery):
    """Show credits and payment help"""
    if not callback.from_user:
        return
    
    try:
        credits_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Buy Credits", callback_data="buy_credits")],
            [InlineKeyboardButton(text="📊 Check Balance", callback_data="user_stats")],
            [InlineKeyboardButton(text="🔙 Help Menu", callback_data="help_main")]
        ])
        
        help_text = (
            "💳 **Credits & Payment Guide**\n\n"
            "💰 **Credit System:**\n"
            "• 1 Credit = 1 Video Generation\n"
            "• Credits never expire\n"
            "• Refunded if generation fails\n"
            "• Track balance anytime\n\n"
            "⭐ **Telegram Stars Payment:**\n"
            "• 1 Credit = 100 Stars (≈ $1.30)\n"
            "• Secure Telegram payment system\n"
            "• Instant credit delivery\n"
            "• No external payment needed\n\n"
            "🛒 **How to Buy:**\n"
            "1️⃣ Tap 'Buy Credits' button\n"
            "2️⃣ Confirm 100 Stars payment\n"
            "3️⃣ Credits added instantly\n"
            "4️⃣ Start generating videos!\n\n"
            "🔒 **Security:** All payments processed by Telegram\n"
            "💵 **Pricing:** Competitive rates, no hidden fees"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=credits_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_credits_callback: {e}")
        await callback.answer("❌ Error loading help.")

@dp.callback_query(F.data == "help_models")
async def help_models_callback(callback: CallbackQuery):
    """Show AI models comparison help"""
    if not callback.from_user:
        return
    
    try:
        models_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Generate Video", callback_data="quick_generate")],
            [InlineKeyboardButton(text="📝 Generation Guide", callback_data="help_generate")],
            [InlineKeyboardButton(text="🔙 Help Menu", callback_data="help_main")]
        ])
        
        help_text = (
            "🤖 **AI Models Comparison**\n\n"
            "⚡ **Veo 3 Fast** - Quick generation (1-2 min)\n"
            "• Best for: Fast results\n"
            "• Quality: Good\n"
            "• Features: Speed optimized\n\n"
            "🎵 **Veo 3** - High quality with audio\n"
            "• Best for: Premium videos with sound\n"
            "• Quality: Excellent\n"
            "• Features: Synchronized audio\n\n"
            "🚀 **Runway Gen-3** - Advanced video\n"
            "• Best for: Complex scenes\n"
            "• Quality: Professional\n"
            "• Features: Advanced reasoning\n\n"
            "📝 **Wan 2.2 T2V** - Text to video\n"
            "• Best for: Text-only prompts\n"
            "• Quality: High\n"
            "• Features: Pure text generation\n\n"
            "🖼️ **Wan 2.2 I2V** - Image to video\n"
            "• Best for: Animating images\n"
            "• Quality: High\n"
            "• Features: Image animation\n\n"
            "💰 **Kling Standard** - Affordable 720p\n"
            "• Best for: Budget-conscious users\n"
            "• Quality: Good (720p)\n"
            "• Features: Cost-effective\n\n"
            "⭐ **Kling Pro** - Enhanced 1080p\n"
            "• Best for: High resolution needs\n"
            "• Quality: Excellent (1080p)\n"
            "• Features: Enhanced quality\n\n"
            "👑 **Kling Master I2V** - Premium image-to-video\n"
            "• Best for: Professional image animation\n"
            "• Quality: Premium\n"
            "• Features: Advanced I2V processing\n\n"
            "🎬 **Kling Master T2V** - Premium text-to-video\n"
            "• Best for: Professional text generation\n"
            "• Quality: Premium\n"
            "• Features: Advanced T2V processing"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=models_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_models_callback: {e}")
        await callback.answer("❌ Error loading help.")

@dp.callback_query(F.data == "help_image")
async def help_image_callback(callback: CallbackQuery):
    """Show image upload tips"""
    if not callback.from_user:
        return
    
    try:
        image_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼️ Try I2V Models", callback_data="quick_generate")],
            [InlineKeyboardButton(text="🤖 Model Guide", callback_data="help_models")],
            [InlineKeyboardButton(text="🔙 Help Menu", callback_data="help_main")]
        ])
        
        help_text = (
            "🖼️ **Image Upload Guide**\n\n"
            "📸 **Supported Formats:**\n"
            "• JPG, JPEG, PNG, WebP, GIF\n"
            "• Maximum size: 20MB\n"
            "• Recommended: 1024x1024+ pixels\n\n"
            "🎯 **Best Results:**\n"
            "• High resolution images\n"
            "• Clear, well-lit photos\n"
            "• Good contrast and composition\n"
            "• Avoid blurry or dark images\n\n"
            "💡 **Pro Tips:**\n"
            "• Images work best with I2V models\n"
            "• Portrait or landscape both work\n"
            "• Add descriptive prompts for context\n"
            "• You can skip images for text-only\n\n"
            "🤖 **Compatible Models:**\n"
            "• Wan 2.2 I2V - Image to video\n"
            "• Kling Standard - Supports images\n"
            "• Kling Pro - Enhanced with images\n"
            "• Kling Master I2V - Premium I2V\n"
            "• Veo 3 - Image enhancement\n"
            "• Runway Gen-3 - Advanced I2V\n\n"
            "⚠️ **Note:** Image processing may add 30-60 seconds"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=image_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_image_callback: {e}")
        await callback.answer("❌ Error loading help.")

@dp.callback_query(F.data == "help_troubleshoot")
async def help_troubleshoot_callback(callback: CallbackQuery):
    """Show troubleshooting help"""
    if not callback.from_user:
        return
    
    try:
        trouble_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Try Again", callback_data="quick_generate")],
            [InlineKeyboardButton(text="👤 Contact Support", callback_data="help_contact")],
            [InlineKeyboardButton(text="🔙 Help Menu", callback_data="help_main")]
        ])
        
        help_text = (
            "🛠️ **Troubleshooting Guide**\n\n"
            "❌ **Generation Failed?**\n"
            "• Check your prompt clarity\n"
            "• Try a different model\n"
            "• Ensure stable internet\n"
            "• Credits are auto-refunded\n\n"
            "📸 **Image Issues?**\n"
            "• Use supported formats (JPG, PNG)\n"
            "• Keep under 20MB size\n"
            "• Ensure good image quality\n"
            "• Try skipping image if problems persist\n\n"
            "⏱️ **Taking Too Long?**\n"
            "• Normal time: 2-5 minutes\n"
            "• Complex prompts take longer\n"
            "• High-quality models need more time\n"
            "• You'll get notified when ready\n\n"
            "💳 **Credit Problems?**\n"
            "• Check balance with /start\n"
            "• Failed generations are refunded\n"
            "• Stars payment is instant\n"
            "• Contact support if issues persist\n\n"
            "🔄 **General Tips:**\n"
            "• Restart with /start\n"
            "• Try different prompts\n"
            "• Use simpler descriptions\n"
            "• Contact support for persistent issues"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=trouble_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_troubleshoot_callback: {e}")
        await callback.answer("❌ Error loading help.")

@dp.callback_query(F.data == "back_main")
async def back_main_callback(callback: CallbackQuery):
    """Return to main menu"""
    if not callback.from_user:
        return
    
    try:
        # Simulate /start command
        if callback.message:
            await cmd_start(callback.message)
        await callback.answer("🏠 Returning to main menu...")
        
    except Exception as e:
        logger.error(f"Error in back_main_callback: {e}")
        await callback.answer("❌ Error returning to menu.")

# Credit package callbacks
@dp.callback_query(F.data == "show_packages")
async def show_packages_callback(callback: CallbackQuery):
    """Show credit packages when Buy Credits button is clicked"""
    await callback.answer()
    
    if not callback.from_user:
        return
        
    try:
        user_id = callback.from_user.id
        credits = get_user_credits(user_id)
        
        # Create credit packages keyboard
        packages_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Starter: 100⭐ → 12 Credits", callback_data="buy_package_100")],
            [InlineKeyboardButton(text="🔥 Popular: 200⭐ → 25 Credits", callback_data="buy_package_200")],
            [InlineKeyboardButton(text="💎 Best Value: 500⭐ → 75 Credits", callback_data="buy_package_500")],
            [InlineKeyboardButton(text="👑 Ultimate: 1000⭐ → 175 Credits", callback_data="buy_package_1000")],
            [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_start")]
        ])
        
        package_text = (
            "⭐ **Credit Packages - Telegram Stars**\n\n"
            f"💳 **Current Balance:** {credits} credits\n\n"
            "🎯 **Starter Package**\n"
            "• 100 Stars → 12 Credits\n"
            "• Great for trying out models\n\n"
            "🔥 **Popular Choice** (25% Bonus!)\n"
            "• 200 Stars → 25 Credits\n"
            "• Perfect for regular users\n\n"
            "💎 **Best Value** (50% Bonus!)\n"
            "• 500 Stars → 75 Credits\n"
            "• Maximum savings per credit\n\n"
            "👑 **Ultimate Package** (75% Bonus!)\n"
            "• 1000 Stars → 175 Credits\n"
            "• For power users and creators\n\n"
            "✨ **All packages include:**\n"
            "• Access to all 5 AI models\n"
            "• Image-to-video support\n"
            "• Instant video delivery\n"
            "• Credits never expire\n\n"
            "💡 Choose a package above to proceed!"
        )
        
        try:
            if callback.message:
                await callback.message.edit_text(package_text, reply_markup=packages_keyboard, parse_mode="Markdown")
        except Exception:
            # Fallback: send new message if editing fails
            await bot.send_message(callback.from_user.id, package_text, reply_markup=packages_keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in show_packages_callback: {e}")
        if callback.message:
            await callback.message.answer("❌ Error showing packages. Please try /buy command.")

@dp.callback_query(F.data.startswith("buy_package_"))
async def buy_package_callback(callback: CallbackQuery):
    """Handle credit package purchase"""
    await callback.answer()
    
    if not callback.from_user:
        return
        
    try:
        # Extract package size from callback data
        if not callback.data:
            await callback.message.answer("❌ Invalid package data.")
            return
        package_stars = int(callback.data.replace("buy_package_", ""))
        user_id = callback.from_user.id
        
        # Package details
        packages = {
            100: {"credits": 12, "title": "Starter Package", "description": "12 credits for video generation"},
            200: {"credits": 25, "title": "Popular Package", "description": "25 credits with 25% bonus"},
            500: {"credits": 75, "title": "Best Value Package", "description": "75 credits with 50% bonus"},
            1000: {"credits": 175, "title": "Ultimate Package", "description": "175 credits with 75% bonus"}
        }
        
        if package_stars not in packages:
            if callback.message:
                await callback.message.answer("❌ Invalid package selected.")
            return
            
        package = packages[package_stars]
        
        # Create invoice
        price = LabeledPrice(label=f"{package['credits']} Video Credits", amount=package_stars)
        
        await bot.send_invoice(
            chat_id=user_id,
            title=package["title"],
            description=package["description"],
            payload=f"credit_package_{package_stars}",
            provider_token="",  # Empty for Telegram Stars (XTR)
            currency="XTR",  # Telegram Stars
            prices=[price],
            need_email=False,
            need_phone_number=False,
            need_name=False,
            need_shipping_address=False,
            is_flexible=False
        )
        
        # Send confirmation message
        confirm_text = (
            f"💰 **Payment Request Sent!**\n\n"
            f"⭐ **Package:** {package_stars} Telegram Stars\n"
            f"💳 **Credits:** {package['credits']} credits\n\n"
            f"📱 **Complete payment in Telegram to receive your credits instantly!**"
        )
        
        if callback.message:
            await callback.message.answer(confirm_text, parse_mode="Markdown")
        else:
            await bot.send_message(user_id, confirm_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in buy_package_callback: {e}")
        if callback.message:
            await callback.message.answer(f"❌ Payment error: {str(e)}\n\nPlease contact @niftysolsol for support.")
        else:
            await bot.send_message(callback.from_user.id, f"❌ Payment error: {str(e)}\n\nPlease contact @niftysolsol for support.")

@dp.callback_query(F.data == "back_to_start")
async def back_to_start_callback(callback: CallbackQuery):
    """Return to main menu"""
    await callback.answer()
    
    if not callback.from_user:
        return
        
    try:
        user_id = callback.from_user.id
        credits = get_user_credits(user_id)
        
        welcome_text = f"""
🎬 **Welcome to AI Video Generator Bot!**

👋 **Hello {callback.from_user.first_name or 'there'}!**

💳 **Your Credits:** {credits} {'credit' if credits == 1 else 'credits'}

🚀 **Quick Start:**
• Use /generate to create amazing videos
• Need credits? Try /buy for great packages
• Get help anytime with /help

🎯 **Available Models:** 5 AI models including Veo 3, Runway Gen-3, and Kling 2.1

💰 **Pricing:** 1 credit per video, bulk discounts available

✨ Ready to create something amazing?
"""
        
        # Create welcome keyboard with quick actions
        welcome_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Generate Video", callback_data="quick_generate")],
            [InlineKeyboardButton(text="💳 Buy Credits", callback_data="show_packages"),
             InlineKeyboardButton(text="❓ Help & Guide", callback_data="help_main")],
            [InlineKeyboardButton(text="📊 My Stats", callback_data="user_stats")]
        ])
        
        try:
            if callback.message:
                await callback.message.edit_text(welcome_text, reply_markup=welcome_keyboard, parse_mode="Markdown")
        except Exception:
            # Fallback: send new message if editing fails
            await bot.send_message(user_id, welcome_text, reply_markup=welcome_keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in back_to_start_callback: {e}")
        if callback.message:
            await callback.message.answer("❌ Error returning to menu. Please use /start.")

@dp.callback_query(F.data == "help_contact")
async def help_contact_callback(callback: CallbackQuery):
    """Show contact support information"""
    if not callback.from_user:
        return
    
    try:
        contact_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠️ Troubleshooting", callback_data="help_troubleshoot")],
            [InlineKeyboardButton(text="🔙 Help Menu", callback_data="help_main")]
        ])
        
        help_text = (
            "👤 **Contact Support**\n\n"
            "💬 **Get Human Help:**\n"
            "• Support: @niftysolsol\n"
            "• Response time: 2-24 hours\n"
            "• Response time: 2-24 hours\n\n"
            "📝 **When Contacting Include:**\n"
            "• Your user ID (shown in stats)\n"
            "• Generation ID if available\n"
            "• Description of the problem\n"
            "• Screenshots if helpful\n\n"
            "🚀 **Before Contacting:**\n"
            "• Try troubleshooting guide\n"
            "• Check your credits balance\n"
            "• Restart with /start\n"
            "• Try a different model\n\n"
            "💰 **Refund Policy:**\n"
            "• Failed generations: Auto-refunded\n"
            "• Technical issues: Case-by-case\n"
            "• Payment problems: Contact support\n\n"
            "🙏 **We're here to help make your experience amazing!**"
        )
        
        await safe_edit_message(callback, help_text, reply_markup=contact_keyboard, parse_mode="Markdown")
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in help_contact_callback: {e}")
        await callback.answer("❌ Error loading contact info.")

@dp.message(GenerationStates.waiting_for_prompt)
async def process_prompt(message: Message, state: FSMContext):
    """Handle text prompt input"""
    try:
        if not message.text:
            await message.answer("Please provide a text prompt.")
            return
            
        prompt = message.text
        await state.update_data(prompt=prompt)
        
        # Enhanced image prompt with skip keyboard
        skip_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Skip Image", callback_data="skip_image")],
            [InlineKeyboardButton(text="❓ Image Tips", callback_data="help_image")]
        ])
        
        await message.answer(
            "🖼️ **Step 2:** Upload an image (optional)\n\n"
            "📸 **Supported formats:** JPG, PNG, WebP, GIF\n"
            "📏 **Best quality:** High resolution (1024x1024+)\n\n"
            "💡 **Pro tip:** Images work great with I2V models!\n\n"
            "📤 **Upload your image now** or tap Skip to continue:",
            reply_markup=skip_keyboard,
            parse_mode="Markdown"
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
        
        # Send to BRS AI API with enhanced progress tracking
        try:
            # Initial progress message
            progress_msg = await message.answer(
                "🎬 **Starting Video Generation**\n\n"
                "⏳ **Status:** Initializing request...\n"
                f"🤖 **Model:** {AVAILABLE_MODELS[model]}\n"
                f"📝 **Prompt:** _{prompt[:50]}{'...' if len(prompt) > 50 else ''}_\n\n"
                "⏱️ **Estimated time:** 2-5 minutes\n"
                "🔄 **Please wait while we process your request...**",
                parse_mode="Markdown"
            )
            
            generation_id = await send_to_brs_api(prompt, model, image_path)
            
            # Update progress with generation ID
            try:
                await progress_msg.edit_text(
                    "✅ **Video Generation Started**\n\n"
                    f"🆔 **Generation ID:** `{generation_id}`\n"
                    f"🤖 **Model:** {AVAILABLE_MODELS[model]}\n"
                    f"📝 **Prompt:** _{prompt[:50]}{'...' if len(prompt) > 50 else ''}_\n\n"
                    "🎬 **Processing steps:**\n"
                    "✅ Request submitted\n"
                    "⏳ Analyzing prompt...\n"
                    "⏳ Generating video...\n"
                    "⏳ Finalizing output...\n\n"
                    "🔔 **You'll be notified when ready!**",
                    parse_mode="Markdown"
                )
            except Exception:
                # If edit fails, send new message
                await message.answer(
                    "✅ **Video Generation Started**\n\n"
                    f"🆔 **Generation ID:** `{generation_id}`\n"
                    "🔔 **You'll be notified when ready!**",
                    parse_mode="Markdown"
                )
            
            # Store pending generation
            pending_generations[generation_id] = {
                "user_id": user_id,
                "prompt": prompt,
                "model": model,
                "image_path": image_path
            }
            
            # Final confirmation with helpful info
            final_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Check Status", callback_data="user_stats")],
                [InlineKeyboardButton(text="🎬 Generate Another", callback_data="quick_generate")]
            ])
            
            await message.answer(
                "🎉 **Generation in Progress!**\n\n"
                f"🆔 **Tracking ID:** `{generation_id}`\n\n"
                "⏰ **What happens next:**\n"
                "• Processing usually takes 2-5 minutes\n"
                "• You'll get a notification when ready\n"
                "• Video will be delivered directly to this chat\n\n"
                "🔄 **You can generate more videos while waiting!**",
                reply_markup=final_keyboard,
                parse_mode="Markdown"
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

# Add comprehensive help command
@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command with comprehensive help menu"""
    if not message.from_user:
        return
        
    try:
        help_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Video Generation", callback_data="help_generate")],
            [InlineKeyboardButton(text="💳 Credits & Payment", callback_data="help_credits")],
            [InlineKeyboardButton(text="🤖 AI Models Guide", callback_data="help_models")],
            [InlineKeyboardButton(text="🖼️ Image Upload Tips", callback_data="help_image")],
            [InlineKeyboardButton(text="🛠️ Troubleshooting", callback_data="help_troubleshoot")],
            [InlineKeyboardButton(text="👤 Contact Support", callback_data="help_contact")]
        ])
        
        help_text = (
            "❓ **AI Video Bot - Complete Help Guide**\n\n"
            f"Welcome {message.from_user.first_name or 'there'}! Choose a topic below for detailed assistance:\n\n"
            "🎬 **Video Generation** - Step-by-step video creation\n"
            "💳 **Credits & Payment** - Understanding the credit system\n"
            "🤖 **AI Models** - Compare all 5 available models\n"
            "🖼️ **Image Tips** - Optimize your image uploads\n"
            "🛠️ **Troubleshooting** - Fix common issues\n"
            "👤 **Contact** - Get human support\n\n"
            "🔥 **Quick Commands:**\n"
            "• `/generate` - Start creating videos\n"
            "• `/buy` - Purchase credits\n"
            "• `/reset` - Clear selection and start fresh\n"
            "• `/start` - Return to main menu\n\n"
            "💡 **Tip:** Use the buttons below for instant help!"
        )
        
        await message.answer(help_text, reply_markup=help_keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error in cmd_help: {e}")
        await message.answer("❌ An error occurred. Please try again later.")

@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    """Handle /buy command - Show credit packages"""
    if not message.from_user:
        return
        
    try:
        user_id = message.from_user.id
        credits = get_user_credits(user_id)
        
        # Create credit packages keyboard
        packages_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Starter: 100⭐ → 12 Credits", callback_data="buy_package_100")],
            [InlineKeyboardButton(text="🔥 Popular: 200⭐ → 25 Credits", callback_data="buy_package_200")],
            [InlineKeyboardButton(text="💎 Best Value: 500⭐ → 75 Credits", callback_data="buy_package_500")],
            [InlineKeyboardButton(text="👑 Ultimate: 1000⭐ → 175 Credits", callback_data="buy_package_1000")],
            [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_start")]
        ])
        
        package_text = (
            "⭐ **Credit Packages - Telegram Stars**\n\n"
            f"💳 **Current Balance:** {credits} credits\n\n"
            "🎯 **Starter Package**\n"
            "• 100 Stars → 12 Credits\n"
            "• Great for trying out models\n\n"
            "🔥 **Popular Choice** (25% Bonus!)\n"
            "• 200 Stars → 25 Credits\n"
            "• Perfect for regular users\n\n"
            "💎 **Best Value** (50% Bonus!)\n"
            "• 500 Stars → 75 Credits\n"
            "• Maximum savings per credit\n\n"
            "👑 **Ultimate Package** (75% Bonus!)\n"
            "• 1000 Stars → 175 Credits\n"
            "• For power users and creators\n\n"
            "✨ **All packages include:**\n"
            "• Access to all 5 AI models\n"
            "• Image-to-video support\n"
            "• Instant video delivery\n"
            "• Credits never expire\n\n"
            "💡 Choose a package above to proceed!"
        )
        
        await message.answer(package_text, reply_markup=packages_keyboard, parse_mode="Markdown")
        
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
        payload = payment.invoice_payload
        
        # Parse package from payload
        if payload.startswith("credit_package_"):
            package_stars = int(payload.replace("credit_package_", ""))
            
            # Credit mapping
            credit_packages = {
                100: 12,
                200: 25,
                500: 75,
                1000: 175
            }
            
            credits_to_add = credit_packages.get(package_stars, 0)
            if credits_to_add > 0:
                add_credits(user_id, credits_to_add)
                total_credits = get_user_credits(user_id)
                
                # Calculate bonus
                base_credits = package_stars // 100 * 12  # Base rate
                bonus_credits = credits_to_add - base_credits
                
                success_text = (
                    "🎉 **Payment Successful!**\n\n"
                    f"⭐ **Purchased:** {package_stars} Telegram Stars\n"
                    f"💳 **Credits Added:** {credits_to_add}\n"
                )
                
                if bonus_credits > 0:
                    success_text += f"🎁 **Bonus Credits:** +{bonus_credits} free!\n\n"
                else:
                    success_text += "\n"
                    
                success_text += (
                    f"💰 **New Balance:** {total_credits} total credits\n\n"
                    "🎬 Ready to create videos! Use /generate to start."
                )
                
                # CRITICAL FIX: Use bot.send_message to ensure correct user gets the message
                await bot.send_message(user_id, success_text, parse_mode="Markdown")
            else:
                # CRITICAL FIX: Use bot.send_message to ensure correct user gets the message
                await bot.send_message(user_id, "❌ Invalid package. Please contact support.")
        else:
            # CRITICAL FIX: Use bot.send_message to ensure correct user gets the message
            await bot.send_message(user_id, "❌ Unknown payment. Please contact support.")
            
    except Exception as e:
        logger.error(f"Error in process_successful_payment: {e}")
        # CRITICAL FIX: Use bot.send_message with user_id to prevent cross-user messages
        if message.from_user:
            await bot.send_message(message.from_user.id, "❌ An error occurred processing your payment. Please contact @niftysolsol for support.")
        else:
            logger.error("Payment error but no user found in message")

# aiohttp web handlers
async def brs_callback(request):
    """Handle BRS AI API callbacks with HMAC authentication"""
    try:
        # Get request body and headers for debugging
        body = await request.read()
        headers = dict(request.headers)
        
        # Log all headers to debug signature format
        logger.info(f"Callback headers: {headers}")
        
        # Try multiple possible signature header formats
        signature = (
            request.headers.get('X-Signature', '') or
            request.headers.get('X-HMAC-Signature', '') or
            request.headers.get('X-Hub-Signature-256', '') or
            request.headers.get('BRS-Signature', '') or
            request.headers.get('Signature', '')
        )
        
        logger.info(f"Found signature: {signature}")
        
        # TEMPORARY: Disable signature verification since BRS AI doesn't send signatures
        # TODO: Contact BRS AI about signature implementation or implement IP whitelist
        disable_verification = True  # os.getenv('DISABLE_SIGNATURE_VERIFICATION', '').lower() == 'true'
        
        if not disable_verification:
            if not signature:
                logger.error("Missing callback signature - potential attack attempt")
                return web.json_response({"error": "Missing signature"}, status=403)
            
            if not verify_callback_signature(body, signature):
                logger.error(f"Invalid callback signature from IP {request.remote} - security breach attempt")
                logger.error(f"Expected signature verification failed for body length: {len(body)}")
                # Log additional security details without exposing sensitive info
                logger.error(f"Signature format received: {signature[:10]}...{signature[-10:] if len(signature) > 20 else ''}")
                return web.json_response({"error": "Invalid signature"}, status=403)
            else:
                logger.info("Callback signature verification successful")
        else:
            logger.warning("SECURITY WARNING: Signature verification disabled for debugging - NOT FOR PRODUCTION!")
        
        # Parse JSON data
        try:
            data = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in callback: {e}")
            return web.json_response({"error": "Invalid JSON"}, status=400)
        
        # Enhanced debugging for callback processing
        logger.info(f"=== Processing BRS AI Callback ===")
        logger.info(f"Parsed JSON data: {json.dumps(data, indent=2)}")
        
        # BRS AI callback format: {"code": 200, "msg": "success", "data": {"taskId": "...", "info": {"resultUrls": "[\"url1\"]"}}}
        code = data.get('code')
        msg = data.get('msg', '')
        task_data = data.get('data', {})
        generation_id = task_data.get('taskId')
        
        logger.info(f"Callback details - Code: {code}, Message: {msg}, TaskId: {generation_id}")
        
        if not generation_id:
            logger.warning("❌ No taskId in callback - rejecting")
            return web.json_response({"error": "Missing taskId"}, status=400)
        
        logger.info(f"Current pending generations: {list(pending_generations.keys())}")
        
        if generation_id not in pending_generations:
            logger.warning(f"❌ Unknown generation_id: {generation_id}")
            logger.info(f"Available pending IDs: {list(pending_generations.keys())}")
            return web.json_response({"error": "Unknown generation_id"}, status=400)
        
        generation_info = pending_generations[generation_id]
        user_id = generation_info['user_id']
        logger.info(f"✅ Found generation for user {user_id}")
        
        if code == 200:
            # Success - extract video URLs from resultUrls JSON string
            logger.info("✅ Generation successful - processing video URLs")
            info = task_data.get('info', {})
            result_urls_str = info.get('resultUrls', info.get('result_urls', []))
            
            # Check for Wan2.2 format (resultJson) if no URLs found in info
            if not result_urls_str and 'resultJson' in task_data:
                try:
                    result_json = json.loads(task_data['resultJson'])
                    result_urls_str = result_json.get('resultUrls', [])
                    logger.info(f"Found Wan2.2 format URLs: {result_urls_str}")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse Wan2.2 resultJson: {e}")
            
            logger.info(f"Info object: {info}")
            logger.info(f"Result URLs string: {result_urls_str}")
            
            try:
                # Handle both JSON string and list formats from BRS AI
                if isinstance(result_urls_str, list):
                    result_urls = result_urls_str  # Already a list
                    logger.info(f"Result URLs received as list: {result_urls}")
                else:
                    result_urls = json.loads(result_urls_str)  # Parse JSON string
                    logger.info(f"Parsed result URLs from JSON: {result_urls}")
                
                if result_urls and len(result_urls) > 0:
                    video_url = result_urls[0]  # Use first video URL
                    logger.info(f"🎬 Sending video to user {user_id}: {video_url}")
                    await send_video_to_user(user_id, video_url, generation_id)
                    
                    # Clear selected model after successful generation
                    if user_id in user_models:
                        del user_models[user_id]
                        logger.info(f"✅ Cleared model selection for user {user_id}")
                else:
                    logger.error("❌ No video URLs in successful callback")
                    add_credits(user_id, 1)
                    await send_failure_message(user_id, generation_id)
            except (json.JSONDecodeError, IndexError, TypeError) as e:
                logger.error(f"❌ Error parsing resultUrls: {e}")
                logger.error(f"Raw resultUrls data: {result_urls_str} (type: {type(result_urls_str)})")
                add_credits(user_id, 1)
                await send_failure_message(user_id, generation_id)
        else:
            # Failure - refund credits
            logger.info(f"Video generation failed: {msg}")
            add_credits(user_id, 1)
            await send_failure_message(user_id, generation_id)
        
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
    """Send enhanced failure message to user"""
    try:
        retry_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Try Again", callback_data="quick_generate")],
            [InlineKeyboardButton(text="💡 Get Help", callback_data="help_troubleshoot"),
             InlineKeyboardButton(text="💳 Check Credits", callback_data="user_stats")]
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ **Video Generation Failed**\n\n"
                f"Generation ID: `{generation_id}`\n\n"
                "💰 **Credits Refunded:** 1 credit has been returned to your account\n\n"
                "🔄 **What to try:**\n"
                "• Check your prompt for clarity\n"
                "• Try a different model\n"
                "• Ensure stable internet connection\n"
                "• Contact support if this persists\n\n"
                "📞 **Support:** @your_support_bot\n\n"
                "👆 **Quick actions below:**"
            ),
            reply_markup=retry_keyboard,
            parse_mode="Markdown"
        )
        logger.info(f"Sent enhanced failure message to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending failure message to user {user_id}: {e}")

async def index_handler(request):
    """Basic health check"""
    return web.json_response({
        "status": "Bot is running", 
        "webhook_url": f"{WEBHOOK_URL}/brs_callback",
        "pending_generations": len(pending_generations)
    })

async def health_handler(request):
    """Health check endpoint"""
    return web.json_response({
        "status": "healthy", 
        "pending_generations": len(pending_generations),
        "user_count": len(user_credits)
    })

async def serve_image(request):
    """Serve uploaded images for BRS AI to access with security hardening"""
    try:
        filename = request.match_info['filename']
        
        # SECURITY: Prevent directory traversal attacks
        secure_filename = os.path.basename(filename)
        if secure_filename != filename:
            logger.warning(f"Potential path traversal attempt blocked: {filename}")
            return web.Response(text="Access denied", status=403)
        
        # SECURITY: Only allow specific file extensions and validate filename
        allowed_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
        if not secure_filename.lower().endswith(allowed_extensions):
            return web.Response(text="Invalid file type", status=400)
            
        # SECURITY: Additional filename validation
        if '..' in secure_filename or '/' in secure_filename or '\\' in secure_filename:
            logger.warning(f"Suspicious filename blocked: {secure_filename}")
            return web.Response(text="Invalid filename", status=400)
        
        # SECURITY: Only serve from controlled directory
        CONTROLLED_IMAGE_DIR = tempfile.gettempdir()
        image_path = os.path.join(CONTROLLED_IMAGE_DIR, secure_filename)
        
        # SECURITY: Ensure the resolved path is still in the controlled directory
        if not os.path.commonpath([CONTROLLED_IMAGE_DIR, os.path.dirname(image_path)]) == CONTROLLED_IMAGE_DIR:
            logger.warning(f"Path traversal attempt blocked: {image_path}")
            return web.Response(text="Access denied", status=403)
            
        if not os.path.exists(image_path):
            return web.Response(text="Image not found", status=404)
        
        # SECURITY: Check file size before reading (max 50MB)
        try:
            file_stat = os.stat(image_path)
            file_size = file_stat.st_size
            MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit
            
            if file_size > MAX_FILE_SIZE:
                logger.warning(f"Large file access blocked: {secure_filename} ({file_size} bytes)")
                return web.Response(text="File too large", status=413)
                
            # SECURITY: Check file age for TTL cleanup (delete files older than 24 hours)
            import time
            file_age = time.time() - file_stat.st_mtime
            TTL_SECONDS = 24 * 60 * 60  # 24 hours
            
            if file_age > TTL_SECONDS:
                logger.info(f"Removing expired image file: {secure_filename}")
                try:
                    os.remove(image_path)
                except OSError:
                    pass
                return web.Response(text="Image expired", status=404)
                
        except OSError as e:
            logger.error(f"Error checking file stats for {secure_filename}: {e}")
            return web.Response(text="File access error", status=500)
        
        # SECURITY: Only serve files with telegram_image_ prefix (created by bot)
        if not secure_filename.startswith('telegram_image_'):
            logger.warning(f"Unauthorized file access attempt: {secure_filename}")
            return web.Response(text="Access denied", status=403)
            
        # Serve the image file with size limit reading
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read(MAX_FILE_SIZE)  # Limit read size as additional safety
                
        except IOError as e:
            logger.error(f"Error reading image file {secure_filename}: {e}")
            return web.Response(text="File read error", status=500)
            
        # Determine content type with proper mapping
        content_type = 'application/octet-stream'  # Safe default
        file_ext = secure_filename.lower().split('.')[-1]
        content_type_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg', 
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        content_type = content_type_map.get(file_ext, 'application/octet-stream')
        
        # SECURITY: Add security headers
        security_headers = {
            'Cache-Control': 'public, max-age=3600',  # Cache for 1 hour
            'X-Content-Type-Options': 'nosniff',      # Prevent MIME sniffing
            'X-Frame-Options': 'DENY',                # Prevent embedding
            'Content-Security-Policy': "default-src 'none'",  # Strict CSP
            'X-Robots-Tag': 'noindex, nofollow',      # Prevent indexing
            'Content-Length': str(len(image_data))    # Explicit content length
        }
            
        return web.Response(
            body=image_data,
            content_type=content_type,
            headers=security_headers
        )
        
    except Exception as e:
        logger.error(f"Error serving image: {e}")
        return web.Response(text="Internal server error", status=500)

async def create_web_app():
    """Create aiohttp web application"""
    app = web.Application()
    
    # Add routes
    app.router.add_post('/brs_callback', brs_callback)
    app.router.add_get('/images/{filename}', serve_image)  # Add image serving endpoint
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
        logger.info(f"Callback URL: {WEBHOOK_URL}/brs_callback")
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

@dp.callback_query(F.data == "reset_model")
async def reset_model_selection(callback: CallbackQuery, state: FSMContext):
    """Handle model selection reset"""
    if not callback.from_user:
        return
        
    try:
        user_id = callback.from_user.id
        
        # Clear selected model
        if user_id in user_models:
            del user_models[user_id]
        
        # Clear any FSM state
        await state.clear()
        
        credits = get_user_credits(user_id)
        keyboard = create_model_selection_keyboard()
        
        await safe_edit_message(
            callback,
            "🔄 **Model Selection Reset**\n\n"
            f"💳 **Your Balance:** `{credits}` credits\n\n"
            "🤖 **Choose Your AI Model:**\n\n"
            "⚡ **Fast:** Quick generation (1-2 min)\n"
            "🎵 **Audio:** High quality with sound\n"
            "🚀 **Advanced:** Premium features\n"
            "💰 **Affordable:** Budget-friendly options\n\n"
            "👆 **Select a model to get started:**",
            keyboard,
            "Markdown"
        )
        await callback.answer("🔄 Model selection reset!")
        
    except Exception as e:
        logger.error(f"Error in reset_model_selection: {e}")
        await callback.answer("❌ Error resetting model selection.")

if __name__ == "__main__":
    # Run the bot and web server in the same event loop
    asyncio.run(main())