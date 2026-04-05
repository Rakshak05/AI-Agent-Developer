"""
Core Anti-Cheat Detector
========================
Main detection pipeline that combines all signals into a unified score.
"""

import json
import time
import sqlite3
import logging
import re
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from collections import defaultdict

from .similarity import get_embedding, cosine_similarity, phrase_overlap_score, semantic_similarity
from .structure import extract_structure, structure_similarity
from .timing import timing_analysis
from .copyring import detect_copy_rings
from .report import generate_explanation, print_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [C4-CHEAT] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

ANTHROPIC_KEY = ""

# Thresholds
AI_SIMILARITY_THRESHOLD    = 0.65  # above this → AI_GENERATED strike (lowered to catch more)
COPY_RING_THRESHOLD        = 0.65  # above this between two candidates → COPY_RING strike
TIMING_SUSPICIOUS_SECONDS  = 120   # under 2 minutes for 3+ paragraph reply → suspicious
TIMING_PARANOID_SECONDS    = 30    # under 30 seconds → nearly certain auto-paste


def generate_llm_answer(question: str, api_key: str = "") -> str:
    """
    Generate a fresh answer using LLM (OpenRouter / OpenAI).
    """
    if not api_key:
        # Use a simple placeholder for demo purposes that mimics an AI response
        return f"To address the question '{question[:50]}...', I would approach this systematically. First, I would analyze the technical requirements and constraints. Second, I would evaluate potential solutions considering both efficiency and ethical implications. Finally, I would recommend a balanced approach that respects both technical feasibility and responsible implementation. This structured three-part response demonstrates comprehensive thinking and addresses the core concerns raised in the question effectively."

    try:
        import requests
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": f"Answer this question as if you were a software engineering candidate applying for an internship:\n\n{question}\n\nWrite a natural, 2-3 paragraph answer."}
                ],
                "temperature": 0.7
            }
        )

        data = response.json()
        return data["choices"][0]["message"]["content"]

    except Exception as e:
        log.error(f"LLM generation failed: {e}")
        # Return a placeholder that mimics an AI response structure
        return f"To address the question '{question[:50]}...', I would approach this systematically. First, I would analyze the technical requirements and constraints. Second, I would evaluate potential solutions considering both efficiency and ethical implications. Finally, I would recommend a balanced approach that respects both technical feasibility and responsible implementation."


def ai_similarity_check(candidate_answer: str, question: str, api_key: str = ""):
    """
    Compare candidate answer vs fresh LLM answer to SAME question
    """
    llm_answer = generate_llm_answer(question, api_key)

    # Calculate semantic similarity between candidate and LLM answer
    semantic_sim = semantic_similarity(candidate_answer, llm_answer)
    
    # Calculate structural similarity
    candidate_struct = extract_structure(candidate_answer)
    llm_struct = extract_structure(llm_answer)
    struct_score = structure_similarity(candidate_struct, llm_struct)

    # Enhanced AI detection: Check for AI-specific patterns in candidate answer
    ai_phrase_patterns = [
        r'\bfirst,\s', r'\bsecond,\s', r'\bthird,\s',  # Sequential enumeration
        r'\bfirstly\b', r'\bsecondly\b', r'\bthirdly\b',  # Sequential enumeration
        r'\balso worth noting\b', r'\badditionally\b', r'\bfurthermore\b',  # Connectors
        r'\bin conclusion\b', r'\nto summarize\b', r'\nto conclude\b',  # Conclusions
        r'\bstructured approach\b', r'\bsystematic approach\b',  # AI favorite phrases
        r'\bcomprehensive analysis\b', r'\bholistic view\b',  # AI favorite phrases
        r'\brespecting both\b', r'\bbalanced approach\b',  # AI favorite phrases
    ]
    
    ai_pattern_count = 0
    for pattern in ai_phrase_patterns:
        if re.search(pattern, candidate_answer, re.IGNORECASE):
            ai_pattern_count += 1
    
    # Normalize pattern count (assume 5+ patterns suggest AI generation)
    ai_pattern_score = min(1.0, ai_pattern_count / 5.0)
    
    # Combined score: 50% semantic similarity, 30% structural similarity, 20% AI pattern detection
    final_score = (semantic_sim * 0.5) + (struct_score * 0.3) + (ai_pattern_score * 0.2)

    return final_score, llm_answer


def analyze_candidate(candidate: Dict, all_candidates: List[Dict], api_key: str = ""):
    """
    Main analysis function for a single candidate
    """
    result = {
        "name": candidate["name"],
        "question": candidate.get("question", ""),
        "answer": candidate.get("answer", ""),
        "response_time": candidate.get("response_time", 0),
        "ai_score": 0.0,
        "structure_score": 0.0,
        "flags": [],
        "strikes": 0,
        "explanation": "",
        "timing_flag": "NORMAL"
    }

    # 1. AI similarity check
    if result["answer"] and result["question"]:
        ai_score, llm_answer = ai_similarity_check(
            result["answer"], 
            result["question"],
            api_key
        )
        
        result["ai_score"] = round(ai_score, 4)
        
        # Extract structure for comparison
        candidate_struct = extract_structure(result["answer"])
        llm_struct = extract_structure(llm_answer)
        struct_score = structure_similarity(candidate_struct, llm_struct)
        result["structure_score"] = round(struct_score, 4)

        # Apply AI flag
        if ai_score > AI_SIMILARITY_THRESHOLD:
            result["flags"].append("AI_GENERATED")
            result["strikes"] += 1

    # 2. Timing analysis
    timing_result = timing_analysis(result["answer"], result["response_time"])
    result["timing_flag"] = timing_result
    
    if timing_result != "NORMAL":
        result["flags"].append(timing_result.upper())
        result["strikes"] += 1

    # 3. Generate explanation
    result["explanation"] = generate_explanation(
        result["ai_score"], 
        result["structure_score"], 
        result["timing_flag"],
        result["response_time"],
        len(result["answer"].split())
    )

    return result


def analyze_batch(candidates: List[Dict], api_key: str = ""):
    """
    Analyze all candidates in a batch, including cross-candidate checks
    """
    # First, analyze each candidate individually
    results = []
    for candidate in candidates:
        result = analyze_candidate(candidate, candidates, api_key)
        results.append(result)

    # Then, detect copy rings across all responses
    responses = [c.get("answer", "") for c in candidates]
    copy_rings = detect_copy_rings(responses, COPY_RING_THRESHOLD)

    # Apply copy ring strikes
    for ring in copy_rings:
        if len(ring) >= 3:  # Only flag rings with 3+ candidates
            for idx in ring:
                if idx < len(results):
                    results[idx]["flags"].append("COPY_RING")
                    results[idx]["strikes"] += 1

    return results


def add_strike(candidate_id: str, reason: str, details: str, db_path: Path = Path("data/recruitment.db")):
    """Record a strike for a candidate. 3 strikes → eliminated."""
    conn = sqlite3.connect(db_path)
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