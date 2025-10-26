# 🚀 Video Forge Bot - Deployment Guide

This guide provides multiple deployment options for your Telegram AI Video Generation Bot outside of Replit.

## 📋 Prerequisites

Before deploying, ensure you have:
- A VPS or cloud server (Ubuntu 20.04+ recommended)
- Domain name (optional but recommended for webhooks)
- Telegram Bot Token from @BotFather
- BRS AI API Key from https://kie.ai/api-key
- Basic knowledge of Linux commands

## 🔧 Environment Setup

### 1. Create Environment File

Copy the template and configure your environment:

```bash
cp env.template .env
nano .env
```

Fill in your actual values:

```env
# Telegram Bot Configuration
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# AI API Configuration
BRS_AI_API_KEY=your_brs_ai_api_key_here

# Security Configuration
CALLBACK_SECRET=your_strong_callback_secret_key_here_32_chars_minimum

# Webhook Configuration
WEBHOOK_URL=https://your-domain.com
WEBHOOK_PATH=/webhook

# Server Configuration
HOST=0.0.0.0
PORT=8000
```

## 🐳 Option 1: Docker Deployment (Recommended)

### Quick Start with Docker Compose

1. **Clone and setup:**
```bash
git clone <your-repo-url>
cd VideoForgeBot
cp env.template .env
# Edit .env with your values
```

2. **Deploy:**
```bash
docker-compose up -d
```

3. **Check status:**
```bash
docker-compose ps
docker-compose logs -f telegram-bot
```

### Manual Docker Deployment

1. **Build the image:**
```bash
docker build -t video-forge-bot .
```

2. **Run the container:**
```bash
docker run -d \
  --name video-forge-bot \
  --restart unless-stopped \
  --env-file .env \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/user_credits.json:/app/user_credits.json \
  video-forge-bot
```

## 🖥️ Option 2: VPS Deployment (Systemd Service)

### 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
sudo apt install python3.11 python3.11-venv python3.11-dev -y

# Install other dependencies
sudo apt install nginx certbot python3-certbot-nginx -y
```

### 2. Application Setup

```bash
# Create bot user
sudo useradd -m -s /bin/bash botuser

# Create application directory
sudo mkdir -p /opt/video-forge-bot
sudo chown botuser:botuser /opt/video-forge-bot

# Switch to bot user
sudo su - botuser

# Clone and setup application
cd /opt/video-forge-bot
git clone <your-repo-url> .
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create data directory
mkdir -p data

# Setup environment
cp env.template .env
nano .env  # Configure your values
```

### 3. Install Systemd Service

```bash
# Copy service file
sudo cp video-forge-bot.service /etc/systemd/system/

# Reload systemd and start service
sudo systemctl daemon-reload
sudo systemctl enable video-forge-bot
sudo systemctl start video-forge-bot

# Check status
sudo systemctl status video-forge-bot
sudo journalctl -u video-forge-bot -f
```

### 4. Configure Nginx

```bash
# Copy nginx config
sudo cp nginx.conf /etc/nginx/sites-available/video-forge-bot
sudo ln -s /etc/nginx/sites-available/video-forge-bot /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default

# Update server name in config
sudo nano /etc/nginx/sites-available/video-forge-bot

# Test and reload nginx
sudo nginx -t
sudo systemctl reload nginx
```

### 5. Setup SSL (Optional but Recommended)

```bash
# Install SSL certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal
sudo crontab -e
# Add: 0 12 * * * /usr/bin/certbot renew --quiet
```

## ☁️ Option 3: Cloud Platform Deployment

### DigitalOcean App Platform

1. **Create app.yaml:**
```yaml
name: video-forge-bot
services:
- name: bot
  source_dir: /
  github:
    repo: your-username/VideoForgeBot
    branch: main
  run_command: python main.py
  environment_slug: python
  instance_count: 1
  instance_size_slug: basic-xxs
  envs:
  - key: BOT_TOKEN
    value: your_bot_token
  - key: BRS_AI_API_KEY
    value: your_api_key
  - key: CALLBACK_SECRET
    value: your_secret
  - key: WEBHOOK_URL
    value: https://your-app.ondigitalocean.app
```

2. **Deploy via CLI:**
```bash
doctl apps create --spec app.yaml
```

### Railway

1. **Connect GitHub repository**
2. **Set environment variables in Railway dashboard**
3. **Deploy automatically on push**

### Heroku

1. **Create Procfile:**
```
web: python main.py
```

2. **Deploy:**
```bash
heroku create your-bot-name
heroku config:set BOT_TOKEN=your_token
heroku config:set BRS_AI_API_KEY=your_key
heroku config:set CALLBACK_SECRET=your_secret
git push heroku main
```

## 🔧 Configuration Details

### Webhook Setup

After deployment, set up the webhook:

```bash
# Set webhook (replace with your domain)
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/webhook"}'

# Check webhook status
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

### Health Monitoring

The bot includes health check endpoints:

- **Health check:** `GET /health`
- **Docker health check:** Built into Dockerfile
- **Systemd monitoring:** `systemctl status video-forge-bot`

### Logging

- **Docker:** `docker-compose logs -f telegram-bot`
- **Systemd:** `journalctl -u video-forge-bot -f`
- **Application logs:** Check console output

## 🔒 Security Considerations

### Environment Variables
- Never commit `.env` files to version control
- Use strong, unique secrets for `CALLBACK_SECRET`
- Rotate API keys regularly

### Server Security
- Keep system updated: `sudo apt update && sudo apt upgrade`
- Configure firewall: `sudo ufw enable`
- Use SSH keys instead of passwords
- Regular security audits

### Bot Security
- HMAC verification on webhooks
- Rate limiting via nginx
- Input validation and sanitization
- Error handling without information leakage

## 📊 Monitoring and Maintenance

### Health Checks

```bash
# Check if bot is running
curl http://localhost:8000/health

# Check Telegram webhook
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"

# Check logs
docker-compose logs telegram-bot
# or
journalctl -u video-forge-bot -f
```

### Backup

```bash
# Backup user credits
cp user_credits.json user_credits.json.backup

# Backup with timestamp
cp user_credits.json "user_credits_$(date +%Y%m%d_%H%M%S).json"
```

### Updates

```bash
# Docker
docker-compose pull
docker-compose up -d

# Systemd
git pull
sudo systemctl restart video-forge-bot
```

## 🚨 Troubleshooting

### Common Issues

1. **Bot not responding:**
   - Check if service is running
   - Verify webhook URL is correct
   - Check logs for errors

2. **Webhook errors:**
   - Ensure HTTPS is working
   - Check nginx configuration
   - Verify SSL certificates

3. **API errors:**
   - Verify API keys are correct
   - Check API quota limits
   - Monitor API response times

4. **Payment issues:**
   - Verify Telegram Stars is enabled
   - Check payment provider token
   - Test with small amounts first

### Debug Commands

```bash
# Test webhook locally
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 1, "message": {"message_id": 1, "from": {"id": 123}, "text": "/start"}}'

# Check environment variables
docker-compose exec telegram-bot env | grep -E "(BOT_TOKEN|BRS_AI_API_KEY)"

# Monitor resource usage
docker stats video-forge-bot
# or
htop
```

## 📈 Scaling Considerations

### High Traffic
- Increase server resources
- Use load balancer with multiple instances
- Implement database for persistent storage
- Add Redis for session management

### Database Migration
- Replace in-memory storage with PostgreSQL
- Implement proper user management
- Add analytics and reporting

## 🎯 Production Checklist

- [ ] Environment variables configured
- [ ] SSL certificate installed
- [ ] Webhook URL set correctly
- [ ] Health checks working
- [ ] Logging configured
- [ ] Backup strategy in place
- [ ] Monitoring setup
- [ ] Security measures applied
- [ ] Bot tested thoroughly
- [ ] Documentation updated

## 📞 Support

For issues specific to this deployment:
1. Check logs first
2. Verify configuration
3. Test individual components
4. Check Telegram Bot API status
5. Review this guide

---

**🎉 Congratulations!** Your Video Forge Bot is now deployed and ready to generate amazing AI videos!
