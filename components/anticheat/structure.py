"""
Structural Analysis Module
==========================
Analyzes document structure to detect AI-generated patterns.
"""

import re
from typing import Dict


def extract_structure(text: str) -> Dict:
    """
    Extract structural patterns: paragraph count, sentence lengths, bullet structure.
    Two AI-generated answers often have identical structure even when words differ.
    """
    def get_structure(text: str) -> Dict:
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

    return get_structure(text)


def structure_similarity(struct_a: Dict, struct_b: Dict) -> float:
    """
    Compare structural patterns between two texts.
    """
    score = 0
    checks = 0

    # Same paragraph count?
    if struct_a["para_count"] == struct_b["para_count"] and struct_a["para_count"] > 0:
        score += 1
    checks += 1

    # Similar sentence count (within 20%)?
    if struct_a["sent_count"] > 0 and struct_b["sent_count"] > 0:
        ratio = min(struct_a["sent_count"], struct_b["sent_count"]) / max(struct_a["sent_count"], struct_b["sent_count"])
        score += ratio
    checks += 1

    # Similar avg sentence length (within 15%)?
    if struct_a["avg_sent_len"] > 0 and struct_b["avg_sent_len"] > 0:
        ratio = min(struct_a["avg_sent_len"], struct_b["avg_sent_len"]) / max(struct_a["avg_sent_len"], struct_b["avg_sent_len"])
        score += ratio
    checks += 1

    # Same bullet count?
    if struct_a["bullet_count"] == struct_b["bullet_count"]:
        score += 1
    checks += 1

    return score / checks if checks > 0 else 0.0