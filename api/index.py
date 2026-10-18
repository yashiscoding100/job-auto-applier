import sys
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
sys.path.append(str(BASE_DIR / "src"))

from src.mobile_server import app

# Vercel ASGI path fix:
# When Vercel rewrites /(.*) to /api/index.py, FastAPI receives path as "/api/index.py"
# We restore the actual URL path requested by the user.
@app.middleware("http")
async def vercel_routing_fix(request: Request, call_next):
    # Check if Vercel forwarded the original URL
    forwarded_path = request.headers.get("x-forwarded-uri") or request.headers.get("x-matched-path")
    if forwarded_path and forwarded_path != "/api/index.py":
        request.scope["path"] = forwarded_path
    elif request.scope.get("path") in ["/api/index.py", "/api/index", "/api"]:
        request.scope["path"] = "/"
    
    response = await call_next(request)
    return response

# Export app for Vercel
app = app
