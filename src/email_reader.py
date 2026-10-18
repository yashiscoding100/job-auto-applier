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

    def parse_job_info_with_gemini(self, email_subject: str, email_body: str) -> Optional[Dict[str, Any]]:
        """Uses Gemini API to extract structured job details and application link from messy email text."""
        if not self.ai_client:
            raise ValueError("GEMINI_API_KEY is not set in .env file.")

        prompt = f"""
You are an intelligent placement cell email parser.
Analyze this email received from a college placement cell.
Extract the company name, job role, and the exact application link/URL (such as Google Forms, Greenhouse, Lever, Unstop, or company career portal link).

Email Subject: {email_subject}
Email Content:
---
{email_body[:4000]}
---

Return a strictly valid JSON object with the following keys:
- "is_job_email": true or false
- "company": "Company Name (or Unknown)"
- "role": "Job Title/Role (or Unknown)"
- "apply_url": "Full application URL starting with http/https, or null if none found"
- "deadline": "Application deadline if mentioned, or null"
- "eligibility": "Brief eligibility (e.g., B.Tech CSE, 60%+, etc.) or null"

Only output raw JSON. Do not include markdown codeblocks or explanation.
"""
        response = self.ai_client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt,
        )

        try:
            cleaned_text = response.text.strip()
            # Remove ```json or ``` wrapper if present
            if cleaned_text.startswith("```"):
                cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text)
                cleaned_text = re.sub(r"\s*```$", "", cleaned_text)
            parsed = json.loads(cleaned_text)
            if parsed.get("is_job_email") and parsed.get("apply_url"):
                return parsed
            return None
        except Exception as e:
            print(f"[Error parsing Gemini response]: {e}")
            return None

    def fetch_placement_emails(self, max_emails: int = 10, mark_as_read: bool = False) -> List[Dict[str, Any]]:
        """Connects to Gmail and fetches emails matching the placement subject filter."""
        if not self.gmail_user or not self.gmail_password:
            raise ValueError("Please provide GMAIL_USER and GMAIL_APP_PASSWORD in .env")

        print(f"Connecting to {self.imap_server} for {self.gmail_user}...")
        mail = imaplib.IMAP4_SSL(self.imap_server)
        mail.login(self.gmail_user, self.gmail_password)
        mail.select("inbox")

        # Search for unread or recent messages
        search_criterion = f'(SUBJECT "{self.subject_filter}")'
        status, response = mail.search(None, search_criterion)

        if status != "OK" or not response[0]:
            print(f"No emails found with subject containing: '{self.subject_filter}'")
            mail.logout()
            return []

        email_ids = response[0].split()
        print(f"Found {len(email_ids)} emails matching '{self.subject_filter}'. Processing latest {min(len(email_ids), max_emails)}...")

        extracted_jobs = []
        # Process the most recent emails first
        for eid in reversed(email_ids[-max_emails:]):
            res, data = mail.fetch(eid, "(RFC822)")
            if res != "OK":
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = self._decode_str(msg.get("Subject", ""))
            body = self._extract_body(msg)

            print(f"\n[Analyzing Email]: {subject}")
            job_data = self.parse_job_info_with_gemini(subject, body)
            if job_data:
                job_data["email_id"] = eid.decode("utf-8")
                extracted_jobs.append(job_data)
                print(f"  --> Found Job: {job_data.get('company')} | Role: {job_data.get('role')}")
                print(f"  --> Apply Link: {job_data.get('apply_url')}")
            else:
                print("  --> No valid job link extracted.")

        mail.logout()
        return extracted_jobs


if __name__ == "__main__":
    reader = PlacementEmailReader()
    try:
        jobs = reader.fetch_placement_emails(max_emails=5)
        print(f"\nTotal extracted actionable jobs: {len(jobs)}")
    except Exception as err:
        print(f"[Error]: {err}")
