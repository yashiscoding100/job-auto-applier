import os
import sys
from pathlib import Path

# Fix Windows console encoding if needed
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))
from form_filler import AIFormFiller

def run_local_mock_test():
    mock_file = Path(__file__).parent / "mock_placement_form.html"
    if not mock_file.exists():
        print(f"Mock form not found at: {mock_file}")
        return

    local_url = mock_file.resolve().as_uri()
    print(f"\n[TEST] Running local form test on: {local_url}")
    print("Launching Chromium, having Gemini 3.8 Flash inspect and fill the form...")

    try:
        filler = AIFormFiller()
        filler.apply_to_job(
            apply_url=local_url,
            company="ABC Tech Campus Recruitment",
            role="Associate Software Engineer",
            auto_submit=True
        )
        print("\n[SUCCESS] Local test completed! Check logs/applications_log.csv")
    except Exception as e:
        print(f"\n[Test Error]: {e}")

if __name__ == "__main__":
    run_local_mock_test()
