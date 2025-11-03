#!/usr/bin/env python3
"""
Simple web server for Video Forge web interface
Serves the HTML interface and handles image uploads
"""

import os
import logging
from aiohttp import web
from aiohttp.web import Request, Response
import aiofiles
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
PORT = int(os.getenv("WEB_PORT", 8080))
HOST = os.getenv("WEB_HOST", "0.0.0.0")

async def serve_html(request: Request) -> Response:
    """Serve the main HTML interface"""
    html_path = Path("web_interface.html")
    if not html_path.exists():
        return web.Response(text="web_interface.html not found", status=404)
    
    async with aiofiles.open(html_path, 'r') as f:
        content = await f.read()
    
    return web.Response(text=content, content_type='text/html')

async def upload_image(request: Request) -> Response:
    """Handle image uploads and return public URL"""
    try:
        # Get the uploaded file
        reader = await request.multipart()
        field = await reader.next()
        
        if field.name != 'image':
            return web.json_response({"error": "No image field"}, status=400)
        
        # Read the image
        filename = field.filename or f"image_{uuid.uuid4().hex[:8]}"
        filepath = UPLOAD_DIR / filename
        
        # Save the file
        async with aiofiles.open(filepath, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                await f.write(chunk)
        
        # Get the base URL from request
        base_url = f"{request.scheme}://{request.host}"
        image_url = f"{base_url}/uploads/{filename}"
        
        logger.info(f"Image uploaded: {filename}")
        
        return web.json_response({
            "success": True,
            "url": image_url
        })
    
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def serve_upload(request: Request) -> Response:
    """Serve uploaded images"""
    filename = request.match_info.get('filename')
    if not filename:
        return web.Response(status=404)
    
    filepath = UPLOAD_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        return web.Response(status=404)
    
    async with aiofiles.open(filepath, 'rb') as f:
        content = await f.read()
    
    # Determine content type
    content_type = 'image/jpeg'
    if filename.endswith('.png'):
        content_type = 'image/png'
    elif filename.endswith('.webp'):
        content_type = 'image/webp'
    elif filename.endswith('.gif'):
        content_type = 'image/gif'
    
    return web.Response(body=content, content_type=content_type)

async def health_check(request: Request) -> Response:
    """Health check endpoint"""
    return web.Response(text="OK")

def create_app():
    """Create and configure the web application"""
    app = web.Application()
    
    # Routes
    app.router.add_get('/', serve_html)
    app.router.add_get('/index.html', serve_html)
    app.router.add_post('/api/upload', upload_image)
    app.router.add_get('/uploads/{filename}', serve_upload)
    app.router.add_get('/health', health_check)
    
    return app

def main():
    """Run the web server"""
    app = create_app()
    logger.info(f"🌐 Starting web server on {HOST}:{PORT}")
    logger.info(f"📁 Upload directory: {UPLOAD_DIR.absolute()}")
    web.run_app(app, host=HOST, port=PORT)

if __name__ == "__main__":
    main()

