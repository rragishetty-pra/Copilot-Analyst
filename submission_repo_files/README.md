# Copilot Analyst — Financial Document Q&A Chatbot

Ask plain-English questions about a library of financial filings (10-Ks,
10-Qs, 8-Ks) and get back answers grounded strictly in those documents —
never the model's own general knowledge — with a document name and page
number attached to every claim. If the answer isn't in the documents, it
says so instead of guessing.

This README gets a fresh copy of this project running end to end, on any
machine, from nothing. For the *why* behind the design choices (why TF-IDF
instead of dense embeddings, why Flask instead of FastAPI, how citation
accuracy is enforced structurally, etc.), see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## What it does

- **Closed-book, cited answers.** Every answer traces back to a specific
  document and page. No source found → "Not found in this data," not a
  best guess.
- **Synthesis across documents.** Ask a question that spans multiple
  filings and it reasons across them — every part of the answer still has
  to trace back to something actually in the corpus.
- **Table-formatted answers.** Multi-line-item data (a rollforward, a
  multi-period comparison) renders as an actual table in the chat, not a
  wall of bolded bullet points.
- **Drag-and-drop ingestion.** Drop a `.pdf`/`.htm` filing on the sidebar
  and it's parsed, chunked, embedded, and queryable within minutes — no
  server restart needed.
- **Clickable citations.** Click a citation to open the source PDF in a
  side panel, jumped to the exact page.
- **Short-window conversational memory.** Follow-up questions use the
  prior exchange if asked within about 3 minutes; after that, the next
  question starts fresh.

## Requirements

- **Python 3.10+**
- **An Anthropic API key** ([console.anthropic.com](https://console.anthropic.com) —
  this is pay-per-token API access, separate from a Claude.ai chat
  subscription, which does not include API access)
- A few hundred MB of free disk space per ~75-document corpus (the vector
  index + encrypted source copies)

No database server, no Docker, no cloud account beyond the Anthropic API
key. Everything else (parsing, embeddings, vector search, the app itself)
runs locally.

## Setup

```bash
cd financial-doc-qa

# (recommended) isolate dependencies in a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

# HTML→PDF conversion (for EDGAR .htm filings) needs a headless browser
playwright install chromium

cp .env.example .env
```

Open `.env` and fill in:

| Variable | Required? | What it's for |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Powers the answer-generation layer. Ingestion/parsing alone doesn't need it. |
| `DOC_ENCRYPTION_KEY` | No | Encrypts source documents at rest. Leave blank and one is auto-generated at `data/.keyfile` on first run — just make sure to back that file up, since losing it makes encrypted documents unrecoverable. To generate your own: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DRIVE_FOLDER_ID` | No | Only relevant if you wire up the (currently unused) Google Drive source in `ingestion/drive_source.py`. Leave blank for normal use — documents come in via drag-and-drop or the CLI. |

## Getting documents into the system

**If you already have a `data/vector_store/` folder** (copied over from
another machine, or from a prior run), the index is ready to query — skip
to "Running the app" below.

**Starting from nothing**, ingest your own filings first:

```bash
# one file
python scripts/ingest.py --file /path/to/document.pdf

# a whole folder
python scripts/ingest.py --folder /path/to/pdf_folder

# re-ingest a file even though it's already indexed (unchanged files are
# skipped by default, based on a content hash)
python scripts/ingest.py --file /path/to/document.pdf --force
```

A born-digital PDF under ~50 pages / ~100–150MB typically finishes in
6–8 minutes. `.htm` EDGAR filings are converted to PDF automatically before
parsing. You can also just start the app (below) and drag-and-drop files
onto the sidebar instead of using the CLI.

Sanity-check what got ingested before spending an LLM call on it:

```bash
python scripts/query.py "What was total revenue for fiscal year 2024?"
```

This shows only the retrieved chunks — no citation, no LLM, no cost — a
fast way to confirm a document actually made it into the index.

## Running the app

```bash
python app.py
```

Open **http://localhost:5000**.

- **Chat** — ask a question in plain English. Every answer (when found)
  comes back with its citation, why it's relevant, and the concept/section
  it maps to; the reasoning trace is hidden by default behind a "Show
  reasoning" toggle.
- **Documents panel** — the sidebar lists every ingested document with its
  page/chunk counts.
- **Citation viewer** — click a citation to open the source PDF, jumped to
  the cited page. (Requires the original PDF to be present under
  `data/raw/` — see "Troubleshooting" if a citation 404s.)

This runs Flask's built-in development server, which is intentional for
local, single-user use — this project has no authentication layer, so
don't expose it beyond `localhost` without adding one.

## Command-line usage (no browser needed)

```bash
# ask a question directly
python scripts/ask.py "What was Nike's total revenue for fiscal year 2023?"

# keep conversational memory across separate calls (3-minute idle window)
python scripts/ask.py "What about in 2021?" --session my-session

# see the hidden reasoning trace
python scripts/ask.py "..." --trace
```

## Try it

A few questions that exercise the system's core behaviors:

- A question with a clean, direct answer in the corpus — should come back
  with a specific figure and a citation.
- A question you know isn't covered by anything in your corpus — should
  come back "Not found in this data," not a guess.
- Something vague or ambiguous — it should either confidently interpret it
  or ask a clarifying question, not return a generic non-answer.
- A rollforward/multi-line-item question (e.g. "restricted stock unit
  activity for fiscal 2022") — should render as a table, not a run of bold
  bullet lines.

## Project structure

```
financial-doc-qa/
├── app.py                       # Flask app: chat, upload, documents panel, citation viewer
├── config.py                    # all tunable constants in one place
├── templates/index.html         # single-page frontend
├── static/{app.js,style.css}    # frontend JS/CSS, no build step
├── ingestion/                    # PDF/HTML parsing → page-aware chunking → storage pipeline
├── embeddings/                   # TF-IDF fit/transform (embedder.py)
├── vectorstore/                  # SQLite + scipy sparse cosine search (local_store.py)
├── answer_generation/            # retrieval → routing → LLM call → citation validation
│   ├── llm_client.py              # Anthropic Messages API client
│   ├── prompts.py                  # closed-book system prompt + forced tool-call schema
│   ├── answer.py                   # orchestrates the whole answer pipeline
│   ├── routing.py                  # Haiku/Sonnet model routing
│   ├── doc_hints.py                # company-name-aware retrieval boost
│   └── session_store.py            # conversational memory
├── security/                     # encryption at rest + access logging
├── scripts/                      # ingest.py, query.py, ask.py — CLI entry points
├── tests/                        # synthetic test document generator
├── docs/ARCHITECTURE.md          # design rationale, validated results, known limitations
└── data/                         # created on first run: raw/, processed/, vector_store/, logs/
```

## Troubleshooting

- **`playwright install chromium` fails or is slow** — only needed for
  converting `.htm` filings to PDF; PDF-only corpora can skip it.
- **A citation link 404s when clicked** — the source PDF for that document
  isn't in `data/raw/`. The vector index and answers/citations still work
  without it; only the page-jump viewer needs the original file present.
- **"Not found" on something you're sure is in a document** — run
  `python scripts/query.py "your question"` to see what the retriever
  actually surfaced; if the right chunk isn't in the results, it's a
  retrieval gap (see `docs/ARCHITECTURE.md`'s "Known retrieval
  limitations"), not a hallucination-avoidance false negative.
- **Changes to `answer_generation/`, `embeddings/`, `vectorstore/`, or any
  other `.py` file don't seem to take effect** — Flask's default dev server
  doesn't hot-reload Python modules; restart `python app.py`.
- **Changes to `templates/index.html` or `static/*` don't seem to take
  effect** — these are served fresh on every request and don't need a
  restart; do a hard refresh in the browser to bypass cached CSS/JS.
- **Port 5000 already in use** — another process (on macOS, often
  AirPlay Receiver) is holding it; either free the port or change
  `port=5000` in `app.py`'s `app.run(...)` call.

## Known limitations

- No authentication — local, single-user use only; don't expose beyond
  `localhost`.
- The citation viewer jumps to the correct page but doesn't yet highlight
  the exact passage on it.
- Retrieval is TF-IDF (lexical), not dense embeddings — very good at exact
  figures and phrases, weaker on paraphrased or purely conceptual
  questions. See `docs/ARCHITECTURE.md` for specifics and mitigations
  already in place (company-name and date-aware retrieval boosts, spelling
  correction, abbreviation expansion).
- Near-tie conflicting data across documents and ambiguous cross-document
  concept definitions are handled by asking the user to clarify, but this
  path is lightly tested against real corpus conflicts.
- No automated test suite yet — validation has been manual, real-question
  testing against a live corpus.

Full design rationale, what was tried and rejected, and every issue found
during real-corpus testing (with root causes and fixes) live in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
