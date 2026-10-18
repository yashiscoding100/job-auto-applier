import os
import email
from email.header import decode_header
import imaplib
import json
import re
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

class PlacementEmailReader:
    def __init__(self):
        self.gmail_user = os.getenv("GMAIL_USER")
        self.gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        self.imap_server = os.getenv("IMAP_SERVER", "imap.gmail.com")
        self.subject_filter = os.getenv("PLACEMENT_EMAIL_SUBJECT", "god bless you")
        self.gemini_key = os.getenv("GEMINI_API_KEY")

        if self.gemini_key:
            self.ai_client = genai.Client(api_key=self.gemini_key)
        else:
            self.ai_client = None

    def _decode_str(self, header_val: str) -> str:
        """Decode email header string."""
        if not header_val:
            return ""
        decoded_bytes, charset = decode_header(header_val)[0]
        if isinstance(decoded_bytes, bytes):
            return decoded_bytes.decode(charset or "utf-8", errors="ignore")
        return str(decoded_bytes)

    def _extract_body(self, msg: email.message.Message) -> str:
        """Extract text content from an email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" not in content_disposition:
                    if content_type == "text/plain":
                        charset = part.get_content_charset() or "utf-8"
                        body += part.get_payload(decode=True).decode(charset, errors="ignore") + "\n"
                    elif content_type == "text/html" and not body:
                        # Fallback to HTML converted to text
                        charset = part.get_content_charset() or "utf-8"
                        html = part.get_payload(decode=True).decode(charset, errors="ignore")
                        soup = BeautifulSoup(html, "html.parser")
                        body += soup.get_text(separator=" ", strip=True) + "\n"
        else:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode(charset, errors="ignore")
                if msg.get_content_type() == "text/html":
                    soup = BeautifulSoup(text, "html.parser")
                    body = soup.get_text(separator=" ", strip=True)
                else:
                    body = text
        return body.strip()

    def parse_all_jobs_with_gemini(self, email_subject: str, email_body: str) -> List[Dict[str, Any]]:
        """Uses Gemini 3.8 Flash to detect and extract ALL job openings present in a single email."""
        if not self.ai_client:
            raise ValueError("GEMINI_API_KEY is not set in .env file.")

        prompt = f"""
You are an expert college placement email parser.
A single email from a college placement cell can contain ONE or MULTIPLE separate job openings or placement drives.
Your job is to find and extract EVERY single job opening that has an application link (Google Forms, Greenhouse, Lever, Unstop, company portals, etc.).

Email Subject: {email_subject}
Email Content:
---
{email_body[:5000]}
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
        models_to_try = ['gemini-3.5-flash-lite', 'gemini-flash-lite-latest', 'gemini-3.1-flash-lite', 'gemini-3.8-flash']
        response = None
        for m in models_to_try:
            try:
                response = self.ai_client.models.generate_content(
                    model=m,
                    contents=prompt,
                )
                if response and response.text:
                    break
            except Exception as e:
                if '429' in str(e) or 'NOT_FOUND' in str(e) or 'quota' in str(e).lower():
                    continue
                print(f"[Model {m} failed]: {e}")

        if not response or not response.text:
            return []

        try:
            cleaned_text = response.text.strip()
            if cleaned_text.startswith("```"):
                cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text)
                cleaned_text = re.sub(r"\s*```$", "", cleaned_text)
            parsed = json.loads(cleaned_text)
            if parsed.get("is_job_email") and parsed.get("jobs"):
                return [j for j in parsed["jobs"] if j.get("apply_url")]
            return []
        except Exception as e:
            print(f"[Error parsing Gemini response]: {e}")
            return []

    def fetch_placement_emails(self, max_emails: int = 10, mark_as_read: bool = False, subject_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Connects to Gmail and fetches emails matching the placement subject filter keywords."""
        if not self.gmail_user or not self.gmail_password:
            raise ValueError("Please provide GMAIL_USER and GMAIL_APP_PASSWORD in .env")

        query_str = subject_query or self.subject_filter
        terms = [t.strip() for t in query_str.split(",") if t.strip()]
        if not terms:
            terms = ["god bless you"]

        print(f"Connecting to {self.imap_server} for {self.gmail_user}...")
        mail = imaplib.IMAP4_SSL(self.imap_server)
        mail.login(self.gmail_user, self.gmail_password)
        mail.select("inbox")

        all_eids = set()
        for term in terms:
            status, response = mail.search(None, f'(SUBJECT "{term}")')
            if status == "OK" and response[0]:
                for eid in response[0].split():
                    all_eids.add(eid)

        if not all_eids:
            print(f"No emails found with subject containing: '{query_str}'")
            mail.logout()
            return []

        sorted_eids = sorted(list(all_eids), key=lambda x: int(x))
        print(f"Found {len(sorted_eids)} emails matching '{query_str}'. Processing latest {min(len(sorted_eids), max_emails)}...")

        extracted_jobs = []
        for eid in reversed(sorted_eids[-max_emails:]):
            res, data = mail.fetch(eid, "(RFC822)")
            if res != "OK":
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = self._decode_str(msg.get("Subject", ""))
            body = self._extract_body(msg)

            print(f"\n[Analyzing Email]: {subject}")
            jobs_in_mail = self.parse_all_jobs_with_gemini(subject, body)
            print(f"  -> Detected {len(jobs_in_mail)} actionable job(s) in this email.")
            for j in jobs_in_mail:
                j["email_id"] = eid.decode("utf-8")
                j["email_subject"] = subject
                extracted_jobs.append(j)
                print(f"     • [{j.get('company')}] {j.get('role')} -> {j.get('apply_url')}")

        mail.logout()
        return extracted_jobs


if __name__ == "__main__":
    reader = PlacementEmailReader()
    try:
        jobs = reader.fetch_placement_emails(max_emails=5)
        print(f"\nTotal extracted actionable jobs: {len(jobs)}")
    except Exception as err:
        print(f"[Error]: {err}")

