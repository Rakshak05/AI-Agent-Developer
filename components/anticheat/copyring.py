"""
Copy Ring Detection Module
==========================
Detects groups of 3+ similar candidates using graph clustering.
"""

from collections import defaultdict
from typing import List
from .similarity import get_embedding, cosine_similarity


def detect_copy_rings(responses: List[str], threshold: float = 0.65) -> List[List[int]]:
    """
    Detect copy rings using graph clustering on pairwise similarity scores.
    
    Args:
        responses: List of response texts
        threshold: Similarity threshold to consider responses similar
        
    Returns:
        List of rings, where each ring is a list of indices of similar responses
    """
    n = len(responses)
    if n < 3:
        return []  # Need at least 3 to form a ring

    # Build similarity graph
    similarity_graph = defaultdict(list)

    for i in range(n):
        for j in range(i + 1, n):
            if not responses[i] or not responses[j]:
                continue
            if len(responses[i].split()) < 20 or len(responses[j].split()) < 20:
                continue

            vec_a = get_embedding(responses[i])
            vec_b = get_embedding(responses[j])

            sim = cosine_similarity(vec_a, vec_b)

            if sim >= threshold:
                similarity_graph[i].append(j)
                similarity_graph[j].append(i)

    # Find connected components (using Union-Find approach)
    visited = set()
    rings = []

    for node in range(n):
        if node not in visited:
            # DFS to find all connected nodes
            stack = [node]
            cluster = []

            while stack:
                curr = stack.pop()
                if curr in visited:
                    continue

                visited.add(curr)
                cluster.append(curr)

                for neighbor in similarity_graph[curr]:
                    if neighbor not in visited:
                        stack.append(neighbor)

            if len(cluster) >= 3:  # Only consider clusters with 3+ members
                rings.append(cluster)

    return rings