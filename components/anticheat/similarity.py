"""
Similarity Analysis Module
==========================
Handles embedding generation, cosine similarity, and phrase overlap detection.
"""

import re
import math
from collections import defaultdict
from typing import Dict, List, Optional


def get_embedding(text: str) -> Dict[str, float]:
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


def cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
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


def semantic_similarity(text_a: str, text_b: str) -> float:
    """
    Enhanced similarity calculation combining multiple metrics
    """
    emb_a = get_embedding(text_a)
    emb_b = get_embedding(text_b)
    
    vocab_sim = cosine_similarity(emb_a, emb_b)
    phrase_sim = phrase_overlap_score(text_a, text_b)
    
    # Weighted combination
    total_sim = (vocab_sim * 0.6) + (phrase_sim * 0.4)
    
    return total_sim