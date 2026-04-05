# Anti-Cheat Detection System

## 🚀 How to Run (IMPORTANT)

```bash
python -m components.anticheat.demo
```

This will:
- Analyze sample candidates with various cheating patterns
- Detect AI-generated responses using LLM comparison
- Identify copy rings among multiple candidates  
- Print a full report with detailed explanations

## 🚨 Key Idea

This system does NOT rely on external AI detection tools.
Instead, it recreates how a human evaluator would detect cheating:

- comparing reasoning patterns
- analyzing structure  
- checking behavioral signals (timing)

## 🔍 Detection Signals

### 1. AI Similarity
- Fresh LLM-generated answer vs candidate answer
- Cosine similarity on embeddings
- Phrase overlap detection
- Structural pattern matching

### 2. Structural Matching
- Paragraph count and distribution
- Sentence complexity patterns
- Formatting and organization
- Writing flow analysis

### 3. Copy Rings
- Graph clustering on similarity scores
- Detects coordinated cheating groups
- Identifies 3+ candidates with similar responses

### 4. Timing Analysis
- Words per second calculation
- Unrealistic response speed detection
- Complexity vs time correlation

## ⚖️ Strike System
- Each signal adds a strike
- 3 strikes → automatic rejection
- Clear reasoning provided for each flag

## 📊 Example Output

```
🚨 ANTI-CHEAT REPORT
====================

👤 Candidate: Bob Smith
----------------------------------------
🧠 AI Similarity Score: 0.90
🏗️ Structure Match: 0.97

⚠️ Flags:
  - AI_GENERATED
  - COPY_RING

📌 Reason:
  • High semantic similarity with LLM output
  • Identical 3-paragraph structure
  • Matching sentence complexity patterns

⏱️ Timing:
  • 88 words in 25 seconds
  • Flag: HIGH_SUSPICION

❌ Strikes: 2 / 3
----------------------------------------
```

## 🧠 Engineering Approach

Instead of relying on black-box AI detectors, this system uses a hybrid approach combining:

1. **Semantic similarity**: Comparing embeddings of candidate vs fresh LLM response
2. **Structural analysis**: Checking paragraph/sentence patterns that AI tends to replicate
3. **Behavioral signals**: Analyzing response timing for unrealistic patterns
4. **Cross-candidate detection**: Finding groups of similar responses using graph clustering

This approach is more defensible because it's transparent, explainable, and doesn't depend on potentially unreliable external services.

## ❌ What Did Not Work (and Why)

1. **Pure Cosine Similarity**
   - Initially used only embedding similarity between candidates
   - Failed when candidates paraphrased AI outputs
   - Result: many false negatives

2. **Keyword-based AI detection**
   - Tried detecting phrases like "In today's world..."
   - Easily bypassed by slight rewording
   - Not robust

3. **External AI detection tools**
   - Tools like GPTZero were inconsistent
   - Black-box → no explainability
   - Not suitable for production

➡️ **Conclusion**: A hybrid system (semantic + structural + behavioral) was required.

## ⚠️ Limitations & Tradeoffs

- LLM-based comparison increases cost and latency
- Structural similarity may flag well-written human answers
- Timing analysis depends on accurate timestamps
- Embedding similarity may struggle with very short answers

➡️ **Mitigation**:
- Threshold tuning
- Combining multiple signals (no single point of failure)

## 📈 Scalability Considerations

- Pairwise comparison is O(n²) → optimized using threshold pruning
- Embeddings cached to avoid recomputation
- Copy ring detection uses efficient graph traversal
- System tested with simulated large datasets

➡️ **Future**:
- Approximate nearest neighbors (FAISS) for large-scale similarity

## 🧪 Trade-offs & Limitations

**What works well:**
- Detects obvious AI-generated responses
- Identifies copy rings among multiple candidates  
- Flags suspicious timing patterns
- Provides clear explanations

**Known limitations:**
- May flag very articulate human responses as AI-like
- Requires tuning of thresholds for different question types
- Computational cost scales quadratically with candidate count for copy ring detection

The system errs on the side of caution and provides clear reasoning to support manual review of flagged cases.