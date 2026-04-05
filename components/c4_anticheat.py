"""
COMPONENT 4: ANTI-CHEAT — Detecting AI-Generated or Copied Responses
=====================================================================
Three detection methods:
  1. AI Similarity: Compare candidate response vs fresh LLM output on same question
  2. Cross-Candidate: Compare every response vs every other (copy ring detection)
  3. Timing Analysis: Flag suspiciously fast or inhumanly polished replies

Strike System:
  Each flag = 1 strike. 3 strikes = auto-eliminated.
  Strikes are logged to DB with evidence.

Usage:
  python c4_anticheat.py --check-all     # Run all checks on all email replies
  python c4_anticheat.py --check-new     # Check only replies received since last run
  python c4_anticheat.py --report        # Show current strike board
"""

import json
import time
import sqlite3
import logging
import argparse
import re
import math
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from collections import defaultdict
import requests

from .anticheat.detector import analyze_batch, add_strike
from .anticheat.similarity import get_embedding, cosine_similarity, phrase_overlap_score
from .anticheat.structure import extract_structure, structure_similarity
from .anticheat.timing import timing_analysis
from .anticheat.copyring import detect_copy_rings
from .anticheat.report import generate_explanation, print_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [C4-CHEAT] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

DB_PATH       = Path("data/recruitment.db")
ANTHROPIC_KEY = ""

# Thresholds
AI_SIMILARITY_THRESHOLD    = 0.70  # above this → AI_GENERATED strike (lowered to catch more)
COPY_RING_THRESHOLD        = 0.65  # above this between two candidates → COPY_RING strike
TIMING_SUSPICIOUS_SECONDS  = 120   # under 2 minutes for 3+ paragraph reply → suspicious
TIMING_PARANOID_SECONDS    = 30    # under 30 seconds → nearly certain auto-paste


# ── EMBEDDINGS & SIMILARITY ───────────────────────────────────────────────────

def get_embedding(text: str, api_key: str) -> Optional[list[float]]:
    """
    Get text embedding from Anthropic (or OpenAI).
    We use a simple TF-IDF-based approach as fallback if no API key.
    """
    if not api_key:
        return _tfidf_vector(text)

    # Anthropic doesn't have an embeddings endpoint yet — use OpenAI's if available
    # or fall back to our custom implementation
    # (In production, you'd use OpenAI text-embedding-3-small or sentence-transformers)
    return _tfidf_vector(text)


def _tfidf_vector(text: str) -> list[float]:
    """
    Simple TF-IDF-style vector using character n-grams.
    Not as good as dense embeddings but works offline with no API.
    Vocabulary: top 500 trigrams from the text itself + common English trigrams.
    """
    text = text.lower()
    # Remove punctuation
    text = re.sub(r'[^\w\s]', '', text)
    words = text.split()

    # Word unigrams
    word_freq = defaultdict(int)
    for word in words:
        word_freq[word] += 1

    # Word bigrams
    for i in range(len(words) - 1):
        bigram = words[i] + "_" + words[i+1]
        word_freq[bigram] += 0.7

    # TF normalize
    total = sum(word_freq.values()) or 1
    vector = {k: v / total for k, v in word_freq.items()}
    return vector  # returns dict, not list — handled in cosine_similarity


def cosine_similarity(vec_a, vec_b) -> float:
    """
    Compute cosine similarity between two vectors.
    Accepts both list[float] and dict{term: weight} formats.
    """
    if isinstance(vec_a, dict) and isinstance(vec_b, dict):
        # Sparse dict format (TF-IDF style)
        shared_keys = set(vec_a.keys()) & set(vec_b.keys())
        if not shared_keys:
            return 0.0

        dot = sum(vec_a[k] * vec_b[k] for k in shared_keys)
        mag_a = math.sqrt(sum(v**2 for v in vec_a.values()))
        mag_b = math.sqrt(sum(v**2 for v in vec_b.values()))

        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    elif isinstance(vec_a, list) and isinstance(vec_b, list):
        # Dense float format
        if len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag_a = math.sqrt(sum(a**2 for a in vec_a))
        mag_b = math.sqrt(sum(b**2 for b in vec_b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    return 0.0


def phrase_overlap_score(text_a: str, text_b: str) -> float:
    """
    Count exact phrase matches (5+ word sequences).
    Normalizes by the shorter text's phrase count.
    This catches copy-pasted blocks that embeddings might miss.
    """
    def get_phrases(text: str, n: int = 5) -> set[str]:
        words = text.lower().split()
        return {" ".join(words[i:i+n]) for i in range(len(words) - n + 1)}

    phrases_a = get_phrases(text_a)
    phrases_b = get_phrases(text_b)

    if not phrases_a or not phrases_b:
        return 0.0

    shared = phrases_a & phrases_b
    shorter = min(len(phrases_a), len(phrases_b))
    return len(shared) / shorter if shorter > 0 else 0.0


def structural_similarity(text_a: str, text_b: str) -> float:
    """
    Compare structural patterns: paragraph count, sentence lengths, bullet structure.
    Two AI-generated answers often have identical structure even when words differ.
    """
    def get_structure(text: str) -> dict:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        bullets = re.findall(r'^[-•*]\s+', text, re.MULTILINE)
        return {
            "para_count": len(paragraphs),
            "sent_count": len(sentences),
            "avg_sent_len": sum(len(s.split()) for s in sentences) / max(1, len(sentences)),
            "bullet_count": len(bullets),
            "para_lengths": [len(p.split()) for p in paragraphs[:5]],
        }

    sa = get_structure(text_a)
    sb = get_structure(text_b)

    score = 0
    checks = 0

    # Same paragraph count?
    if sa["para_count"] == sb["para_count"] and sa["para_count"] > 0:
        score += 1
    checks += 1

    # Similar sentence count (within 20%)?
    if sa["sent_count"] > 0 and sb["sent_count"] > 0:
        ratio = min(sa["sent_count"], sb["sent_count"]) / max(sa["sent_count"], sb["sent_count"])
        score += ratio
    checks += 1

    # Similar avg sentence length (within 15%)?
    if sa["avg_sent_len"] > 0 and sb["avg_sent_len"] > 0:
        ratio = min(sa["avg_sent_len"], sb["avg_sent_len"]) / max(sa["avg_sent_len"], sb["avg_sent_len"])
        score += ratio
    checks += 1

    # Same bullet count?
    if sa["bullet_count"] == sb["bullet_count"]:
        score += 1
    checks += 1

    return score / checks if checks > 0 else 0.0


# ── DETECTION METHOD 1: AI SIMILARITY ────────────────────────────────────────

class AIDetector:
    """
    Compare candidate response against a freshly generated LLM response
    to the same question. High similarity → likely AI-generated.
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._llm_cache = {}

    def check(self, question: str, candidate_answer: str) -> dict:
        """
        Returns:
          {
            "is_ai": bool,
            "similarity_score": float,
            "method": str,
            "evidence": list[str]
          }
        """
        if not candidate_answer or len(candidate_answer.split()) < 10:
            return {
                "is_ai": False,
                "similarity_score": 0.0,
                "method": "skipped_too_short",
                "evidence": ["answer too short to analyze"]
            }

        # Generate a fresh LLM answer to the same question
        llm_answer = self._generate_llm_answer(question)

        if not llm_answer:
            # Can't compare without LLM answer — fall back to phrase/structure only
            return self._check_without_llm(candidate_answer)

        # Three similarity measures
        vec_candidate = _tfidf_vector(candidate_answer)
        vec_llm       = _tfidf_vector(llm_answer)

        vocab_sim    = cosine_similarity(vec_candidate, vec_llm)
        phrase_sim   = phrase_overlap_score(candidate_answer, llm_answer)
        struct_sim   = structural_similarity(candidate_answer, llm_answer)

        # Weighted composite
        composite = (vocab_sim * 0.4) + (phrase_sim * 0.35) + (struct_sim * 0.25)

        evidence = []
        if vocab_sim > 0.5:
            evidence.append(f"vocabulary overlap: {vocab_sim:.2%}")
        if phrase_sim > 0.3:
            evidence.append(f"exact phrase matches: {phrase_sim:.2%}")
        if struct_sim > 0.7:
            evidence.append(f"identical structure: {struct_sim:.2%}")

        is_ai = composite > AI_SIMILARITY_THRESHOLD

        return {
            "is_ai": is_ai,
            "similarity_score": round(composite, 4),
            "vocab_similarity": round(vocab_sim, 4),
            "phrase_similarity": round(phrase_sim, 4),
            "structural_similarity": round(struct_sim, 4),
            "method": "llm_comparison",
            "evidence": evidence,
        }

    def _generate_llm_answer(self, question: str) -> Optional[str]:
        """Ask Claude/GPT the same question to get a baseline answer."""
        cache_key = hashlib.md5(question.encode()).hexdigest()
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]

        if not self.api_key:
            return None

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
                        "content": f"Answer this question as if you were a software engineering candidate applying for an internship:\n\n{question}\n\nWrite a natural, 2-3 paragraph answer."
                    }]
                },
                timeout=20
            )
            answer = resp.json()["content"][0]["text"]
            self._llm_cache[cache_key] = answer
            return answer
        except Exception as e:
            log.warning(f"LLM answer generation failed: {e}")
            return None

    def _check_without_llm(self, answer: str) -> dict:
        """Check for AI signals without a comparison answer."""
        from .c2_intelligence import AI_PHRASES
        answer_lower = answer.lower()
        matched = [p for p in AI_PHRASES if p in answer_lower]

        phrase_density = len(matched) / max(1, len(answer.split()) / 100)
        score = min(1.0, phrase_density * 0.3 + (len(matched) * 0.05))

        return {
            "is_ai": score > 0.4,
            "similarity_score": round(score, 4),
            "method": "phrase_only",
            "evidence": [f"AI phrase match: '{p}'" for p in matched[:5]]
        }


# ── DETECTION METHOD 2: COPY RING ─────────────────────────────────────────────

class CopyRingDetector:
    """
    Compare all candidate responses against each other.
    O(n²) comparison — batched to avoid slowness for 1000+ candidates.
    Groups similar responses into "rings".
    """

    def __init__(self):
        self._vector_cache = {}

    def detect_rings(self, responses: list[dict]) -> list[dict]:
        """
        responses: list of {candidate_id, name, email, answer, round}
        Returns: list of {ring_id, candidates, similarity, evidence}
        """
        log.info(f"Running copy ring detection on {len(responses)} responses...")

        # Pre-compute vectors
        for r in responses:
            text = r.get("answer", "")
            if text:
                self._vector_cache[r["candidate_id"]] = _tfidf_vector(text)

        rings = []
        flagged_pairs = []

        # O(n²) comparison — for 1140 candidates this is ~650k comparisons
        # Takes ~60s with our sparse TF-IDF. With dense embeddings, batch it.
        n = len(responses)
        for i in range(n):
            for j in range(i + 1, n):
                r_a = responses[i]
                r_b = responses[j]

                text_a = r_a.get("answer", "")
                text_b = r_b.get("answer", "")

                if not text_a or not text_b:
                    continue
                if len(text_a.split()) < 20 or len(text_b.split()) < 20:
                    continue

                vec_a = self._vector_cache.get(r_a["candidate_id"])
                vec_b = self._vector_cache.get(r_b["candidate_id"])

                if vec_a is None or vec_b is None:
                    continue

                vocab_sim  = cosine_similarity(vec_a, vec_b)
                phrase_sim = phrase_overlap_score(text_a, text_b)

                # Composite similarity
                sim = (vocab_sim * 0.5) + (phrase_sim * 0.5)

                if sim >= COPY_RING_THRESHOLD:
                    flagged_pairs.append({
                        "candidate_a": r_a["candidate_id"],
                        "name_a": r_a.get("name"),
                        "candidate_b": r_b["candidate_id"],
                        "name_b": r_b.get("name"),
                        "similarity": round(sim, 4),
                        "vocab_sim": round(vocab_sim, 4),
                        "phrase_sim": round(phrase_sim, 4),
                    })

        # Group into rings (connected components)
        rings = self._group_into_rings(flagged_pairs)
        log.info(f"Found {len(rings)} copy ring(s) ({len(flagged_pairs)} similar pairs)")
        return rings

    def _group_into_rings(self, pairs: list[dict]) -> list[dict]:
        """Union-Find grouping of similar candidate pairs into rings."""
        parent = {}

        def find(x):
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x, y):
            parent[find(x)] = find(y)

        for pair in pairs:
            union(pair["candidate_a"], pair["candidate_b"])

        groups = defaultdict(list)
        all_ids = set()
        for pair in pairs:
            all_ids.add(pair["candidate_a"])
            all_ids.add(pair["candidate_b"])

        for cid in all_ids:
            groups[find(cid)].append(cid)

        rings = []
        for root, members in groups.items():
            if len(members) >= 2:
                # Find the pairs within this ring
                ring_pairs = [p for p in pairs if p["candidate_a"] in members]
                avg_sim = sum(p["similarity"] for p in ring_pairs) / len(ring_pairs)
                rings.append({
                    "ring_id": root[:8] if len(root) > 8 else root,
                    "member_ids": members,
                    "size": len(members),
                    "avg_similarity": round(avg_sim, 4),
                    "pairs": ring_pairs,
                })

        return sorted(rings, key=lambda r: r["size"], reverse=True)


# ── DETECTION METHOD 3: TIMING ANALYSIS ──────────────────────────────────────

class TimingAnalyzer:
    """
    Analyze response timing. Fast polished replies are suspicious.
    Uses email timestamps from DB.
    """

    def check(self, sent_at: str, received_at: str, answer_text: str) -> dict:
        """
        sent_at: ISO timestamp of our email
        received_at: ISO timestamp of their reply
        answer_text: their reply body
        """
        try:
            sent = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
            received = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            elapsed_seconds = (received - sent).total_seconds()
        except (ValueError, TypeError):
            return {"suspicious": False, "reason": "could_not_parse_timestamps"}

        if elapsed_seconds < 0:
            return {"suspicious": False, "reason": "timestamp_error"}

        word_count = len(answer_text.split())
        elapsed_minutes = elapsed_seconds / 60

        # Classify response time
        if elapsed_seconds < TIMING_PARANOID_SECONDS:
            level = "extreme"
            suspicious = True
            reason = f"replied in {elapsed_seconds:.0f}s with {word_count} words — near-instant paste"
        elif elapsed_seconds < TIMING_SUSPICIOUS_SECONDS and word_count > 100:
            level = "suspicious"
            suspicious = True
            reason = f"replied in {elapsed_minutes:.1f}min with {word_count} words — too fast for original thought"
        elif elapsed_seconds < 300 and word_count > 250:  # under 5 min, 250+ words
            level = "suspicious"
            suspicious = True
            reason = f"replied in {elapsed_minutes:.1f}min with {word_count} words — very fast for detailed reply"
        elif elapsed_seconds > 3600 * 72:  # over 3 days
            level = "slow"
            suspicious = False
            reason = f"took {elapsed_seconds/3600:.0f}h — slow but human"
        else:
            level = "normal"
            suspicious = False
            reason = f"replied in {elapsed_minutes:.0f}min — normal timing"

        return {
            "suspicious": suspicious,
            "level": level,
            "elapsed_seconds": int(elapsed_seconds),
            "elapsed_minutes": round(elapsed_minutes, 1),
            "word_count": word_count,
            "reason": reason,
        }


# ── STRIKE SYSTEM ─────────────────────────────────────────────────────────────

def add_strike(candidate_id: str, reason: str, details: str):
    """Record a strike for a candidate. 3 strikes → eliminated."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute(
        "INSERT INTO strikes (candidate_id, reason, details) VALUES (?, ?, ?)",
        (candidate_id, reason, details)
    )

    # Count strikes
    strike_count = conn.execute(
        "SELECT COUNT(*) as n FROM strikes WHERE candidate_id = ?",
        (candidate_id,)
    ).fetchone()["n"]

    if strike_count >= 3:
        conn.execute(
            "UPDATE candidates SET status = 'eliminated', updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), candidate_id)
        )
        log.warning(f"Candidate {candidate_id} ELIMINATED (3 strikes)")

    conn.execute(
        "INSERT INTO system_log (event_type, candidate_id, details) VALUES (?, ?, ?)",
        ("strike_added", candidate_id, json.dumps({"reason": reason, "total_strikes": strike_count}))
    )

    conn.commit()
    conn.close()
    return strike_count


# ── MAIN RUNNER ───────────────────────────────────────────────────────────────

class AntiCheatRunner:
    """Orchestrates all three detection methods."""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def run_all_checks(self, check_only_new: bool = False):
        """Run all anti-cheat checks on email replies in DB."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # Get all received Round 2 emails (Round 1 = our initial outreach)
        query = """
            SELECT t.*, c.name, c.email, c.id as cid,
                   prev.sent_at as our_sent_at
            FROM email_threads t
            JOIN candidates c ON t.candidate_id = c.id
            LEFT JOIN email_threads prev ON (
                prev.candidate_id = t.candidate_id 
                AND prev.direction = 'sent' 
                AND prev.round = t.round - 1
            )
            WHERE t.direction = 'received'
            AND c.status != 'eliminated'
        """
        if check_only_new:
            # Only check replies from last 24h
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            query += f" AND t.received_at > '{since}'"

        replies = conn.execute(query).fetchall()
        conn.close()

        log.info(f"Running anti-cheat on {len(replies)} replies...")

        # Convert replies to our standardized format
        candidates = []
        for reply in replies:
            answer = reply["body"] or ""
            if not answer.strip():
                continue
                
            # Calculate response time if timestamps are available
            response_time = 0
            if reply["our_sent_at"] and reply["received_at"]:
                try:
                    sent = datetime.fromisoformat(reply["our_sent_at"].replace("Z", "+00:00"))
                    received = datetime.fromisoformat(reply["received_at"].replace("Z", "+00:00"))
                    response_time = int((received - sent).total_seconds())
                except (ValueError, TypeError):
                    response_time = 0
            
            candidates.append({
                "name": reply["name"],
                "question": "We need to programmatically extract applicant data from a platform that uses reCAPTCHA Enterprise and IP-bound cookies. Describe how you would approach this problem.",
                "answer": answer,
                "response_time": response_time,
                "candidate_id": reply["cid"]
            })

        if not candidates:
            log.info("No candidates to analyze")
            return

        # Run the comprehensive analysis
        results = analyze_batch(candidates, self.api_key)

        # Process results and add strikes
        for i, result in enumerate(results):
            candidate_data = candidates[i]
            
            if result["strikes"] > 0:
                details = json.dumps({
                    "ai_score": result["ai_score"],
                    "structure_score": result["structure_score"],
                    "flags": result["flags"],
                    "explanation": result["explanation"],
                    "response_time": result["response_time"],
                    "word_count": len(result["answer"].split())
                })
                
                strikes = add_strike(
                    candidate_data["candidate_id"], 
                    " | ".join(result["flags"]), 
                    details
                )
                
                log.warning(
                    f"STRIKE for {result['name']}: {', '.join(result['flags'])} "
                    f"(strikes now={strikes})"
                )
            else:
                log.info(f"  {result['name']}: clean (ai_score={result['ai_score']:.2%})")

        self._print_report()

    def _print_report(self):
        """Print current strike board."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        print("\n" + "="*70)
        print("ANTI-CHEAT REPORT")
        print("="*70)

        rows = conn.execute("""
            SELECT c.name, c.email, c.status,
                   COUNT(s.id) as strike_count,
                   GROUP_CONCAT(s.reason, ', ') as reasons
            FROM candidates c
            LEFT JOIN strikes s ON c.id = s.candidate_id
            GROUP BY c.id
            HAVING strike_count > 0
            ORDER BY strike_count DESC
        """).fetchall()

        for row in rows:
            status = "❌ ELIMINATED" if row["status"] == "eliminated" else "⚠️ FLAGGED"
            print(f"{status} | {row['name']:25s} | Strikes: {row['strike_count']} | {row['reasons']}")

        eliminated = conn.execute(
            "SELECT COUNT(*) as n FROM candidates WHERE status = 'eliminated'"
        ).fetchone()["n"]
        flagged = len(rows)

        print(f"\nTotal eliminated: {eliminated}")
        print(f"Total flagged (at least 1 strike): {flagged}")
        print("="*70)
        conn.close()


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Anti-cheat detection")
    parser.add_argument("--check-all", action="store_true", help="Check all replies")
    parser.add_argument("--check-new", action="store_true", help="Check replies from last 24h")
    parser.add_argument("--report", action="store_true", help="Show strike report")
    parser.add_argument("--api-key", default="", help="Anthropic API key")
    args = parser.parse_args()

    runner = AntiCheatRunner(api_key=args.api_key)

    if args.check_all:
        runner.run_all_checks(check_only_new=False)
    elif args.check_new:
        runner.run_all_checks(check_only_new=True)
    elif args.report:
        runner._print_report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()