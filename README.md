# GenoTek Global - AI Recruitment Automation Pipeline

### Building an End-to-End Autonomous Hiring System

This project implements a sophisticated AI-powered recruitment automation system that handles the complete hiring pipeline from candidate sourcing to evaluation and engagement.

## Key Features

### 1. **Sandboxed Code Execution**
- Extracts Python code from candidate email submissions
- Executes in isolated environment with 10s timeout
- Captures stdout/stderr and classifies errors (SyntaxError, KeyError, etc.)
- Provides contextual feedback: "I ran your code and noticed a KeyError on line 12..."

### 2. **Self-Healing Web Scraping**
- Primary: BeautifulSoup with multiple selector fallbacks
- Fallback: LLM-based extraction when HTML structure changes
- Automatically adapts to new layouts without manual intervention

### 3. **Graph-Based Anti-Cheat Detection**
- Union-Find algorithm to detect copy-rings of 3+ candidates
- Plagiarism detection via TF-IDF + cosine similarity
- AI-written text detection using perplexity scoring

### 4. **Intelligent Candidate Scoring**
- Multi-factor scoring: GitHub quality, technical skills, answer depth
- Uses Claude Sonnet for nuanced evaluation of responses
- Automatic tier classification (Fast-Track, Standard, Review, Reject)

### 5. **Proactive Engagement System**
- Multi-round email conversations with contextual follow-ups
- Proactive nudges for unresponsive Fast-Track candidates after 48 hours
- Thread tracking and conversation history management

### 6. **Real-Time Dashboard**
- Built with Streamlit for live recruitment metrics
- Visualizations: tier distribution, score histograms, email activity
- Candidate search and filtering capabilities
- Export functionality to CSV

## Architecture

```
                       ┌───────────────────┐
                       │    Internshala    │
                       └─────────┬─────────┘
                                 │
                                 ▼
                       ┌───────────────────┐
                       │    C1: Access     │
                       └─────────┬─────────┘
                                 │
                                 ▼
                       ┌───────────────────┐
                       │    C2: Scoring    │
                       └─────────┬─────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    Ranked Applicants    │
                    │        Database         │
                    └────────────┬────────────┘
                                 │
             ┌───────────────────┼────────────────────┐
             ▼                   ▼                    ▼
   ┌───────────────────┐ ┌────────────────┐ ┌──────────────────┐
   │  C3: Engagement   │ │ C4: Anti-Cheat │ │   C5: Learning   │
   │    (Gmail API)    │ │    Module      │ │     Module       │
   └─────────┬─────────┘ └───────┬────────┘ └─────────┬────────┘
             │                   │                    │
             └─────────────────┐ │ ┌──────────────────┘
                               ▼ ▼ ▼
                       ┌───────────────────┐
                       │ SQLite Database   │
                       │  (recruitment)    │
                       └─────────┬─────────┘
                                 │
                                 ▼
                       ┌───────────────────┐
                       │     Streamlit     │
                       │     Dashboard     │
                       └───────────────────┘
```

## Quick Start

### Prerequisites
- Python 3.9+
- Chrome browser installed
- Gmail account for sending/receiving emails
- Anthropic API key (optional but recommended)

### Installation

```bash
# Clone repository
git clone https://github.com/Rakshak05/AI-Agent-Developer.git
cd AI-Agent-Developer

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p data logs
```

### Initial Setup

Before running the code, set up your local configuration by copying the example files and filling in your own API keys:

```bash
# 1. Set up Environment variables
cp .env.example .env
cp config.example.json config.json
```
*(Make sure to open `.env` and `config.json` and insert your API keys)*

```bash
# 2. Configure Internshala job ID
python components/c1_access.py --job-id YOUR_JOB_ID

# 2. Capture authentication cookies
python components/c1_access.py --setup

# 3. Authenticate Gmail
python components/c3_engagement.py --auth --sender-email your-email@gmail.com

# 4. Run the full pipeline
python components/c6_integration.py --run-pipeline

# 5. Launch dashboard
streamlit run dashboard.py
```

## Running the Demo

### Step 1: Activate your virtual environment and install missing packages

```powershell
# Activate the virtual environment
.\venv\Scripts\Activate.ps1

# Install the new packages (streamlit, plotly, etc.) added to requirements.txt
pip install -r requirements.txt
```

### Step 2: Populate the Database with Mock Data

Instead of needing real API keys and waiting for the scraper, you can populate the system's SQLite database with 100 realistic fake candidates instantly:

```bash
python setup_mock_db.py
```

### Step 3: Run the Feature Demonstrations

You can also run the comprehensive demo script which explicitly simulates the new Sandboxed Code Execution and Self-Healing LLM scrapers without needing live API calls:

```bash
python demo_improvements.py
```

### Step 3: Launch the Dashboard

Once the demo script finishes and your recruitment.db is populated with data, run the dashboard command:

```powershell
streamlit run dashboard.py
```

This will automatically open your browser to http://localhost:8501. Since you activated your virtual environment and installed the requirements, the command will now be successfully recognized!

## Usage Examples

### Run Complete Pipeline
```bash
python components/c6_integration.py --run-pipeline
```

### Start Continuous Monitoring
```bash
python components/c6_integration.py --daemon
```

### Check System Status
```bash
python components/c6_integration.py --status
```

### Launch Dashboard
```bash
streamlit run dashboard.py
```

## Security Considerations

- **Code Execution**: All candidate code runs in isolated subprocesses with timeouts
- **Credential Management**: API keys and tokens stored separately and gitignored
- **Rate Limiting**: Built-in delays to prevent IP blocking
- **Data Isolation**: Temporary execution environments cleaned after each run

## Project Structure

```
├── components/           # Core system components
│   ├── c1_access.py     # Platform authentication & data extraction
│   ├── c2_intelligence.py # Candidate scoring & ranking
│   ├── c3_engagement.py  # Email conversations & engagement
│   ├── c4_anticheat.py   # Plagiarism & AI detection
│   ├── c5_learning.py    # Self-improvement module
│   └── c6_integration.py # System orchestrator
├── data/                # Runtime data (gitignored)
│   ├── config.json      # Authentication credentials
│   ├── applicants.json  # Raw scraped data
│   ├── ranked_applicants.json # Scored candidates
│   └── recruitment.db   # SQLite database
├── dashboard.py         # Streamlit dashboard
├── demo_improvements.py # Demo script
├── main.py              # Main orchestrator
├── README.md            # This file
├── setup_mock_db.py     # Script to populate mock dashboard data
├── ARCHITECTURE.md      # System design documentation
└── requirements.txt     # Python dependencies
```

## Components Overview

### Component 1: Access Layer
Handles platform authentication, bypassing reCAPTCHA Enterprise using Chrome DevTools Protocol.

### Component 2: Intelligence Engine
Multi-factor candidate scoring using GitHub analysis, technical skills assessment, and answer quality evaluation.

### Component 3: Engagement System
Manages multi-round email conversations with contextual follow-ups and proactive nudging.

### Component 4: Anti-Cheat Module
Detects plagiarism, AI-written responses, and collaboration rings using advanced algorithms.

### Component 5: Learning Module
Self-improvement system that analyzes patterns and adjusts scoring weights automatically.

### Component 6: Integration Layer
Orchestrates all components with retry mechanisms and error handling.

## Performance & Scalability

- Handles 1,000+ candidates efficiently
- SQLite WAL mode for concurrent reads
- Exponential backoff for error recovery
- Modular architecture for easy scaling

## Testing

```bash
# Run tests
python -m pytest tests/

# Check system status
python components/c6_integration.py --status
```

## Deployment

For production deployment:

1. Generate systemd service: `python components/c6_integration.py --generate-systemd`
2. Copy service file to `/etc/systemd/system/`
3. Start service: `sudo systemctl start recruitment`
4. Monitor logs: `sudo journalctl -u recruitment -f`

## Acknowledgments

This system was built for GenoTek Global's AI Agent Developer Challenge to demonstrate advanced AI integration, autonomous decision-making, and production-ready engineering practices.