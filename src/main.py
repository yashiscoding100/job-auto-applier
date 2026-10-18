import os
import argparse
from pathlib import Path
from email_reader import PlacementEmailReader
from form_filler import AIFormFiller

def run_email_pipeline(max_emails: int = 5, auto_submit: bool = False):
    print("\n=======================================================")
    print("🚀 STEP 1: Scanning Gmail for 'God Bless You' Emails...")
    print("=======================================================")
    reader = PlacementEmailReader()
    jobs = reader.fetch_placement_emails(max_emails=max_emails)

    if not jobs:
        print("No new actionable job links found in recent emails.")
        return

    print(f"\nFound {len(jobs)} jobs ready to apply:")
    for i, job in enumerate(jobs, 1):
        print(f" {i}. [{job.get('company')}] {job.get('role')} -> {job.get('apply_url')}")

    print("\n=======================================================")
    print("🤖 STEP 2: Launching AI Form Filler...")
    print("=======================================================")
    filler = AIFormFiller()

    for i, job in enumerate(jobs, 1):
        company = job.get("company", "Placement Company")
        role = job.get("role", "Candidate")
        url = job.get("apply_url")

        print(f"\nProcessing Job {i} of {len(jobs)}: {company} ({role})")
        filler.apply_to_job(apply_url=url, company=company, role=role, auto_submit=auto_submit)

    print("\n All jobs processed! Check logs/applications_log.csv for summary.")

def main():
    parser = argparse.ArgumentParser(description="Auto Job Applier & Email Reader")
    parser.add_argument("--mode", choices=["email", "url"], default="email", help="Mode: 'email' (scan inbox) or 'url' (single link)")
    parser.add_argument("--url", type=str, help="Direct job/form URL to test (when mode=url)")
    parser.add_argument("--auto-submit", action="store_true", help="Automatically submit without 30s review pause")
    parser.add_argument("--max-emails", type=int, default=5, help="Max emails to process")

    args = parser.parse_args()

    if args.mode == "url":
        if not args.url:
            print("Please provide a URL with --url <link>")
            return
        filler = AIFormFiller()
        filler.apply_to_job(apply_url=args.url, company="Manual Test", role="Software Engineer", auto_submit=args.auto_submit)
    else:
        run_email_pipeline(max_emails=args.max_emails, auto_submit=args.auto_submit)

if __name__ == "__main__":
    main()
