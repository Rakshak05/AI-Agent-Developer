"""
Reporting Module
================
Generates human-readable reports and explanations for anti-cheat decisions.
"""


def generate_explanation(ai_score: float, struct_score: float, timing_flag: str, 
                        response_time: int, word_count: int) -> str:
    """
    Generate a human-readable explanation of why a candidate was flagged.
    """
    reasons = []

    if ai_score > 0.8:
        reasons.append(f"High semantic similarity with LLM output ({ai_score:.2%})")

    if struct_score > 0.7:
        reasons.append(f"Similar paragraph structure and sentence patterns ({struct_score:.2%})")

    if timing_flag == "HIGH_SUSPICION":
        reasons.append(f"Response time too fast ({response_time}s for {word_count} words)")
    elif timing_flag == "LIKELY_AI":
        reasons.append(f"Unrealistic typing speed ({word_count/max(1, response_time):.1f} words/sec)")
    elif timing_flag == "SUSPICIOUS":
        reasons.append(f"Suspicious timing pattern ({response_time}s for {word_count} words)")

    return ", ".join(reasons) if reasons else "No significant flags"


def print_report(results: list):
    """
    Print a clean, human-readable anti-cheat report with enhanced formatting.
    """
    print("\n🚨 ANTI-CHEAT REPORT")
    print("====================")

    for r in results:
        print(f"\n👤 Candidate: {r['name']}")
        print("----------------------------------------")
        print(f"🧠 AI Similarity Score: {r['ai_score']:.2f}")
        print(f"🏗️  Structure Match: {r['structure_score']:.2f}")
        
        print(f"\n⚠️  Flags:")
        if r['flags']:
            for flag in r['flags']:
                print(f"  - {flag}")
        else:
            print("  - None")
        
        print(f"\n📌 Reason:")
        if r['explanation'] != "No significant flags":
            # Format the explanation with bullet points
            parts = r['explanation'].split(', ')
            for part in parts:
                print(f"  • {part}")
        else:
            print("  • No significant flags")
        
        print(f"\n⏱️  Timing:")
        print(f"  • {len(r['answer'].split())} words in {r['response_time']} seconds")
        print(f"  • Flag: {r['timing_flag']}")
        
        print(f"\n❌ Strikes: {r['strikes']} / 3")
        print("----------------------------------------")