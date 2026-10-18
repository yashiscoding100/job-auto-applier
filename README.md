# ⚡ JobBot Pro - Zero-Rupee Autonomous Job Applier & Placement Bot

An intelligent, zero-cost (₹0) autonomous job application bot powered by **Google Gemini 3.8 Flash** and **Playwright**. Designed specifically for college campus placements and off-campus recruitment drives.

---

## ✨ Features

- **🧠 Multi-Resume AI Matching:** Gemini 3.8 Flash evaluates the Job Description (JD) and automatically picks the best matching resume (SDE, Data Analyst, Web Dev, etc.).
- **⚡ 1-Click Quick Apply:** Paste any Google Form or job portal link from your phone; the bot inspects, types, uploads, and applies instantly.
- **📧 Email Placement Scanner:** Listens for college placement emails with subjects like `"god bless you"`, extracts company links, and queues them.
- **📱 Ultra-Friendly Mobile PWA Dashboard:** Access from your smartphone with an Emergency Kill Switch, subject filter editor, profile manager, and live activity feed.
- **🛡️ Anti-Bot & Human Mimicry:** Types with natural human keystroke delay (25–65ms) and handles multi-page form wizards.
- **💰 100% Free (₹0):** Built on open-source libraries and Google AI Studio free tier.

---

## 🚀 Quick Setup & Run

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/your-username/job-auto-applier.git
cd job-auto-applier
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment
Copy `.env.example` to `.env` and insert your free Gemini API key:
```env
GEMINI_API_KEY=your_key_from_aistudio.google.com
PLACEMENT_EMAIL_SUBJECT=god bless you
```

### 3. Launch Dashboard
```bash
# On Windows, simply double click:
start_mobile_dashboard.bat

# Or run directly:
python src/mobile_server.py
```
Open `http://localhost:8000` on your PC, or open the local Wi-Fi IP on your phone!

---

## 🌐 Deploying Web Controller to Vercel

1. Push this repository to your GitHub account:
   ```bash
   git init
   git add .
   git commit -m "Initial commit of JobBot Pro"
   git remote add origin https://github.com/YOUR_USERNAME/job-auto-applier.git
   git branch -M main
   git push -u origin main
   ```
2. Go to [Vercel.com](https://vercel.com) $\rightarrow$ Click **"Add New Project"**.
3. Import your GitHub repository.
4. Add your `GEMINI_API_KEY` in Environment Variables.
5. Click **Deploy**! 🚀
