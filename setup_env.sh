#!/bin/bash

# Video Forge Bot Environment Setup Script
echo "🤖 Setting up Video Forge Bot environment..."

# Create .env file with your credentials
cat > .env << EOF
# Telegram Bot Configuration
BOT_TOKEN=8429679769:AAFGs-a28UvHT9YINkqKNQ9QxSjpX58UNXQ

# AI API Configuration (using KIE_AI_API_KEY for backward compatibility)
KIE_AI_API_KEY=e173f63f487beae17d24cf8b834ef674

# Payment Configuration (Telegram Stars - leave empty)
PAYMENT_PROVIDER_TOKEN=

# Security Configuration
CALLBACK_SECRET=PHrswWg4f2PSLy0z5HMcPJSHeD-_ryBQnqwbd5GPfRs

# Webhook Configuration
WEBHOOK_URL=https://f3ecb50a-1c24-4f50-a6c6-9317cd067666-00-1digwuwh1vbmx.janeway.replit.dev
WEBHOOK_PATH=/webhook

# Server Configuration
HOST=0.0.0.0
PORT=8000
EOF

echo "✅ Environment file created successfully!"
echo "📝 Your .env file contains:"
echo "   - BOT_TOKEN: ${BOT_TOKEN:0:10}..."
echo "   - KIE_AI_API_KEY: ${KIE_AI_API_KEY:0:10}..."
echo "   - CALLBACK_SECRET: ${CALLBACK_SECRET:0:10}..."
echo "   - WEBHOOK_URL: ${WEBHOOK_URL}"

echo ""
echo "🚀 Ready to deploy! Choose your deployment method:"
echo "   1. Docker: docker-compose up -d"
echo "   2. Local Python: python main.py"
echo "   3. VPS: Follow DEPLOYMENT_GUIDE.md"
