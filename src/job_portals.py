import os
import sys
import time
import json
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from google import genai

load_dotenv()

class JobPortalsApplier:
    def __init__(self, profile_path: str = "config/profile.json"):
        self.profile_path = Path(profile_path)
        with open(self.profile_path, "r", encoding="utf-8") as f:
            self.profile = json.load(f)

        self.resume_path = Path(self.profile.get("resume_path", "resumes/resume.pdf")).resolve()
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        if self.gemini_key:
            self.ai_client = genai.Client(api_key=self.gemini_key)
        else:
            self.ai_client = None

        # Persistent user data directory so you remain logged in
        self.user_data_dir = Path("chrome_session").resolve()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    def launch_logged_in_browser(self):
        """
        Launches a persistent browser session.
        The first time you run this, you can log in to LinkedIn & Naukri manually.
        Your session will be saved for all future runs!
        """
        print(f"Opening browser with saved session at: {self.user_data_dir}")
        print("Please log into LinkedIn and Naukri once. Your login will be remembered!")
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=False,
                args=["--start-maximized"]
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.linkedin.com/login")
            print("Keep browser open as long as you need to log in. Press Ctrl+C in terminal when done.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("Session saved successfully.")
            finally:
                context.close()

    def run_linkedin_easy_apply(self, query: str = "Software Engineer", location: str = "India", max_jobs: int = 5):
        """
        Searches LinkedIn for Easy Apply jobs and applies automatically.
        """
        print(f"\n[LinkedIn] Searching for '{query}' jobs in '{location}' with Easy Apply...")
        # f_AL=true filters strictly for Easy Apply jobs
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={query.replace(' ', '%20')}&location={location.replace(' ', '%20')}&f_AL=true"

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=False,
                viewport={"width": 1280, "height": 800}
            )
            page = context.pages[0] if context.pages else context.new_page()

            try:
                page.goto(search_url, wait_until="domcontentloaded")
                time.sleep(4)

                # Locate job cards
                job_cards = page.locator(".job-card-container, .jobs-search-results__list-item").all()
                print(f"Found {len(job_cards)} job cards on current search page.")

                count = 0
                for card in job_cards[:max_jobs]:
                    try:
                        card.scroll_into_view_if_needed()
                        card.click()
                        time.sleep(2)

                        # Look for Easy Apply button
                        easy_apply_btn = page.locator("button.jobs-apply-button")
                        if easy_apply_btn.count() > 0:
                            print(f"\n[Applying] Clicking Easy Apply...")
                            easy_apply_btn.first.click()
                            time.sleep(2)

                            # Modal loop (handle Next, Review, Submit)
                            for step in range(5):
                                # Check if resume upload is needed
                                upload_input = page.locator('input[type="file"]')
                                if upload_input.count() > 0 and self.resume_path.exists():
                                    upload_input.first.set_input_files(str(self.resume_path))
                                    print("  -> Uploaded resume.pdf")
                                    time.sleep(1)

                                # Look for Next / Review / Submit button
                                next_btn = page.locator('button[aria-label="Continue to next step"], button[aria-label="Review your application"]')
                                submit_btn = page.locator('button[aria-label="Submit application"]')

                                if submit_btn.count() > 0:
                                    print("  -> Reached Submit step! (Reviewing)")
                                    time.sleep(3)
                                    # For safety, you can click submit or pause
                                    # submit_btn.first.click()
                                    break
                                elif next_btn.count() > 0:
                                    print(f"  -> Advancing Step {step + 1}...")
                                    next_btn.first.click()
                                    time.sleep(2)
                                else:
                                    break

                            # Close modal or dismiss
                            dismiss_btn = page.locator('button[aria-label="Dismiss"]')
                            if dismiss_btn.count() > 0:
                                dismiss_btn.first.click()
                                discard_btn = page.locator('button[data-control-name="discard_application_confirm_btn"]')
                                if discard_btn.count() > 0:
                                    discard_btn.first.click()

                            count += 1
                            time.sleep(3)
                    except Exception as card_err:
                        print(f"  [Notice]: Could not process job card: {card_err}")

            finally:
                context.close()

if __name__ == "__main__":
    applier = JobPortalsApplier()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        applier.launch_logged_in_browser()
    else:
        print("To first login to LinkedIn/Naukri, run:")
        print("  python src/job_portals.py login")
        print("\nTo run Easy Apply search:")
        print("  python src/job_portals.py")
