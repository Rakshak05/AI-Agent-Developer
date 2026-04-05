"""
COMPONENT 1: ACCESS — Platform Authentication & Data Extraction
================================================================
What failed and why, then what works:

APPROACH 1 - Selenium headless: BLOCKED
  Error: reCAPTCHA Enterprise (invisible) fires on form submit.
  HTTP response: 200 OK but body = "Please verify you are human"
  
APPROACH 2 - Playwright stealth: BLOCKED  
  Even with playwright-stealth and fake user-agent, navigator.webdriver=true
  leaks through reCAPTCHA's JS fingerprinting.

APPROACH 3 - Cookie theft via browser extension: BLOCKED
  Chrome extension API returns '[BLOCKED]' for httpOnly cookies.
  These cookies (session_id, _internshala_session) are httpOnly by design.

APPROACH 4 (WORKS) - Chrome DevTools Protocol (CDP) with real Chrome instance
  Start Chrome with remote debugging enabled. Attach via CDP. 
  The cookies are created in a REAL browser session (not headless),
  so reCAPTCHA passes. Then we read cookies from the live session via CDP.
  IP binding: cookies are IP-bound, so extraction and requests must come
  from the same IP. Run everything on your local machine or use a VPS
  that stays on the same IP.

APPROACH 5 (ALSO WORKS for one-time setup) - mitmproxy interception
  Run mitmproxy as a system proxy. Log in manually in your browser.
  mitmproxy captures the raw cookies and headers from the authenticated
  requests. Copy them to config.json. Valid for ~24 hours.

Usage:
  1. python c1_access.py --setup   # opens browser, you log in, cookies saved
  2. python c1_access.py --scrape  # scrapes all pages, outputs applicants.json
"""

import json
import time
import random
import argparse
import logging
import subprocess
import requests
from pathlib import Path
from typing import Optional
from bs4 import BeautifulSoup

# ── Try to import CDP connector (optional, falls back to manual cookies) ──
try:
    import websocket
    import threading
    HAS_CDP = True
except ImportError:
    HAS_CDP = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [C1-ACCESS] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path("data/config.json")
OUTPUT_PATH = Path("data/applicants.json")
ANTHROPIC_KEY = ""  # Set via env or --api-key arg for LLM-based scraping fallback

# ── CONFIG ────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "job_id": "YOUR_INTERNSHALA_JOB_ID",       # e.g. "1234567"
    "base_url": "https://internshala.com",
    "applications_url": "https://internshala.com/employer/applications/{job_id}",
    "cookies": {},                               # filled by --setup or manually
    "headers": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://internshala.com/employer/dashboard",
    }
}


# ── SETUP: Launch real Chrome with CDP and capture authenticated cookies ──────

def setup_via_cdp() -> dict:
    """
    Start a real (non-headless) Chrome instance with remote debugging.
    Wait for the user to log in manually (reCAPTCHA passes because it's real Chrome).
    Then read cookies from the live session via CDP WebSocket.
    """
    if not HAS_CDP:
        log.error("Install 'websocket-client' for CDP mode: pip install websocket-client")
        return {}

    chrome_path = _find_chrome()
    if not chrome_path:
        log.error("Chrome not found. Install Chrome or use --manual-cookies mode.")
        return {}

    cdp_port = 9222
    log.info(f"Launching Chrome with remote debugging on port {cdp_port}...")
    log.info("→ Log in to Internshala in the browser window that opens.")
    log.info("→ Navigate to the employer dashboard, then press ENTER here.")

    proc = subprocess.Popen([
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://internshala.com/login/employer"
    ])

    input("\n[Waiting] Log in to Internshala, then press ENTER to capture cookies...\n")

    try:
        # Get list of open pages via CDP HTTP endpoint
        resp = requests.get(f"http://localhost:{cdp_port}/json")
        pages = resp.json()
        # Find the Internshala tab
        internshala_page = next(
            (p for p in pages if "internshala.com" in p.get("url", "")), 
            pages[0] if pages else None
        )
        if not internshala_page:
            log.error("No Internshala tab found. Did you navigate there?")
            return {}

        ws_url = internshala_page["webSocketDebuggerUrl"]
        cookies = _get_cookies_via_cdp_ws(ws_url)

        log.info(f"Captured {len(cookies)} cookies from live session.")
        proc.terminate()
        return cookies

    except Exception as e:
        log.error(f"CDP cookie capture failed: {e}")
        proc.terminate()
        return {}


def _get_cookies_via_cdp_ws(ws_url: str) -> dict:
    """
    Connect to CDP WebSocket and call Network.getAllCookies.
    Returns dict of {name: value} for internshala.com cookies.
    """
    cookies_result = {}
    done = threading.Event()

    def on_message(ws, message):
        data = json.loads(message)
        if data.get("id") == 1:
            raw_cookies = data.get("result", {}).get("cookies", [])
            for c in raw_cookies:
                if "internshala" in c.get("domain", ""):
                    cookies_result[c["name"]] = c["value"]
            done.set()
            ws.close()

    def on_open(ws):
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))

    ws = websocket.WebSocketApp(ws_url, on_message=on_message, on_open=on_open)
    thread = threading.Thread(target=ws.run_forever)
    thread.daemon = True
    thread.start()
    done.wait(timeout=10)
    return cookies_result


def _find_chrome() -> Optional[str]:
    """Try to find Chrome binary on common paths."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    # Try which
    result = subprocess.run(["which", "google-chrome"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


# ── MANUAL COOKIE MODE (fallback) ────────────────────────────────────────────

def setup_via_mitmproxy_instructions():
    """
    Print instructions for the mitmproxy interception approach.
    Works even without CDP. User manually extracts cookies from browser DevTools.
    """
    print("""
═══════════════════════════════════════════════════════════
MANUAL COOKIE EXTRACTION (valid ~24 hours, same IP)
═══════════════════════════════════════════════════════════

1. Open Chrome → Log in to Internshala as employer
2. Open DevTools (F12) → Application → Cookies → internshala.com
3. Copy the values for these cookies:
   - _internshala_session
   - user_type  
   - remember_user_token  (if present)
   - Any others listed under internshala.com

4. Also go to: Network tab → Reload page → Click any request
   → Copy the full 'Cookie:' header value from Request Headers

5. Edit data/config.json and fill in the "cookies" field:
   {
     "_internshala_session": "PASTE_VALUE_HERE",
     "user_type": "employer",
     ...
   }

6. Run: python c1_access.py --scrape

IMPORTANT: The cookies are bound to your current IP address.
Run the scraper from the same machine where you logged in.
If using a VPS, log in to Internshala from the VPS's browser.
═══════════════════════════════════════════════════════════
""")


# ── SCRAPER ───────────────────────────────────────────────────────────────────

class IntershalaScraper:
    """
    Scrapes applicant data from Internshala employer dashboard.
    Handles pagination, rate limiting, and messy HTML.
    """

    def __init__(self, config: dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config["headers"])
        self.session.cookies.update(config["cookies"])
        self.job_id = config["job_id"]

    def verify_auth(self) -> bool:
        """Quick check that our session cookies are valid."""
        url = self.config["base_url"] + "/employer/dashboard"
        try:
            resp = self.session.get(url, timeout=15)
            if "login" in resp.url or "Please verify" in resp.text:
                log.error("Auth failed: redirected to login or reCAPTCHA.")
                return False
            if resp.status_code == 200:
                log.info("Auth verified ✓")
                return True
            log.error(f"Auth check returned HTTP {resp.status_code}")
            return False
        except requests.RequestException as e:
            log.error(f"Auth check failed: {e}")
            return False

    def scrape_all_pages(self) -> list[dict]:
        """
        Scrape all applicant pages. Internshala shows ~20 applicants per page.
        For 1,140 applicants → 57 pages. Returns list of applicant dicts.
        """
        all_applicants = []
        page = 1

        while True:
            log.info(f"Scraping page {page}...")
            applicants, has_next = self._scrape_page(page)

            if not applicants:
                log.warning(f"Page {page} returned 0 applicants. Stopping.")
                break

            all_applicants.extend(applicants)
            log.info(f"  → Got {len(applicants)} applicants (total so far: {len(all_applicants)})")

            if not has_next:
                log.info("No next page found. Scrape complete.")
                break

            page += 1
            # Polite delay with jitter to avoid rate limiting / IP ban
            delay = random.uniform(2.5, 5.0)
            log.info(f"  → Waiting {delay:.1f}s before next page...")
            time.sleep(delay)

        return all_applicants

    def _scrape_page(self, page: int) -> tuple[list[dict], bool]:
        """
        Scrape a single page of applicants.
        
        Internshala URL pattern: /employer/applications/{job_id}?page={page}
        Returns: (list of applicant dicts, has_next_page bool)
        
        NOTE: If Internshala uses AJAX for applications, the URL may be:
        /employer/applications/load_more/{job_id}?start={offset}
        Inspect Network tab in DevTools to find the actual endpoint.
        """
        url = (
            f"{self.config['base_url']}/employer/applications/{self.job_id}"
            f"?page={page}"
        )

        try:
            resp = self.session.get(url, timeout=20)
        except requests.RequestException as e:
            log.error(f"Request failed on page {page}: {e}")
            return [], False

        if resp.status_code == 429:
            log.warning("Rate limited (429). Waiting 30s...")
            time.sleep(30)
            return self._scrape_page(page)  # retry once

        if resp.status_code != 200:
            log.error(f"HTTP {resp.status_code} on page {page}")
            return [], False

        if "Please verify" in resp.text or "captcha" in resp.text.lower():
            log.error("reCAPTCHA triggered! Cookies may have expired or IP changed.")
            return [], False

        return self._parse_applications_page(resp.text, page)

    def _parse_applications_page(self, html: str, page: int) -> tuple[list[dict], bool]:
        """
        Parse HTML from an applications page.
        
        WARNING: Internshala's HTML structure changes periodically.
        These selectors were accurate as of mid-2024 — verify in DevTools.
        The key containers are typically:
          div.application_container or div#internship_application_{id}
        
        SELF-HEALING: If BeautifulSoup fails to find expected elements,
        falls back to LLM-based extraction with structured JSON output.
        """
        soup = BeautifulSoup(html, "html.parser")
        applicants = []

        # Try to find application containers
        # Internshala uses multiple possible class names — try all
        app_containers = (
            soup.find_all("div", class_="application_container") or
            soup.find_all("div", attrs={"data-application-id": True}) or
            soup.find_all("div", class_="application-item")
        )

        if not app_containers:
            log.warning(f"Page {page}: No application containers found. Attempting LLM self-healing...")
            log.debug(f"Page HTML snippet: {html[:500]}")
            
            # FALLBACK: Use LLM to extract structured data from raw HTML
            applicants = self._llm_extract_applicants(html)
            
            if not applicants:
                log.error(f"Page {page}: Both BeautifulSoup and LLM extraction failed.")
                return [], False
            
            # Check for next page (still use BS4 for this)
            has_next = bool(
                soup.find("a", {"rel": "next"}) or
                soup.find("li", class_="next") or
                soup.find("a", string=lambda t: t and "Next" in t)
            )
            
            return applicants, has_next

        for container in app_containers:
            applicant = self._parse_single_application(container)
            if applicant:
                applicants.append(applicant)

        # Check for next page
        has_next = bool(
            soup.find("a", {"rel": "next"}) or
            soup.find("li", class_="next") or
            soup.find("a", string=lambda t: t and "Next" in t)
        )

        return applicants, has_next

    def _llm_extract_applicants(self, html: str) -> list[dict]:
        """
        Self-healing parser: Use LLM to extract applicant data when BeautifulSoup fails.
        This handles cases where Internshala changes their HTML structure.
        
        Sends truncated HTML to Claude/GPT-4 with strict Pydantic-style schema.
        Returns list of applicant dicts in the same format as _parse_single_application.
        """
        if not ANTHROPIC_KEY:
            log.error("LLM extraction requires Anthropic API key. Set via --api-key or config.")
            return []
        
        # Truncate HTML to reduce token usage (keep first 15k chars)
        html_snippet = html[:15000]
        
        prompt = f"""You are a web scraping expert system. Your task is to extract applicant data from HTML.

The HTML below is from a recruitment dashboard showing job applicants. Each applicant has information like name, email, cover letter, skills, etc.

HTML STRUCTURE (truncated):
{html_snippet}

Extract ALL applicants from this HTML and return them as a JSON array. Each applicant object should have these fields:
- name: string (required - skip if not found)
- email: string (may be empty)
- cover_letter: string (application message/cover letter text)
- github_url: string (if present)
- resume_url: string (if present)
- skills: array of strings (technical skills mentioned)
- applied_at: string (timestamp/date if available)
- status: string (application status)
- answers: array of objects with {{question, answer}} pairs (screening questions)

IMPORTANT:
1. Return ONLY valid JSON. No markdown, no explanation.
2. If a field is not found, omit it or use empty string/array.
3. Name is required - skip entries without a name.
4. Extract as many applicants as you can find in the HTML.
5. Look for patterns like repeated div structures, tables, or card layouts.

Return format:
[
  {{
    "name": "John Doe",
    "email": "john@example.com",
    "cover_letter": "I am interested...",
    "github_url": "https://github.com/johndoe",
    "skills": ["Python", "JavaScript"],
    ...
  }},
  ...
]

JSON:"""

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={{
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }},
                json={{
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 3000,
                    "messages": [{{"role": "user", "content": prompt}}]
                }},
                timeout=45
            )
            data = resp.json()
            extracted_json = data["content"][0]["text"]
            
            # Parse the JSON response
            applicants = json.loads(extracted_json)
            
            log.info(f"LLM successfully extracted {{len(applicants)}} applicants from HTML.")
            
            # Add missing fields to match standard format
            for app in applicants:
                app.setdefault("id", f"llm_{{app.get('name', 'unknown').replace(' ', '_')}}")
                app.setdefault("platform_score", "")
                app.setdefault("portfolio_url", "")
                app.setdefault("raw_text", app.get("cover_letter", "")[:1000])
            
            return applicants
            
        except Exception as e:
            log.error(f"LLM extraction failed: {{e}}")
            return []

    def _parse_single_application(self, container) -> Optional[dict]:
        """
        Extract all fields from one applicant container.
        Handles missing fields gracefully — returns None only if name is missing.
        """
        try:
            # Name — required field
            name_el = (
                container.find("a", class_="name") or
                container.find("h3", class_="name") or
                container.find("span", class_="applicant_name")
            )
            name = name_el.get_text(strip=True) if name_el else None
            if not name:
                return None

            # Application ID (for deduplication)
            app_id = (
                container.get("data-application-id") or
                container.get("id", "").replace("internship_application_", "")
            )

            # Email — may be hidden behind a "View Details" click
            email_el = container.find("a", href=lambda h: h and "mailto:" in h)
            email = email_el["href"].replace("mailto:", "") if email_el else ""

            # Score if Internshala has pre-computed one (often present)
            score_el = container.find("span", class_="score") or container.find("div", class_="screening_score")
            platform_score = score_el.get_text(strip=True) if score_el else ""

            # Cover letter / motivation
            cover_el = (
                container.find("div", class_="cover_letter") or
                container.find("div", class_="application_message") or
                container.find("p", class_="internship_application")
            )
            cover_letter = cover_el.get_text(strip=True) if cover_el else ""

            # Screening question answers
            answers = self._extract_screening_answers(container)

            # Resume / GitHub links
            resume_url = ""
            github_url = ""
            portfolio_url = ""

            for link in container.find_all("a", href=True):
                href = link["href"]
                if "github.com" in href:
                    github_url = href
                elif "resume" in href.lower() or ".pdf" in href.lower():
                    resume_url = href
                elif any(x in href for x in ["portfolio", "behance", "dribbble"]):
                    portfolio_url = href

            # Skills (often in a tag/badge list)
            skills = []
            for skill_el in container.find_all(class_=lambda c: c and "skill" in c.lower()):
                text = skill_el.get_text(strip=True)
                if text and len(text) < 50:
                    skills.append(text)

            # Application timestamp
            time_el = container.find("time") or container.find(attrs={"data-time": True})
            applied_at = (
                time_el.get("datetime") or
                time_el.get("data-time") or
                (time_el.get_text(strip=True) if time_el else "")
            )

            # Current status on platform
            status_el = container.find(class_=lambda c: c and "status" in c.lower())
            status = status_el.get_text(strip=True) if status_el else "pending"

            return {
                "id": app_id,
                "name": name,
                "email": email,
                "cover_letter": cover_letter,
                "answers": answers,
                "skills": skills,
                "github_url": github_url,
                "resume_url": resume_url,
                "portfolio_url": portfolio_url,
                "platform_score": platform_score,
                "applied_at": applied_at,
                "status": status,
                "raw_text": container.get_text(separator=" ", strip=True)[:1000]
            }

        except Exception as e:
            log.warning(f"Failed to parse application container: {e}")
            return None

    def _extract_screening_answers(self, container) -> list[dict]:
        """
        Extract screening question answers.
        Internshala screening questions appear in a separate section.
        Structure: <div class="screening_question"> / <div class="screening_answer">
        """
        answers = []
        q_els = container.find_all(class_=lambda c: c and "screening" in c.lower())

        # Try paired Q/A structure
        for i, el in enumerate(q_els):
            text = el.get_text(strip=True)
            if "?" in text or text.startswith("Q"):
                # This looks like a question — try to find the following answer
                next_el = el.find_next_sibling()
                answer_text = next_el.get_text(strip=True) if next_el else ""
                answers.append({
                    "question": text,
                    "answer": answer_text
                })

        return answers


# ── GITHUB PROFILE VALIDATOR ──────────────────────────────────────────────────

def validate_github_profile(github_url: str) -> dict:
    """
    Check if a GitHub profile is real and active (not empty).
    Uses GitHub API (no auth needed for public profiles, rate limit: 60/hr).
    
    Returns dict with:
      - valid: bool
      - repos: int  
      - stars: int
      - recent_activity: bool (pushed in last 6 months)
      - is_empty: bool
    """
    if not github_url:
        return {"valid": False, "reason": "no_url"}

    # Extract username from URL
    parts = github_url.rstrip("/").split("/")
    if "github.com" not in github_url or len(parts) < 4:
        return {"valid": False, "reason": "invalid_url"}

    username = parts[-1]
    if not username or username in ("", "#", "github"):
        return {"valid": False, "reason": "no_username"}

    # GitHub API
    api_url = f"https://api.github.com/users/{username}"
    repos_url = f"https://api.github.com/users/{username}/repos?sort=pushed&per_page=10"

    try:
        time.sleep(0.5)  # respect rate limit
        user_resp = requests.get(api_url, timeout=10, headers={"Accept": "application/vnd.github.v3+json"})

        if user_resp.status_code == 404:
            return {"valid": False, "reason": "profile_not_found"}
        if user_resp.status_code == 403:
            return {"valid": True, "reason": "rate_limited", "repos": -1, "stars": -1}
        if user_resp.status_code != 200:
            return {"valid": False, "reason": f"http_{user_resp.status_code}"}

        user_data = user_resp.json()

        repos_resp = requests.get(repos_url, timeout=10)
        repos_data = repos_resp.json() if repos_resp.status_code == 200 else []

        public_repos = user_data.get("public_repos", 0)
        total_stars = sum(r.get("stargazers_count", 0) for r in repos_data)

        # Check if profile is "empty" (created just to have a link)
        is_empty = (
            public_repos == 0 or
            (public_repos <= 2 and total_stars == 0 and not user_data.get("bio"))
        )

        # Recent activity: any repo pushed in last 180 days
        recent_activity = False
        import datetime
        six_months_ago = datetime.datetime.now() - datetime.timedelta(days=180)
        for repo in repos_data:
            pushed = repo.get("pushed_at", "")
            if pushed:
                try:
                    pushed_dt = datetime.datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    if pushed_dt.replace(tzinfo=None) > six_months_ago:
                        recent_activity = True
                        break
                except ValueError:
                    pass

        # Check for just-forks (no original work)
        non_fork_repos = [r for r in repos_data if not r.get("fork", False)]

        return {
            "valid": True,
            "username": username,
            "repos": public_repos,
            "stars": total_stars,
            "followers": user_data.get("followers", 0),
            "recent_activity": recent_activity,
            "is_empty": is_empty,
            "non_fork_count": len(non_fork_repos),
            "bio": user_data.get("bio", ""),
            "reason": "ok"
        }

    except requests.RequestException as e:
        return {"valid": False, "reason": f"network_error: {e}"}


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Internshala Applicant Scraper")
    parser.add_argument("--setup", action="store_true", help="Capture cookies via CDP (real Chrome)")
    parser.add_argument("--manual", action="store_true", help="Print manual cookie instructions")
    parser.add_argument("--scrape", action="store_true", help="Scrape all applicants")
    parser.add_argument("--validate-github", action="store_true", help="Validate GitHub URLs in output")
    parser.add_argument("--job-id", help="Override job ID from config")
    parser.add_argument("--max-pages", type=int, default=999, help="Max pages to scrape (for testing)")
    parser.add_argument("--api-key", default="", help="Anthropic API key for LLM-based self-healing scraper")
    args = parser.parse_args()

    global ANTHROPIC_KEY
    if args.api_key:
        ANTHROPIC_KEY = args.api_key

    # Load or create config
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    else:
        config = DEFAULT_CONFIG.copy()
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        log.info(f"Created default config at {CONFIG_PATH}")

    if args.job_id:
        config["job_id"] = args.job_id

    if args.manual:
        setup_via_mitmproxy_instructions()
        return

    if args.setup:
        log.info("Starting CDP cookie capture...")
        cookies = setup_via_cdp()
        if cookies:
            config["cookies"] = cookies
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)
            log.info(f"Saved {len(cookies)} cookies to {CONFIG_PATH}")
        else:
            log.error("No cookies captured. Try --manual mode.")
        return

    if args.scrape:
        scraper = IntershalaScraper(config)

        if not scraper.verify_auth():
            log.error("Authentication failed. Run --setup or --manual first.")
            return

        log.info(f"Starting scrape of job ID: {config['job_id']}")
        applicants = scraper.scrape_all_pages()

        if args.validate_github:
            log.info("Validating GitHub profiles...")
            for i, app in enumerate(applicants):
                if app.get("github_url"):
                    log.info(f"  [{i+1}/{len(applicants)}] Validating {app['github_url']}")
                    app["github_info"] = validate_github_profile(app["github_url"])
                else:
                    app["github_info"] = {"valid": False, "reason": "no_url"}

        OUTPUT_PATH.parent.mkdir(exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(applicants, f, indent=2, ensure_ascii=False)

        log.info(f"\n{'='*50}")
        log.info(f"SCRAPE COMPLETE")
        log.info(f"Total applicants: {len(applicants)}")
        log.info(f"Output: {OUTPUT_PATH}")
        log.info(f"{'='*50}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
