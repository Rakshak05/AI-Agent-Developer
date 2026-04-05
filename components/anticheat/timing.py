"""
Timing Analysis Module
======================
Analyzes response timing patterns to detect suspicious behavior.
"""

def timing_analysis(answer: str, time_taken_sec: int) -> str:
    """
    Analyze response timing. Fast polished replies are suspicious.
    Uses response time and word count to determine if timing is realistic.
    """
    word_count = len(answer.split())
    wps = word_count / max(1, time_taken_sec)  # words per second

    if time_taken_sec < 30:
        return "HIGH_SUSPICION"

    if wps > 3:  # typing 3+ words/sec is unrealistic for thoughtful responses
        return "LIKELY_AI"

    if time_taken_sec < 120 and word_count > 150:
        return "SUSPICIOUS"

    if time_taken_sec > 3600 * 72:  # over 3 days
        return "SLOW_BUT_HUMAN"

    return "NORMAL"