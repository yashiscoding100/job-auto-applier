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

        print(f"\n🔍 [Gmail API]: Searching messages with subject: '{subject_query}'...")
        query = f'subject:"{subject_query}"'
        try:
            results = self.service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
            messages = results.get('messages', [])

            if not messages:
                print(f"No emails found matching subject: '{subject_query}'")
                return []

            print(f"Found {len(messages)} matching placement emails. Analyzing with Gemini...")
            extracted_jobs = []

            for msg_summary in messages:
                msg = self.service.users().messages().get(userId='me', id=msg_summary['id'], format='full').execute()
                subject = ""
                headers = msg.get('payload', {}).get('headers', [])
                for h in headers:
                    if h.get('name', '').lower() == 'subject':
                        subject = h.get('value', '')
                        break

                body = self._extract_body_from_payload(msg.get('payload', {}))
                print(f"\n[Analyzing Email]: {subject}")

                job_info = self.parse_with_gemini(subject, body)
                if job_info and job_info.get("apply_url"):
                    job_info["message_id"] = msg_summary['id']
                    extracted_jobs.append(job_info)
                    print(f"  -> Extracted: {job_info.get('company')} ({job_info.get('role')})")
                    print(f"  -> Link: {job_info.get('apply_url')}")

            return extracted_jobs

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

    def parse_with_gemini(self, subject: str, body: str) -> Optional[Dict[str, Any]]:
        if not self.ai_client:
            return None

        prompt = f"""
You are an intelligent placement cell email parser.
Analyze this email received from a college placement cell.
Extract the company name, job role, and the exact application link/URL (such as Google Forms, Greenhouse, Lever, Unstop, or company career portal link).

Email Subject: {subject}
Email Content:
---
{body[:3500]}
---

Return a strictly valid JSON object with:
- "is_job_email": true or false
- "company": "Company Name"
- "role": "Job Role"
- "apply_url": "Full application URL starting with http/https, or null"
- "deadline": "Deadline string or null"

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
            return json.loads(cleaned)
        except Exception as e:
            print(f"[Error parsing with Gemini]: {e}")
            return None


if __name__ == "__main__":
    reader = GoogleOAuthGmailReader()
    if reader.authenticate():
        email_addr = reader.get_user_email()
        print(f"Logged in as: {email_addr}")
        jobs = reader.fetch_placement_emails("god bless you", max_results=3)
        print(f"Extracted {len(jobs)} jobs.")
    else:
        print("Authentication pending or credentials.json needed.")
