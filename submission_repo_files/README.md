# Copilot Analyst — Financial Document Q&A

Ask plain-English questions about financial filings (10-Ks, 10-Qs, 8-Ks) and get answers drawn only from those documents, with the document name and page number attached to every claim. If the answer isn't in the documents, it says **"Not found in this data"** instead of guessing.

👉 **To install and run it, follow [SETUP.md](SETUP.md).**
For the reasons behind the design choices, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What it does

- **Cited answers** — every answer names its source document and page.
- **Answers across documents** — combines information from several filings, and every part of the answer still traces back to a source.
- **Tables when they help** — multi-line data such as rollforwards and period comparisons are shown as real tables.
- **Drag-and-drop upload** — drop a `.pdf` or `.htm` filing onto the sidebar and you can query it a few minutes later.
- **Clickable citations** — opens the source PDF at the cited page.
- **Follow-up questions** — questions asked within about 3 minutes can refer back to the previous answer.

## Requirements

- Python 3.10+
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com)), which is pay-per-use and separate from a Claude.ai subscription
- No database, Docker or other cloud services. Everything else runs on your computer.

## Command-line tools

```bash
python scripts/ingest.py --file doc.pdf      # add one document
python scripts/ingest.py --folder ./pdfs     # add a folder of documents
python scripts/query.py "question"           # search only: no API call, no cost
python scripts/ask.py "question" --trace     # full answer in the terminal, with reasoning
```

## Project structure

```
app.py                 Flask web app (chat, upload, documents panel, citation viewer)
config.py              all settings in one place
templates/, static/    web interface (no build step)
ingestion/             PDF/HTML parsing → page-aware chunking
embeddings/            TF-IDF search vectors
vectorstore/           SQLite + sparse cosine search
answer_generation/     retrieval → model routing → Claude call → citation checks
security/              encryption at rest + access logging
scripts/               command-line tools
tests/                 sample 10-K for a first test
data/                  created on first run (documents, index, key file)
```

## Known limitations

- **No login.** It's for local, single-user use only, so don't expose it to a network.
- **Keyword search (TF-IDF)**, not semantic search. It's strong on exact figures and phrases, weaker on reworded or purely conceptual questions.
- **Citations open the correct page** but don't highlight the exact passage.
- **The layout isn't built for phones** yet.
- **No automated test suite.** It has been checked manually against a real corpus.

## Security

- Never commit or share `.env`, because it contains your API key. Share `.env.example` instead.
- Back up `data/.keyfile`. Without it, the documents you've loaded can't be decrypted.
