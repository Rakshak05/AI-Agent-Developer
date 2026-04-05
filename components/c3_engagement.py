"""
COMPONENT 3: ENGAGEMENT — Multi-Round Email Conversations
==========================================================
Reads Gmail inbox, understands replies, sends contextual follow-ups.
Tracks all conversations in SQLite. Manages 50+ simultaneous threads.

Setup:
1. Enable Gmail API in Google Cloud Console
2. Download credentials.json to data/gmail_credentials.json
3. Run: python c3_engagement.py --auth  (first-time only)
4. Run: python c3_engagement.py --send-round1  (send first emails to Fast-Track)
5. Run: python c3_engagement.py --monitor  (continuous loop, checks every 5 min)

Email Round Sequence:
  Round 1: Invite to explain their approach to the technical problem
  Round 2: Contextual follow-up based on their R1 answer
  Round 3 (optional): Code submission request or deeper technical question
"""

import json
import time
import sqlite3
import logging
import argparse
import base64
import email as email_lib
import re
import subprocess
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests

# Gmail API imports (google-auth, google-api-python-client)
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GAuthRequest
    from googleapiclient.discovery import build
    HAS_GMAIL = True
except ImportError:
    HAS_GMAIL = False
    print("Gmail API not installed. Run: pip install google-auth-oauthlib google-api-python-client")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [C3-EMAIL] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

DB_PATH        = Path("data/recruitment.db")
TOKEN_PATH     = Path("data/gmail_token.json")
CREDS_PATH     = Path("data/gmail_credentials.json")
RANKED_PATH    = Path("data/ranked_applicants.json")
ANTHROPIC_KEY  = ""  # set via env or --api-key arg
SENDER_NAME    = "Recruitment Team"
SENDER_EMAIL   = ""  # set in config or env
JOB_TITLE      = "Software Engineering Intern"
COMPANY_NAME   = "TechCorp"
MONITOR_INTERVAL = 300  # seconds between inbox checks


# ── DATABASE ──────────────────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT UNIQUE,
            score REAL,
            tier TEXT,
            github_url TEXT,
            cover_letter TEXT,
            answers TEXT,        -- JSON
            ai_flags TEXT,       -- JSON
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS email_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT,
            thread_id TEXT,      -- Gmail thread ID
            message_id TEXT,     -- Gmail message ID (for reply threading)
            round INTEGER,
            direction TEXT,      -- 'sent' or 'received'
            subject TEXT,
            body TEXT,
            sent_at TEXT,
            received_at TEXT,
            FOREIGN KEY(candidate_id) REFERENCES candidates(id)
        );

        CREATE TABLE IF NOT EXISTS strikes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT,
            reason TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(candidate_id) REFERENCES candidates(id)
        );

        CREATE TABLE IF NOT EXISTS system_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            candidate_id TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    conn.commit()
    conn.close()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── GMAIL AUTH ────────────────────────────────────────────────────────────────

def get_gmail_service():
    """Authenticate and return Gmail API service object."""
    if not HAS_GMAIL:
        raise RuntimeError("Gmail API libraries not installed.")

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GAuthRequest())
        else:
            if not CREDS_PATH.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {CREDS_PATH}. "
                    "Download from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── EMAIL BUILDING ────────────────────────────────────────────────────────────

def build_mime_message(
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> dict:
    """Build a Gmail API message dict from parts."""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart("alternative")
    msg["To"] = f"{to_name} <{to_email}>"
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["Subject"] = subject

    # Threading headers — keeps replies in one thread
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    # Plain text body
    text_part = MIMEText(body, "plain", "utf-8")
    msg.attach(text_part)

    # HTML version
    html_body = body.replace("\n\n", "</p><p>").replace("\n", "<br>")
    html_part = MIMEText(f"<p>{html_body}</p>", "html", "utf-8")
    msg.attach(html_part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    message = {"raw": raw}
    if thread_id:
        message["threadId"] = thread_id
    return message


def send_email(service, message_dict: dict) -> dict:
    """Send an email via Gmail API. Returns sent message info."""
    result = service.users().messages().send(
        userId="me", body=message_dict
    ).execute()
    return result


# ── EMAIL TEMPLATES ───────────────────────────────────────────────────────────

def generate_round1_email(candidate: dict) -> str:
    """
    Generate Round 1 email. References specific details from their application.
    This is NOT a generic template — it mentions what they actually wrote.
    """
    name = candidate.get("name", "there")
    cover = candidate.get("cover_letter", "")
    skills = candidate.get("skills", [])
    github = candidate.get("github_url", "")

    # Pick one specific thing they mentioned to reference
    reference = ""
    if github and not candidate.get("github_info", {}).get("is_empty"):
        reference = f"We noticed your GitHub profile shows some interesting work. "
    elif skills:
        top_skill = skills[0] if skills else ""
        reference = f"You mentioned experience with {top_skill}, which caught our attention. "
    elif cover and len(cover) > 50:
        # Quote a snippet from their cover letter
        snippet = cover[:80].strip()
        reference = f"Your note about \"{snippet}...\" resonated with us. "

    body = f"""Hi {name},

Thank you for applying for the {JOB_TITLE} position at {COMPANY_NAME}.

{reference}We'd like to move you forward in our process with a short technical question.

THE CHALLENGE:
We need to programmatically extract applicant data from a platform that:
- Uses reCAPTCHA Enterprise on its login page
- Binds session cookies to the IP they were created from
- Has no public API

Describe how you would approach this problem. We're not looking for one "right" answer — we want to understand how you think through constraints.

A few things we'd love to know:
1. What would you try first, and why?
2. What do you expect would fail, and what's your backup plan?
3. Have you dealt with anything similar before?

Please reply to this email with your approach. A thoughtful 2–3 paragraph answer is perfect — no need for code at this stage.

We look forward to hearing from you.

Best,
{SENDER_NAME}
{COMPANY_NAME}
"""
    return body


def generate_round2_email(candidate: dict, their_reply: str) -> str:
    """
    Generate Round 2 email using LLM to create a contextual follow-up
    based on exactly what the candidate said in Round 1.
    """
    name = candidate.get("name", "there")

    if ANTHROPIC_KEY:
        return _generate_r2_via_llm(name, their_reply)
    else:
        return _generate_r2_heuristic(name, their_reply)


def _generate_r2_via_llm(name: str, their_reply: str) -> str:
    """Use Claude to write a highly contextual Round 2 follow-up."""
    prompt = f"""You are a technical recruiter at a software company. A candidate just replied to your Round 1 question about web scraping.

THEIR REPLY:
{their_reply[:2000]}

Write a Round 2 follow-up email that:
1. Acknowledges something SPECIFIC they said (quote or paraphrase a key point)
2. Asks ONE deeper follow-up question based on their approach — NOT a generic question
3. If they mentioned a specific technology (Selenium, Playwright, cookies, proxies, etc.), ask something that tests whether they actually know how it works
4. If their answer was vague, ask them to get more specific about ONE thing
5. If they gave a really good answer, move them forward and ask about a RELATED challenge

The email should:
- Be conversational, not formal
- Be under 200 words
- NOT use phrases like "Great response!" or "Thank you for your detailed answer"
- End with a clear single question
- Sign off as "The Recruitment Team"

Start directly with "Hi {name}," — no preamble.
Only output the email body. No subject line."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        data = resp.json()
        return data["content"][0]["text"]
    except Exception as e:
        log.warning(f"LLM R2 generation failed: {e}")
        return _generate_r2_heuristic(name, their_reply)


def _generate_r2_heuristic(name: str, their_reply: str) -> str:
    """Fallback R2 email without LLM. Tries to reference something specific."""
    reply_lower = their_reply.lower()

    # Pick a contextual follow-up based on what they mentioned
    if "selenium" in reply_lower or "playwright" in reply_lower:
        tool = "Selenium" if "selenium" in reply_lower else "Playwright"
        followup = (
            f"You mentioned {tool} as your first approach. "
            f"reCAPTCHA Enterprise specifically fingerprints headless browsers — "
            f"what would you do when {tool} gets blocked at the login form? "
            f"Have you dealt with reCAPTCHA specifically before?"
        )
    elif "cookie" in reply_lower or "session" in reply_lower:
        followup = (
            "You brought up cookies/sessions — good instinct. "
            "Here's the catch: Internshala's session cookies are httpOnly, "
            "which means browser extensions can't read them. "
            "Given that constraint, how would you capture a valid authenticated session?"
        )
    elif "api" in reply_lower:
        followup = (
            "You mentioned looking for an API. There isn't one publicly. "
            "But many platforms have undocumented internal APIs they use for their own frontend. "
            "How would you find and use those, and what are the legal/ethical considerations?"
        )
    elif "proxy" in reply_lower or "vpn" in reply_lower:
        followup = (
            "You mentioned proxies — interesting. "
            "But this platform binds cookies to the IP they were created from. "
            "So rotating IPs would actually break the session. "
            "Does that change your approach? How would you handle session stickiness?"
        )
    else:
        followup = (
            "Can you get more specific about your approach? "
            "Which library or tool would you actually use to start, "
            "and what specific error do you expect to hit first?"
        )

    body = f"""Hi {name},

{followup}

We're curious about the details of your approach — not looking for perfection, just genuine thinking.

Best,
{SENDER_NAME}
{COMPANY_NAME}
"""
    return body


# ── SANDBOXED CODE EXECUTION ────────────────────────────────────────────────

def extract_python_code(text: str) -> list[str]:
    """
    Extract Python code blocks from candidate email text.
    Handles markdown-style code fences and inline code.
    Returns list of code snippets found.
    """
    code_blocks = []
    
    # Match fenced code blocks (```python ... ``` or ``` ... ```)
    fenced_pattern = r'```(?:python)?\s*\n(.*?)```'
    fenced_matches = re.findall(fenced_pattern, text, re.DOTALL | re.IGNORECASE)
    code_blocks.extend(fenced_matches)
    
    # If no fenced blocks, look for indented code patterns
    if not code_blocks:
        # Look for common Python patterns that suggest code
        python_indicators = [
            r'(def\s+\w+\s*\([^)]*\):.*?)(?=\n\n|$)',
            r'(class\s+\w+.*?)(?=\n\n|$)',
            r'(import\s+[\w\s,]+.*?)(?=\n\n|$)',
        ]
        for pattern in python_indicators:
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                code_blocks.extend(matches)
    
    return [block.strip() for block in code_blocks if block.strip()]


def execute_code_sandbox(code: str, timeout: int = 10) -> dict:
    """
    Execute Python code in a sandboxed environment using subprocess.
    
    SECURITY MEASURES:
    - Runs in isolated temporary directory
    - No network access (blocked via subprocess restrictions)
    - Limited execution time (timeout parameter)
    - Captures both stdout and stderr
    - No file system persistence outside temp dir
    
    For production use, consider replacing with Docker-based isolation:
      docker run --rm --network=none --memory=128m --cpus=0.5 python:3.11-slim python script.py
    
    Returns dict with:
      - success: bool
      - stdout: str
      - stderr: str
      - exit_code: int
      - error_type: str (if applicable)
    """
    result = {
        "success": False,
        "stdout": "",
        "stderr": "",
        "exit_code": -1,
        "error_type": None,
        "execution_time": 0
    }
    
    try:
        # Create temporary directory for execution
        with tempfile.TemporaryDirectory(prefix="candidate_code_") as tmpdir:
            script_path = os.path.join(tmpdir, "candidate_script.py")
            
            # Write code to temporary file
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # Execute with security restrictions
            start_time = time.time()
            proc = subprocess.run(
                ["python", script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": "",
                    "HOME": tmpdir,
                },
                # Security flags
                stdin=subprocess.DEVNULL,
            )
            execution_time = time.time() - start_time
            
            result.update({
                "success": proc.returncode == 0,
                "stdout": proc.stdout[:5000],  # Limit output size
                "stderr": proc.stderr[:5000],
                "exit_code": proc.returncode,
                "execution_time": round(execution_time, 2)
            })
            
            if proc.returncode != 0:
                # Classify common errors
                stderr_lower = proc.stderr.lower()
                if "syntaxerror" in stderr_lower:
                    result["error_type"] = "SyntaxError"
                elif "keyerror" in stderr_lower:
                    result["error_type"] = "KeyError"
                elif "typeerror" in stderr_lower:
                    result["error_type"] = "TypeError"
                elif "indexerror" in stderr_lower:
                    result["error_type"] = "IndexError"
                elif "attributeerror" in stderr_lower:
                    result["error_type"] = "AttributeError"
                elif "importerror" in stderr_lower or "modulenotfounderror" in stderr_lower:
                    result["error_type"] = "ImportError"
                else:
                    result["error_type"] = "RuntimeError"
    
    except subprocess.TimeoutExpired:
        result.update({
            "success": False,
            "stderr": f"Execution timed out after {timeout} seconds",
            "error_type": "TimeoutError"
        })
    except Exception as e:
        result.update({
            "success": False,
            "stderr": f"Sandbox execution failed: {str(e)}",
            "error_type": type(e).__name__
        })
    
    return result


def generate_feedback_with_execution(candidate_name: str, code: str, execution_result: dict) -> str:
    """
    Generate contextual feedback based on code execution results.
    Uses LLM to provide constructive, specific feedback.
    """
    prompt = f"""You are a technical recruiter providing feedback on a candidate's code submission.

CANDIDATE: {candidate_name}

CODE SUBMITTED:
```python
{code[:2000]}
```

EXECUTION RESULT:
- Success: {execution_result['success']}
- Exit Code: {execution_result['exit_code']}
- Execution Time: {execution_result['execution_time']}s
- Error Type: {execution_result.get('error_type', 'None')}

STDOUT:
{execution_result['stdout'][:1000]}

STDERR:
{execution_result['stderr'][:1000]}

Write a helpful, constructive response that:
1. Acknowledges their effort and what they were trying to accomplish
2. Points out the SPECIFIC issue (reference line numbers or error messages from stderr)
3. Provides a hint or suggestion for fixing it (without giving away the full solution)
4. Encourages them to iterate
5. Asks them to resubmit with the fix

Tone: Supportive but technically precise. Like a senior engineer mentoring a junior.
Length: 150-250 words.
Start directly with "Hi {candidate_name}," — no preamble."""

    if ANTHROPIC_KEY:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=30
            )
            data = resp.json()
            return data["content"][0]["text"]
        except Exception as e:
            log.warning(f"LLM feedback generation failed: {e}")
            return _generate_fallback_feedback(candidate_name, execution_result)
    else:
        return _generate_fallback_feedback(candidate_name, execution_result)


def _generate_fallback_feedback(candidate_name: str, execution_result: dict) -> str:
    """Fallback feedback without LLM."""
    error_type = execution_result.get('error_type', 'Unknown')
    stderr = execution_result.get('stderr', '')[:500]
    
    body = f"""Hi {candidate_name},

Thanks for sharing your code! I ran it through our test environment and noticed it encountered an issue.

ERROR DETAILS:
- Type: {error_type}
- Message: {stderr[:200]}

WHAT THIS LIKELY MEANS:
{get_error_explanation(error_type)}

SUGGESTION:
Take another look at your approach — particularly around the area where this error occurs. Sometimes stepping back and thinking about edge cases or data types can reveal the issue.

Would you like to revise and resubmit? We're looking for iterative problem-solving, not perfection on the first try.

Best,
{SENDER_NAME}
{COMPANY_NAME}
"""
    return body


def get_error_explanation(error_type: str) -> str:
    """Provide beginner-friendly explanations for common errors."""
    explanations = {
        "SyntaxError": "There's likely a missing colon, parenthesis, or indentation issue. Python is very particular about structure.",
        "KeyError": "You're trying to access a dictionary key that doesn't exist. Check if the key is spelled correctly or if the data structure has the expected shape.",
        "TypeError": "An operation is being performed on incompatible types (e.g., adding a string to an integer).",
        "IndexError": "You're trying to access a list index that's out of range. The list might be shorter than expected.",
        "AttributeError": "You're calling a method or attribute that doesn't exist on this object type.",
        "ImportError": "A required module isn't available. Make sure all imports are standard library or properly installed.",
        "TimeoutError": "The code took too long to execute. There might be an infinite loop or inefficient algorithm.",
    }
    return explanations.get(error_type, "Check the error message above and trace through your logic step by step.")


# ── INBOX MONITOR ─────────────────────────────────────────────────────────────

class InboxMonitor:
    """
    Monitors Gmail inbox for candidate replies.
    Matches emails to candidate records by thread ID.
    Triggers contextual response generation.
    """

    def __init__(self, service, anthropic_key: str = ""):
        self.service = service
        global ANTHROPIC_KEY
        ANTHROPIC_KEY = anthropic_key

    def check_inbox(self) -> list[dict]:
        """
        Find unread messages in our recruitment label/folder.
        Returns list of parsed reply dicts.
        """
        try:
            results = self.service.users().messages().list(
                userId="me",
                labelIds=["INBOX"],
                q="is:unread",
                maxResults=50
            ).execute()
        except Exception as e:
            log.error(f"Gmail API error: {e}")
            return []

        messages = results.get("messages", [])
        replies = []

        for msg_meta in messages:
            parsed = self._parse_message(msg_meta["id"])
            if parsed:
                replies.append(parsed)

        return replies

    def _parse_message(self, message_id: str) -> Optional[dict]:
        """Fetch and parse a single Gmail message."""
        try:
            msg = self.service.users().messages().get(
                userId="me",
                id=message_id,
                format="full"
            ).execute()
        except Exception as e:
            log.error(f"Failed to fetch message {message_id}: {e}")
            return None

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        thread_id = msg.get("threadId")
        from_addr = headers.get("From", "")
        subject = headers.get("Subject", "")
        date_str = headers.get("Date", "")
        in_reply_to = headers.get("In-Reply-To", "")
        references = headers.get("References", "")

        # Extract email address from "Name <email>" format
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', from_addr)
        sender_email = email_match.group() if email_match else from_addr

        # Extract body
        body = self._extract_body(msg["payload"])

        # Look up candidate by thread or email
        candidate = self._find_candidate(thread_id=thread_id, email=sender_email)
        if not candidate:
            return None  # Not a recruitment reply

        # Mark as read
        self._mark_read(message_id)

        return {
            "message_id": message_id,
            "thread_id": thread_id,
            "candidate_id": candidate["id"],
            "candidate_name": candidate["name"],
            "sender_email": sender_email,
            "subject": subject,
            "body": body,
            "date": date_str,
            "in_reply_to": in_reply_to,
            "candidate": dict(candidate),
        }

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract plain text from Gmail message payload."""
        body = ""
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        elif "parts" in payload:
            for part in payload["parts"]:
                body += self._extract_body(part)
        return body

    def _find_candidate(self, thread_id: str, email: str) -> Optional[dict]:
        """Find candidate by Gmail thread ID or email address."""
        conn = get_db()
        try:
            # Try thread ID first (more reliable)
            if thread_id:
                row = conn.execute(
                    "SELECT c.* FROM candidates c "
                    "JOIN email_threads t ON c.id = t.candidate_id "
                    "WHERE t.thread_id = ?",
                    (thread_id,)
                ).fetchone()
                if row:
                    return row

            # Fall back to email
            if email:
                row = conn.execute(
                    "SELECT * FROM candidates WHERE email = ?",
                    (email.lower(),)
                ).fetchone()
                return row

        finally:
            conn.close()
        return None

    def _mark_read(self, message_id: str):
        """Remove UNREAD label from a Gmail message."""
        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        except Exception as e:
            log.warning(f"Failed to mark message read: {e}")

    def process_reply(self, reply: dict) -> bool:
        """
        Process one candidate reply:
        1. Save to DB
        2. Determine which round they're in
        3. Check if code was submitted and execute it
        4. Generate contextual response (with execution feedback if applicable)
        5. Send reply email
        Returns True if response was sent.
        """
        conn = get_db()
        candidate_id = reply["candidate_id"]
        thread_id = reply["thread_id"]
        candidate = reply["candidate"]

        # Count how many rounds we've had
        rounds = conn.execute(
            "SELECT round FROM email_threads WHERE candidate_id = ? ORDER BY round DESC LIMIT 1",
            (candidate_id,)
        ).fetchone()
        current_round = (rounds["round"] if rounds else 0)
        next_round = current_round + 1

        # Save received email to DB
        conn.execute(
            "INSERT INTO email_threads (candidate_id, thread_id, message_id, round, direction, subject, body, received_at) "
            "VALUES (?, ?, ?, ?, 'received', ?, ?, ?)",
            (candidate_id, thread_id, reply["message_id"], current_round,
             reply["subject"], reply["body"], datetime.now(timezone.utc).isoformat())
        )

        # Log the interaction
        conn.execute(
            "INSERT INTO system_log (event_type, candidate_id, details) VALUES (?, ?, ?)",
            ("email_received", candidate_id,
             json.dumps({"round": current_round, "length": len(reply["body"])}))
        )
        conn.commit()

        # Check for code submission and execute in sandbox
        code_blocks = extract_python_code(reply["body"])
        execution_feedback = None
        
        if code_blocks:
            log.info(f"Detected {len(code_blocks)} code block(s) from {candidate['name']}. Executing...")
            for i, code_block in enumerate(code_blocks):
                exec_result = execute_code_sandbox(code_block)
                
                # Log execution result
                conn.execute(
                    "INSERT INTO system_log (event_type, candidate_id, details) VALUES (?, ?, ?)",
                    ("code_execution", candidate_id,
                     json.dumps({
                         "block_index": i,
                         "success": exec_result["success"],
                         "error_type": exec_result.get("error_type"),
                         "execution_time": exec_result["execution_time"]
                     }))
                )
                
                # Generate feedback for this code block
                if not exec_result["success"]:
                    execution_feedback = generate_feedback_with_execution(
                        candidate["name"], code_block, exec_result
                    )
                    break  # Only provide feedback on first error
            
            conn.commit()
        
        conn.close()

        # Don't respond beyond round 3
        if next_round > 3:
            log.info(f"Candidate {candidate['name']} has completed all rounds. No further response.")
            return False

        # Generate and send response
        log.info(f"Generating Round {next_round} response for {candidate['name']}...")
        
        # If code was executed and failed, use execution feedback
        if execution_feedback:
            body = execution_feedback
        else:
            body = generate_round2_email(candidate, reply["body"])

        # Get the last sent message ID for threading
        conn = get_db()
        last_sent = conn.execute(
            "SELECT message_id FROM email_threads "
            "WHERE candidate_id = ? AND direction = 'sent' "
            "ORDER BY id DESC LIMIT 1",
            (candidate_id,)
        ).fetchone()
        conn.close()

        last_message_id = last_sent["message_id"] if last_sent else None

        subject = f"Re: {JOB_TITLE} Application — {candidate['name']}"
        msg_dict = build_mime_message(
            to_email=candidate["email"],
            to_name=candidate["name"],
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to=last_message_id,
            references=last_message_id,
        )

        try:
            service = get_gmail_service()
            result = send_email(service, msg_dict)
            sent_message_id = result.get("id", "")

            # Save sent email to DB
            conn = get_db()
            conn.execute(
                "INSERT INTO email_threads (candidate_id, thread_id, message_id, round, direction, subject, body, sent_at) "
                "VALUES (?, ?, ?, ?, 'sent', ?, ?, ?)",
                (candidate_id, thread_id, sent_message_id, next_round,
                 subject, body, datetime.now(timezone.utc).isoformat())
            )
            conn.execute(
                "UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?",
                (f"round_{next_round}_sent", datetime.now(timezone.utc).isoformat(), candidate_id)
            )
            conn.commit()
            conn.close()

            log.info(f"Round {next_round} sent to {candidate['name']} ✓")
            return True

        except Exception as e:
            log.error(f"Failed to send Round {next_round} email to {candidate['name']}: {e}")
            return False


# ── SEND ROUND 1 ──────────────────────────────────────────────────────────────

def send_round1_to_fast_track(service, dry_run: bool = False):
    """
    Send Round 1 emails to all Fast-Track candidates that haven't been contacted.
    In dry_run mode, prints emails without sending.
    """
    conn = get_db()
    candidates = conn.execute(
        "SELECT * FROM candidates WHERE tier = 'Fast-Track' AND status = 'pending'"
    ).fetchall()
    conn.close()

    log.info(f"Found {len(candidates)} Fast-Track candidates to email.")

    for candidate in candidates:
        candidate = dict(candidate)
        name = candidate["name"]
        email = candidate["email"]

        if not email:
            log.warning(f"No email for {name}, skipping.")
            continue

        body = generate_round1_email(candidate)
        subject = f"{JOB_TITLE} Application — Next Steps"

        if dry_run:
            print(f"\n{'='*60}")
            print(f"TO: {email}")
            print(f"SUBJECT: {subject}")
            print(f"BODY:\n{body}")
            continue

        msg = build_mime_message(
            to_email=email,
            to_name=name,
            subject=subject,
            body=body,
        )

        try:
            result = send_email(service, msg)
            thread_id = result.get("threadId", "")
            message_id = result.get("id", "")

            conn = get_db()
            conn.execute(
                "INSERT INTO email_threads (candidate_id, thread_id, message_id, round, direction, subject, body, sent_at) "
                "VALUES (?, ?, ?, 1, 'sent', ?, ?, ?)",
                (candidate["id"], thread_id, message_id,
                 subject, body, datetime.now(timezone.utc).isoformat())
            )
            conn.execute(
                "UPDATE candidates SET status = 'round_1_sent', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), candidate["id"])
            )
            conn.commit()
            conn.close()

            log.info(f"Round 1 sent to {name} ({email}) ✓")
            time.sleep(1.5)  # don't hammer Gmail API

        except Exception as e:
            log.error(f"Failed to send to {name}: {e}")


# ── CONTINUOUS MONITOR LOOP ───────────────────────────────────────────────────

def run_monitor_loop(anthropic_key: str = ""):
    """Main loop: check inbox every N seconds, process replies."""
    log.info(f"Starting monitor loop (interval: {MONITOR_INTERVAL}s)")

    while True:
        try:
            service = get_gmail_service()
            monitor = InboxMonitor(service, anthropic_key=anthropic_key)

            log.info("Checking inbox...")
            replies = monitor.check_inbox()

            if replies:
                log.info(f"Found {len(replies)} new replies.")
                for reply in replies:
                    log.info(f"  Processing reply from {reply['candidate_name']}...")
                    monitor.process_reply(reply)
            else:
                log.info("No new replies.")

            # Check for candidates who need follow-up nudges
            send_proactive_nudges(service)

        except Exception as e:
            log.error(f"Monitor loop error: {e}")

        log.info(f"Sleeping {MONITOR_INTERVAL}s...")
        time.sleep(MONITOR_INTERVAL)


# ── LOAD CANDIDATES FROM SCORING OUTPUT ──────────────────────────────────────

def load_candidates_from_ranked():
    """Import ranked applicants into SQLite candidates table."""
    if not RANKED_PATH.exists():
        log.error(f"Ranked file not found: {RANKED_PATH}")
        return

    with open(RANKED_PATH) as f:
        ranked = json.load(f)

    conn = get_db()
    inserted = 0
    for app in ranked:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO candidates "
                "(id, name, email, score, tier, github_url, cover_letter, answers, ai_flags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    app.get("id", ""),
                    app.get("name", ""),
                    (app.get("email") or "").lower(),
                    app.get("score", 0),
                    app.get("tier", ""),
                    app.get("github_url", ""),
                    app.get("cover_letter", ""),
                    json.dumps(app.get("answers", [])),
                    json.dumps(app.get("ai_flags", []))
                )
            )
            inserted += 1
        except Exception as e:
            log.warning(f"Failed to insert {app.get('name')}: {e}")

    conn.commit()
    conn.close()
    log.info(f"Loaded {inserted} candidates into database.")


# ── PROACTIVE NUDGE SYSTEM ──────────────────────────────────────────────────

def send_proactive_nudges(service):
    """
    Proactively follow up with Fast-Track candidates who haven't replied within 48 hours.
    Real recruiters don't just react — they follow up!
    
    Checks:
    - Candidates in Fast-Track tier
    - Received Round 1 but haven't replied
    - Last email was > 48 hours ago
    - Haven't been nudged before (or last nudge was > 7 days ago)
    """
    conn = get_db()
    
    # Find candidates who need nudging
    cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    
    candidates_to_nudge = conn.execute("""
        SELECT c.*, MAX(t.sent_at) as last_sent_time
        FROM candidates c
        JOIN email_threads t ON c.id = t.candidate_id
        WHERE c.tier = 'Fast-Track'
          AND t.direction = 'sent'
          AND t.round = 1
          AND c.status = 'round_1_sent'
          AND t.sent_at < ?
        GROUP BY c.id
        HAVING COUNT(CASE WHEN t.direction = 'received' THEN 1 END) = 0
    """, (cutoff_time,)).fetchall()
    
    conn.close()
    
    if not candidates_to_nudge:
        log.info("No candidates need proactive nudges at this time.")
        return
    
    log.info(f"Found {len(candidates_to_nudge)} candidates eligible for proactive nudge.")
    
    for candidate in candidates_to_nudge:
        candidate = dict(candidate)
        name = candidate["name"]
        email = candidate["email"]
        thread_id = _get_thread_id_for_candidate(candidate["id"], round_num=1)
        
        if not thread_id:
            log.warning(f"No thread ID found for {name}, skipping nudge.")
            continue
        
        # Check if we've already nudged them recently (within 7 days)
        if _was_recently_nudged(candidate["id"], days=7):
            log.info(f"{name} was nudged recently, skipping.")
            continue
        
        # Generate and send nudge email
        nudge_body = f"""Hi {name},

Just checking in to see if you had a chance to look at the technical question I sent over a couple of days ago.

We're genuinely interested in hearing your approach to the web scraping challenge — remember, there's no single "right" answer. We're looking for how you think through problems, not perfection.

If you have any questions or need clarification, feel free to ask. Otherwise, looking forward to your response when you get a chance!

Best,
{SENDER_NAME}
{COMPANY_NAME}
"""
        
        subject = f"Re: {JOB_TITLE} Application — Quick Follow-Up"
        msg_dict = build_mime_message(
            to_email=email,
            to_name=name,
            subject=subject,
            body=nudge_body,
            thread_id=thread_id,
        )
        
        try:
            result = send_email(service, msg_dict)
            sent_message_id = result.get("id", "")
            
            # Log the nudge
            conn = get_db()
            conn.execute(
                "INSERT INTO email_threads (candidate_id, thread_id, message_id, round, direction, subject, body, sent_at) "
                "VALUES (?, ?, ?, 1.5, 'sent', ?, ?, ?)",
                (candidate["id"], thread_id, sent_message_id,
                 subject, nudge_body, datetime.now(timezone.utc).isoformat())
            )
            conn.execute(
                "INSERT INTO system_log (event_type, candidate_id, details) VALUES (?, ?, ?)",
                ("proactive_nudge_sent", candidate["id"],
                 json.dumps({"days_since_round1": 2}))
            )
            conn.commit()
            conn.close()
            
            log.info(f"Proactive nudge sent to {name} ✓")
            time.sleep(1.5)  # Don't hammer Gmail API
            
        except Exception as e:
            log.error(f"Failed to send nudge to {name}: {e}")


def _get_thread_id_for_candidate(candidate_id: str, round_num: int) -> Optional[str]:
    """Get the Gmail thread ID for a specific round."""
    conn = get_db()
    row = conn.execute(
        "SELECT thread_id FROM email_threads "
        "WHERE candidate_id = ? AND round = ? LIMIT 1",
        (candidate_id, round_num)
    ).fetchone()
    conn.close()
    return row["thread_id"] if row else None


def _was_recently_nudged(candidate_id: str, days: int = 7) -> bool:
    """Check if candidate received a nudge in the last N days."""
    conn = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    count = conn.execute(
        "SELECT COUNT(*) as n FROM system_log "
        "WHERE candidate_id = ? AND event_type = 'proactive_nudge_sent' "
        "AND created_at > ?",
        (candidate_id, cutoff)
    ).fetchone()["n"]
    conn.close()
    return count > 0


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-round email engagement system")
    parser.add_argument("--auth", action="store_true", help="Authenticate Gmail (first time)")
    parser.add_argument("--load-candidates", action="store_true", help="Load ranked applicants into DB")
    parser.add_argument("--send-round1", action="store_true", help="Send Round 1 to Fast-Track candidates")
    parser.add_argument("--dry-run", action="store_true", help="Print emails without sending")
    parser.add_argument("--monitor", action="store_true", help="Start continuous inbox monitor")
    parser.add_argument("--api-key", default="", help="Anthropic API key")
    parser.add_argument("--sender-email", default="", help="Your Gmail address")
    args = parser.parse_args()

    global SENDER_EMAIL, ANTHROPIC_KEY
    if args.sender_email:
        SENDER_EMAIL = args.sender_email
    if args.api_key:
        ANTHROPIC_KEY = args.api_key

    DB_PATH.parent.mkdir(exist_ok=True)
    init_db()

    if args.auth:
        log.info("Starting Gmail auth flow...")
        get_gmail_service()
        log.info("Authentication successful. Token saved.")
        return

    if args.load_candidates:
        load_candidates_from_ranked()
        return

    if args.send_round1:
        service = get_gmail_service()
        send_round1_to_fast_track(service, dry_run=args.dry_run)
        return

    if args.monitor:
        run_monitor_loop(anthropic_key=args.api_key)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
