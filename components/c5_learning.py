"""
COMPONENT 5: SELF-LEARNING — The System Gets Smarter Over Time
==============================================================
Logs every interaction. After every 10 candidates, auto-analyzes patterns.
Updates scoring weights based on discovered patterns.
Answers natural-language questions about the candidate pool.

Usage:
  python c5_learning.py --analyze        # Run analysis on current data
  python c5_learning.py --query "Which 3 candidates showed the most original thinking?"
  python c5_learning.py --query "What % of candidates suggested Selenium?"
  python c5_learning.py --update-weights # Update scoring model with learned patterns
"""

import json
import sqlite3
import logging
import argparse
import datetime
from pathlib import Path
from typing import Any
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [C5-LEARN] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

DB_PATH          = Path("data/recruitment.db")
INSIGHTS_PATH    = Path("data/insights.json")
WEIGHTS_PATH     = Path("data/learned_weights.json")
ANTHROPIC_KEY    = ""

# Analysis triggers every N candidates processed
ANALYSIS_BATCH_SIZE = 10


# ── KNOWLEDGE BASE SCHEMA ─────────────────────────────────────────────────────

KNOWLEDGE_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_run INTEGER,           -- which batch this came from
    candidate_count INTEGER,        -- how many candidates at time of analysis
    insight_type TEXT,              -- 'pattern', 'question_quality', 'ai_detection', 'approach_trend'
    insight_text TEXT,              -- human-readable insight
    data_json TEXT,                 -- structured data
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scoring_weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT UNIQUE,           -- 'answer_quality', 'technical_skills', etc.
    weight REAL,
    learned_from_run INTEGER,
    notes TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approach_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT,                    -- e.g. 'uses_selenium', 'mentions_cookies', 'tries_api'
    count INTEGER DEFAULT 0,
    success_rate REAL,               -- % of this approach → Fast-Track
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def init_knowledge_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(KNOWLEDGE_BASE_SCHEMA)
    conn.commit()
    conn.close()


# ── DATA AGGREGATOR ───────────────────────────────────────────────────────────

class DataAggregator:
    """Collects and structures data for analysis."""

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def get_candidate_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) as n FROM candidates").fetchone()["n"]

    def get_processed_count(self) -> int:
        """Candidates who have at least completed Round 1."""
        return self.conn.execute(
            "SELECT COUNT(DISTINCT candidate_id) as n FROM email_threads WHERE direction = 'received'"
        ).fetchone()["n"]

    def get_all_interactions(self) -> list[dict]:
        """All email threads with candidate context."""
        rows = self.conn.execute("""
            SELECT 
                t.candidate_id, t.round, t.direction, t.body, t.sent_at, t.received_at,
                c.name, c.email, c.score, c.tier, c.status,
                c.cover_letter, c.answers
            FROM email_threads t
            JOIN candidates c ON t.candidate_id = c.id
            ORDER BY t.candidate_id, t.round, t.direction
        """).fetchall()
        return [dict(r) for r in rows]

    def get_round_performance_correlation(self) -> list[dict]:
        """
        For each candidate who completed Round 2:
        Compare their Round 1 answer quality vs Round 2 answer quality.
        Returns correlation data for learning what R1 predicted R2.
        """
        rows = self.conn.execute("""
            SELECT 
                r1.candidate_id,
                r1.body as r1_answer,
                r2.body as r2_answer,
                c.score, c.tier
            FROM email_threads r1
            JOIN email_threads r2 ON (
                r1.candidate_id = r2.candidate_id 
                AND r2.direction = 'received'
                AND r2.round = 2
            )
            JOIN candidates c ON r1.candidate_id = c.id
            WHERE r1.direction = 'received' AND r1.round = 1
        """).fetchall()
        return [dict(r) for r in rows]

    def get_approach_statistics(self) -> dict:
        """
        Extract what technical approaches candidates mentioned.
        Returns counts and their tier outcomes.
        """
        rows = self.conn.execute("""
            SELECT t.body, c.tier, c.score
            FROM email_threads t
            JOIN candidates c ON t.candidate_id = c.id
            WHERE t.direction = 'received' AND t.round = 1
        """).fetchall()

        approach_keywords = {
            "selenium": ["selenium", "webdriver"],
            "playwright": ["playwright"],
            "cookies": ["cookie", "session", "httponly"],
            "cdp": ["cdp", "chrome devtools", "devtools protocol"],
            "proxy": ["proxy", "proxies", "mitm", "mitmproxy"],
            "api": ["api", "endpoint", "json", "xhr"],
            "beautifulsoup": ["beautifulsoup", "bs4", "beautiful soup"],
            "requests": ["requests library", "http request", "urllib"],
            "ocr_captcha": ["ocr", "captcha bypass", "2captcha", "anti-captcha"],
            "manual": ["manual", "human", "click myself"],
        }

        stats = {}
        for keyword, patterns in approach_keywords.items():
            matching = []
            for row in rows:
                text = (row["body"] or "").lower()
                if any(p in text for p in patterns):
                    matching.append({"tier": row["tier"], "score": row["score"]})

            if matching:
                fast_track = sum(1 for m in matching if m["tier"] == "Fast-Track")
                avg_score = sum(m["score"] or 0 for m in matching) / len(matching)
                stats[keyword] = {
                    "mention_count": len(matching),
                    "mention_pct": 100 * len(matching) / max(1, len(rows)),
                    "fast_track_count": fast_track,
                    "fast_track_rate": 100 * fast_track / len(matching),
                    "avg_score": round(avg_score, 1)
                }

        return {"approaches": stats, "total_r1_replies": len(rows)}

    def get_score_breakdown_trends(self) -> dict:
        """What's the average score for each scoring component across all candidates?"""
        rows = self.conn.execute(
            "SELECT score FROM candidates WHERE score IS NOT NULL"
        ).fetchall()
        scores = [r["score"] for r in rows if r["score"] is not None]

        if not scores:
            return {}

        return {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 1),
            "median_score": round(sorted(scores)[len(scores) // 2], 1),
            "p90_score": round(sorted(scores)[int(len(scores) * 0.9)], 1),
            "p10_score": round(sorted(scores)[int(len(scores) * 0.1)], 1),
        }

    def get_ai_detection_patterns(self) -> dict:
        """What AI fingerprints are most common? Which are false positives?"""
        rows = self.conn.execute("""
            SELECT s.reason, s.details, c.tier, c.score
            FROM strikes s
            JOIN candidates c ON s.candidate_id = c.id
            WHERE s.reason = 'AI_GENERATED'
        """).fetchall()

        patterns = {}
        for row in rows:
            try:
                details = json.loads(row["details"])
                evidence = details.get("evidence", [])
                for e in evidence:
                    patterns[e] = patterns.get(e, 0) + 1
            except Exception:
                pass

        return {
            "total_ai_flags": len(rows),
            "pattern_frequency": dict(sorted(patterns.items(), key=lambda x: x[1], reverse=True)),
        }


# ── LLM ANALYZER ─────────────────────────────────────────────────────────────

class LLMAnalyzer:
    """Uses Claude to generate insights from raw data."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def analyze_batch(self, data: dict, run_number: int) -> list[dict]:
        """
        Send aggregated data to Claude and get structured insights back.
        Returns list of insight dicts.
        """
        if not self.api_key:
            log.warning("No API key — generating heuristic insights only.")
            return self._heuristic_insights(data)

        prompt = f"""You are analyzing data from an automated recruitment system that has processed {data.get('processed_count', 0)} candidate email conversations.

Here is the aggregated data:

APPROACH STATISTICS (what techniques candidates mentioned):
{json.dumps(data.get('approaches', {}), indent=2)}

SCORE DISTRIBUTION:
{json.dumps(data.get('score_trends', {}), indent=2)}

AI DETECTION PATTERNS:
{json.dumps(data.get('ai_patterns', {}), indent=2)}

SAMPLE ROUND 1 ANSWERS (first 500 chars each):
{json.dumps([r.get('r1_answer', '')[:500] for r in data.get('correlations', [])[:5]], indent=2)}

Generate a structured analysis with these EXACT keys in your JSON response:
{{
  "patterns": [
    {{"insight": "...", "evidence": "...", "actionable": "..."}}
  ],
  "question_quality": {{"best_differentiating_questions": [], "worst_questions": [], "recommendation": ""}},
  "approach_trends": {{"most_common": "", "highest_success_rate": "", "surprising_finding": ""}},
  "ai_detection_updates": {{"new_phrases_to_add": [], "false_positive_phrases": []}},
  "scoring_recommendations": {{"adjust_weights": {{}}, "reasoning": ""}},
  "memorable_candidates": [
    {{"description": "...", "why_notable": "..."}}
  ]
}}

Be specific. Use actual numbers from the data. Return ONLY the JSON object."""

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=60
            )
            text = resp.json()["content"][0]["text"]
            # Strip markdown fences if present
            text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            analysis = json.loads(text)

            # Convert to flat insight list
            insights = []
            for pattern in analysis.get("patterns", []):
                insights.append({
                    "type": "pattern",
                    "text": pattern.get("insight"),
                    "evidence": pattern.get("evidence"),
                    "actionable": pattern.get("actionable"),
                    "run": run_number
                })

            trend = analysis.get("approach_trends", {})
            if trend.get("surprising_finding"):
                insights.append({
                    "type": "approach_trend",
                    "text": trend["surprising_finding"],
                    "run": run_number
                })

            ai_updates = analysis.get("ai_detection_updates", {})
            if ai_updates.get("new_phrases_to_add"):
                insights.append({
                    "type": "ai_detection",
                    "text": f"New AI phrases discovered: {ai_updates['new_phrases_to_add']}",
                    "data": ai_updates,
                    "run": run_number
                })

            # Store scoring recommendations
            score_recs = analysis.get("scoring_recommendations", {})
            if score_recs.get("adjust_weights"):
                insights.append({
                    "type": "weight_update",
                    "text": score_recs.get("reasoning"),
                    "data": score_recs["adjust_weights"],
                    "run": run_number
                })

            return insights, analysis

        except Exception as e:
            log.error(f"LLM analysis failed: {e}")
            return self._heuristic_insights(data), {}

    def answer_query(self, question: str) -> str:
        """Answer a natural language question about the candidate pool."""
        if not self.api_key:
            return "LLM API key required for natural language queries."

        # Gather context
        agg = DataAggregator()
        approach_stats = agg.get_approach_statistics()
        score_trends   = agg.get_score_breakdown_trends()
        agg.close()

        # Load recent insights
        insights = []
        if INSIGHTS_PATH.exists():
            with open(INSIGHTS_PATH) as f:
                insights = json.load(f)

        context = f"""
QUESTION: {question}

APPROACH STATISTICS:
{json.dumps(approach_stats, indent=2)}

SCORE DISTRIBUTION:
{json.dumps(score_trends, indent=2)}

RECENT INSIGHTS (from previous analysis runs):
{json.dumps(insights[-10:], indent=2)}
"""

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 500,
                    "messages": [{
                        "role": "user",
                        "content": f"You are analyzing recruitment data. Answer this question using the data provided:\n\n{context}"
                    }]
                },
                timeout=30
            )
            return resp.json()["content"][0]["text"]
        except Exception as e:
            return f"Query failed: {e}"

    def _heuristic_insights(self, data: dict) -> list[dict]:
        """Generate basic insights without LLM."""
        insights = []
        approaches = data.get("approaches", {}).get("approaches", {})

        if approaches:
            most_common = max(approaches, key=lambda k: approaches[k]["mention_count"])
            insights.append({
                "type": "approach_trend",
                "text": f"Most common first approach: {most_common} ({approaches[most_common]['mention_count']} candidates, {approaches[most_common]['mention_pct']:.0f}%)",
            })

            best = max(approaches, key=lambda k: approaches[k]["fast_track_rate"])
            insights.append({
                "type": "approach_trend",
                "text": f"Highest Fast-Track rate among approaches: {best} ({approaches[best]['fast_track_rate']:.0f}%)",
            })

        return insights


# ── WEIGHT UPDATER ────────────────────────────────────────────────────────────

def update_scoring_weights(analysis: dict):
    """
    Apply learned weight adjustments to the scoring model.
    Saved to data/learned_weights.json — picked up by C2 on next run.
    """
    weight_recs = analysis.get("scoring_recommendations", {}).get("adjust_weights", {})
    if not weight_recs:
        return

    # Load current weights (or defaults)
    if WEIGHTS_PATH.exists():
        with open(WEIGHTS_PATH) as f:
            current = json.load(f)
    else:
        current = {
            "answer_quality":   40,
            "technical_skills": 20,
            "github_quality":   20,
            "ai_penalty_max":   10,
            "completeness":     10,
        }

    # Apply adjustments (LLM suggests percentage changes)
    for component, adjustment in weight_recs.items():
        if component in current:
            if isinstance(adjustment, (int, float)):
                current[component] = max(0, current[component] + adjustment)
            elif isinstance(adjustment, str) and "increase" in adjustment.lower():
                current[component] = min(50, current[component] * 1.1)
            elif isinstance(adjustment, str) and "decrease" in adjustment.lower():
                current[component] = max(5, current[component] * 0.9)

    # Normalize to sum to 100
    total = sum(current.values())
    if total > 0:
        current = {k: round(v * 100 / total, 1) for k, v in current.items()}

    current["updated_at"] = datetime.datetime.now().isoformat()
    current["source"] = "self_learning_analysis"

    with open(WEIGHTS_PATH, "w") as f:
        json.dump(current, f, indent=2)

    log.info(f"Updated scoring weights: {current}")


# ── MAIN ANALYSIS ORCHESTRATOR ────────────────────────────────────────────────

class LearningOrchestrator:

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.llm = LLMAnalyzer(api_key)

    def should_run_analysis(self) -> bool:
        """Check if we've processed enough new candidates since last analysis."""
        if not DB_PATH.exists():
            return False

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        last_run = conn.execute(
            "SELECT MAX(analysis_run) as n FROM knowledge_base"
        ).fetchone()
        last_run_n = last_run["n"] or 0

        last_run_count = conn.execute(
            "SELECT candidate_count FROM knowledge_base WHERE analysis_run = ? LIMIT 1",
            (last_run_n,)
        ).fetchone()
        last_count = last_run_count["candidate_count"] if last_run_count else 0

        current_processed = conn.execute(
            "SELECT COUNT(DISTINCT candidate_id) as n FROM email_threads WHERE direction = 'received'"
        ).fetchone()["n"]
        conn.close()

        return (current_processed - last_count) >= ANALYSIS_BATCH_SIZE

    def run_analysis(self, force: bool = False):
        """Run full analysis cycle."""
        if not force and not self.should_run_analysis():
            log.info(f"Less than {ANALYSIS_BATCH_SIZE} new candidates since last analysis. Skipping.")
            return

        log.info("Running self-learning analysis...")
        agg = DataAggregator()

        data = {
            "processed_count": agg.get_processed_count(),
            "total_count":     agg.get_candidate_count(),
            "approaches":      agg.get_approach_statistics(),
            "score_trends":    agg.get_score_breakdown_trends(),
            "ai_patterns":     agg.get_ai_detection_patterns(),
            "correlations":    agg.get_round_performance_correlation()[:20],  # sample
        }
        agg.close()

        log.info(f"Analyzing {data['processed_count']} processed candidates...")

        # Get current run number
        conn = sqlite3.connect(DB_PATH)
        last = conn.execute("SELECT MAX(analysis_run) as n FROM knowledge_base").fetchone()["n"] or 0
        run_number = last + 1
        conn.close()

        # Run LLM analysis
        insights, full_analysis = self.llm.analyze_batch(data, run_number)

        # Save insights to DB
        conn = sqlite3.connect(DB_PATH)
        for insight in insights:
            conn.execute(
                "INSERT INTO knowledge_base (analysis_run, candidate_count, insight_type, insight_text, data_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    run_number,
                    data["processed_count"],
                    insight.get("type", "pattern"),
                    insight.get("text", ""),
                    json.dumps(insight.get("data", {}))
                )
            )
        conn.commit()
        conn.close()

        # Save to JSON for easy querying
        all_insights = []
        if INSIGHTS_PATH.exists():
            with open(INSIGHTS_PATH) as f:
                all_insights = json.load(f)
        all_insights.extend(insights)
        with open(INSIGHTS_PATH, "w") as f:
            json.dump(all_insights, f, indent=2)

        # Update scoring weights based on learnings
        if full_analysis:
            update_scoring_weights(full_analysis)

        # Print summary
        print("\n" + "="*60)
        print(f"SELF-LEARNING ANALYSIS — Run #{run_number}")
        print(f"Based on {data['processed_count']} processed candidates")
        print("="*60)

        for insight in insights:
            print(f"\n[{insight.get('type', 'insight').upper()}]")
            print(f"  {insight.get('text', '')}")
            if insight.get("actionable"):
                print(f"  → {insight['actionable']}")

        print("\n" + "="*60)

        # Approach statistics
        approaches = data["approaches"].get("approaches", {})
        print("\nAPPROACH BREAKDOWN:")
        for approach, stats in sorted(approaches.items(), key=lambda x: x[1]["mention_count"], reverse=True):
            print(
                f"  {approach:20s}: {stats['mention_count']:3d} mentions "
                f"({stats['mention_pct']:.0f}%), "
                f"Fast-Track rate: {stats['fast_track_rate']:.0f}%"
            )

        return insights


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Self-learning analysis system")
    parser.add_argument("--analyze", action="store_true", help="Run batch analysis now")
    parser.add_argument("--force", action="store_true", help="Force analysis even if <10 new candidates")
    parser.add_argument("--query", type=str, help="Ask a natural language question about candidates")
    parser.add_argument("--update-weights", action="store_true", help="Re-apply weight updates")
    parser.add_argument("--api-key", default="", help="Anthropic API key")
    parser.add_argument("--show-insights", action="store_true", help="Print all stored insights")
    args = parser.parse_args()

    init_knowledge_db()
    orchestrator = LearningOrchestrator(api_key=args.api_key)

    if args.analyze:
        orchestrator.run_analysis(force=args.force)

    elif args.query:
        print(f"\nQuery: {args.query}\n")
        answer = orchestrator.llm.answer_query(args.query)
        print(f"Answer:\n{answer}")

    elif args.show_insights:
        if INSIGHTS_PATH.exists():
            with open(INSIGHTS_PATH) as f:
                insights = json.load(f)
            print(f"\n{len(insights)} total insights stored:\n")
            for i, insight in enumerate(insights[-20:], 1):
                print(f"{i}. [{insight.get('type')}] {insight.get('text')}")
        else:
            print("No insights stored yet. Run --analyze first.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
