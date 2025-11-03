# Railway Callback Troubleshooting Guide

## 🔧 Fix Callback Issues

### Problem: Videos aren't being sent to Telegram after generation

### Solution: Update WEBHOOK_URL in Railway

1. **Go to Railway Dashboard** → Your Project → **Variables** tab

2. **Update WEBHOOK_URL** to include `https://`:
   ```
   WEBHOOK_URL=https://web-production-37e0.up.railway.app
   ```
   
   **Important**: 
   - ✅ Must include `https://` prefix
   - ✅ Do NOT include `/webhook` or `/brs_callback` path
   - ✅ Just the base domain URL

3. **Verify all environment variables are set**:
   ```
   BOT_TOKEN=8429679769:AAFGs-a28UvHT9YINkqKNQ9QxSjpX58UNXQ
   BRS_AI_API_KEY=e173f63f487beae17d24cf8b834ef674
   KIE_AI_API_KEY=e173f63f487beae17d24cf8b834ef674
   CALLBACK_SECRET=PHrswWg4f2PSLy0z5HMcPJSHeD-_ryBQnqwbd5GPfRs
   WEBHOOK_URL=https://web-production-37e0.up.railway.app
   HOST=0.0.0.0
   PORT=8000
   ```

4. **Redeploy** after updating variables

## 🔍 How Callback URLs Work

- **Telegram Webhook**: `https://web-production-37e0.up.railway.app/webhook`
- **AI Service Callback**: `https://web-production-37e0.up.railway.app/brs_callback`

Both URLs are built automatically from `WEBHOOK_URL` in your code.

## 🧪 Testing Callbacks

### Test callback endpoint:
```bash
curl -X POST "https://web-production-37e0.up.railway.app/brs_callback" \
  -H "Content-Type: application/json" \
  -d '{"taskId": "test123"}'
```

Expected response: `{"error": "Missing taskId"}` or similar (endpoint is working)

### Check Railway logs:
1. Go to Railway Dashboard → Your Project
2. Click on **Deployments** tab
3. Click on latest deployment
4. Check **Logs** tab for callback errors

## ✅ Verification Checklist

- [ ] WEBHOOK_URL includes `https://` prefix
- [ ] WEBHOOK_URL doesn't include `/webhook` or `/brs_callback`
- [ ] All environment variables are set correctly
- [ ] Bot is deployed and running
- [ ] Callback endpoint responds to test requests
- [ ] Railway logs show callback requests being received

## 🐛 Common Issues

### Issue 1: Callback URL missing https://
**Symptom**: AI service can't reach your callback endpoint
**Fix**: Add `https://` to WEBHOOK_URL

### Issue 2: Callback secret mismatch
**Symptom**: Callbacks rejected due to invalid HMAC signature
**Fix**: Ensure CALLBACK_SECRET matches what AI service expects

### Issue 3: Bot not receiving callbacks
**Symptom**: Videos generated but not sent to users
**Fix**: Check Railway logs for callback errors, verify endpoint is accessible

