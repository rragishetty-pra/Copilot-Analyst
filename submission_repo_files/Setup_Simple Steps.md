# Copilot Analyst — Setup Guide

Get the financial-document Q&A chatbot running end to end on your machine: clone it, add your API key, feed it a filing, and start asking questions.

## Before you start

Make sure you have:

- **Python 3.10 or newer** — check with `python3 --version`
- **An Anthropic API key** — from [console.anthropic.com](https://console.anthropic.com). This is separate from a Claude.ai chat subscription and is billed per token.
- **A little disk space** — a few hundred MB per ~75 documents, for the search index and encrypted source copies.

No database, no Docker, no other cloud account needed.

## Setup steps

### 1. Open the project folder

```bash
cd financial-doc-qa
```

### 2. Create an isolated environment (recommended)

Keeps this project's dependencies separate from everything else on your machine.

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. Install the dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

The second line installs a headless browser used only to convert EDGAR `.htm` filings to PDF. Skip it if you'll only ever feed in PDFs directly.

### 4. Add your API key

```bash
cp .env.example .env
```

Open the new `.env` file and fill in:

| Variable | Required? | What it's for |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Powers answer generation. Not needed just to ingest/index documents. |
| `DOC_ENCRYPTION_KEY` | No | Encrypts stored documents. Leave blank and one is generated for you on first run — back up `data/.keyfile` once it exists, since losing it makes encrypted documents unrecoverable. |
| `DRIVE_FOLDER_ID` | No | Only used if you wire up the (currently unused) Google Drive source. Leave blank. |

### 5. Feed it some documents

```bash
# one file
python scripts/ingest.py --file /path/to/document.pdf

# a whole folder
python scripts/ingest.py --folder /path/to/pdf_folder
```

A born-digital PDF under ~50 pages typically finishes in 6–8 minutes.

Already have a `data/vector_store/` folder copied over from another machine or a prior run? The index is ready to query — skip straight to step 6.

Optional sanity check before spending an LLM call on it (shows retrieved chunks only, no citation, no cost):

```bash
python scripts/query.py "What was total revenue for fiscal year 2024?"
```

### 6. Start the app

```bash
python app.py
```

Open **http://localhost:5000** and start asking questions.

> ⚠️ No login screen by design — this is meant for local, single-user use. Don't expose it beyond `localhost`.

Once you've done steps 1–4, starting the app again later is just step 6.

## Verify it works

Try these four questions — each one exercises something the system is specifically built to get right:

1. **A direct question** with a clean answer in your corpus → should come back with a specific figure and a citation.
2. **A question you know isn't covered** by anything in your corpus → should say "Not found in this data," not a guess.
3. **Something vague or ambiguous** → should confidently interpret it, or ask a clarifying question — not a generic non-answer.
4. **A multi-line-item question** (e.g. "restricted stock unit activity for fiscal 2022") → should render as an actual table, not a wall of bold bullets.

## If something's not working

| Symptom | Fix |
|---|---|
| `playwright install chromium` fails or is slow | Only needed for `.htm`→PDF conversion; skip it for PDF-only corpora. |
| A citation link 404s | The source PDF isn't in `data/raw/`. Answers/citations still work — only the page-jump viewer needs the original file. |
| "Not found" on something you're sure is there | Run `python scripts/query.py "your question"` to see what the retriever actually surfaced — likely a retrieval gap, not a hallucination-avoidance false negative. |
| Changes to `.py` files don't take effect | Flask's dev server doesn't hot-reload Python — restart `python app.py`. |
| Changes to `templates/`/`static/` don't take effect | Those reload automatically — hard-refresh the browser to clear cached CSS/JS. |
| Port 5000 already in use | Often AirPlay Receiver on macOS. Free the port, or change `port=5000` in `app.py`. |

For the reasoning behind the design choices (TF-IDF vs. embeddings, Flask vs. FastAPI, how citations are enforced), see `docs/ARCHITECTURE.md`.
