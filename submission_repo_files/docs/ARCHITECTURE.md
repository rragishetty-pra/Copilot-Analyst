# Architecture, Design Rationale & Build History

This is the deep-dive companion to the top-level [`README.md`](../README.md).
Read the README first if you just want to get the app running — come back
here for *why* it's built the way it is, what was tried and rejected, what
was validated against the real corpus, and what's still open.

## What this is

A complete pipeline end to end: `PDF/HTM -> parsed pages -> chunks -> vector
index -> retrieval -> grounded Claude answer with validated citations ->
Flask app (chat, drag-and-drop upload, documents panel, citation viewer)`.
See the project's DNA document for the full spec this implements against.

## How documents get in: drag-and-drop, not automatic Google Drive sync

A Google Drive folder ("Copilot Analyst") was tried as the document source
and specifically rejected after testing — worth understanding why, since it's
not obvious:

Google Drive is reachable in a Claude session only through an MCP tool
surface, not a standalone API client. Downloading a file's bytes returns them
as a base64 string inside a tool result; getting that string from the tool
result into a file on disk requires Claude to reproduce it as text — there is
no tool-to-tool pipe that moves raw bytes directly. In testing, that
reproduction step silently flipped a single character inside a 4.9KB PDF's
compressed content stream. The file still opened fine — but the extracted
text on the affected page came out garbled ("Total revenueOperating expenses
.and And modestoy $22.ovel" instead of the real sentence), with no error and
a page citation still attached to the wrong text. That's the exact failure
mode this project exists to prevent, and the risk only grows with real
filings (70,000-150,000+ words each).

**So: documents reach this pipeline either through the app's drag-and-drop
upload zone (`/api/upload` in app.py — a real HTTP file upload, not a text
relay through an LLM), or via the batch ingestion CLI scripts for bulk
loading (used for the full 78-file corpus).** Both avoid the failure mode
above entirely. `ingestion/drive_source.py` documents the Drive-API decision
in full and stays in the repo unused, in case a later phase adds a
*properly engineered* Drive sync (real OAuth + Python download, no LLM in
the byte path).

## A note on the embedding/vector-store backend

The project's architecture doc (§7) specifies BGE embeddings via
`sentence-transformers` and Chroma/FAISS as the vector store. This build was
done inside an environment that blocked `pip`/`npm` installs of anything not
already cached — so `torch`, `sentence-transformers`, `chromadb`, `faiss`,
and `tiktoken` weren't installable there.

Rather than block on that, the embedding and vector-store layers were built
behind the **same interfaces** using only what's already available:

| Layer | Spec'd choice | What's actually running | Why it's an OK substitute |
|---|---|---|---|
| Embeddings | BGE (dense, semantic) | TF-IDF (`scikit-learn`), local, zero network | Preserves exact tokens — good for matching literal figures ("$22.1 million", "Item 7A") that dense embeddings sometimes blur. Standard hybrid RAG systems use both dense *and* lexical retrieval for this reason. |
| Vector store | Chroma or FAISS | SQLite (metadata/text) + a `scipy` sparse matrix (vectors), brute-force cosine similarity | No document/character ceiling either way at this corpus size (78 docs / 400MB); brute-force cosine over a few tens of thousands of chunks is milliseconds. |
| Token counting | `tiktoken` | word count | Only used for chunk *sizing*, not billing or context-window math — word count is an adequate ruler for "is this chunk roughly the right length." |

**This is a real, working, fully local, zero-cost vector database today** —
not a stub. Swapping in the originally spec'd stack (BGE + Chroma) is a
contained change — rewrite the internals of `embeddings/embedder.py` and
`vectorstore/local_store.py` only; every other module calls into those
through the same handful of functions. See `requirements.txt` for the
optional-upgrade package list.

## Why raw HTTPS instead of the `anthropic` SDK, and Flask instead of FastAPI

The DNA doc (§7) specifies the official `anthropic` SDK and FastAPI. Both
were left out of the original build because the development sandbox at the
time blocked installs of anything not already cached, and neither was
cached — so `answer_generation/llm_client.py` calls the Messages API
directly over HTTPS with `httpx`, and `app.py` uses Flask. Both are complete,
working implementations, not stubs — swap in `anthropic>=0.34` and
`fastapi>=0.115` (+ `uvicorn`) only if you specifically want the SDK's
streaming support or FastAPI's auto-generated OpenAPI docs; neither swap is
required for the app to work as-is on a normal machine with internet access.

## How citation accuracy is enforced structurally

This is the part of the DNA doc (§2, §5) that has to be true at the data
layer, not just prompted for at the LLM layer:

- **Chunks never span a page boundary.** `ingestion/chunker.py` chunks
  within a single page only — never across pages — so every chunk maps
  cleanly to one page number.
- **Tables are never merged into prose chunks.** A table becomes its own
  chunk (`chunk_type="table"`), and its cell text is stripped out of the
  page's running text, so a numeric answer doesn't come from a garbled
  mix of the two.
- **Every stored chunk carries `doc_name` + `page_number` + `section_heading`**
  as first-class metadata, not something reconstructed after the fact —
  `query()` returns this alongside every result.
- **`doc_id` is a content hash**, not a filename — re-ingesting the same
  bytes never creates duplicate or conflicting entries (DNA doc §3: revised
  documents are added as new entries, not overwritten in place).
- **Documents only enter the pipeline through byte-exact transfer**
  (drag-and-drop or CLI), never through a path that requires reconstructing
  binary content from text — see the Drive section above for why that
  distinction turned out to matter in practice, not just in theory.
- **Every citation the model returns is cross-checked against the chunks it
  was actually given** (`answer.py`'s `_validate_citations`) — a citation
  pointing at a document/page that wasn't retrieved gets the whole answer
  downgraded to `not_found` rather than trusted. That's Core Rule #4 ("a
  hallucinated-but-correct answer is still a failure") enforced in code, not
  just in the prompt.

## The answer-generation layer

`answer_generation/` implements the DNA doc's Core Principles (§2) and
Answer Behavior Rules (§4) end to end:

- **`llm_client.py`** — Anthropic Messages API over raw HTTPS (see above).
- **`prompts.py`** — the closed-book system prompt and a forced tool-call
  schema (`submit_answer`) the model must respond through, plus table
  formatting instructions for naturally-tabular answers (rollforwards,
  multi-period comparisons).
- **`routing.py`** — Haiku 4.5 by default, routes to Sonnet when retrieval
  spans 3+ distinct documents or the question's wording signals
  cross-document synthesis ("compare," "trend," "versus," etc.).
- **`doc_hints.py`** — a document-name-aware retrieval boost; see "Known
  retrieval limitations" below.
- **`session_store.py`** — the 3-minute conversational memory window (§6),
  SQLite-backed.

**The two ambiguity-handling behaviors are user-decided, not assumed:** when
retrieved sources disagree with no clear winner (a near-tie), this build
asks the user which source to trust (`status=needs_clarification_conflict`)
rather than picking silently. When the same concept is defined differently
across documents, it lists every definition found with its source and asks
which one applies (`status=needs_clarification_concept`).

## Known retrieval limitations (found through real testing)

TF-IDF is lexical, not semantic, and several concrete weaknesses surfaced
through testing against the real 77-document corpus — each has a mitigation
already in place, but none of them are a substitute for dense embeddings:

1. **Company name vs. generic financial phrase competition.** A short,
   repetitive chunk (a dense summary table) can outrank a chunk that
   actually names the right company but states it less densely. Mitigated
   by a company-name-aware boost in `local_store.py` and `doc_hints.py`
   (which also disambiguates by year when a company has multiple filings).
2. **Fact-specific questions with no company named.** A question
   identifiable only by a date or figure, with generic boilerplate section
   titles ("Consolidated Statements of Stockholders' Equity") shared by
   nearly every filing, can fail to surface the right document at all
   since there's no company name for the existing boost to grab onto.
   Mitigated by a parallel date-aware boost (`_detect_date_mentions` /
   `_rows_mentioning_dates` in `local_store.py`) that matches literal
   date strings in chunk text. Only fires on an exact (or comma-different)
   date match — a relative phrasing ("end of fiscal 2022") won't trigger it.
3. **Misspellings and abbreviations.** TF-IDF only matches literal tokens,
   so "CEO" doesn't match "Chief Executive Officer," and a typo like
   "revenuw" is invisible to the vectorizer entirely. Mitigated by
   `embeddings/embedder.py`: an abbreviation-expansion table (~25 common
   finance/business terms) and a `difflib`-based spelling correction
   against the corpus's own vocabulary (company names get a more permissive
   match threshold than generic words, since a wrong company would
   misdirect retrieval entirely).
4. **Raw cosine score is not a reliable relevance signal on its own.** A
   genuinely correct answer can score *below* an unrelated chunk that
   happens to repeat a common word densely (a real test case: "what is the
   capital of France" scored 0.894 against a finance chunk dominated by the
   word "capital"). The score-based short-circuit was removed; the LLM's
   own closed-book judgment is the sole not-found authority now — only a
   genuinely empty retrieval result skips the LLM call.
5. **Duplicate chunks per page.** Some pages (seen on tables specifically)
   produce two chunks: a `chunk_type="text"` extraction where table
   row-labels were garbled by generic text parsing, and a separate,
   correctly-structured `chunk_type="table"` extraction. Not yet a proven
   source of wrong answers (retrieval currently favors the clean chunk in
   every case checked), but flagged as a potential systemic ingestion issue
   worth a closer look if a sourcing gap turns up on a table-heavy page.
6. **`doc_hints.py`'s `_MIN_KEY_LENGTH = 4` guard** excludes short company
   keys (e.g. "3M," squashed length 2) from its own company detection.
   Currently masked by `local_store.py`'s separate, unguarded company
   boost, so it hasn't caused an observed failure — but doc_hints' year-
   disambiguation logic never runs for short-ticker companies as a result.

Dense embeddings (BGE, per the original spec) would likely reduce most of
these failure modes at once, since semantic similarity doesn't have TF-IDF's
"repeated common term in a short chunk" problem. This is the single
highest-value upgrade if you have normal internet access to install
`sentence-transformers` — see `requirements.txt`.

## Validated on real filings

The full 78-document corpus (FinanceBench-sourced 10-Ks, 10-Qs, and 8-Ks
across ~30 companies) has been ingested: 77 distinct documents (one
byte-identical duplicate correctly skipped by the content-hash idempotency
check), 13,627 total pages, 38,821 total chunks, zero ingestion errors. Full
per-file timing breakdown is in `data/vector_store/ingestion_report.md`.
Parse time (pdfplumber) dominates at ~65% of total pipeline time, correlated
with page count and table density rather than raw file size.

## Since the initial build: bug fixes and features from live testing

Real-question testing against the live corpus (not synthetic test cases)
surfaced and fixed several concrete gaps:

- **CEO/officer lookups returning false "not found."** Root cause was two
  compounding TF-IDF weaknesses: vocabulary mismatch ("CEO" vs. "Chief
  Executive Officer") and answer-dilution inside long bio-heavy chunks.
  Fixed via query-side abbreviation expansion (`embedder.py`) plus a
  company-name retrieval boost (`local_store.py`).
- **Follow-up questions losing conversational context.** Root cause was
  that retrieval ran on the raw follow-up text alone, before conversation
  history ever reached the LLM — an elliptical follow-up ("what about in
  2021?") has almost no retrievable keywords by itself. Fixed by having
  retrieval borrow prior turns only when the current question can't
  resolve a company on its own (a fresh, explicit mention always wins over
  inherited context).
- **Misspellings breaking retrieval entirely.** A `difflib`-based
  correction against the corpus's own vocabulary (see limitation #3 above),
  wired into both the TF-IDF query vector and `doc_hints`' company
  detection, which run at different points in the pipeline and both needed
  the correction independently.
- **Fact-specific, company-unnamed questions failing to retrieve at all.**
  See limitation #2 above (the date-aware boost).
- **Tabular answers (rollforwards, multi-period line items) rendering as
  plain text.** The LLM was already capable of producing GFM-style Markdown
  tables, but the frontend (`static/app.js`) escaped everything into plain
  `<p>` text with no Markdown interpretation. Fixed with a prompt
  instruction (`prompts.py`) plus a small, dependency-free Markdown
  renderer in `app.js` that recognizes pipe-table blocks and `**bold**`
  emphasis specifically (not a general Markdown parser) and escapes
  everything else, so the fix doesn't introduce an HTML-injection surface.
- **Cosmetic UI pass.** Refreshed visual design (avatars, status pills,
  icons, animations, color palette) across `templates/index.html`,
  `static/style.css`, and `static/app.js` — visual only, no retrieval or
  answer-generation logic touched.

## Project structure

```
financial-doc-qa/
├── config.py                  # all tunable constants in one place
├── app.py                      # Flask app: chat, upload, documents panel, citation viewer
├── templates/index.html        # single-page frontend
├── static/{app.js,style.css}   # frontend JS/CSS, no build step
├── ingestion/
│   ├── models.py               # ParsedDocument, PageContent, TableBlock, Chunk
│   ├── parser.py                # pdfplumber-based PDF parsing + heading detection
│   ├── chunker.py               # page-aware, paragraph-packed chunking
│   ├── html_converter.py        # Playwright HTML->PDF conversion for EDGAR filings
│   ├── pipeline.py              # orchestrates convert -> parse -> chunk -> store -> encrypt -> log
│   └── drive_source.py          # NOT used for PDF ingestion — see note above; kept for
│                                  # text-only Drive reads and as a record of why binary
│                                  # transfer through Drive's MCP surface was rejected
├── embeddings/
│   └── embedder.py              # TF-IDF fit/transform (swap point for BGE later)
├── vectorstore/
│   └── local_store.py           # SQLite + scipy sparse cosine search (swap point for Chroma/FAISS)
├── answer_generation/
│   ├── llm_client.py             # raw-HTTPS Anthropic Messages API client (swap point for the SDK)
│   ├── prompts.py                 # closed-book system prompt + forced tool-call schema
│   ├── answer.py                  # orchestrates retrieval -> routing -> LLM call -> citation validation
│   ├── routing.py                 # Haiku/Sonnet model routing heuristic
│   ├── doc_hints.py               # document-name-aware retrieval boost (see "Known retrieval limitations")
│   └── session_store.py           # 3-minute conversational memory window
├── security/
│   ├── encryption.py            # Fernet encryption at rest for source PDFs + originals
│   └── access_log.py            # append-only JSONL access log (ingestion + every Q&A)
├── scripts/
│   ├── ingest.py                 # CLI: ingest a file or folder
│   ├── ingest_timed.py            # CLI: ingest one file with full stage timing (used for the batch corpus load)
│   ├── query.py                   # CLI: test retrieval without an LLM call
│   └── ask.py                     # CLI: full question -> grounded answer, no app server needed
├── tests/
│   ├── generate_test_pdf.py     # builds the synthetic test document
│   └── sample_docs/              # generated test PDF lives here
└── data/                         # created on first run: raw/ (encrypted originals + PDFs),
                                    # processed/, incoming/, vector_store/ (SQLite + matrix + timing/report)
```

## Known limitations / open items

**Closed since the initial ingestion-only build:** the answer-generation
layer, the Flask app layer, and the raw-`.htm`-staging encryption gap are
all built and validated against the real 77-document corpus (see above).

**Still open:**
- The DNA doc's golden test set (§9) has no source data behind it yet —
  needs either a supplied Q&A set or a different validation plan.
- The SQLite/vector-index files are not encrypted at rest (only the source
  documents are) — would need SQLCipher or an encrypted volume.
- TF-IDF's vocabulary is corpus-wide, so `rebuild_index()` refits over
  every chunk on each ingestion run — fine at this corpus size, would need
  revisiting at a much larger one.
- Heading detection is a font-size heuristic, not a true document outline —
  works reasonably on filings with clear "Item X" section headers, isn't
  guaranteed on unusual formatting.
- The citation viewer jumps to the correct page but doesn't highlight the
  exact passage — would need full PDF.js text-layer integration.
- No authentication on the Flask app — fine for local single-user use as
  scoped, don't expose it beyond localhost without adding some.
- Near-tie conflict resolution and ambiguous-concept clarification are
  implemented and schema-validated, but haven't been exercised against a
  real conflicting-data case found organically in the corpus.
- No automated test suite (pytest) yet — validation so far has been
  manual, real-question testing against the live corpus.
