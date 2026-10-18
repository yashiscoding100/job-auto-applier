import sys
from pathlib import Path

# Add project root and src to python path for Vercel serverless runtime
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
sys.path.append(str(BASE_DIR / "src"))

from src.mobile_server import app

# Export app for Vercel Serverless Function
export_app = app
