import os
import sys
import json
import threading
import time
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from dotenv import load_dotenv

load_dotenv()

# Add src to path
sys.path.append(str(Path(__file__).parent))
try:
    from email_reader import PlacementEmailReader
    from form_filler import AIFormFiller
except ImportError:
    pass

app = FastAPI(title="JobBot Mobile Controller")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "profile.json"
RESUMES_DIR = BASE_DIR / "resumes"
LOGS_FILE = BASE_DIR / "logs" / "applications_log.csv"
ENV_FILE = BASE_DIR / ".env"

bot_state = {
    "is_running": False,
    "current_status": "Idle",
    "stop_requested": False,
    "last_run_time": None,
    "stats": {
        "applied_count": 0,
        "failed_count": 0
    }
}
bot_thread: Optional[threading.Thread] = None


def get_profile():
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_profile(data: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_email_subject():
    """Reads current email subject filter from .env or profile.json."""
    prof = get_profile()
    if "email_settings" in prof and "subject_filter" in prof["email_settings"]:
        return prof["email_settings"]["subject_filter"]
    return os.getenv("PLACEMENT_EMAIL_SUBJECT", "god bless you")


def save_email_subject(new_subject: str):
    """Saves updated email subject filter into profile.json and .env."""
    prof = get_profile()
    if "email_settings" not in prof:
        prof["email_settings"] = {}
    prof["email_settings"]["subject_filter"] = new_subject
    save_profile(prof)

    # Update .env in memory and file
    os.environ["PLACEMENT_EMAIL_SUBJECT"] = new_subject
    if ENV_FILE.exists():
        try:
            content = ENV_FILE.read_text(encoding="utf-8")
            if "PLACEMENT_EMAIL_SUBJECT=" in content:
                import re
                content = re.sub(r"PLACEMENT_EMAIL_SUBJECT=.*", f"PLACEMENT_EMAIL_SUBJECT={new_subject}", content)
            else:
                content += f"\nPLACEMENT_EMAIL_SUBJECT={new_subject}\n"
            ENV_FILE.write_text(content, encoding="utf-8")
        except Exception as e:
            print(f"[Notice updating .env]: {e}")


def bot_worker_loop():
    global bot_state
    bot_state["is_running"] = True
    bot_state["stop_requested"] = False
    bot_state["current_status"] = "Active & monitoring..."

    try:
        from form_filler import AIFormFiller
        from gmail_oauth import GoogleOAuthGmailReader
        filler = AIFormFiller()
        oauth_reader = GoogleOAuthGmailReader()

        while not bot_state["stop_requested"]:
            subject_to_search = get_email_subject()
            bot_state["current_status"] = f"Checking inbox for '{subject_to_search}'..."
            bot_state["last_run_time"] = time.strftime("%H:%M:%S")

            try:
                # Use official Google OAuth reader if authenticated
                if oauth_reader.is_authenticated():
                    jobs = oauth_reader.fetch_placement_emails(subject_query=subject_to_search, max_results=3)
                else:
                    from email_reader import PlacementEmailReader
                    reader = PlacementEmailReader()
                    jobs = reader.fetch_placement_emails(max_emails=3)
                if bot_state["stop_requested"]:
                    break

                if jobs:
                    bot_state["current_status"] = f"Found {len(jobs)} jobs. Applying..."
                    for job in jobs:
                        if bot_state["stop_requested"]:
                            break
                        company = job.get("company", "Placement Company")
                        role = job.get("role", "Software Role")
                        url = job.get("apply_url")
                        bot_state["current_status"] = f"Applying to {company} ({role})..."
                        filler.apply_to_job(apply_url=url, company=company, role=role, auto_submit=False)
                        bot_state["stats"]["applied_count"] += 1
                else:
                    bot_state["current_status"] = f"Inbox clear. Listening for '{subject_to_search}'..."
            except Exception as e:
                bot_state["current_status"] = f"Ready (Last check: {str(e)[:40]})"

            for _ in range(60):
                if bot_state["stop_requested"]:
                    break
                time.sleep(2)
    finally:
        bot_state["is_running"] = False
        bot_state["current_status"] = "Stopped"


# ---------------- API Endpoints ----------------

@app.get("/api/status")
def get_status():
    resumes = list(RESUMES_DIR.glob("*.pdf")) if RESUMES_DIR.exists() else []
    return {
        "is_running": bot_state["is_running"],
        "status_text": bot_state["current_status"],
        "stats": bot_state["stats"],
        "last_run": bot_state["last_run_time"],
        "active_subject": get_email_subject(),
        "resumes_count": len(resumes),
        "resumes_list": [r.name for r in resumes]
    }


@app.post("/api/start")
def start_bot():
    global bot_thread, bot_state
    if bot_state["is_running"]:
        return {"message": "Bot is already running"}
    bot_state["stop_requested"] = False
    bot_thread = threading.Thread(target=bot_worker_loop, daemon=True)
    bot_thread.start()
    return {"message": "Bot started"}


@app.post("/api/stop")
def stop_bot():
    global bot_state
    bot_state["stop_requested"] = True
    bot_state["current_status"] = "Stopped by user"
    return {"message": "Emergency Stop Triggered."}


@app.get("/api/subject")
def api_get_subject():
    return {"subject": get_email_subject()}


@app.post("/api/subject")
async def api_save_subject(request: Request):
    data = await request.json()
    new_sub = data.get("subject", "").strip()
    if not new_sub:
        raise HTTPException(status_code=400, detail="Subject cannot be empty")
    save_email_subject(new_sub)
    return {"message": f"Email subject filter updated to: '{new_sub}'", "subject": new_sub}


@app.get("/api/auth/email_status")
def email_status():
    """Returns Gmail connection status (App Password or OAuth)."""
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    if gmail_user and gmail_pass:
        return {
            "authenticated": True,
            "email": gmail_user,
            "method": "App Password (Read-Only)"
        }
    from gmail_oauth import GoogleOAuthGmailReader
    r = GoogleOAuthGmailReader()
    if r.is_authenticated():
        return {
            "authenticated": True,
            "email": r.get_user_email(),
            "method": "Official OAuth (Read-Only)"
        }
    return {
        "authenticated": False,
        "email": None,
        "method": None
    }


@app.post("/api/auth/connect_gmail")
async def connect_gmail(request: Request):
    """Verifies Gmail IMAP credentials (Read-Only) and saves to .env and profile."""
    import imaplib
    import re
    data = await request.json()
    email_addr = data.get("email", "").strip()
    app_pwd = data.get("app_password", "").strip()

    if not email_addr or not app_pwd:
        raise HTTPException(status_code=400, detail="Please enter both your Gmail address and 16-letter App Password.")

    clean_pwd = app_pwd.replace(" ", "")

    try:
        # Test IMAP connection strictly read-only
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_addr, clean_pwd)
        mail.logout()
    except Exception as e:
        err_msg = str(e)
        if "AUTHENTICATIONFAILED" in err_msg:
            raise HTTPException(
                status_code=400,
                detail="Authentication failed. Please verify your Gmail address and 16-character App Password, and ensure IMAP is enabled in Gmail Settings."
            )
        raise HTTPException(status_code=400, detail=f"Connection failed: {err_msg}")

    # Set in memory
    os.environ["GMAIL_USER"] = email_addr
    os.environ["GMAIL_APP_PASSWORD"] = clean_pwd

    # Write to .env
    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        if "GMAIL_USER=" in content:
            content = re.sub(r"GMAIL_USER=.*", f"GMAIL_USER={email_addr}", content)
        else:
            content += f"\nGMAIL_USER={email_addr}"
        if "GMAIL_APP_PASSWORD=" in content:
            content = re.sub(r"GMAIL_APP_PASSWORD=.*", f"GMAIL_APP_PASSWORD={clean_pwd}", content)
        else:
            content += f"\nGMAIL_APP_PASSWORD={clean_pwd}"
        ENV_FILE.write_text(content, encoding="utf-8")

    # Update profile email
    prof = get_profile()
    if "personal" not in prof:
        prof["personal"] = {}
    prof["personal"]["email"] = email_addr
    save_profile(prof)

    return {"message": f"Successfully connected to {email_addr}! Read-only scanning active.", "email": email_addr}


@app.get("/api/auth/google_status")
def google_status():
    """Returns official Google OAuth connection status."""
    from gmail_oauth import GoogleOAuthGmailReader
    r = GoogleOAuthGmailReader()
    is_auth = r.is_authenticated()
    email_addr = r.get_user_email() if is_auth else None
    return {
        "authenticated": is_auth,
        "email": email_addr
    }


@app.post("/api/auth/trigger_oauth")
def trigger_oauth():
    """Opens official Google login popup in Chrome."""
    from gmail_oauth import GoogleOAuthGmailReader
    def run_auth():
        r = GoogleOAuthGmailReader()
        r.authenticate()
    threading.Thread(target=run_auth, daemon=True).start()
    return {"message": "Google Login opening in your browser! Click Allow to finish."}




@app.get("/api/profile")
def api_get_profile():
    return get_profile()


@app.post("/api/profile")
async def api_save_profile(request: Request):
    data = await request.json()
    save_profile(data)
    return {"message": "Profile updated successfully"}


@app.post("/api/upload_resume")
async def upload_resume(file: UploadFile = File(...), tag: str = Form("sde")):
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    clean_tag = tag.strip().lower().replace(" ", "_")
    target_path = RESUMES_DIR / f"resume_{clean_tag}.pdf"
    contents = await file.read()
    with open(target_path, "wb") as f:
        f.write(contents)
    return {"message": f"Saved as {target_path.name}!"}


@app.post("/api/quick_apply")
async def quick_apply(request: Request):
    data = await request.json()
    url = data.get("url")
    company = data.get("company", "Campus Placement")
    role = data.get("role", "Software Engineer")
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    def run_apply():
        try:
            from form_filler import AIFormFiller
            filler = AIFormFiller()
            filler.apply_to_job(apply_url=url, company=company, role=role, auto_submit=False)
        except Exception as e:
            print(f"[Quick Apply Error]: {e}")

    threading.Thread(target=run_apply, daemon=True).start()
    return {"message": f"Applying to {company} in the background!"}


@app.get("/api/logs")
def get_logs():
    if not LOGS_FILE.exists():
        return {"logs": []}
    with open(LOGS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return {"logs": lines[-20:]}


# ---------------- Modern, Ultra User-Friendly Mobile UI ----------------

@app.get("/", response_class=HTMLResponse)
@app.get("/api", response_class=HTMLResponse)
@app.get("/api/index.py", response_class=HTMLResponse)
def mobile_ui():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>XBOX // JobBot Pro Core</title>
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Segoe+UI:wght@400;600;700;800&family=Plus+Jakarta+Sans:wght@500;700;800;900&display=swap" rel="stylesheet">
    <style>
        :root {
            --xbox-green: #107c10;
            --xbox-green-light: #2ecc71;
            --xbox-green-glow: rgba(16, 124, 16, 0.45);
            --xbox-dark: #0a0c0f;
            --xbox-tile: #15181e;
            --xbox-tile-hover: #1c212a;
        }
        body {
            font-family: 'Segoe UI', 'Plus Jakarta Sans', -apple-system, sans-serif;
            background-color: var(--xbox-dark);
            background-image: 
                radial-gradient(circle at 85% 12%, rgba(16, 124, 16, 0.22) 0%, transparent 45%),
                radial-gradient(circle at 15% 85%, rgba(14, 122, 13, 0.14) 0%, transparent 55%),
                linear-gradient(180deg, #07090b 0%, #0c0e12 50%, #090b0e 100%);
            background-attachment: fixed;
        }
        .xbox-tile {
            background: rgba(21, 24, 30, 0.85);
            backdrop-filter: blur(18px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .xbox-tile:hover {
            border-color: rgba(16, 124, 16, 0.5);
            box-shadow: 0 0 25px rgba(16, 124, 16, 0.25);
            transform: translateY(-2px);
        }
        .xbox-tile-focused {
            border-color: #107c10 !important;
            box-shadow: 0 0 30px var(--xbox-green-glow) !important;
        }
        .tab-btn.active {
            background: #107c10;
            color: #ffffff;
            border-color: #2ecc71;
            box-shadow: 0 0 20px rgba(16, 124, 16, 0.55);
            transform: scale(1.02);
        }
        .tab-btn:not(.active) {
            color: #94a3b8;
            background: rgba(18, 21, 27, 0.8);
            border-color: rgba(255, 255, 255, 0.05);
        }
        .tab-btn:not(.active):hover {
            color: #ffffff;
            background: rgba(28, 33, 42, 0.9);
            border-color: rgba(16, 124, 16, 0.4);
        }
        /* Custom Glowing Scrollbars */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #0c0e12; }
        ::-webkit-scrollbar-thumb { background: #1a221b; border-radius: 999px; }
        ::-webkit-scrollbar-thumb:hover { background: #107c10; }
        
        /* Xbox Controller Badges */
        .badge-a { background: #107c10; color: white; border: 1px solid #2ecc71; }
        .badge-b { background: #dc2626; color: white; border: 1px solid #ef4444; }
        .badge-x { background: #2563eb; color: white; border: 1px solid #60a5fa; }
        .badge-y { background: #d97706; color: white; border: 1px solid #fbbf24; }
        
        /* Achievement Toast Animation */
        #achievementToast {
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            transform: translateY(-130%);
        }
        #achievementToast.show {
            transform: translateY(0);
        }
    </style>
</head>
<body class="text-zinc-100 min-h-screen pb-20 selection:bg-[#107c10] selection:text-white">

    <!-- Xbox Achievement Notification Toast -->
    <div id="achievementToast" class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] w-[90%] max-w-sm pointer-events-none">
        <div class="bg-zinc-900/95 border-2 border-[#107c10] text-white p-3.5 rounded-2xl shadow-[0_0_35px_rgba(16,124,16,0.6)] flex items-center gap-3 backdrop-blur-xl">
            <div class="w-10 h-10 rounded-full bg-[#107c10] flex items-center justify-center text-lg shadow-[0_0_15px_#107c10] animate-bounce">
                🏆
            </div>
            <div class="flex-1">
                <div class="text-[10px] font-bold tracking-widest uppercase text-[#2ecc71]">Achievement Unlocked</div>
                <div id="achievementTitle" class="text-xs font-black truncate">Job Application Dispatched!</div>
            </div>
            <div class="text-xs font-mono font-bold text-emerald-400 bg-emerald-950/80 px-2.5 py-1 rounded-lg border border-emerald-500/30">
                +50 G
            </div>
        </div>
    </div>

    <!-- Xbox Guide Top Header -->
    <header class="sticky top-0 z-40 bg-[#0c0e12]/85 backdrop-blur-xl border-b border-white/5 px-4 py-3 shadow-2xl">
        <div class="max-w-xl mx-auto flex justify-between items-center">
            
            <!-- Left: Xbox Logo & Brand -->
            <div class="flex items-center gap-3">
                <div class="w-9 h-9 rounded-full bg-black border-2 border-[#107c10] flex items-center justify-center shadow-[0_0_15px_rgba(16,124,16,0.5)]">
                    <svg viewBox="0 0 24 24" class="w-6 h-6 fill-[#107c10]">
                        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm3.62 14.85c-.99.55-2.28.89-3.62.89s-2.63-.34-3.62-.89c-1.3-1.63-2.18-4.22-2.34-7.2 1.4-1.25 3.51-2.02 5.96-2.02s4.56.77 5.96 2.02c-.16 2.98-1.04 5.57-2.34 7.2z"/>
                    </svg>
                </div>
                <div>
                    <h1 class="text-sm font-black tracking-wider flex items-center gap-1.5 uppercase">
                        <span>XBOX // JOBBOT</span>
                        <span class="text-[10px] font-mono bg-[#107c10]/20 text-[#2ecc71] px-2 py-0.5 rounded-md border border-[#107c10]/40 font-bold">CORE 3.8</span>
                    </h1>
                    <p id="topStatus" class="text-[11px] text-zinc-400 font-medium">System Ready</p>
                </div>
            </div>

            <!-- Right: Gamertag Badge & Live Pulse -->
            <div class="flex items-center gap-2">
                <div class="hidden sm:flex flex-col text-right">
                    <span class="text-xs font-bold text-zinc-200">YASH // 23BCE</span>
                    <span class="text-[10px] text-emerald-400 font-mono">1,450 G</span>
                </div>
                <div id="liveBadge" class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-rose-500/15 text-rose-400 border border-rose-500/30">
                    <span class="w-2 h-2 rounded-full bg-rose-500"></span>
                    <span class="tracking-wide">STANDBY</span>
                </div>
            </div>
        </div>
    </header>

    <!-- Main Xbox Dashboard Grid -->
    <main class="max-w-xl mx-auto p-4 space-y-4">

        <!-- Xbox Hero Tile: Auto-Pilot Controller -->
        <div class="xbox-tile p-5 rounded-3xl relative overflow-hidden border border-emerald-500/20">
            <div class="absolute -right-8 -top-8 w-32 h-32 bg-[#107c10]/15 rounded-full blur-3xl pointer-events-none"></div>
            
            <div class="flex justify-between items-center mb-3">
                <div class="flex items-center gap-2">
                    <span class="text-xs font-extrabold uppercase tracking-widest text-[#2ecc71] flex items-center gap-1.5">
                        <span class="w-2 h-2 rounded-full bg-[#107c10] animate-ping"></span>
                        Placement Mission Control
                    </span>
                </div>
                <span id="lastCheckTime" class="text-[11px] font-mono text-zinc-400">Patrol: --:--</span>
            </div>

            <!-- Big Command Buttons -->
            <div class="grid grid-cols-2 gap-3 pt-1">
                <button onclick="startBot()" id="btnStart" class="py-4 bg-gradient-to-r from-[#107c10] to-[#15803d] hover:brightness-110 active:scale-95 rounded-2xl font-black text-sm text-white shadow-[0_0_25px_rgba(16,124,16,0.4)] flex items-center justify-center gap-2.5 transition-all">
                    <span class="w-5 h-5 rounded-full badge-a text-xs flex items-center justify-center font-bold">A</span>
                    <span>ENGAGE BOT</span>
                </button>
                <button onclick="stopBot()" id="btnStop" class="py-4 bg-gradient-to-r from-red-600 to-rose-700 hover:brightness-110 active:scale-95 rounded-2xl font-black text-sm text-white shadow-[0_0_20px_rgba(220,38,38,0.35)] flex items-center justify-center gap-2.5 transition-all">
                    <span class="w-5 h-5 rounded-full badge-b text-xs flex items-center justify-center font-bold">B</span>
                    <span>KILL SWITCH</span>
                </button>
            </div>
        </div>

        <!-- Xbox Bento Stats Bar -->
        <div class="grid grid-cols-3 gap-3">
            <div class="xbox-tile p-3.5 rounded-2xl text-center">
                <div class="text-[11px] text-zinc-400 font-medium">GamerScore / Sent</div>
                <div id="statApplied" class="text-xl font-black text-[#2ecc71] font-mono mt-0.5">0</div>
            </div>
            <div class="xbox-tile p-3.5 rounded-2xl text-center">
                <div class="text-[11px] text-zinc-400 font-medium">Loadouts (PDFs)</div>
                <div id="statResumes" class="text-xl font-black text-indigo-400 font-mono mt-0.5">3</div>
            </div>
            <div class="xbox-tile p-3.5 rounded-2xl text-center">
                <div class="text-[11px] text-zinc-400 font-medium">Inbox Shield</div>
                <div class="text-xs font-black text-emerald-400 mt-1 uppercase tracking-tight">Read-Only</div>
            </div>
        </div>

        <!-- Xbox Controller Guide Navigation (Tabs) -->
        <div class="grid grid-cols-4 gap-2">
            <button onclick="switchTab('apply')" id="tab-apply" class="tab-btn active py-3 px-2 rounded-2xl text-xs font-bold border transition flex flex-col items-center gap-1">
                <span class="w-5 h-5 rounded-full badge-a text-[10px] flex items-center justify-center font-black">A</span>
                <span class="truncate">Quick Launch</span>
            </button>
            <button onclick="switchTab('email')" id="tab-email" class="tab-btn py-3 px-2 rounded-2xl text-xs font-bold border transition flex flex-col items-center gap-1">
                <span class="w-5 h-5 rounded-full badge-x text-[10px] flex items-center justify-center font-black">X</span>
                <span class="truncate">Inbox Radar</span>
            </button>
            <button onclick="switchTab('resumes')" id="tab-resumes" class="tab-btn py-3 px-2 rounded-2xl text-xs font-bold border transition flex flex-col items-center gap-1">
                <span class="w-5 h-5 rounded-full badge-y text-[10px] flex items-center justify-center font-black">Y</span>
                <span class="truncate">Armory</span>
            </button>
            <button onclick="switchTab('profile')" id="tab-profile" class="tab-btn py-3 px-2 rounded-2xl text-xs font-bold border transition flex flex-col items-center gap-1">
                <span class="w-5 h-5 rounded-full badge-b text-[10px] flex items-center justify-center font-black">B</span>
                <span class="truncate">Spartan ID</span>
            </button>
        </div>

        <!-- TAB 1: QUICK LAUNCH (A) -->
        <div id="view-apply" class="space-y-4">
            <div class="xbox-tile p-5 rounded-3xl space-y-3.5">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-black text-white flex items-center gap-2 uppercase tracking-wide">
                        <span class="text-[#2ecc71]">⚡</span> Direct Mission Deployment
                    </h3>
                    <span class="text-[10px] bg-[#107c10]/20 text-[#2ecc71] px-2.5 py-0.5 rounded-full border border-[#107c10]/30 font-bold">100% PRIVATE</span>
                </div>
                <p class="text-xs text-zinc-400">Received a Google Form or placement portal link via WhatsApp or email? Paste it here to deploy Gemini:</p>

                <div class="space-y-2.5">
                    <input type="url" id="quickLink" placeholder="https://forms.gle/... or career portal link" class="w-full bg-zinc-950/90 border border-zinc-700/80 rounded-2xl p-3.5 text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-[#107c10] focus:ring-1 focus:ring-[#107c10] font-mono">
                    
                    <button onclick="triggerQuickApply()" class="w-full py-4 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 active:scale-95 rounded-2xl font-black text-xs text-white shadow-lg shadow-blue-900/30 transition flex items-center justify-center gap-2 uppercase tracking-wider">
                        <span>🚀 Launch Auto-Apply Sequence</span>
                    </button>
                </div>
            </div>
        </div>

        <!-- TAB 2: INBOX RADAR & COMMS (X) -->
        <div id="view-email" class="hidden space-y-4">
            
            <!-- Gmail Connection (Strictly Read-Only) -->
            <div class="xbox-tile p-5 rounded-3xl space-y-3.5 border border-emerald-500/30">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-black text-emerald-400 flex items-center gap-2 uppercase tracking-wide">
                        <span>🔒</span> Inbox Comms Link (Read-Only)
                    </h3>
                    <span id="gmailBadge" class="text-[10px] bg-zinc-800 text-zinc-400 px-2.5 py-0.5 rounded-full font-bold">CHECKING...</span>
                </div>
                <div id="gmailConnectedInfo" class="text-xs text-zinc-300 bg-zinc-950/60 p-3 rounded-2xl border border-white/5">
                    Zero sending permissions. The bot only monitors incoming emails matching your subject filter.
                </div>

                <div class="space-y-3 pt-1">
                    <div>
                        <label class="text-[11px] text-zinc-400 font-bold uppercase tracking-wider">Gmail Address:</label>
                        <input type="email" id="gmailUserInput" placeholder="e.g. yourname@gmail.com" class="w-full bg-zinc-950 border border-zinc-700 rounded-2xl p-3.5 text-xs text-white focus:outline-none focus:border-[#107c10]">
                    </div>
                    <div>
                        <div class="flex justify-between items-center mb-1">
                            <label class="text-[11px] text-zinc-400 font-bold uppercase tracking-wider">16-Character App Password:</label>
                            <a href="https://myaccount.google.com/apppasswords" target="_blank" class="text-[10px] text-emerald-400 hover:underline font-semibold">Generate in Google ↗</a>
                        </div>
                        <input type="password" id="gmailPassInput" placeholder="cfqb qwme nxnd bqwu" class="w-full bg-zinc-950 border border-zinc-700 rounded-2xl p-3.5 text-xs text-white focus:outline-none focus:border-[#107c10] font-mono">
                    </div>
                    <button onclick="saveGmailAuth()" id="btnSaveGmail" class="w-full py-3.5 bg-gradient-to-r from-[#107c10] to-[#15803d] hover:brightness-110 active:scale-95 rounded-2xl font-black text-xs text-white shadow-lg transition flex items-center justify-center gap-2 uppercase tracking-wider">
                        <span>🔗 Connect & Test Comms</span>
                    </button>
                </div>
            </div>

            <!-- Subject Filter Keywords -->
            <div class="xbox-tile p-5 rounded-3xl space-y-3">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-black text-amber-400 flex items-center gap-2 uppercase tracking-wide">
                        <span>📡</span> Subject Filter Keywords
                    </h3>
                </div>
                <p class="text-xs text-zinc-400">Patrols your inbox for placement drives containing these keywords:</p>

                <div class="space-y-2">
                    <div class="flex gap-2">
                        <input type="text" id="subjectInput" placeholder="god bless you, Placement, Offcampus" class="flex-1 bg-zinc-950 border border-zinc-700 rounded-2xl p-3 text-xs text-amber-300 font-mono font-bold focus:outline-none focus:border-amber-500">
                        <button onclick="saveSubject()" class="bg-amber-600 hover:bg-amber-500 active:scale-95 px-5 rounded-2xl text-xs font-black text-white transition uppercase">
                            Save
                        </button>
                    </div>
                </div>

                <div class="bg-zinc-950/80 p-3 rounded-2xl border border-white/5 text-[11px] text-zinc-400 space-y-1">
                    <div class="text-zinc-200 font-bold">💡 Multi-Opening Radar:</div>
                    <div>• If a single email contains multiple job openings, Gemini extracts and applies to <strong>every single one</strong>!</div>
                </div>
            </div>
        </div>

        <!-- TAB 3: ARMORY / RESUMES (Y) -->
        <div id="view-resumes" class="hidden space-y-4">
            <div class="xbox-tile p-5 rounded-3xl space-y-3.5">
                <h3 class="text-sm font-black text-indigo-400 flex items-center gap-2 uppercase tracking-wide">
                    <span>⚔️</span> Tailored Loadouts (Resumes)
                </h3>
                <p class="text-xs text-zinc-400">Gemini inspects the job description and equips the winning loadout automatically:</p>

                <div id="resumesList" class="space-y-2 text-xs font-mono">
                    Scanning loadouts...
                </div>

                <div class="pt-3 border-t border-white/5 space-y-2.5">
                    <label class="text-[11px] text-zinc-400 font-bold uppercase tracking-wider">Equip New Loadout PDF:</label>
                    <div class="grid grid-cols-3 gap-2">
                        <select id="resumeTag" class="col-span-1 bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-xs text-white">
                            <option value="sde">SDE / Dev</option>
                            <option value="data_analyst">Data Analyst</option>
                            <option value="web_dev">Frontend</option>
                            <option value="core">General</option>
                        </select>
                        <label class="col-span-2 block py-2.5 px-3 bg-indigo-600 hover:bg-indigo-500 active:scale-95 rounded-xl text-center text-xs font-black cursor-pointer transition uppercase tracking-wide">
                            <span>📤 Choose PDF</span>
                            <input type="file" id="resumeFileInput" accept=".pdf" class="hidden" onchange="uploadResumeFile()">
                        </label>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB 4: SPARTAN ID / PROFILE (B) -->
        <div id="view-profile" class="hidden space-y-4">
            <div class="xbox-tile p-5 rounded-3xl space-y-3.5">
                <div class="flex justify-between items-center">
                    <h3 class="text-sm font-black text-white uppercase tracking-wide">Spartan Identity Card</h3>
                    <button onclick="saveProfileData()" class="px-4 py-1.5 bg-[#107c10] hover:bg-[#15803d] text-xs font-black rounded-xl text-white shadow-md uppercase">
                        💾 Save
                    </button>
                </div>

                <div class="space-y-2.5 text-xs">
                    <div>
                        <label class="text-zinc-400 font-semibold">Candidate Full Name</label>
                        <input type="text" id="profName" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-white">
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="text-zinc-400 font-semibold">CGPA / %</label>
                            <input type="text" id="profCgpa" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-white font-mono">
                        </div>
                        <div>
                            <label class="text-zinc-400 font-semibold">Passing Year</label>
                            <input type="text" id="profYear" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-white font-mono">
                        </div>
                    </div>
                    <div>
                        <label class="text-zinc-400 font-semibold">Roll Number</label>
                        <input type="text" id="profRoll" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-white font-mono">
                    </div>
                    <div>
                        <label class="text-zinc-400 font-semibold">Phone Number</label>
                        <input type="text" id="profPhone" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-white font-mono">
                    </div>
                    <div>
                        <label class="text-zinc-400 font-semibold">LinkedIn Profile URL</label>
                        <input type="text" id="profLinkedin" class="w-full bg-zinc-950 border border-zinc-700 rounded-xl p-2.5 text-white">
                    </div>
                </div>
            </div>
        </div>

        <!-- Xbox Community / Activity Feed (Live Logs) -->
        <div class="xbox-tile p-5 rounded-3xl space-y-2.5 border border-white/5">
            <div class="flex justify-between items-center">
                <h3 class="text-xs font-black text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                    <span class="w-2 h-2 rounded-full bg-[#107c10]"></span>
                    Live Activity Feed
                </h3>
                <button onclick="fetchLogs()" class="text-xs text-[#2ecc71] hover:underline font-bold">Refresh</button>
            </div>
            <div id="logsBox" class="bg-zinc-950/90 p-3.5 rounded-2xl text-[11px] font-mono text-zinc-300 max-h-40 overflow-y-auto space-y-1.5 border border-white/5">
                Patrolling activity stream...
            </div>
        </div>

    </main>

    <script>
        let currentProfile = {};

        // Xbox SFX Synth (Pure Web Audio API)
        function playXboxChime() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(523.25, audioCtx.currentTime); // C5
                osc.frequency.exponentialRampToValueAtTime(659.25, audioCtx.currentTime + 0.15); // E5
                gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.35);
            } catch(e) {}
        }

        function triggerAchievementToast(title) {
            playXboxChime();
            const toast = document.getElementById('achievementToast');
            document.getElementById('achievementTitle').innerText = title;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 4000);
        }

        function switchTab(tabId) {
            ['apply', 'email', 'resumes', 'profile'].forEach(t => {
                document.getElementById('view-' + t).classList.add('hidden');
                document.getElementById('tab-' + t).classList.remove('active');
            });
            document.getElementById('view-' + tabId).classList.remove('hidden');
            document.getElementById('tab-' + tabId).classList.add('active');
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                const badge = document.getElementById('liveBadge');
                const topStatus = document.getElementById('topStatus');
                if (data.is_running) {
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-[#107c10] animate-pulse"></span><span>PATROLLING</span>';
                    badge.className = 'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-[#107c10]/20 text-[#2ecc71] border border-[#107c10]/40';
                } else {
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-rose-500"></span><span>STANDBY</span>';
                    badge.className = 'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30';
                }

                topStatus.innerText = data.status_text || 'Ready';
                document.getElementById('statApplied').innerText = data.stats.applied_count || 0;
                document.getElementById('statResumes').innerText = data.resumes_count || 0;
                document.getElementById('lastCheckTime').innerText = 'Patrol: ' + (data.last_run || '--:--');

                // Render resumes catalog
                const rList = document.getElementById('resumesList');
                if (data.resumes_list && data.resumes_list.length > 0) {
                    rList.innerHTML = data.resumes_list.map(r => `
                        <div class="flex items-center justify-between p-2.5 rounded-xl bg-zinc-950/70 border border-white/5">
                            <span class="text-zinc-200">⚔️ ${r}</span>
                            <span class="text-[10px] text-[#2ecc71] bg-[#107c10]/20 border border-[#107c10]/30 px-2 py-0.5 rounded-full font-bold">EQUIPPED</span>
                        </div>
                    `).join('');
                } else {
                    rList.innerText = 'No loadouts found in resumes/ folder';
                }

                if (document.activeElement !== document.getElementById('subjectInput')) {
                    document.getElementById('subjectInput').value = data.active_subject || 'god bless you';
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function startBot() {
            await fetch('/api/start', { method: 'POST' });
            triggerAchievementToast('Auto-Pilot Scanner Engaged!');
            fetchStatus();
        }

        async function stopBot() {
            await fetch('/api/stop', { method: 'POST' });
            fetchStatus();
        }

        async function saveSubject() {
            const sub = document.getElementById('subjectInput').value;
            const res = await fetch('/api/subject', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: sub })
            });
            const data = await res.json();
            triggerAchievementToast('Filter Updated: ' + sub);
            fetchStatus();
        }

        async function triggerQuickApply() {
            const input = document.getElementById('quickLink');
            const url = input.value.trim();
            if (!url) {
                alert('Please paste a job or Google Form link first!');
                return;
            }
            triggerAchievementToast('Mission Launched: Auto-Applying!');
            try {
                await fetch('/api/quick_apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url, company: 'Campus Offcampus Job', role: 'Software Engineer' })
                });
                input.value = '';
                fetchStatus();
                fetchLogs();
            } catch (err) {
                alert('Error: ' + err);
            }
        }

        async function uploadResumeFile() {
            const input = document.getElementById('resumeFileInput');
            const tag = document.getElementById('resumeTag').value;
            if (!input.files || input.files.length === 0) return;
            const formData = new FormData();
            formData.append('file', input.files[0]);
            formData.append('tag', tag);

            const res = await fetch('/api/upload_resume', { method: 'POST', body: formData });
            const data = await res.json();
            triggerAchievementToast('New Loadout Equipped: ' + tag);
            fetchStatus();
        }

        async function loadProfile() {
            const res = await fetch('/api/profile');
            currentProfile = await res.json();
            if (currentProfile.personal) {
                document.getElementById('profName').value = currentProfile.personal.full_name || '';
                document.getElementById('profPhone').value = currentProfile.personal.phone || '';
            }
            if (currentProfile.academics) {
                document.getElementById('profCgpa').value = currentProfile.academics.cgpa_or_percentage || '';
                document.getElementById('profYear').value = currentProfile.academics.graduation_year || '';
                document.getElementById('profRoll').value = currentProfile.academics.roll_number || '';
            }
            if (currentProfile.links) {
                document.getElementById('profLinkedin').value = currentProfile.links.linkedin || '';
            }
        }

        async function saveProfileData() {
            if (!currentProfile.personal) currentProfile.personal = {};
            if (!currentProfile.academics) currentProfile.academics = {};
            if (!currentProfile.links) currentProfile.links = {};

            currentProfile.personal.full_name = document.getElementById('profName').value;
            currentProfile.personal.phone = document.getElementById('profPhone').value;
            currentProfile.academics.cgpa_or_percentage = document.getElementById('profCgpa').value;
            currentProfile.academics.graduation_year = document.getElementById('profYear').value;
            currentProfile.academics.roll_number = document.getElementById('profRoll').value;
            currentProfile.links.linkedin = document.getElementById('profLinkedin').value;

            await fetch('/api/profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentProfile)
            });
            triggerAchievementToast('Spartan Profile Synchronized!');
        }

        async function fetchLogs() {
            const res = await fetch('/api/logs');
            const data = await res.json();
            const box = document.getElementById('logsBox');
            if (data.logs && data.logs.length > 0) {
                box.innerHTML = data.logs.map(l => `<div class="border-b border-white/5 pb-1 text-zinc-300">🎮 ${l}</div>`).join('');
            } else {
                box.innerText = 'No patrol events recorded yet.';
            }
        }

        async function checkGmailStatus() {
            try {
                const res = await fetch('/api/auth/email_status');
                const data = await res.json();
                const badge = document.getElementById('gmailBadge');
                const info = document.getElementById('gmailConnectedInfo');
                const btn = document.getElementById('btnSaveGmail');

                if (data.authenticated && data.email) {
                    badge.innerText = 'SECURE LINK ACTIVE';
                    badge.className = 'text-[10px] bg-[#107c10]/20 text-[#2ecc71] px-2.5 py-0.5 rounded-full font-bold border border-[#107c10]/40';
                    info.innerHTML = `✅ <strong>Connected:</strong> <span class="font-mono text-[#2ecc71]">${data.email}</span> (${data.method})<br><span class="text-[11px] text-zinc-400">Read-Only Shield: Placement email monitoring active.</span>`;
                    document.getElementById('gmailUserInput').value = data.email;
                    btn.innerHTML = '<span>🔄 Re-verify Connection</span>';
                } else {
                    badge.innerText = 'UNLINKED';
                    badge.className = 'text-[10px] bg-amber-500/20 text-amber-400 px-2.5 py-0.5 rounded-full font-bold border border-amber-500/30';
                    info.innerHTML = `Enter your Gmail and 16-character App Password below. <strong>Zero emails will ever be sent. Read-only inbox scanning only.</strong>`;
                    btn.innerHTML = '<span>🔗 Connect & Test Comms</span>';
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function saveGmailAuth() {
            const email = document.getElementById('gmailUserInput').value.trim();
            const pwd = document.getElementById('gmailPassInput').value.trim();
            if (!email || !pwd) {
                alert('Please enter both your Gmail address and 16-character App Password.');
                return;
            }
            const btn = document.getElementById('btnSaveGmail');
            btn.innerText = '⏳ Verifying IMAP Comms...';
            try {
                const res = await fetch('/api/auth/connect_gmail', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: email, app_password: pwd })
                });
                const data = await res.json();
                if (!res.ok) {
                    alert('⚠️ ' + (data.detail || 'Connection failed'));
                    btn.innerHTML = '<span>🔗 Try Again</span>';
                    return;
                }
                triggerAchievementToast('Gmail Comms Secured!');
                checkGmailStatus();
                fetchStatus();
            } catch (err) {
                alert('Error: ' + err);
                btn.innerHTML = '<span>🔗 Connect & Test Comms</span>';
            }
        }

        setInterval(fetchStatus, 3000);
        setInterval(fetchLogs, 10000);
        setInterval(checkGmailStatus, 5000);

        fetchStatus();
        checkGmailStatus();
        loadProfile();
        fetchLogs();
    </script>
</body>
</html>"""

if __name__ == "__main__":
    import socket
    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    local_ip = get_local_ip()
    print("\n" + "=" * 55)
    print("⚡ JOBBOT PRO SERVER IS LIVE!")
    print("=" * 55)
    print(f"💻 PC Browser:    http://localhost:8000")
    print(f"📱 Phone Browser: http://{local_ip}:8000")
    print("=" * 55 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
