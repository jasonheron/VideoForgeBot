# Telegram Video Generation Bot

A sophisticated Telegram bot that generates AI videos using the KIE.ai API with Telegram Stars payment system.

## Overview

This bot allows users to:
- Generate AI videos using 5 advanced AI models (Veo 3, Runway Gen-3, Wan 2.2, Kling 2.1)
- Purchase credits using Telegram Stars (XTR)
- Upload optional images for video generation (text-only models like Wan 2.2 T2V skip this step)
- Receive generated videos directly in Telegram

## Features Implemented

### ✅ Bot Commands
- `/start` - Welcome message with current credit balance
- `/generate` - Video generation with model selection and prompts
- `/buy` - Purchase credits using Telegram Stars

### ✅ Payment System
- **Telegram Stars (XTR)** integration
- 1 credit = 100 Stars ($1.30 equivalent)
- Automatic credit deduction and refunds
- Secure payment processing

### ✅ Video Generation
- **5 AI Models Available**: Veo 3 Fast, Runway Gen-3, Wan 2.2 (T2V/I2V), Kling 2.1 (Standard)
- Model selection via inline keyboard with descriptive names
- Text prompt input with validation
- Smart image upload - automatically skipped for text-only models (Wan 2.2 T2V)
- Optional image upload for image-to-video models
- Integration with KIE.ai API

### ✅ Architecture
- **Async/await throughout** - Uses aiogram and aiohttp
- **Secure callback handling** - HMAC signature verification
- **Basic image handling** - Downloads from Telegram (requires external storage setup)
- **Error handling** - Comprehensive try/catch blocks
- **Credit system** - In-memory tracking with refund logic

## Project Architecture

```
main.py
├── Bot Setup (aiogram)
├── aiohttp Web Server (replaces Flask)
├── Command Handlers (/start, /generate, /buy)
├── FSM States (prompt input, image upload)
├── Payment Processing (Telegram Stars)
├── KIE.ai API Integration
└── Callback Handler (secure with HMAC)
```

## Required Environment Variables

The bot requires these API keys to function:

| Variable | Description | How to Get |
|----------|-------------|------------|
| `BOT_TOKEN` | Telegram Bot Token | Message @BotFather → /newbot |
| `BRS_AI_API_KEY` or `KIE_AI_API_KEY` | BRS AI API Key (supports both names) | Visit https://kie.ai/api-key |
| `PAYMENT_PROVIDER_TOKEN` | Not needed for XTR | Leave empty (Telegram Stars) |
| `CALLBACK_SECRET` | Strong secret for HMAC verification | Generate a random string (32+ characters) |
| `WEBHOOK_URL` | Your Replit's public URL | Copy from browser address bar when running |

## User Flow

1. **Start**: User sends `/start` → See welcome message and credit balance
2. **Purchase Credits**: `/buy` → Pay with Telegram Stars → Credits added
3. **Generate Video**: 
   - `/generate` → Select model → Enter prompt → Optional image upload
   - Bot deducts 1 credit and sends request to KIE.ai
   - KIE.ai processes video and sends callback
   - Bot delivers finished video to user
4. **Error Handling**: Credits refunded if generation fails

## Technical Details

### Payment Integration
- Uses Telegram Stars (XTR) for payments
- Empty `provider_token` for digital goods
- Pre-checkout validation and successful payment handling
- Credit tracking in memory (scalable to database)

### Security Features
- HMAC SHA-256 signature verification on callbacks
- Input validation and sanitization
- Secure error handling without information leakage
- Proper async/await to prevent race conditions

### KIE.ai Integration
- Async HTTP requests using aiohttp
- Basic image file handling (downloads from Telegram)
- **Note**: Images require external storage setup for KIE.ai access
- Callback URL for completion notifications
- Generation ID tracking for user mapping

## Current Status

✅ **MVP Implementation** - Core features implemented
✅ **Critical Fix Applied** - Persistent credit storage implemented (Dec 26, 2025)
✅ **Admin System** - Full admin panel with credit management (Sept 28, 2025)
✅ **Production Deploy Config** - VM deployment configured for always-on bot (Sept 28, 2025)
⚠️ **Image Upload Limitation** - Requires external storage setup
🚀 **Ready for Production** - Bot configured for Reserved VM deployment with 5 AI models

## Production Deployment Guide

### 🚀 **Publishing to Always-On Production**

**Important**: The bot works in development but stops when you close the chat because it's running in dev mode. For production:

1. **Click "Publish" Button** in the top toolbar
2. **Select "Reserved VM"** (not Autoscale) for always-on operation
3. **Choose Background Worker** app type (bot doesn't need web interface)
4. **Configure Resources**:
   - Minimum: 0.25 vCPU, 1GB RAM (sufficient for bot)
   - Upgrade if needed for high traffic
5. **Environment Variables**: Copy all secrets from dev to production
6. **Deploy**: Click "Publish" - bot will run 24/7

**Why Reserved VM?**: Bots need persistent connections for Telegram webhooks. Autoscale deployments stop when inactive, breaking the bot.

### 🔧 **Environment Setup**

1. **Get Telegram Bot Token**:
   - Message @BotFather on Telegram
   - Use `/newbot` command and follow instructions

2. **Get KIE.ai API Key**:
   - Visit https://kie.ai/api-key
   - Sign up and obtain API key

3. **Set Up Environment Variables**:
   - Click "Tools" → "Secrets" in Replit
   - Add BOT_TOKEN and BRS_AI_API_KEY (or KIE_AI_API_KEY for legacy compatibility)
   - Generate a strong CALLBACK_SECRET (random 32+ char string)
   - Set WEBHOOK_URL to your production URL (will be provided after publishing)

## Dependencies Installed

- `aiogram` - Modern Telegram Bot framework
- `aiohttp` - Async HTTP client/server
- `aiofiles` - Async file operations
- `python-multipart` - File upload handling

## Architecture Decisions

- **Single Event Loop**: Uses aiohttp to eliminate threading issues
- **Secure Callbacks**: HMAC verification prevents spoofed requests  
- **Async Throughout**: All operations use async/await for better performance
- **State Management**: FSM for multi-step user interactions
- **Error Recovery**: Credit refunds and user notifications on failures
- **Polling Updates**: Uses long polling for simplicity (no webhook setup needed)

## Important Setup Notes

- **WEBHOOK_URL**: Must be set to your actual Replit public URL (visible when running)
- **CALLBACK_SECRET**: Generate a strong random string for security
- **Image Uploads**: Current implementation downloads images but may need external storage for KIE.ai to access them
- **Testing**: Bot is ready for testing once proper environment variables are set

## Recent Updates

### ⚠️ Sora 2 Removal (Oct 6, 2025)
- **Removed**: Sora 2 models - not actually available on KIE.ai API yet
- **Reason**: KIE.ai documentation doesn't list Sora 2 as available
- **Current**: Bot supports 5 working AI models (Veo 3, Runway Gen-3, Wan 2.2, Kling 2.1)

### 🔧 Credit Storage Fix (Dec 26, 2025)
- **Issue**: Credits were lost on bot restart due to in-memory storage
- **Solution**: Implemented persistent JSON file storage (`user_credits.json`)
- **Result**: Credits now persist permanently across restarts
- **Compensation**: Affected users received free credits

## Known Limitations

- **Image handling**: Requires external storage service for full functionality
- **Local development**: Use polling instead of webhooks for simplicity

The bot is ready for testing and follows Telegram's latest 2025 API standards for Stars payments. For production use, consider adding database persistence and proper image storage.