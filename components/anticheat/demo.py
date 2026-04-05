"""
Anti-Cheat System Demo
======================
Demonstrates the full anti-cheat system capabilities.
"""

import json
from .detector import analyze_batch


def run_demo():
    """
    Run a comprehensive demo of the anti-cheat system.
    """
    print("🔍 Running Anti-Cheat System Demo")
    print("=" * 60)

    # Sample candidate data with more realistic examples
    candidates = [
        {
            "name": "Alice Johnson",
            "question": "How would you bypass reCAPTCHA Enterprise programmatically?",
            "answer": "To address the question about bypassing reCAPTCHA Enterprise, I would approach this systematically. First, I would analyze the technical requirements and constraints. Second, I would evaluate potential solutions considering both efficiency and ethical implications. Finally, I would recommend a balanced approach that respects both technical feasibility and responsible implementation. This structured three-part response demonstrates comprehensive thinking and addresses the core concerns raised in the question effectively.",
            "response_time": 180  # 3 minutes
        },
        {
            "name": "Bob Smith",
            "question": "How would you bypass reCAPTCHA Enterprise programmatically?",
            "answer": "To address the question about bypassing reCAPTCHA Enterprise programmatically, I would approach this systematically. First, I would analyze the technical requirements and constraints. Second, I would evaluate potential solutions considering both efficiency and ethical implications. Furthermore, I would recommend a balanced approach that respects both technical feasibility and responsible implementation. Additionally, this structured three-part response demonstrates comprehensive thinking and addresses the core concerns raised in the question effectively. In conclusion, a systematic approach ensures both effectiveness and ethical compliance.",
            "response_time": 45  # 45 seconds (suspicious for such a detailed answer)
        },
        {
            "name": "Carol Davis",
            "question": "How would you bypass reCAPTCHA Enterprise programmatically?",
            "answer": "To address the question about bypassing reCAPTCHA Enterprise programmatically, I would approach this systematically. First, I would analyze the technical requirements and constraints. Second, I would evaluate potential solutions considering both efficiency and ethical implications. Furthermore, I would recommend a balanced approach that respects both technical feasibility and responsible implementation. Additionally, this structured three-part response demonstrates comprehensive thinking and addresses the core concerns raised in the question effectively. In conclusion, a systematic approach ensures both effectiveness and ethical compliance.",
            "response_time": 50  # Identical answer to Bob (copy ring!)
        },
        {
            "name": "David Wilson",
            "question": "How would you bypass reCAPTCHA Enterprise programmatically?",
            "answer": "I would probably try selenium and see if I can automate it somehow, not really sure about the specifics but I heard there are some tools online that might help with this kind of thing.",
            "response_time": 900  # 15 minutes (slow but human)
        },
        {
            "name": "Eve Martinez",
            "question": "How would you bypass reCAPTCHA Enterprise programmatically?",
            "answer": "First, I would conduct a comprehensive analysis of the reCAPTCHA implementation. Secondly, I would explore potential vulnerabilities in the challenge-response mechanism. Thirdly, I would develop a systematic approach to bypass the verification process. Additionally, I would consider using advanced automation techniques and machine learning models to solve image-based challenges. Also worth noting, behavioral analysis evasion is crucial for successful bypass. Furthermore, a holistic view of the security architecture would inform a balanced approach. In conclusion, respecting both technical feasibility and ethical considerations is essential for a comprehensive solution.",
            "response_time": 25  # Very fast response, AI-like structure
        },
        {
            "name": "Frank Brown",
            "question": "How would you bypass reCAPTCHA Enterprise programmatically?",
            "answer": "To bypass reCAPTCHA Enterprise programmatically, I would recommend using specialized bypass services that employ human solvers or advanced AI. These services typically offer APIs that integrate seamlessly with your automation scripts. Alternatively, you could use headless browsers with stealth configurations to avoid detection. The key is to simulate realistic human behavior patterns including mouse movements, typing speeds, and navigation flows. Advanced techniques might involve solving audio challenges or implementing machine learning models trained specifically on reCAPTCHA images.",
            "response_time": 120  # Structured response, potentially AI-generated
        }
    ]

    print(f"Testing {len(candidates)} candidates with similar questions...\n")

    # Analyze all candidates
    results = analyze_batch(candidates)

    # Print the report
    from .report import print_report
    print_report(results)

    # Summary statistics
    flagged_count = sum(1 for r in results if r['strikes'] > 0)
    eliminated_count = sum(1 for r in results if r['strikes'] >= 3)
    
    print(f"\n📊 SUMMARY:")
    print(f"Total candidates: {len(candidates)}")
    print(f"Flagged candidates: {flagged_count}")
    print(f"Eliminated candidates: {eliminated_count}")

    # Show detection breakdown
    ai_flags = sum(1 for r in results if 'AI_GENERATED' in r['flags'])
    timing_flags = sum(1 for r in results if any('SUSPICION' in f or f == 'LIKELY_AI' for f in r['flags']))
    copy_ring_flags = sum(1 for r in results if 'COPY_RING' in r['flags'])

    print(f"\n🔍 DETECTION BREAKDOWN:")
    print(f"AI-generated responses: {ai_flags}")
    print(f"Timing anomalies: {timing_flags}")
    print(f"Copy rings detected: {copy_ring_flags}")


if __name__ == "__main__":
    run_demo()