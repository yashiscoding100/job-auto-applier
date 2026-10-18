import os
import sys
import json
import base64
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google import genai

load_dotenv()

# We only ask for READ-ONLY access to Gmail messages
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

class GoogleOAuthGmailReader:
    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if self.gemini_key:
            self.ai_client = genai.Client(api_key=self.gemini_key)
        else:
            self.ai_client = None
        self.service = None

    def is_authenticated(self) -> bool:
        """Checks if a valid token.json exists."""
        if not TOKEN_FILE.exists():
            return False
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            return creds and creds.valid
        except Exception:
            return False

    def authenticate(self) -> bool:
        """
        Launches the official Google Sign-In popup in Chrome.
        You click 'Continue with Google' and 'Allow'.
        Saves encrypted token.json locally on your machine with ZERO passwords stored.
        """
        creds = None
        if TOKEN_FILE.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            except Exception:
                creds = None

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None

            if not creds:
                if not CREDENTIALS_FILE.exists():
                    print("\n[OAuth Notice]: credentials.json not found!")
                    print(f"Please place your OAuth client file at: {CREDENTIALS_FILE}")
                    return False

                print("\n🌐 Opening official Google Sign-In in your browser...")
                print("Click 'Continue with Google' and 'Allow' to grant read-only placement email access.")
                flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
                creds = flow.run_local_server(port=0)

            # Save the credentials for future runs
            with open(TOKEN_FILE, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
            print("✅ Official Google OAuth token saved securely to token.json!")

        self.service = build('gmail', 'v1', credentials=creds)
        return True

    def get_user_email(self) -> Optional[str]:
        """Gets authenticated user's email address without asking for passwords."""
        if not self.service:
            if not self.authenticate():
                return None
        try:
            profile = self.service.users().getProfile(userId='me').execute()
            return profile.get('emailAddress')
        except Exception as e:
            print(f"[Error getting profile]: {e}")
            return None

    def fetch_placement_emails(self, subject_query: str = "god bless you", max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Searches Gmail inbox using official Gmail API for messages matching subject.
        """
        if not self.service:
            if not self.authenticate():
                return []

    def _build_search_query(self, subject_query: str) -> str:
        """Constructs an OR search query for multiple subjects/keywords (e.g. 'god bless you, Placement, Drive')."""
        terms = [s.strip() for s in subject_query.split(",") if s.strip()]
        if not terms:
            return 'subject:"god bless you"'
        if len(terms) == 1:
            return f'subject:"{terms[0]}"'
        # e.g. (subject:"god bless you" OR subject:"Placement")
        or_clause = " OR ".join([f'subject:"{t}"' for t in terms])
        return f"({or_clause})"

    def fetch_placement_emails(self, subject_query: str = "god bless you", max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Searches Gmail inbox using official Gmail API for messages matching any of the subject keywords.
        Finds and extracts ALL job openings inside each email!
        """
        if not self.service:
            if not self.authenticate():
                return []

        query = self._build_search_query(subject_query)
        print(f"\n🔍 [Gmail API Query]: {query}")
        try:
            results = self.service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
            messages = results.get('messages', [])

            if not messages:
                print(f"No emails found matching query: {query}")
                return []

            print(f"Found {len(messages)} matching placement emails. Analyzing all job openings with Gemini...")
            all_extracted_jobs = []

            for msg_summary in messages:
                msg = self.service.users().messages().get(userId='me', id=msg_summary['id'], format='full').execute()
                subject = ""
                headers = msg.get('payload', {}).get('headers', [])
                for h in headers:
                    if h.get('name', '').lower() == 'subject':
                        subject = h.get('value', '')
                        break

                body = self._extract_body_from_payload(msg.get('payload', {}))
                print(f"\n[Analyzing Email Subject]: {subject}")

                jobs_in_email = self.parse_all_jobs_with_gemini(subject, body)
                print(f"  -> Gemini detected {len(jobs_in_email)} job opening(s) in this single email!")
                for j in jobs_in_email:
                    j["email_subject"] = subject
                    j["message_id"] = msg_summary['id']
                    all_extracted_jobs.append(j)
                    print(f"     • [{j.get('company')}] {j.get('role')} -> {j.get('apply_url')}")

            return all_extracted_jobs

        except Exception as err:
            print(f"[Gmail API Error]: {err}")
            return []

    def _extract_body_from_payload(self, payload: dict) -> str:
        """Extracts text from Gmail API message payload."""
        text = ""
        if 'parts' in payload:
            for part in payload['parts']:
                mime = part.get('mimeType', '')
                data = part.get('body', {}).get('data', '')
                if data:
                    decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    if mime == 'text/plain':
                        text += decoded + "\n"
                    elif mime == 'text/html' and not text:
                        soup = BeautifulSoup(decoded, 'html.parser')
                        text += soup.get_text(separator=' ', strip=True) + "\n"
                if 'parts' in part:
                    text += self._extract_body_from_payload(part)
        else:
            data = payload.get('body', {}).get('data', '')
            if data:
                decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                if payload.get('mimeType') == 'text/html':
                    soup = BeautifulSoup(decoded, 'html.parser')
                    text = soup.get_text(separator=' ', strip=True)
                else:
                    text = decoded
        return text.strip()

    def parse_all_jobs_with_gemini(self, subject: str, body: str) -> List[Dict[str, Any]]:
        """
        Uses Gemini 3.8 Flash to detect and extract ALL job openings present in a single email.
        College placement cells often bundle 2 to 5 different companies/links in one mail.
        """
        if not self.ai_client:
            return []

        prompt = f"""
You are an expert college placement email parser.
A single email from a college placement cell can contain ONE or MULTIPLE separate job openings or offcampus drives.
Your job is to find and extract EVERY single job opening that has an application link (Google Forms, Greenhouse, Lever, Unstop, company portals, etc.).

Email Subject: {subject}
Email Content:
---
{body[:5000]}
---

TASK:
1. Identify all distinct job openings mentioned.
2. For each job opening, extract:
   - "company": Company Name
   - "role": Job Title / Role
   - "apply_url": Direct application URL (must start with http/https)
   - "requirements": Key skills or branch eligibility

Return a strictly valid JSON object:
{{
  "is_job_email": true or false,
  "jobs": [
    {{
      "company": "Company A",
      "role": "Role A",
      "apply_url": "https://...",
      "requirements": "..."
    }}
  ]
}}

If no actionable job application links are found, return {{"is_job_email": false, "jobs": []}}.
Only output raw JSON. Do not include markdown codeblocks or explanation.
"""
        try:
            res = self.ai_client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt
            )
            cleaned = res.text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)
            return [j for j in data.get("jobs", []) if j.get("apply_url")]
        except Exception as e:
            print(f"[Error parsing jobs with Gemini]: {e}")
            return []


if __name__ == "__main__":
    reader = GoogleOAuthGmailReader()
    if reader.authenticate():
        email_addr = reader.get_user_email()
        print(f"Logged in as: {email_addr}")
        jobs = reader.fetch_placement_emails("god bless you", max_results=3)
        print(f"Extracted {len(jobs)} jobs.")
    else:
        print("Authentication pending or credentials.json needed.")
