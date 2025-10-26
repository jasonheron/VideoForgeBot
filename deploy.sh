#!/bin/bash

# Video Forge Bot Quick Deployment Script
echo "🚀 Video Forge Bot Deployment Script"
echo "=================================="

# Check if .env exists, if not create it
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    ./setup_env.sh
else
    echo "✅ .env file already exists"
fi

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is available
if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose is not available. Please ensure Docker Desktop is running."
    exit 1
fi

echo "🐳 Starting Docker deployment..."

# Build and start the containers
docker compose up -d --build

echo "⏳ Waiting for services to start..."
sleep 10

# Check if services are running
echo "📊 Checking service status..."
docker compose ps

# Test health endpoint
echo "🏥 Testing health endpoint..."
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Bot is running and healthy!"
    echo "🌐 Health check: http://localhost:8000/health"
    echo "📱 Your bot should now be responding on Telegram"
else
    echo "❌ Health check failed. Check logs with: docker compose logs"
fi

echo ""
echo "📋 Useful commands:"
echo "   View logs: docker compose logs -f telegram-bot"
echo "   Stop bot: docker compose down"
echo "   Restart: docker compose restart"
echo "   Update: docker compose pull && docker compose up -d"
