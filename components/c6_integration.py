"""
COMPONENT 6: INTEGRATION — Everything Works Together as One System
==================================================================
Main orchestration layer. Runs all 5 components in a continuous loop.
Handles errors, restarts, and ensures no candidate falls through the cracks.

Pipeline flow:
  ACCESS → scrape applicants.json
  INTELLIGENCE → score + rank → ranked_applicants.json  
  ENGAGEMENT → send Round 1 → monitor inbox → send contextual replies
  ANTI-CHEAT → check each reply for AI/copy/timing flags → apply strikes
  SELF-LEARNING → analyze patterns every 10 candidates → update weights

Error handling:
  - Gmail down: queue emails locally, retry on reconnect
  - DB locked: exponential backoff
  - Scoring crash: skip candidate, log error, continue
  - Anti-cheat failure: log but don't strike (avoid false eliminates)

Deployment:
  - Run on any VPS (DigitalOcean, AWS, GCP)
  - Systemd service keeps it alive after restarts
  - State fully persisted in SQLite — safe to restart any time
  - No candidate falls through: 'pending' status = not yet processed

Usage:
  python c6_integration.py --run-pipeline  # One full cycle
  python c6_integration.py --daemon        # Continuous 24/7 operation
  python c6_integration.py --status        # Show system status
  python c6_integration.py --resume        # Resume after crash/restart
"""

import json
import time
import sqlite3
import logging
import argparse
import traceback
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Import all components
sys.path.insert(0, str(Path(__file__).parent))
from c1_access import IntershalaScraper, validate_github_profile
from c2_intelligence import ApplicantScorer, export_to_xlsx
from c3_engagement import (
    init_db, get_db, get_gmail_service, send_round1_to_fast_track,
    InboxMonitor, load_candidates_from_ranked
)
from c4_anticheat import AntiCheatRunner
from c5_learning import LearningOrchestrator, init_knowledge_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [C6-MAIN] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/system.log")
    ]
)
log = logging.getLogger(__name__)

DB_PATH    = Path("data/recruitment.db")
CONFIG_PATH = Path("data/config.json")

# Pipeline intervals
INBOX_CHECK_INTERVAL    = 300   # 5 minutes
ANTICHEAT_INTERVAL      = 1800  # 30 minutes
LEARNING_INTERVAL       = 3600  # 1 hour
HEALTH_CHECK_INTERVAL   = 60    # 1 minute


# ── PIPELINE STATE MACHINE ────────────────────────────────────────────────────

class PipelineState:
    """Tracks which stages have completed. Persisted to DB."""

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.commit()

    def get(self, key: str, default=None):
        row = self.conn.execute(
            "SELECT value FROM pipeline_state WHERE key = ?", (key,)
        ).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]
        return default

    def set(self, key: str, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO pipeline_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), datetime.now(timezone.utc).isoformat())
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


# ── ERROR RECOVERY ────────────────────────────────────────────────────────────

class RetryQueue:
    """In-memory + DB queue for failed operations."""

    def __init__(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS retry_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT,
                payload TEXT,
                attempts INTEGER DEFAULT 0,
                next_retry TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()

    def enqueue(self, operation: str, payload: dict, delay_seconds: int = 60):
        conn = sqlite3.connect(DB_PATH)
        next_retry = (
            datetime.now(timezone.utc).timestamp() + delay_seconds
        )
        conn.execute(
            "INSERT INTO retry_queue (operation, payload, next_retry) VALUES (?, ?, ?)",
            (operation, json.dumps(payload), str(next_retry))
        )
        conn.commit()
        conn.close()
        log.info(f"Queued retry: {operation} in {delay_seconds}s")

    def get_ready(self) -> list[dict]:
        """Get items ready for retry."""
        conn = sqlite3.connect(DB_PATH)
        now = str(datetime.now(timezone.utc).timestamp())
        rows = conn.execute(
            "SELECT * FROM retry_queue WHERE next_retry <= ? AND attempts < 5",
            (now,)
        ).fetchall()
        conn.close()
        return [{"id": r[0], "operation": r[1], "payload": json.loads(r[2]), "attempts": r[3]} for r in rows]

    def mark_done(self, item_id: int):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM retry_queue WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()

    def increment_attempts(self, item_id: int, next_delay: int = 300):
        conn = sqlite3.connect(DB_PATH)
        next_retry = str(datetime.now(timezone.utc).timestamp() + next_delay)
        conn.execute(
            "UPDATE retry_queue SET attempts = attempts + 1, next_retry = ? WHERE id = ?",
            (next_retry, item_id)
        )
        conn.commit()
        conn.close()


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

class RecruitmentPipeline:

    def __init__(self, config: dict):
        self.config         = config
        self.state          = PipelineState()
        self.retry_queue    = RetryQueue()
        self.api_key        = config.get("anthropic_api_key", "")
        self.scorer         = ApplicantScorer(use_llm=bool(self.api_key), anthropic_api_key=self.api_key)
        self.anticheat      = AntiCheatRunner(api_key=self.api_key)
        self.learner        = LearningOrchestrator(api_key=self.api_key)
        self._gmail_service = None

    def gmail(self):
        """Lazy-load Gmail service with reconnect on failure."""
        if not self._gmail_service:
            try:
                self._gmail_service = get_gmail_service()
            except Exception as e:
                log.error(f"Gmail connect failed: {e}")
                return None
        return self._gmail_service

    # ── STAGE 1: SCRAPE ──────────────────────────────────────────────────────

    def stage_scrape(self) -> bool:
        """Scrape Internshala if we haven't yet (or force-refresh)."""
        if self.state.get("scrape_complete"):
            log.info("Stage 1 (SCRAPE): Already complete. Skipping.")
            return True

        log.info("Stage 1 (SCRAPE): Starting...")
        try:
            scraper = IntershalaScraper(self.config)

            if not scraper.verify_auth():
                log.error("Stage 1 FAILED: Auth invalid. Run c1_access.py --setup first.")
                return False

            applicants = scraper.scrape_all_pages()

            if not applicants:
                log.error("Stage 1 FAILED: No applicants scraped.")
                return False

            # Validate GitHub profiles
            log.info(f"Validating GitHub profiles for {len(applicants)} applicants...")
            for i, app in enumerate(applicants):
                if app.get("github_url"):
                    app["github_info"] = validate_github_profile(app["github_url"])
                else:
                    app["github_info"] = {"valid": False, "reason": "no_url"}
                if (i + 1) % 50 == 0:
                    log.info(f"  GitHub validation: {i+1}/{len(applicants)}")

            output = Path("data/applicants.json")
            output.parent.mkdir(exist_ok=True)
            with open(output, "w") as f:
                json.dump(applicants, f, indent=2, ensure_ascii=False)

            self.state.set("scrape_complete", True)
            self.state.set("scrape_count", len(applicants))
            log.info(f"Stage 1 COMPLETE: {len(applicants)} applicants scraped.")
            return True

        except Exception as e:
            log.error(f"Stage 1 ERROR: {e}\n{traceback.format_exc()}")
            return False

    # ── STAGE 2: SCORE ───────────────────────────────────────────────────────

    def stage_score(self) -> bool:
        """Score and rank all applicants."""
        if self.state.get("score_complete"):
            log.info("Stage 2 (SCORE): Already complete. Skipping.")
            return True

        log.info("Stage 2 (SCORE): Starting...")
        input_path = Path("data/applicants.json")

        if not input_path.exists():
            log.error("Stage 2 FAILED: applicants.json not found. Run stage 1 first.")
            return False

        try:
            with open(input_path) as f:
                applicants = json.load(f)

            ranked = self.scorer.score_all(applicants)

            output = Path("data/ranked_applicants.json")
            with open(output, "w") as f:
                json.dump(ranked, f, indent=2, ensure_ascii=False)

            export_to_xlsx(ranked, Path("data/ranked_applicants.xlsx"))

            self.state.set("score_complete", True)
            tiers = {}
            for app in ranked:
                t = app.get("tier", "Unknown")
                tiers[t] = tiers.get(t, 0) + 1
            self.state.set("tier_distribution", tiers)
            log.info(f"Stage 2 COMPLETE: {tiers}")
            return True

        except Exception as e:
            log.error(f"Stage 2 ERROR: {e}\n{traceback.format_exc()}")
            return False

    # ── STAGE 3: LOAD DB ─────────────────────────────────────────────────────

    def stage_load_db(self) -> bool:
        """Load ranked candidates into SQLite for engagement tracking."""
        if self.state.get("db_loaded"):
            log.info("Stage 3 (LOAD DB): Already complete. Skipping.")
            return True

        try:
            load_candidates_from_ranked()
            self.state.set("db_loaded", True)
            log.info("Stage 3 COMPLETE: Candidates loaded to DB.")
            return True
        except Exception as e:
            log.error(f"Stage 3 ERROR: {e}")
            return False

    # ── STAGE 4: SEND ROUND 1 ────────────────────────────────────────────────

    def stage_send_round1(self) -> bool:
        """Send Round 1 emails to Fast-Track candidates."""
        if self.state.get("round1_sent"):
            log.info("Stage 4 (ROUND 1): Already sent. Skipping.")
            return True

        log.info("Stage 4 (ROUND 1): Sending emails to Fast-Track candidates...")
        service = self.gmail()
        if not service:
            log.error("Stage 4 FAILED: Gmail not available.")
            self.retry_queue.enqueue("send_round1", {}, delay_seconds=300)
            return False

        try:
            send_round1_to_fast_track(service)
            self.state.set("round1_sent", True)
            log.info("Stage 4 COMPLETE: Round 1 emails sent.")
            return True
        except Exception as e:
            log.error(f"Stage 4 ERROR: {e}")
            self.retry_queue.enqueue("send_round1", {}, delay_seconds=300)
            return False

    # ── STAGE 5: CONTINUOUS MONITORING ───────────────────────────────────────

    def run_inbox_check(self):
        """Check inbox for replies and process them."""
        service = self.gmail()
        if not service:
            log.warning("Gmail unavailable during inbox check. Will retry.")
            self._gmail_service = None  # Force reconnect next time
            return

        try:
            monitor = InboxMonitor(service, anthropic_key=self.api_key)
            replies = monitor.check_inbox()

            if replies:
                log.info(f"Found {len(replies)} new replies.")
                for reply in replies:
                    log.info(f"  Processing: {reply['candidate_name']}")
                    try:
                        monitor.process_reply(reply)
                        # Immediately run anti-cheat on this reply
                        self.run_anticheat_single(reply)
                    except Exception as e:
                        log.error(f"Failed to process reply from {reply['candidate_name']}: {e}")
            else:
                log.info("No new replies.")

        except Exception as e:
            log.error(f"Inbox check error: {e}")
            self._gmail_service = None  # Reconnect next time

    def run_anticheat_single(self, reply: dict):
        """Run anti-cheat on a single just-received reply."""
        try:
            self.anticheat.run_all_checks(check_only_new=True)
        except Exception as e:
            log.error(f"Anti-cheat check failed (non-fatal): {e}")

    def run_retry_queue(self):
        """Process any queued retries (e.g. failed emails)."""
        ready = self.retry_queue.get_ready()
        for item in ready:
            log.info(f"Retrying: {item['operation']} (attempt {item['attempts']+1})")
            success = False

            if item["operation"] == "send_round1":
                service = self.gmail()
                if service:
                    try:
                        send_round1_to_fast_track(service)
                        success = True
                    except Exception as e:
                        log.error(f"Retry failed: {e}")

            if success:
                self.retry_queue.mark_done(item["id"])
            else:
                # Exponential backoff: 5min, 15min, 45min, 2hr, 6hr
                delays = [300, 900, 2700, 7200, 21600]
                delay = delays[min(item["attempts"], len(delays)-1)]
                self.retry_queue.increment_attempts(item["id"], delay)

    # ── MAIN DAEMON LOOP ──────────────────────────────────────────────────────

    def run_daemon(self):
        """
        Continuous 24/7 operation.
        Separated concerns run on their own intervals.
        """
        log.info("="*60)
        log.info("RECRUITMENT PIPELINE DAEMON STARTED")
        log.info("="*60)

        last_inbox_check  = 0
        last_anticheat    = 0
        last_learning     = 0

        while True:
            now = time.time()

            # Health tick
            try:
                self._health_tick()
            except Exception as e:
                log.error(f"Health tick error: {e}")

            # Inbox check
            if now - last_inbox_check >= INBOX_CHECK_INTERVAL:
                try:
                    self.run_inbox_check()
                except Exception as e:
                    log.error(f"Inbox check crashed: {e}")
                last_inbox_check = now

            # Anti-cheat batch
            if now - last_anticheat >= ANTICHEAT_INTERVAL:
                try:
                    self.anticheat.run_all_checks(check_only_new=True)
                except Exception as e:
                    log.error(f"Anti-cheat batch crashed: {e}")
                last_anticheat = now

            # Self-learning
            if now - last_learning >= LEARNING_INTERVAL:
                try:
                    self.learner.run_analysis()
                except Exception as e:
                    log.error(f"Learning analysis crashed: {e}")
                last_learning = now

            # Retry queue
            try:
                self.run_retry_queue()
            except Exception as e:
                log.error(f"Retry queue error: {e}")

            time.sleep(HEALTH_CHECK_INTERVAL)

    def _health_tick(self):
        """Log a periodic health status line."""
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) as n FROM candidates").fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) as n FROM candidates WHERE status = 'pending'"
        ).fetchone()["n"]
        r1_sent = conn.execute(
            "SELECT COUNT(*) as n FROM candidates WHERE status LIKE 'round_%'"
        ).fetchone()["n"]
        eliminated = conn.execute(
            "SELECT COUNT(*) as n FROM candidates WHERE status = 'eliminated'"
        ).fetchone()["n"]
        conn.close()

        log.info(
            f"STATUS: total={total} | pending={pending} | "
            f"in_process={r1_sent} | eliminated={eliminated}"
        )

    def run_full_pipeline(self) -> bool:
        """Run all stages once (for initial setup or manual trigger)."""
        log.info("Running full pipeline...")
        stages = [
            ("SCRAPE",    self.stage_scrape),
            ("SCORE",     self.stage_score),
            ("LOAD_DB",   self.stage_load_db),
            ("ROUND_1",   self.stage_send_round1),
        ]
        for name, fn in stages:
            log.info(f"\n{'='*40}\nSTAGE: {name}\n{'='*40}")
            ok = fn()
            if not ok:
                log.error(f"Pipeline stopped at stage: {name}")
                return False

        log.info("\n" + "="*40 + "\nFull pipeline complete.\n" + "="*40)
        return True

    def show_status(self):
        """Print current system status."""
        print("\n" + "="*60)
        print("RECRUITMENT SYSTEM STATUS")
        print("="*60)

        # Pipeline state
        print("\n📋 PIPELINE STATE:")
        for key in ["scrape_complete", "score_complete", "db_loaded", "round1_sent"]:
            val = self.state.get(key)
            icon = "✅" if val else "⏳"
            print(f"  {icon} {key}: {val}")

        if not DB_PATH.exists():
            print("\n(Database not initialized yet)")
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # Candidate counts
        print("\n👥 CANDIDATES:")
        tiers = conn.execute(
            "SELECT tier, COUNT(*) as n FROM candidates GROUP BY tier"
        ).fetchall()
        for row in tiers:
            print(f"  {row['tier']:12s}: {row['n']}")

        # Status breakdown
        print("\n📊 STATUS BREAKDOWN:")
        statuses = conn.execute(
            "SELECT status, COUNT(*) as n FROM candidates GROUP BY status ORDER BY n DESC"
        ).fetchall()
        for row in statuses:
            print(f"  {row['status']:20s}: {row['n']}")

        # Email stats
        print("\n📧 EMAIL ACTIVITY:")
        email_stats = conn.execute("""
            SELECT direction, round, COUNT(*) as n 
            FROM email_threads 
            GROUP BY direction, round 
            ORDER BY round, direction
        """).fetchall()
        for row in email_stats:
            print(f"  Round {row['round']} {row['direction']:8s}: {row['n']}")

        # Strikes
        print("\n⚠️ ANTI-CHEAT:")
        strikes = conn.execute(
            "SELECT reason, COUNT(*) as n FROM strikes GROUP BY reason"
        ).fetchall()
        for row in strikes:
            print(f"  {row['reason']:20s}: {row['n']}")
        eliminated = conn.execute(
            "SELECT COUNT(*) as n FROM candidates WHERE status = 'eliminated'"
        ).fetchone()["n"]
        print(f"  ELIMINATED: {eliminated}")

        # Retry queue
        retry_items = conn.execute("SELECT COUNT(*) as n FROM retry_queue").fetchone()
        if retry_items["n"] > 0:
            print(f"\n🔄 RETRY QUEUE: {retry_items['n']} pending items")

        conn.close()
        print("="*60)


# ── DEPLOYMENT HELPERS ────────────────────────────────────────────────────────

SYSTEMD_SERVICE = """[Unit]
Description=Recruitment Automation Pipeline
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={workdir}
ExecStart=/usr/bin/python3 {workdir}/components/c6_integration.py --daemon
Restart=always
RestartSec=30
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def generate_systemd_service():
    """Generate a systemd service file for 24/7 deployment."""
    import os
    workdir = Path.cwd().resolve()
    user = os.getenv("USER", "ubuntu")
    service = SYSTEMD_SERVICE.format(user=user, workdir=workdir)
    path = Path("deployment/recruitment.service")
    path.parent.mkdir(exist_ok=True)
    with open(path, "w") as f:
        f.write(service)
    print(f"\nSystemd service file written to: {path}")
    print("\nTo deploy:")
    print(f"  sudo cp {path} /etc/systemd/system/")
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable recruitment")
    print("  sudo systemctl start recruitment")
    print("  sudo journalctl -u recruitment -f  # tail logs")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Recruitment Pipeline Orchestrator")
    parser.add_argument("--run-pipeline", action="store_true",
                        help="Run full pipeline once (scrape → score → email)")
    parser.add_argument("--daemon",        action="store_true",
                        help="Start continuous 24/7 monitoring daemon")
    parser.add_argument("--status",        action="store_true",
                        help="Show current system status")
    parser.add_argument("--resume",        action="store_true",
                        help="Resume monitoring (skip already-completed stages)")
    parser.add_argument("--generate-systemd", action="store_true",
                        help="Generate systemd service file for deployment")
    parser.add_argument("--reset-stage",   type=str,
                        help="Reset a specific stage so it runs again (e.g. 'score_complete')")
    parser.add_argument("--api-key",       default="",
                        help="Anthropic API key")
    args = parser.parse_args()

    # Ensure directories exist
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)

    if args.generate_systemd:
        generate_systemd_service()
        return

    if not CONFIG_PATH.exists():
        log.error(f"Config not found: {CONFIG_PATH}. Run c1_access.py --setup first.")
        if not args.status:
            return

    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = json.load(f)

    if args.api_key:
        config["anthropic_api_key"] = args.api_key

    # Initialize DBs
    init_db()
    init_knowledge_db()

    pipeline = RecruitmentPipeline(config)

    if args.reset_stage:
        pipeline.state.set(args.reset_stage, False)
        log.info(f"Reset stage: {args.reset_stage}")
        return

    if args.status:
        pipeline.show_status()
        return

    if args.run_pipeline:
        pipeline.run_full_pipeline()
        return

    if args.daemon or args.resume:
        # In resume mode, skip completed stages then go continuous
        if not pipeline.state.get("scrape_complete"):
            pipeline.stage_scrape()
        if not pipeline.state.get("score_complete"):
            pipeline.stage_score()
        if not pipeline.state.get("db_loaded"):
            pipeline.stage_load_db()
        if not pipeline.state.get("round1_sent"):
            pipeline.stage_send_round1()

        pipeline.run_daemon()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
