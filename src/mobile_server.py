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
def mobile_ui():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>⚡ JobBot Pro</title>
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
        .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
        .glass-card { background: rgba(30, 41, 59, 0.85); backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.1); }
        .tab-active { background: #3b82f6; color: white; }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen pb-24 selection:bg-blue-600">

    <!-- Top Navigation Bar -->
    <header class="sticky top-0 z-50 glass border-b border-slate-800 px-4 py-3.5 flex justify-between items-center shadow-lg">
        <div class="flex items-center gap-2.5">
            <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center shadow-md shadow-blue-500/20">
                <span class="text-lg font-black">⚡</span>
            </div>
            <div>
                <h1 class="text-base font-bold tracking-tight flex items-center gap-1.5">
                    JobBot Pro
                    <span class="text-[10px] font-semibold bg-blue-500/20 text-blue-400 px-2 py-0.5 rounded-full border border-blue-500/30">Gemini 3.8</span>
                </h1>
                <p id="topStatus" class="text-[11px] text-slate-400">Ready</p>
            </div>
        </div>

        <div id="liveBadge" class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-rose-500/20 text-rose-400 border border-rose-500/30">
            <span class="w-2 h-2 rounded-full bg-rose-500 animate-pulse"></span>
            <span>STOPPED</span>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-md mx-auto p-4 space-y-4">

        <!-- Master Controller Card -->
        <div class="glass-card p-4 rounded-3xl shadow-xl space-y-3">
            <div class="flex justify-between items-center text-xs text-slate-400">
                <span class="font-semibold uppercase tracking-wider">System Control</span>
                <span id="lastCheckTime" class="font-mono text-slate-300">Last check: --:--</span>
            </div>

            <div class="grid grid-cols-2 gap-2.5">
                <button onclick="startBot()" id="btnStart" class="py-3.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 active:scale-95 rounded-2xl font-bold text-sm text-white shadow-lg shadow-emerald-900/30 flex items-center justify-center gap-2 transition-all">
                    <span>▶</span> Start Auto-Bot
                </button>
                <button onclick="stopBot()" id="btnStop" class="py-3.5 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 active:scale-95 rounded-2xl font-bold text-sm text-white shadow-lg shadow-rose-900/30 flex items-center justify-center gap-2 transition-all">
                    <span>🛑</span> KILL SWITCH
                </button>
            </div>
        </div>

        <!-- Tab Switcher -->
        <div class="flex p-1 bg-slate-900/90 rounded-2xl border border-slate-800 text-xs font-semibold">
            <button onclick="switchTab('apply')" id="tab-apply" class="flex-1 py-2 rounded-xl transition tab-active">⚡ Quick Apply</button>
            <button onclick="switchTab('email')" id="tab-email" class="flex-1 py-2 rounded-xl transition text-slate-400 hover:text-white">📧 Subject</button>
            <button onclick="switchTab('resumes')" id="tab-resumes" class="flex-1 py-2 rounded-xl transition text-slate-400 hover:text-white">📄 Resumes</button>
            <button onclick="switchTab('profile')" id="tab-profile" class="flex-1 py-2 rounded-xl transition text-slate-400 hover:text-white">👤 Profile</button>
        </div>

        <!-- TAB 1: QUICK APPLY -->
        <div id="view-apply" class="space-y-4">
            <div class="glass-card p-4 rounded-3xl space-y-3">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-bold text-blue-400 flex items-center gap-1.5">
                        <span>⚡</span> Paste Job Link to Apply
                    </h3>
                    <span class="text-[10px] bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded-full border border-emerald-500/30 font-medium">100% Private</span>
                </div>
                <p class="text-xs text-slate-400">Received a link in WhatsApp or college email? Paste it here and Gemini will pick the best resume, fill fields, and submit:</p>

                <div class="space-y-2">
                    <input type="url" id="quickLink" placeholder="https://forms.gle/... or job link" class="w-full bg-slate-900/90 border border-slate-700/80 rounded-2xl p-3 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500">
                    <button onclick="triggerQuickApply()" class="w-full py-3 bg-blue-600 hover:bg-blue-500 active:scale-95 rounded-2xl font-bold text-xs text-white shadow-lg shadow-blue-900/30 transition flex items-center justify-center gap-1.5">
                        <span>🚀</span> Launch Auto-Apply Now
                    </button>
                </div>
            </div>

            <!-- Stats Bar -->
            <div class="grid grid-cols-2 gap-3">
                <div class="glass-card p-3 rounded-2xl text-center">
                    <div class="text-[11px] text-slate-400">Total Applied</div>
                    <div id="statApplied" class="text-2xl font-black text-blue-400">0</div>
                </div>
                <div class="glass-card p-3 rounded-2xl text-center">
                    <div class="text-[11px] text-slate-400">Resumes Loaded</div>
                    <div id="statResumes" class="text-2xl font-black text-indigo-400">0</div>
                </div>
            </div>
        </div>

        <!-- TAB 2: EMAIL SUBJECT CHECKER & CUSTOMIZER -->
        <div id="view-email" class="hidden space-y-4">
            <div class="glass-card p-4 rounded-3xl space-y-3">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-bold text-amber-400 flex items-center gap-1.5">
                        <span>📧</span> Placement Email Subject Filter
                    </h3>
                </div>
                <p class="text-xs text-slate-400">The bot searches your placement emails for this exact subject phrase. You can check or change it anytime:</p>

                <div class="space-y-2">
                    <label class="text-[11px] text-slate-400 font-semibold uppercase">Current Monitored Subject:</label>
                    <div class="flex gap-2">
                        <input type="text" id="subjectInput" class="flex-1 bg-slate-900/90 border border-slate-700 rounded-2xl p-3 text-xs text-amber-300 font-mono font-bold focus:outline-none focus:border-amber-500">
                        <button onclick="saveSubject()" class="bg-amber-600 hover:bg-amber-500 active:scale-95 px-4 rounded-2xl text-xs font-bold text-white transition">
                            Save
                        </button>
                    </div>
                </div>

                <div class="bg-slate-900/60 p-3 rounded-2xl border border-slate-800 text-[11px] text-slate-400 space-y-1">
                    <div class="text-slate-300 font-semibold">💡 Common Examples:</div>
                    <div>• <code class="text-amber-300">god bless you</code> (Your college signoff)</div>
                    <div>• <code class="text-amber-300">Placement Alert</code></div>
                    <div>• <code class="text-amber-300">Offcampus Drive 2025</code></div>
                </div>
            </div>

            <!-- Official Google OAuth 2.0 Sign In Card -->
            <div class="glass-card p-4 rounded-3xl space-y-3 border border-emerald-500/30">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-bold text-emerald-400 flex items-center gap-1.5">
                        <span>🔗</span> Official Google Sign-In (OAuth 2.0)
                    </h3>
                    <span id="oauthBadge" class="text-[10px] bg-slate-800 text-slate-400 px-2 py-0.5 rounded-full font-medium">Checking...</span>
                </div>
                <p class="text-xs text-slate-400">Opens official Google Login in Chrome. You click "Allow". Zero passwords ever typed or stored.</p>
                <div id="oauthAccountText" class="text-xs font-mono text-emerald-300">Checking connection...</div>
                <button onclick="triggerGoogleOAuth()" id="btnOAuth" class="w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 rounded-2xl font-bold text-xs text-white shadow-lg transition flex items-center justify-center gap-2">
                    <span>🌐</span> Sign In with Google
                </button>
            </div>
        </div>

        <!-- TAB 3: RESUME MANAGER -->
        <div id="view-resumes" class="hidden space-y-4">
            <div class="glass-card p-4 rounded-3xl space-y-3">
                <h3 class="text-sm font-bold text-indigo-400 flex items-center gap-1.5">
                    <span>📄</span> Tailored Resumes Catalog
                </h3>
                <p class="text-xs text-slate-400">Gemini automatically compares each job description and picks the winning resume from this list:</p>

                <div id="resumesList" class="space-y-1.5 text-xs font-mono">
                    Loading resumes...
                </div>

                <div class="pt-2 border-t border-slate-800 space-y-2">
                    <label class="text-[11px] text-slate-400 font-semibold">Upload New Resume (with role tag):</label>
                    <div class="grid grid-cols-3 gap-2">
                        <select id="resumeTag" class="col-span-1 bg-slate-900 border border-slate-700 rounded-xl p-2 text-xs text-white">
                            <option value="sde">SDE / Dev</option>
                            <option value="data_analyst">Data Analyst</option>
                            <option value="web_dev">Frontend</option>
                            <option value="core">General</option>
                        </select>
                        <label class="col-span-2 block py-2 px-3 bg-indigo-600 hover:bg-indigo-500 rounded-xl text-center text-xs font-bold cursor-pointer transition">
                            <span>📤 Choose PDF</span>
                            <input type="file" id="resumeFileInput" accept=".pdf" class="hidden" onchange="uploadResumeFile()">
                        </label>
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB 4: PROFILE DETAILS -->
        <div id="view-profile" class="hidden space-y-4">
            <div class="glass-card p-4 rounded-3xl space-y-3">
                <div class="flex justify-between items-center">
                    <h3 class="text-sm font-bold text-slate-200">Personal & Academic Details</h3>
                    <button onclick="saveProfileData()" class="px-3 py-1 bg-blue-600 text-xs font-bold rounded-xl hover:bg-blue-500">
                        💾 Save
                    </button>
                </div>

                <div class="space-y-2 text-xs">
                    <div>
                        <label class="text-slate-400">Full Name</label>
                        <input type="text" id="profName" class="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-white">
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="text-slate-400">CGPA / %</label>
                            <input type="text" id="profCgpa" class="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-white">
                        </div>
                        <div>
                            <label class="text-slate-400">Passing Year</label>
                            <input type="text" id="profYear" class="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-white">
                        </div>
                    </div>
                    <div>
                        <label class="text-slate-400">Roll Number</label>
                        <input type="text" id="profRoll" class="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-white">
                    </div>
                    <div>
                        <label class="text-slate-400">Phone</label>
                        <input type="text" id="profPhone" class="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-white">
                    </div>
                    <div>
                        <label class="text-slate-400">LinkedIn URL</label>
                        <input type="text" id="profLinkedin" class="w-full bg-slate-900 border border-slate-700 rounded-xl p-2 text-white">
                    </div>
                </div>
            </div>
        </div>

        <!-- Live Activity Logs -->
        <div class="glass-card p-4 rounded-3xl space-y-2">
            <div class="flex justify-between items-center">
                <h3 class="text-xs font-bold text-slate-400 uppercase tracking-wider">Live Activity Log</h3>
                <button onclick="fetchLogs()" class="text-[11px] text-blue-400 hover:underline">Refresh</button>
            </div>
            <div id="logsBox" class="bg-slate-950/80 p-3 rounded-2xl text-[11px] font-mono text-slate-300 max-h-36 overflow-y-auto space-y-1 border border-slate-800">
                Loading activity logs...
            </div>
        </div>

    </main>

    <script>
        let currentProfile = {};

        function switchTab(tabId) {
            ['apply', 'email', 'resumes', 'profile'].forEach(t => {
                document.getElementById('view-' + t).classList.add('hidden');
                document.getElementById('tab-' + t).classList.remove('tab-active');
                document.getElementById('tab-' + t).classList.add('text-slate-400');
            });
            document.getElementById('view-' + tabId).classList.remove('hidden');
            document.getElementById('tab-' + tabId).classList.add('tab-active');
            document.getElementById('tab-' + tabId).classList.remove('text-slate-400');
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                const badge = document.getElementById('liveBadge');
                const topStatus = document.getElementById('topStatus');
                if (data.is_running) {
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span><span>RUNNING</span>';
                    badge.className = 'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
                } else {
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-rose-500"></span><span>STOPPED</span>';
                    badge.className = 'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-rose-500/20 text-rose-400 border border-rose-500/30';
                }

                topStatus.innerText = data.status_text || 'Ready';
                document.getElementById('statApplied').innerText = data.stats.applied_count || 0;
                document.getElementById('statResumes').innerText = data.resumes_count || 0;
                document.getElementById('lastCheckTime').innerText = 'Last: ' + (data.last_run || '--:--');

                // Render resumes list
                const rList = document.getElementById('resumesList');
                if (data.resumes_list && data.resumes_list.length > 0) {
                    rList.innerHTML = data.resumes_list.map(r => `
                        <div class="flex items-center justify-between p-2 rounded-xl bg-slate-900/60 border border-slate-800">
                            <span class="text-indigo-300">📄 ${r}</span>
                            <span class="text-[10px] text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full">Ready</span>
                        </div>
                    `).join('');
                } else {
                    rList.innerText = 'No resumes in resumes/ folder';
                }

                // Update subject input if not focused
                if (document.activeElement !== document.getElementById('subjectInput')) {
                    document.getElementById('subjectInput').value = data.active_subject || 'god bless you';
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function startBot() {
            await fetch('/api/start', { method: 'POST' });
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
            alert(data.message || 'Subject updated!');
            fetchStatus();
        }

        async function triggerQuickApply() {
            const input = document.getElementById('quickLink');
            const url = input.value.trim();
            if (!url) {
                alert('Please paste a job or Google Form link first!');
                return;
            }
            alert('🚀 Triggered Auto-Apply! The bot is opening Chrome and filling the form now.');
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
            alert(data.message || 'Resume uploaded!');
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

            const res = await fetch('/api/profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentProfile)
            });
            const data = await res.json();
            alert(data.message || 'Profile saved!');
        }

        async function fetchLogs() {
            const res = await fetch('/api/logs');
            const data = await res.json();
            const box = document.getElementById('logsBox');
            if (data.logs && data.logs.length > 0) {
                box.innerHTML = data.logs.map(l => `<div class="border-b border-slate-800 pb-1 text-slate-300">${l}</div>`).join('');
            } else {
                box.innerText = 'No applications logged yet.';
            }
        }

        async function checkGoogleOAuth() {
            try {
                const res = await fetch('/api/auth/google_status');
                const data = await res.json();
                const badge = document.getElementById('oauthBadge');
                const text = document.getElementById('oauthAccountText');
                const btn = document.getElementById('btnOAuth');

                if (data.authenticated && data.email) {
                    badge.innerText = 'CONNECTED';
                    badge.className = 'text-[10px] bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded-full font-medium border border-emerald-500/30';
                    text.innerText = 'Connected: ' + data.email;
                    btn.innerText = '✅ Google Account Active';
                    btn.className = 'w-full py-3 bg-emerald-800 text-slate-300 rounded-2xl font-bold text-xs cursor-default';
                } else {
                    badge.innerText = 'NOT CONNECTED';
                    badge.className = 'text-[10px] bg-slate-800 text-slate-400 px-2 py-0.5 rounded-full font-medium';
                    text.innerText = 'Zero passwords stored. Click to connect:';
                    btn.innerHTML = '<span>🌐</span> Sign In with Google';
                    btn.className = 'w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 rounded-2xl font-bold text-xs text-white shadow-lg transition flex items-center justify-center gap-2';
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function triggerGoogleOAuth() {
            alert('Opening Google Sign-In window on your PC screen! Click "Allow" to finish.');
            await fetch('/api/auth/trigger_oauth', { method: 'POST' });
            setTimeout(checkGoogleOAuth, 5000);
        }

        setInterval(fetchStatus, 3000);
        setInterval(fetchLogs, 10000);
        setInterval(checkGoogleOAuth, 5000);

        fetchStatus();
        checkGoogleOAuth();
        loadProfile();
        fetchLogs();
    </script>
</body>
</html>
    """

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
