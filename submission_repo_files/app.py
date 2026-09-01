"""
Flask app layer (DNA doc §6) — chat endpoint, document upload (drag-and-drop
+ documents panel), and a citation-viewer endpoint that serves the decrypted
PDF for a document so the frontend can jump to the cited page.

Flask, not FastAPI: FastAPI can't be pip-installed in this cloud sandbox
(see README "A note on the embedding/vector-store backend" for the same
pattern applied to other layers) — Flask is already available and covers
everything this app needs. Swapping to FastAPI later is a contained change;
the route handlers below don't do anything Flask-specific that FastAPI
couldn't do the same way.

Run with: python app.py  (dev server) — see README for production notes.
"""
import logging
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, Response, render_template, send_from_directory

from answer_generation.answer import ask
from answer_generation import doc_hints
from ingestion.pipeline import ingest_document
from vectorstore import local_store
from security.encryption import load_decrypted_bytes
from security.access_log import log_event
from config import INCOMING_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".htm", ".html"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not question:
        return jsonify({"error": "question is required"}), 400

    result = ask(question, session_id=session_id)
    result.setdefault("session_id", session_id)
    return jsonify(result)


@app.route("/api/documents", methods=["GET"])
def api_documents():
    docs = local_store.list_documents()
    stats = local_store.collection_stats()
    return jsonify({"documents": docs, "stats": stats})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "no file provided"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({"error": f"unsupported file type '{ext}' — only .pdf, .htm, .html"}), 400

    dest = INCOMING_DIR / f.filename
    f.save(dest)
    logger.info("Uploaded %s -> %s", f.filename, dest)

    try:
        result = ingest_document(dest, rebuild=True)
    except Exception as e:
        logger.error("Ingestion failed for uploaded file %s: %s", f.filename, e)
        return jsonify({"error": str(e)}), 500

    doc_hints.refresh()  # new document may add a detectable company key
    return jsonify(result)


@app.route("/api/document/<doc_id>/pdf")
def api_document_pdf(doc_id):
    try:
        pdf_bytes = load_decrypted_bytes(doc_id, kind="pdf")
    except FileNotFoundError:
        return jsonify({"error": "document not found"}), 404

    log_event("document_view", doc_id=doc_id)
    return Response(pdf_bytes, mimetype="application/pdf")


@app.route("/health")
def health():
    return jsonify({"status": "ok", **local_store.collection_stats()})


if __name__ == "__main__":
    # debug=False: this handles real financial documents and an API key —
    # Flask's debugger (if enabled) would expose both via its interactive
    # traceback console. host="0.0.0.0" is fine for local single-user use;
    # tighten this before exposing the app beyond localhost.
    app.run(host="0.0.0.0", port=5000, debug=False)
