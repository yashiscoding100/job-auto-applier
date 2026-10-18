import sys
from playwright.sync_api import sync_playwright

url = "https://dpdhlgroup.avature.net/en_US/jobs/ApplicationMethods?jobId=370444&source=LinkedIn&utm_source=LinkedIn&utm_medium=LIjob&utm_campaign=LIjobslot"

print("Navigating to DHL Avature portal...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded")
    print("Loaded title:", page.title())

    # Look for Without Resume or Upload resume
    btn = page.query_selector('a:has-text("Without Resume"), button:has-text("Without Resume"), span:has-text("Without Resume")')
    if btn:
        print("Found 'Without Resume' button, clicking...")
        btn.click()
        page.wait_for_timeout(4000)
        print("New URL:", page.url)
        inputs = page.query_selector_all("input")
        print(f"Detected {len(inputs)} input fields on the application form!")
        for inp in inputs[:6]:
            print(" -> Field:", inp.get_attribute("name"), "| placeholder:", inp.get_attribute("placeholder"))
    else:
        print("Could not find button. Page body snippet:")
        print(page.inner_text("body")[:300])

    browser.close()
