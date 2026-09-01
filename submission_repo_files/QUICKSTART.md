# Quickstart for this checkout

Full setup instructions and everything else are in [`README.md`](README.md).
This page just covers what's specific to *this* copy of the project.

## This copy already has a working index

`data/vector_store/` (the SQLite chunk store + TF-IDF matrix/vectorizer) is
already built for all 77 documents in the corpus — you can start asking
real questions immediately, no ingestion needed.

**Not included:** the encrypted source PDFs (`data/raw/`). Answers and
citations (document name + page number) work normally without them; only
the citation viewer's page-jump will 404 until those files are added back.

## Get it running

```bash
cd financial-doc-qa
pip install -r requirements.txt
cp .env.example .env
```

Add your Anthropic API key to `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Leave `DOC_ENCRYPTION_KEY` blank — `data/.keyfile` (already present in this
checkout) already has the key that matches this corpus's encrypted data.

```bash
python app.py
```

Open **http://localhost:5000** and try a few questions — see README.md's
"Try it" section for good first questions to ask, and "Known limitations"
for what to expect vs. what's a real bug worth flagging.
