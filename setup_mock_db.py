import sqlite3
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("data/recruitment.db")

def setup_mock_db():
    if not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True)
        
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Create tables based on C5/C6 schemas
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS candidates (
        id TEXT PRIMARY KEY,
        name TEXT,
        email TEXT,
        score REAL,
        tier TEXT,
        status TEXT,
        github_url TEXT,
        cover_letter TEXT,
        answers TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    
    CREATE TABLE IF NOT EXISTS email_threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id TEXT,
        round INTEGER,
        direction TEXT,
        body TEXT,
        sent_at TEXT,
        received_at TEXT
    );
    
    CREATE TABLE IF NOT EXISTS strikes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id TEXT,
        reason TEXT,
        details TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    
    CREATE TABLE IF NOT EXISTS system_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        candidate_id TEXT,
        details TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    
    # Clear old data if any
    conn.execute("DELETE FROM candidates")
    conn.execute("DELETE FROM email_threads")
    conn.execute("DELETE FROM strikes")
    conn.execute("DELETE FROM system_log")
    
    print("Populating fake candidates...")
    names = ["Alice Smith", "Bob Jones", "Charlie Brown", "Diana Prince", "Ethan Hunt", "Fiona Gallagher", 
             "George Costanza", "Hannah Abbott", "Ian Malcolm", "Jane Doe", "Kevin Space", "Laura Croft"]
    tiers_dist = ["Fast-Track"] * 15 + ["Consider"] * 45 + ["Reject"] * 40
    
    # Generate 100 candidates
    for i in range(1, 101):
        c_id = f"cand_{1000+i}"
        name = random.choice(names) + f" {i}"
        tier = random.choice(tiers_dist)
        
        # Assign realistic scores based on tier
        if tier == "Fast-Track":
            score = random.uniform(85, 98)
            status = random.choice(["round_1_sent", "round_2_evaluating", "pending"])
        elif tier == "Consider":
            score = random.uniform(55, 84)
            status = random.choice(["pending", "round_1_sent"])
        else:
            score = random.uniform(20, 54)
            status = "eliminated" if random.random() > 0.5 else "rejected"
            
        conn.execute(
            "INSERT INTO candidates (id, name, email, score, tier, status, github_url, cover_letter) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (c_id, name, f"{name.split()[0].lower()}@example.com", round(score, 1), tier, status, f"https://github.com/{name.split()[0].lower()}", "I am very interested in this AI role...")
        )
        
        # Add email threads for active ones
        if status in ["round_1_sent", "round_2_evaluating"]:
            conn.execute(
                "INSERT INTO email_threads (candidate_id, round, direction, body) VALUES (?, ?, ?, ?)",
                (c_id, 1, "sent", "Here is your technical assessment...")
            )
        if status == "round_2_evaluating":
            conn.execute(
                "INSERT INTO email_threads (candidate_id, round, direction, body) VALUES (?, ?, ?, ?)",
                (c_id, 1, "received", "Here is my Python script solving the problem...")
            )
            
        # Add some strikes for rejected/eliminated
        if status == "eliminated" and random.random() > 0.4:
            reasons = ["COPY_RING", "AI_GENERATED", "HIGH_SUSPICION"]
            conn.execute(
                "INSERT INTO strikes (candidate_id, reason, details) VALUES (?, ?, ?)",
                (c_id, random.choice(reasons), json.dumps({"evidence": "Matched 95% with cand_1021", "severity": "high"}))
            )
            
    # Add some system logs
    log_types = ["proactive_nudge_sent", "scraper_self_healed", "code_sandbox_executed", "weights_updated"]
    for i in range(25):
        conn.execute(
            "INSERT INTO system_log (event_type, candidate_id, details) VALUES (?, ?, ?)",
            (random.choice(log_types), f"cand_{random.randint(1001, 1100)}", json.dumps({"action": "success"}))
        )
        
    conn.commit()
    conn.close()
    print("Database populated! Launch the dashboard now.")

if __name__ == "__main__":
    setup_mock_db()
