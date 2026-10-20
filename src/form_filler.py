import os
import sys
import json
import time
import csv
import re
import random
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, Locator
from google import genai

# Ensure UTF-8 and unbuffered console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

load_dotenv()

class AIFormFiller:
    def __init__(self, profile_path: str = "config/profile.json"):
        self.profile_path = Path(profile_path)
        if not self.profile_path.exists():
            raise FileNotFoundError(f"Profile config not found at: {self.profile_path}")

        with open(self.profile_path, "r", encoding="utf-8") as f:
            self.profile = json.load(f)

        self.resumes_dir = Path("resumes").resolve()
        self.default_resume = Path(self.profile.get("default_resume", "resumes/resume.pdf")).resolve()
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if not self.gemini_key:
            raise ValueError("GEMINI_API_KEY is not set in your .env file.")

        self.ai_client = genai.Client(api_key=self.gemini_key)
        self.model_name = "gemini-3.5-flash-lite"
        self.models_to_try = [
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash"
        ]

        self.log_file = Path("logs/applications_log.csv")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_log_file()

    def call_gemini_with_retry(self, prompt: str, max_retries: int = 3) -> str:
        """Calls Gemini API with automatic model fallback and retries on 429/503 errors."""
        for model_id in self.models_to_try:
            for attempt in range(max_retries):
                try:
                    res = self.ai_client.models.generate_content(
                        model=model_id,
                        contents=prompt
                    )
                    if res and res.text:
                        return res.text
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "quota" in err_str.lower() or "NOT_FOUND" in err_str:
                        # Fallback immediately to next model in the list
                        break
                    if ("503" in err_str or "UNAVAILABLE" in err_str) and attempt < max_retries - 1:
                        wait_time = 2 * (attempt + 1)
                        print(f"[Gemini 503 Spike on {model_id}]: Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        break
        return ""


    def select_best_resume(self, company: str, role: str, page_content: str) -> Path:
        """
        Analyzes the Job Description / Role and picks
        the optimal tailored resume from the resumes/ folder (fast heuristic first, Gemini fallback).
        """
        # Find all PDF files in resumes directory
        available_resumes = list(self.resumes_dir.glob("*.pdf"))
        if not available_resumes:
            print("[Warning]: No resumes found in resumes/ directory!")
            return self.default_resume
        if len(available_resumes) == 1:
            return available_resumes[0]

        # Fast direct matching for standard roles (<1ms)
        role_lower = (role or "").lower()
        if any(k in role_lower for k in ["sde", "software", "backend", "full stack", "fullstack", "developer", "engineer"]):
            sde_path = self.resumes_dir / "resume_sde.pdf"
            if sde_path.exists():
                print(f"\n🧠 [AI RESUME MATCH]: Fast-matched '{sde_path.name}' for role '{role}'")
                return sde_path
        elif any(k in role_lower for k in ["data", "analyst", "analytics", "bi "]):
            da_path = self.resumes_dir / "resume_data_analyst.pdf"
            if da_path.exists():
                print(f"\n🧠 [AI RESUME MATCH]: Fast-matched '{da_path.name}' for role '{role}'")
                return da_path
        elif any(k in role_lower for k in ["frontend", "web", "react", "ui", "javascript"]):
            web_path = self.resumes_dir / "resume_web_developer.pdf"
            if web_path.exists():
                print(f"\n🧠 [AI RESUME MATCH]: Fast-matched '{web_path.name}' for role '{role}'")
                return web_path

        catalog_info = self.profile.get("resumes", {})
        resumes_metadata = []
        for r_path in available_resumes:
            name = r_path.name
            tag = name.replace("resume_", "").replace(".pdf", "")
            roles = catalog_info.get(tag, {}).get("target_roles", [])
            resumes_metadata.append({
                "filename": name,
                "absolute_path": str(r_path).replace(os.sep, "/"),
                "tagged_roles": roles
            })

        prompt = f"""
You are an expert technical recruiter and resume selector.
Analyze this job posting requirements and choose the single BEST tailored resume from the candidate's available resumes.

Company: {company}
Role / Job Title: {role}
Job Context / Page Snippet:
---
{page_content[:2500]}
---

CANDIDATE'S AVAILABLE RESUMES:
---
{json.dumps(resumes_metadata, indent=2)}
---

TASK:
Decide which resume filename matches the skills and tech stack of this job best (e.g. SDE vs Data Analyst vs Web Dev).
Return a strictly valid JSON object with:
- "selected_filename": "exact filename of the chosen resume"
- "reason": "crisp 1-sentence justification"

Only output raw JSON. Do not include markdown or explanation.
"""
        try:
            text_res = self.call_gemini_with_retry(prompt)
            cleaned = text_res.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)
            chosen_name = data.get("selected_filename")
            reason = data.get("reason", "Best fit for JD")

            chosen_path = self.resumes_dir / chosen_name
            if chosen_path.exists():
                print(f"\n🧠 [AI RESUME MATCH]: Gemini selected '{chosen_name}'")
                print(f"   Reason: {reason}\n")
                return chosen_path
        except Exception as e:
            print(f"[Notice in Resume Selection]: {e}")

        # Fallback
        return available_resumes[0]

    def _init_log_file(self):
        if not self.log_file.exists():
            with open(self.log_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Company", "Role", "URL", "Status", "Notes"])

    def log_application(self, company: str, role: str, url: str, status: str, notes: str = ""):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, company, role, url, status, notes])
        print(f"[{status}] Logged: {company} - {role}")

    def notify_user_alert(self):
        """Audio chime on Windows if CAPTCHA or user intervention needed."""
        try:
            import winsound
            winsound.Beep(1000, 400)
            winsound.Beep(1400, 400)
        except Exception:
            pass

    def inspect_form_fields(self, page: Page) -> List[Dict[str, Any]]:
        """
        Robust DOM extractor that inspects interactive fields across all platforms:
        Google Forms, Greenhouse, Lever, Workday, and custom placement portals.
        """
        js_code = """
        () => {
            const elements = [];
            let index = 0;

            function getLabel(el) {
                // 1. Direct label attributes
                if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
                if (el.placeholder) return el.placeholder.trim();
                if (el.getAttribute('title')) return el.getAttribute('title').trim();

                // 2. Explicit <label for="id">
                if (el.id) {
                    const label = document.querySelector(`label[for="${el.id}"]`);
                    if (label && label.innerText) return label.innerText.trim();
                }

                // 3. Closest enclosing form question container
                let parent = el.closest('div[role="listitem"], .freebirdFormviewerViewNumberedItemContainer, .form-group, .field, div');
                if (parent) {
                    const heading = parent.querySelector('div[role="heading"], span.M7eMe, label, strong, h3, h4');
                    if (heading && heading !== el && heading.innerText) {
                        return heading.innerText.trim();
                    }
                }
                return "";
            }

            const targets = document.querySelectorAll('input, textarea, select, div[role="radio"], div[role="checkbox"]');
            targets.forEach(el => {
                // Ignore hidden or zero-size inputs
                if (el.type === 'hidden' || el.style.display === 'none' || el.offsetParent === null) {
                    return;
                }

                index++;
                const marker = `ai-field-${index}`;
                el.setAttribute('data-ai-id', marker);

                const label = getLabel(el);
                const tag = el.tagName.toLowerCase();
                const type = el.type || el.getAttribute('role') || 'text';

                let options = [];
                if (tag === 'select') {
                    options = Array.from(el.options).map(o => o.text.trim());
                }

                elements.push({
                    id: marker,
                    tag: tag,
                    type: type,
                    label: label,
                    name: el.name || '',
                    options: options,
                    current_value: el.value || ''
                });
            });

            return elements;
        }
        """
        return page.evaluate(js_code)

    def generate_form_actions_with_gemini(self, fields: List[Dict[str, Any]], page_title: str, resume_path: Path) -> List[Dict[str, Any]]:
        """
        Calls Gemini 3.8 Flash to map form fields against Yash's master profile,
        using the dynamically selected tailored resume for this job.
        """
        prompt = f"""
You are an expert autonomous browser agent applying for high-priority placement jobs.
Your goal is to accurately match user profile information to form fields on screen.

Page Title: {page_title}

USER PROFILE DATA:
---
{json.dumps(self.profile, indent=2)}
---
SELECTED TAILORED RESUME ABSOLUTE PATH:
{str(resume_path).replace(os.sep, '/')}

FORM FIELDS FOUND ON SCREEN:
---
{json.dumps(fields, indent=2)}
---

RULES & INTELLIGENCE:
1. For Resume/CV uploads, use action "upload" with the exact selected resume path: "{str(resume_path).replace(os.sep, '/')}".

2. RECRUITER "GREEN-FLAG" ANSWERING PROTOCOL (CRITICAL FOR SHORTLISTING):
   Recruiters look for specific signals to shortlist candidates. Whenever answering screening questions, ALWAYS follow these recruiter-approved rules:
   
   A. "Why should we hire you?" / "Why are you fit for this role?":
      Highlight strong DSA fundamentals, clean code architecture, hands-on development experience, and the ability to ramp up fast and ship reliable code from Day 1.
   
   B. Expected Salary / CTC:
      Answer "As per company standards and industry norms for freshers / negotiable" (avoids getting filtered out).
   
   C. Notice Period / Availability:
      Always answer "Immediate joining / 0 days notice period" (major recruiter green flag).
   
   D. Relocation / Work Mode / Shifts:
      Always answer enthusiastically: "100% open and willing to relocate to any company location (Bangalore, Pune, Gurgaon, Hyderabad, etc.) and comfortable with in-office/hybrid/rotational shifts."
   
   E. Service Agreement / Bond:
      Always answer positively: "Yes, comfortable with standard company commitment and service agreements."
   
   F. Work Authorization:
      "Indian citizen, authorized to work in India with zero visa sponsorship required."
   
   G. Technical Strength / Bug Solved:
      Mention algorithmic rigor, structured debugging, clean Git version control, and modular system design.

   Length: 2 to 4 crisp, high-impact professional sentences (40-75 words). Confident, humble, and results-focused.

3. For dropdowns/radios, pick the best matching option from the available options.
4. For phone numbers, match 10 digits or standard format as requested.
5. If a field cannot be matched or is irrelevant/optional, set action to "skip".

Return a strictly valid JSON array of action objects.
Each object must have:
- "id": string (the exact data-ai-id, e.g. "ai-field-1")
- "action": "fill" | "select" | "click" | "upload" | "skip"
- "value": string value to type / option / file path
- "reason": brief explanation (e.g. "Tailored pitch for SDE role")

Only output raw JSON. Do not include markdown codeblocks or explanation.
"""
        try:
            text_res = self.call_gemini_with_retry(prompt)
            cleaned = text_res.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            return json.loads(cleaned)
        except Exception as e:
            print(f"[Error parsing Gemini action plan]: {e}")
            return []

    def human_type(self, locator: Locator, text: str):
        """Types text quickly and reliably, triggering input events."""
        try:
            locator.scroll_into_view_if_needed()
            locator.focus()
            locator.fill(str(text))
        except Exception:
            try:
                locator.type(str(text), delay=20)
            except Exception:
                pass

    def heuristic_match_field(self, field: Dict[str, Any], resume_path: Path) -> Optional[Dict[str, Any]]:
        """Fast instant heuristic matcher for standard placement fields (0ms response)."""
        label = (field.get("label") or "").lower()
        name = (field.get("name") or "").lower()
        tag = field.get("tag", "")
        type_ = field.get("type", "")
        options = field.get("options", [])
        field_id = field.get("id")
        
        combined = f"{label} {name}"
        
        # 1. Resume / CV upload
        if type_ == "file" or "resume" in combined or "cv" in combined:
            return {"id": field_id, "action": "upload", "value": str(resume_path), "reason": "Equipping tailored resume PDF"}
            
        # 2. Full Name / First Name
        if any(k in combined for k in ["full name", "your name", "candidate name", "cand_name", "first name", "name *"]):
            return {"id": field_id, "action": "fill", "value": self.profile.get("personal", {}).get("full_name", "Yash"), "reason": "Filling candidate full name"}
            
        # 3. Email
        if any(k in combined for k in ["email", "e-mail", "mail id"]):
            return {"id": field_id, "action": "fill", "value": self.profile.get("personal", {}).get("email", ""), "reason": "Filling candidate email"}
            
        # 4. Mobile / Phone
        if any(k in combined for k in ["phone", "mobile", "whatsapp", "contact"]):
            return {"id": field_id, "action": "fill", "value": self.profile.get("personal", {}).get("phone", ""), "reason": "Filling 10-digit mobile number"}
            
        # 5. Roll number / Reg No
        if any(k in combined for k in ["roll", "reg", "registration", "student id", "college id"]):
            return {"id": field_id, "action": "fill", "value": self.profile.get("academics", {}).get("roll_number", ""), "reason": "Filling college roll number"}
            
        # 6. CGPA / Percentage
        if any(k in combined for k in ["cgpa", "gpa", "aggregate", "percentage", "marks"]):
            return {"id": field_id, "action": "fill", "value": str(self.profile.get("academics", {}).get("cgpa_or_percentage", "7.82")), "reason": "Filling aggregate CGPA"}
            
        # 7. Graduation year
        if any(k in combined for k in ["graduat", "passing year", "passout", "batch", "year of pass"]):
            target_year = str(self.profile.get("academics", {}).get("graduation_year", "2026"))
            best_opt = target_year
            if options:
                for opt in options:
                    if target_year in opt:
                        best_opt = opt
                        break
                else:
                    valid_opts = [o for o in options if any(char.isdigit() for char in o)]
                    if valid_opts:
                        best_opt = valid_opts[-1]
            if tag == "select":
                return {"id": field_id, "action": "select", "value": best_opt, "reason": "Selecting graduation year"}
            else:
                return {"id": field_id, "action": "fill", "value": best_opt, "reason": "Filling graduation year"}
                
        # 8. Branch / Stream
        if any(k in combined for k in ["branch", "stream", "department", "degree", "specialization"]):
            target_branch = self.profile.get("academics", {}).get("branch", "Computer Science & Engineering")
            best_opt = target_branch
            if options:
                for opt in options:
                    if "computer" in opt.lower() or "cse" in opt.lower():
                        best_opt = opt
                        break
            if tag == "select":
                return {"id": field_id, "action": "select", "value": best_opt, "reason": "Selecting academic branch"}
            else:
                return {"id": field_id, "action": "fill", "value": best_opt, "reason": "Filling academic branch"}
                
        # 9. Cover pitch / why fit
        if any(k in combined for k in ["why", "interest", "pitch", "about yourself", "cover", "describe", "experience"]):
            pitch = "I am a dedicated software engineer with strong fundamentals in data structures, algorithms, and full-stack development. I bring a structured problem-solving mindset and clean code architecture to ramp up fast and ship reliable features from Day 1."
            return {"id": field_id, "action": "fill", "value": pitch, "reason": "Providing recruiter green-flag pitch"}
            
        return None

    def check_for_captcha(self, page: Page) -> bool:
        """Detects if Cloudflare or reCAPTCHA is present."""
        captcha_indicators = [
            'iframe[src*="recaptcha"]',
            'iframe[src*="turnstile"]',
            'iframe[src*="hcaptcha"]',
            '#cf-challenge-running',
            'text="Verify you are human"'
        ]
        for sel in captcha_indicators:
            if page.locator(sel).count() > 0:
                return True
        return False

    def fill_current_page_fields(self, page: Page, resume_path: Path) -> int:
        """Inspects, maps, and populates fields on the active page using fast heuristics and Gemini."""
        fields = self.inspect_form_fields(page)
        if not fields:
            return 0

        # Step 1: Fast Heuristic Match (Instant 0ms response for standard fields)
        actions = []
        unmatched_fields = []
        for f in fields:
            matched = self.heuristic_match_field(f, resume_path)
            if matched:
                actions.append(matched)
            else:
                unmatched_fields.append(f)

        # Step 2: Use Gemini for any remaining non-standard / custom questions
        if unmatched_fields:
            print(f"Found {len(unmatched_fields)} custom questions. Consulting Gemini...")
            ai_actions = self.generate_form_actions_with_gemini(unmatched_fields, page.title(), resume_path=resume_path)
            actions.extend(ai_actions)

        filled_count = 0
        for act in actions:
            field_id = act.get("id")
            action_type = act.get("action")
            val = act.get("value")
            reason = act.get("reason", "")

            if action_type == "skip" or not field_id or val is None:
                continue

            locator = page.locator(f'[data-ai-id="{field_id}"]')
            if not locator.count():
                continue

            print(f"  -> [{action_type.upper()}] {reason}: '{str(val)[:45]}'")
            try:
                locator.scroll_into_view_if_needed()
                if action_type == "fill":
                    self.human_type(locator, val)
                elif action_type == "select":
                    try:
                        locator.select_option(label=str(val), timeout=1500)
                    except Exception:
                        try:
                            locator.select_option(value=str(val), timeout=1000)
                        except Exception:
                            try:
                                locator.select_option(index=1, timeout=1000)
                            except Exception:
                                pass
                elif action_type == "click":
                    locator.click()
                elif action_type == "upload":
                    upload_file = resume_path if resume_path.exists() else self.default_resume
                    if upload_file.exists():
                        locator.set_input_files(str(upload_file))
                        print(f"     [Uploaded Tailored Resume]: {upload_file.name}")
                    else:
                        print(f"     [Warning]: Resume file not found at {upload_file}")
                filled_count += 1
            except Exception as field_err:
                print(f"     [Field Warning] on {field_id}: {field_err}")

        return filled_count

    def consult_gemini_to_navigate_or_unblock(self, page: Page, company: str, role: str, issue_description: str) -> bool:
        """
        Self-Healing AI Agent: If the bot gets stuck, detects no fields, or encounters an unusual portal layout,
        it consults Gemini 3.8 Flash with the full interactive page state to deduce what to click or do next.
        """
        print(f"\n🧠 [AI AGENT CONSULTATION]: Consulting Gemini to analyze page & navigate...")
        print(f"   Context: {issue_description}")

        # Extract all visible interactive elements (buttons, links, tabs)
        js_get_interactives = """
        () => {
            const items = [];
            const clickable = document.querySelectorAll('button, a, div[role="button"], span[role="button"], input[type="submit"], input[type="button"]');
            clickable.forEach((el, i) => {
                if (el.offsetParent !== null && el.innerText.trim().length > 0 && el.innerText.trim().length < 60) {
                    items.push({
                        text: el.innerText.trim(),
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || '',
                        aria: el.getAttribute('aria-label') || ''
                    });
                }
            });
            return items.slice(0, 35); // top 35 visible interactive options
        }
        """
        try:
            interactives = page.evaluate(js_get_interactives)
            page_snippet = page.inner_text("body")[:3000]
        except Exception:
            interactives = []
            page_snippet = ""

        prompt = f"""
You are an Autonomous AI Browser Navigation Agent.
The automated job application bot is currently stuck or encountering an unusual portal layout.

Target Job: {company} - {role}
Current URL: {page.url}
Page Title: {page.title()}
Detected Issue: {issue_description}

VISIBLE CLICKABLE ELEMENTS ON SCREEN:
---
{json.dumps(interactives, indent=2)}
---

PAGE TEXT SNIPPET:
---
{page_snippet}
---

TASK:
Examine the page. What is the single best button, link, or tab to click to proceed with applying for this job (e.g. "Apply Now", "Register for Drive", "Student Sign In", "Proceed to Form", "Next", "Continue")?
If scrolling down is needed to reveal the form, tell the browser to scroll.

Return a strictly valid JSON object:
{{
  "diagnosis": "1-sentence explanation of what is needed on this page",
  "action": "click" | "scroll_down" | "none",
  "click_text": "Exact text of the button/link to click (or null if none)",
  "selector": "CSS or text selector like 'button:has-text(\"Apply\")'"
}}

Only output raw JSON. Do not include markdown codeblocks or explanation.
"""
        try:
            text_res = self.call_gemini_with_retry(prompt)
            cleaned = text_res.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)

            print(f"   💡 Gemini Diagnosis: {data.get('diagnosis')}")
            action = data.get("action")
            click_text = data.get("click_text")

            if action == "scroll_down":
                page.evaluate("window.scrollBy(0, 500)")
                time.sleep(2)
                return True
            elif action == "click" and click_text:
                target = page.locator(f'text="{click_text}"').first
                if target.count() > 0:
                    print(f"   👉 Gemini commanded click on: '{click_text}'")
                    target.scroll_into_view_if_needed()
                    target.click()
                    time.sleep(3)
                    return True
                else:
                    # Fallback to selector
                    sel = data.get("selector")
                    if sel and page.locator(sel).count() > 0:
                        print(f"   👉 Gemini commanded click on selector: {sel}")
                        page.locator(sel).first.click()
                        time.sleep(3)
                        return True
        except Exception as e:
            print(f"[Error in Gemini consultation]: {e}")

        return False

    def bring_window_to_foreground(self):
        """Forces the Chrome window to the absolute front of the Windows desktop."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            def enum_handler(hwnd, extra):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    if title and any(k.lower() in title.lower() for k in ["chrome", "google", "form", "apply", "mock", "placement", "jobbot"]):
                        user32.ShowWindow(hwnd, 3) # SW_MAXIMIZE
                        user32.SetForegroundWindow(hwnd)
                        try:
                            user32.SwitchToThisWindow(hwnd, True)
                        except Exception:
                            pass
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(WNDENUMPROC(enum_handler), 0)
        except Exception:
            pass

    def inject_banner(self, page: Page, text: str, bg_color: str = "#107c10", border_color: str = "#2ecc71"):
        """Injects or updates a high-visibility Xbox neon banner inside the browser page reflecting live status."""
        try:
            banner_js = f"""
            let b = document.getElementById('jobbot-debug-banner');
            if (!b) {{
                b = document.createElement('div');
                b.id = 'jobbot-debug-banner';
                b.style.position = 'fixed';
                b.style.top = '0';
                b.style.left = '0';
                b.style.right = '0';
                b.style.fontFamily = 'Segoe UI, sans-serif';
                b.style.fontWeight = 'bold';
                b.style.fontSize = '14px';
                b.style.textAlign = 'center';
                b.style.padding = '12px 20px';
                b.style.zIndex = '99999999';
                b.style.boxShadow = '0 4px 25px rgba(0, 0, 0, 0.6)';
                document.body.prepend(b);
            }}
            b.style.backgroundColor = '{bg_color}';
            b.style.color = '#ffffff';
            b.style.borderBottom = '2px solid {border_color}';
            b.innerHTML = '{text}';
            """
            page.evaluate(banner_js)
        except Exception:
            pass

    def apply_to_job(self, apply_url: str, company: str = "Unknown", role: str = "Unknown", auto_submit: bool = False, headless: bool = False):
        """
        End-to-End Multi-Page Job Application Engine.
        Opens a REAL VISIBLE Google Chrome window so you can watch Gemini work live!
        """
        print(f"\n=======================================================")
        print(f"⚡ PROCESSING APPLICATION: {company} - {role}")
        print(f"🌐 URL: {apply_url}")
        print(f"⚙️ Auto-Submit: {'ON (Instant Apply)' if auto_submit else 'OFF (Review Mode - Will NOT Submit)'}")
        print(f"🖥️ Mode: {'Visible Real Chrome Window' if not headless else 'Silent Background'}")
        print(f"=======================================================")

        with sync_playwright() as p:
            # Opens real visible Google Chrome with clean independent context (zero lock conflicts)
            try:
                browser = p.chromium.launch(
                    channel="chrome",
                    headless=headless,
                    slow_mo=90,
                    args=[
                        "--start-maximized",
                        "--new-window",
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check"
                    ]
                )
            except Exception as e:
                print(f"[Notice falling back to bundled Chromium]: {e}")
                browser = p.chromium.launch(
                    headless=headless,
                    slow_mo=90,
                    args=["--start-maximized", "--new-window"]
                )

            context = browser.new_context(viewport=None, no_viewport=True)
            page = context.new_page()
            page.bring_to_front()
            self.bring_window_to_foreground()

            try:
                page.goto(apply_url, timeout=60000, wait_until="domcontentloaded")
                time.sleep(2)
                self.bring_window_to_foreground()
                self.inject_banner(page, "⚡ <strong>JOBBOT ACTIVE:</strong> Inspecting form & preparing candidate profile...", "#b45309", "#f59e0b")

                # Check if this form requires Google Account Sign-In (e.g. VIT Bhopal forms)
                if "accounts.google.com" in page.url:
                    print("\n🔑 [GOOGLE ACCOUNT SIGN-IN REQUIRED FOR THIS FORM]")
                    print("👉 Please sign into your VIT Bhopal Google Account in the open Chrome window.")
                    print("🔔 Chime sounded! Waiting up to 60 seconds for you to sign in...")
                    self.notify_user_alert()
                    for _ in range(60):
                        if "accounts.google.com" not in page.url:
                            print("✅ Google Sign-in detected! Resuming form filler...")
                            time.sleep(3)
                            break
                        time.sleep(1)

                # Check for initial "Apply", "Upload resume", or "Register" buttons on career portals
                apply_trigger = page.query_selector(
                    'a:has-text("Upload resume"), button:has-text("Upload resume"), [role="button"]:has-text("Upload resume"), '
                    'a:has-text("Without Resume"), button:has-text("Without Resume"), [role="button"]:has-text("Without Resume"), '
                    'a:has-text("Apply Now"), button:has-text("Apply Now"), a:has-text("Apply"), button:has-text("Apply"), '
                    'a:has-text("Autofill with Resume"), button:has-text("Autofill with Resume")'
                )
                if apply_trigger:
                    try:
                        print("Clicking initial portal action button...")
                        apply_trigger.click()
                        time.sleep(3)
                    except Exception:
                        pass

                # Extract page content for resume matching and JD evaluation
                try:
                    page_text = page.inner_text("body")
                except Exception:
                    page_text = ""

                selected_resume = self.select_best_resume(company=company, role=role, page_content=page_text)

                # Multi-Page Wizard Loop (Handles Page 1 -> Page 2 -> Submit)
                max_pages = 6
                for page_num in range(1, max_pages + 1):
                    # Check for CAPTCHA
                    if self.check_for_captcha(page):
                        print("\n⚠️ [CAPTCHA / HUMAN VERIFICATION DETECTED]!")
                        print("🔔 Chime sounded! Please solve the puzzle on screen (waiting up to 45 seconds)...")
                        self.notify_user_alert()
                        # Wait for user to solve
                        for _ in range(45):
                            if not self.check_for_captcha(page):
                                print("✅ CAPTCHA solved! Resuming automation...")
                                break
                            time.sleep(1)

                    print(f"\n--- [Page {page_num}] Scanning Form Elements ---")
                    self.inject_banner(page, f"✍️ <strong>JOBBOT ACTIVE:</strong> Populating candidate details & equipping resume (Page {page_num})...", "#1d4ed8", "#3b82f6")
                    filled = self.fill_current_page_fields(page, resume_path=selected_resume)

                    # Self-Healing: If 0 fields detected on initial page, consult Gemini!
                    if filled == 0 and page_num == 1:
                        print("⚠️ No fields detected automatically. Consulting Gemini to analyze page & find application form...")
                        unblocked = self.consult_gemini_to_navigate_or_unblock(
                            page, company=company, role=role,
                            issue_description="No interactive form fields found on page. Identify button, link, or tab to open the application."
                        )
                        if unblocked:
                            time.sleep(3)
                            filled = self.fill_current_page_fields(page, resume_path=selected_resume)

                    time.sleep(1)

                    # Look for Next or Submit buttons
                    next_btn = page.query_selector('span:has-text("Next"), button:has-text("Next"), div[role="button"]:has-text("Next")')
                    submit_btn = page.query_selector('button[type="submit"], input[type="submit"], span:has-text("Submit"), div[role="button"]:has-text("Submit")')

                    # If this is the final submit step
                    if submit_btn and (not next_btn or page_num > 1):
                        print("\n🎯 Reached the Final Submit Step!")
                        self.inject_banner(page, "🛡️ <strong>JOBBOT DEBUG MODE:</strong> All fields filled by AI! (Final Submit is paused for inspection — will NOT click Apply)", "#107c10", "#2ecc71")
                        if auto_submit:
                            print("Auto-Submit is ON. Submitting Application...")
                            submit_btn.scroll_into_view_if_needed()
                            submit_btn.click()
                            time.sleep(4)
                            self.log_application(company, role, apply_url, "SUBMITTED", f"Auto-submitted with {selected_resume.name}")
                            print("✅ APPLICATION SUBMITTED SUCCESSFULLY!")
                        else:
                            print("\n>>> 🛡️ DEBUG REVIEW MODE ACTIVE: Everything has been filled.")
                            print(f">>> Attached Tailored Resume: {selected_resume.name}")
                            print(">>> 🛑 Submit button was deliberately NOT clicked as instructed (debugging phase).")
                            print(">>> The real Chrome window will stay open for 60 seconds so you can review!")
                            self.bring_window_to_foreground()
                            self.notify_user_alert()
                            time.sleep(60)
                            self.log_application(company, role, apply_url, "REVIEWED_AND_FILLED", f"Filled with {selected_resume.name} (Submit paused)")
                        break
                    elif next_btn:
                        print(f"Advancing from Page {page_num} to Page {page_num + 1}...")
                        next_btn.scroll_into_view_if_needed()
                        next_btn.click()
                        time.sleep(3)
                    else:
                        print("No further 'Next' or 'Submit' buttons detected. Application completed.")
                        self.inject_banner(page, "🛡️ <strong>JOBBOT DEBUG MODE:</strong> Form complete! Review your details.", "#107c10", "#2ecc71")
                        time.sleep(30)
                        self.log_application(company, role, apply_url, "COMPLETED", f"No next button, used {selected_resume.name}")
                        break

            except Exception as e:
                print(f"[Error during application]: {e}")
                self.log_application(company, role, apply_url, "FAILED", str(e))
            finally:
                browser.close()


if __name__ == "__main__":
    test_url = sys.argv[1] if len(sys.argv) > 1 else None
    company_arg = sys.argv[2] if len(sys.argv) > 2 else "DHL Group"
    role_arg = sys.argv[3] if len(sys.argv) > 3 else "AI / Software Engineer"
    if test_url:
        filler = AIFormFiller()
        filler.apply_to_job(test_url, company=company_arg, role=role_arg, auto_submit=False)
    else:
        print("Usage: python src/form_filler.py <job_application_url> [company] [role]")
