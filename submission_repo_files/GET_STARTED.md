# Get Started

## 1. Get the code

```bash
git clone financial-doc-qa.bundle financial-doc-qa
cd financial-doc-qa
```

(Using a hosted repo instead? Just `git clone <repo-url>` there.)

## 2. Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## 3. Add your API key

```bash
cp .env.example .env
```

Open `.env` and add your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-...
```
(Get one at console.anthropic.com — each user needs their own key.)

## 4. Add your documents

```bash
python scripts/ingest.py --folder /path/to/your/pdfs
```

Wait for it to finish, then move on.

## 5. Run it

```bash
python app.py
```

Open **http://localhost:5000** and start asking questions.
