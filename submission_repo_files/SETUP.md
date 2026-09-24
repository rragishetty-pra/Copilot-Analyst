# Copilot Analyst — Setup Guide

Copilot Analyst answers plain-English questions about financial filings (10-Ks, 10-Qs, 8-Ks). Every answer cites the document and page it came from, and if the answer isn't in your documents, it says so instead of guessing.

This guide takes about **15 minutes** the first time. After that, starting the app takes a single command.

---

## What you need

| Item | How to check / get it |
|---|---|
| **Python 3.10 or newer** | Open a terminal and run `python --version` (Mac: `python3 --version`). If it's missing or older, install it from [python.org](https://www.python.org/downloads/). On Windows, tick **"Add Python to PATH"** during install. |
| **An Anthropic API key** | Create one at [console.anthropic.com](https://console.anthropic.com) → **API Keys**. This is pay-as-you-go and separate from a Claude.ai subscription. Each person needs their own key. |
| **About 500 MB of free disk space** | Enough for about 75 documents. |

> **What's a terminal?** On Windows, open the Start menu and search for **PowerShell**. On Mac, press Cmd + Space and type **Terminal**. You'll type every command in this guide there, pressing Enter after each one.

---

## Step 1 — Open the project folder

Unzip the project, then move the terminal into the folder that contains `app.py`:

```bash
cd path/to/Copilot-Analyst-main/submission_repo_files
```

**Tip:** type `cd ` (with a space), then drag the folder from File Explorer or Finder into the terminal window, and press Enter.

To check you're in the right place, run `dir` (Windows) or `ls` (Mac). You should see `app.py` in the list.

---

## Step 2 — Create a private Python environment

This keeps the project's packages separate from everything else on your computer.

**Windows (PowerShell)**
```powershell
python -m venv venv
venv\Scripts\activate
```

> If PowerShell says *"running scripts is disabled"*, run this once, then try `venv\Scripts\activate` again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**Mac / Linux**
```bash
python3 -m venv venv
source venv/bin/activate
```

✅ It worked if the start of your terminal line now shows `(venv)`.

---

## Step 3 — Install the packages

```bash
pip install -r requirements.txt
```

This takes 1–3 minutes.

**Optional:** if you'll load `.htm` filings downloaded from SEC EDGAR, also run the command below. If you'll only use PDFs, skip it.
```bash
playwright install chromium
```

---

## Step 4 — Add your API key

1. Make a copy of the settings template:
   - Windows: `copy .env.example .env`
   - Mac: `cp .env.example .env`
2. Open `.env` in any text editor (Notepad works) and fill in your key:
   ```
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   ```
3. Leave the other two lines blank and save the file.

> 🔒 **Keep `.env` private.** Never email it, zip it up with the project, or upload it anywhere, because anyone who has your key can use it and run up charges on your account.

---

## Step 5 — Test with the sample document

The project includes a small sample 10-K. Load it to confirm everything works:

```bash
python scripts/ingest.py --file tests/sample_docs/acme_corp_10k_test.pdf
```

✅ It worked if you see:
```
[OK]      acme_corp_10k_test.pdf: 4 pages, 5 chunks
```

---

## Step 6 — Start the app

```bash
python app.py
```

Then open your browser and go to **http://localhost:5000**.

Try asking: *"What was total revenue for fiscal year 2024?"*
You should get **$22.1 million**, with a citation to page 2.

To **stop** the app, go back to the terminal and press **Ctrl + C**.

---

## Step 7 — Add your own documents

You can add documents in either of two ways:

- **In the app:** drag a `.pdf` or `.htm` file onto the sidebar, or click **Browse files**.
- **In bulk from the terminal** (open a second terminal window and activate the environment first, as in Step 2):
  ```bash
  python scripts/ingest.py --folder path/to/your/pdfs
  ```

A typical 50-page filing takes a few minutes. Files you've already loaded are skipped automatically.

---

## Starting the app again later

You only need Steps 1–5 once. After that, each time you want to use the app:

```bash
cd path/to/submission_repo_files
venv\Scripts\activate          # Mac: source venv/bin/activate
python app.py
```

Then open **http://localhost:5000**.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `python` is not recognized | Python isn't installed or isn't on PATH. Reinstall it and tick **"Add Python to PATH"**. On Mac, use `python3`. |
| `No such file: requirements.txt` | You're in the wrong folder. Go back to Step 1 and check that you can see `app.py`. |
| Answer says **"ANTHROPIC_API_KEY is not set"** | `.env` is missing or empty. Redo Step 4, and make sure the file is named exactly `.env`, not `.env.txt`. |
| Answer shows an **authentication / 401 error** | The key is wrong or has been revoked. Create a new key and paste it into `.env`. |
| **"Not found in this data"** for something you know is in a document | Run `python scripts/query.py "your question"` to see which passages the search is finding. If the right passage isn't listed, try rephrasing with the exact words the filing uses. |
| Clicking a citation shows **"document not found"** | The original PDF isn't stored on this computer. Load the file again (Step 7). |
| **Port 5000 already in use** | Another program is using that port. Close it, or change `port=5000` near the bottom of `app.py` to `port=5050` and open http://localhost:5050 instead. |
| `(venv)` has disappeared | You opened a new terminal. Run the activate command from Step 2 again. |

---

## Good to know

- **Cost:** each question uses your API key. Simple questions use the cheaper Haiku model, and complex multi-document questions use Sonnet.
- **Free check:** `python scripts/query.py "question"` searches your documents without calling the API, so it costs nothing.
- **Privacy:** your documents stay on your computer. Only the passages relevant to a question are sent to Anthropic to write the answer.
- **Back up** `data/.keyfile` (created on first run). Without it, the citation viewer can't open the PDFs you've loaded.
- **Single user only:** there's no login, so use it on your own computer only and don't share it over a network.
- **Works best on a laptop or desktop.** The layout doesn't fit phone screens yet.
