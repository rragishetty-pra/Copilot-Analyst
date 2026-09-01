// Chat + upload + citation-viewer wiring. Vanilla JS, no build step —
// consistent with the DNA doc's "lightweight web UI" framing (§7).

const sessionId = crypto.randomUUID();

const messagesEl = document.getElementById("messages");
const askForm = document.getElementById("askForm");
const questionInput = document.getElementById("questionInput");
const documentListEl = document.getElementById("documentList");
const docCountEl = document.getElementById("docCount");
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const browseBtn = document.getElementById("browseBtn");
const uploadStatus = document.getElementById("uploadStatus");
const viewer = document.getElementById("viewer");
const viewerFrame = document.getElementById("viewerFrame");
const viewerTitle = document.getElementById("viewerTitle");
const closeViewer = document.getElementById("closeViewer");

// ---- Small inline icons (kept here rather than as files — no build step,
// no icon-font dependency, and these are the only handful the app needs) ----

const ICON_ASSISTANT =
  '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
  '<path d="M6 3.5h8l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V5A1.5 1.5 0 0 1 5.5 3.5H6Z" stroke="white" stroke-width="1.4"/>' +
  '<path d="M14 3.5V7a1 1 0 0 0 1 1h3.5" stroke="white" stroke-width="1.4"/>' +
  '<path d="M8.2 13.4l2.2 2.2L15.8 9.9" stroke="white" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
  "</svg>";

const ICON_USER =
  '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
  '<circle cx="12" cy="8.5" r="3.5" stroke="white" stroke-width="1.5"/>' +
  '<path d="M4.5 20c1-3.5 4-5.5 7.5-5.5s6.5 2 7.5 5.5" stroke="white" stroke-width="1.5" stroke-linecap="round"/>' +
  "</svg>";

const ICON_DOC =
  '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
  '<path d="M6 3.5h8l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V5A1.5 1.5 0 0 1 5.5 3.5H6Z" stroke="currentColor" stroke-width="1.4"/>' +
  '<path d="M14 3.5V7a1 1 0 0 0 1 1h3.5" stroke="currentColor" stroke-width="1.4"/>' +
  "</svg>";

function assistantAvatarHtml() {
  return `<div class="avatar avatar-assistant" aria-hidden="true">${ICON_ASSISTANT}</div>`;
}
function userAvatarHtml() {
  return `<div class="avatar avatar-user" aria-hidden="true">${ICON_USER}</div>`;
}

const STATUS_LABELS = {
  answered: "Answered",
  partial: "Partial match",
  not_found: "Not found",
  needs_clarification_conflict: "Needs input",
  needs_clarification_concept: "Needs input",
  error: "Error",
};

function statusPillHtml(status) {
  const label = STATUS_LABELS[status] || status || "";
  return `<span class="status-pill pill-${status}">${escapeHtml(label)}</span>`;
}

// ---- Documents panel ----

async function loadDocuments() {
  const res = await fetch("/api/documents");
  const data = await res.json();
  docCountEl.textContent = data.stats.distinct_documents;
  documentListEl.innerHTML = "";
  data.documents.forEach((doc) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="doc-icon" aria-hidden="true">${ICON_DOC}</span>
                     <span class="doc-info">
                       <span class="doc-name">${escapeHtml(doc.doc_name)}</span>
                       <span class="doc-meta">${doc.page_count} pages · ${doc.chunk_count} chunks</span>
                     </span>`;
    documentListEl.appendChild(li);
  });
}

// ---- Upload (drag-and-drop + browse) ----

browseBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFile(fileInput.files[0]);
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

async function uploadFile(file) {
  uploadStatus.textContent = `Ingesting ${file.name}… this can take a minute or two.`;
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) {
      uploadStatus.textContent = `Failed: ${data.error || "unknown error"}`;
      return;
    }
    if (data.status === "ingested") {
      uploadStatus.textContent = `Done — ${data.page_count} pages, ${data.chunk_count} chunks indexed.`;
    } else if (data.status === "skipped") {
      uploadStatus.textContent = "Already ingested (unchanged file).";
    } else {
      uploadStatus.textContent = `Status: ${data.status}`;
    }
    loadDocuments();
  } catch (err) {
    uploadStatus.textContent = `Upload failed: ${err}`;
  }
}

// ---- Chat ----

askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  addUserMessage(question);
  questionInput.value = "";
  questionInput.disabled = true;

  const thinkingBubble = addAssistantThinking();

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId }),
    });
    const result = await res.json();
    thinkingBubble.remove();
    renderAssistantMessage(result);
  } catch (err) {
    thinkingBubble.remove();
    renderErrorMessage(String(err));
  } finally {
    questionInput.disabled = false;
    questionInput.focus();
  }
});

function addUserMessage(text) {
  const row = document.createElement("div");
  row.className = "msg user";
  row.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>${userAvatarHtml()}`;
  messagesEl.appendChild(row);
  scrollToBottom();
}

function addAssistantThinking() {
  const row = document.createElement("div");
  row.className = "msg assistant";
  row.innerHTML = `${assistantAvatarHtml()}<div class="bubble thinking-bubble" aria-label="Thinking">
    <span class="thinking-dots"><span></span><span></span><span></span></span>
  </div>`;
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function renderErrorMessage(text) {
  const row = document.createElement("div");
  row.className = "msg assistant";
  row.innerHTML = `${assistantAvatarHtml()}<div class="bubble status-error">${statusPillHtml("error")}<p class="answer-text">Something went wrong: ${escapeHtml(text)}</p></div>`;
  messagesEl.appendChild(row);
  scrollToBottom();
}

// ---- lightweight markdown rendering (tables + bold) ----
//
// The answer-generation LLM is now instructed (see prompts.py) to format
// naturally tabular data — rollforwards, multi-period schedules, anything
// with repeated line items x multiple attributes — as a GFM-style Markdown
// pipe table. This app has no build step and no markdown dependency, so
// this is a small, self-contained renderer for just the two things the
// model actually produces: pipe tables and **bold** emphasis. Everything
// else stays as escaped plain text — this is not a general markdown parser.

function renderAnswerMarkdown(text) {
  if (!text) return "";
  const blocks = text.split(/\n\s*\n/);
  return blocks.map(renderBlock).join("");
}

function renderBlock(block) {
  const lines = block.split("\n").filter((l) => l.trim().length > 0);
  if (lines.length >= 2 && isTableRow(lines[0]) && isSeparatorRow(lines[1])) {
    return renderTable(lines);
  }
  return `<p>${renderInline(block).replace(/\n/g, "<br>")}</p>`;
}

function isTableRow(line) {
  return line.includes("|");
}

function isSeparatorRow(line) {
  return /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/.test(line.trim());
}

function splitTableRow(line) {
  let cells = line.trim();
  if (cells.startsWith("|")) cells = cells.slice(1);
  if (cells.endsWith("|")) cells = cells.slice(0, -1);
  return cells.split("|").map((c) => c.trim());
}

function renderTable(lines) {
  const header = splitTableRow(lines[0]);
  const bodyLines = lines.slice(2);
  let html = `<div class="table-wrap"><table class="answer-table"><thead><tr>`;
  header.forEach((h) => (html += `<th>${renderInline(h)}</th>`));
  html += `</tr></thead><tbody>`;
  bodyLines.forEach((line) => {
    if (!line.includes("|")) return;
    const cells = splitTableRow(line);
    html += `<tr>`;
    cells.forEach((c) => (html += `<td>${renderInline(c)}</td>`));
    html += `</tr>`;
  });
  html += `</tbody></table></div>`;
  return html;
}

function renderInline(text) {
  let escaped = escapeHtml(text);
  // **bold** -> <strong>bold</strong> (escapeHtml already neutralized any
  // raw HTML in the source text, so this only ever matches literal asterisks)
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  return escaped;
}

function renderAssistantMessage(result) {
  const row = document.createElement("div");
  row.className = "msg assistant";
  row.innerHTML = assistantAvatarHtml();
  const bubble = document.createElement("div");
  bubble.className = `bubble status-${result.status}`;

  if (result.status === "not_found") {
    bubble.innerHTML = `${statusPillHtml(result.status)}<p class="answer-text">Not found in this data.</p>`;
  } else if (result.status === "error") {
    bubble.innerHTML = `${statusPillHtml(result.status)}<p class="answer-text">${escapeHtml(result.error || "An error occurred.")}</p>`;
  } else if (result.status === "answered" || result.status === "partial") {
    let html = statusPillHtml(result.status);
    html += `<div class="answer-text">${renderAnswerMarkdown(result.answer || "")}</div>`;
    if (result.status === "partial" && result.partial_note) {
      html += `<p class="partial-note">${renderInline(result.partial_note)}</p>`;
    }
    if (result.citations && result.citations.length) {
      html += `<div class="citations">`;
      result.citations.forEach((c, i) => {
        html += `<div class="citation" data-citation-index="${i}">
          <span class="cite-head">${escapeHtml(c.doc_name)}, p.${c.page_number} — ${escapeHtml(c.concept || "")}</span>
          <span class="cite-via">"${escapeHtml((c.sourced_via || "").slice(0, 200))}"</span>
        </div>`;
      });
      html += `</div>`;
    }
    html += traceToggleHtml(result.reasoning_trace);
    bubble.innerHTML = html;
    bubble.querySelectorAll(".citation").forEach((el) => {
      el.addEventListener("click", () => {
        const c = result.citations[Number(el.dataset.citationIndex)];
        openViewer(c);
      });
    });
  } else if (result.status === "needs_clarification_conflict") {
    let html = statusPillHtml(result.status);
    html += `<p class="answer-text">${escapeHtml(result.clarification_question || "")}</p>`;
    html += `<ul class="clarify-list">`;
    (result.conflicting_sources || []).forEach((s) => {
      html += `<li>${escapeHtml(s.doc_name)}, p.${s.page_number}: <strong>${escapeHtml(s.value)}</strong></li>`;
    });
    html += `</ul>`;
    bubble.innerHTML = html;
  } else if (result.status === "needs_clarification_concept") {
    let html = statusPillHtml(result.status);
    html += `<p class="answer-text">${escapeHtml(result.clarification_question || "")}</p>`;
    html += `<ul class="clarify-list">`;
    (result.concept_definitions || []).forEach((d) => {
      html += `<li>${escapeHtml(d.doc_name)}, p.${d.page_number}: ${escapeHtml(d.definition)}</li>`;
    });
    html += `</ul>`;
    bubble.innerHTML = html;
  } else {
    bubble.innerHTML = `${statusPillHtml(result.status || "error")}<p class="answer-text">${escapeHtml(JSON.stringify(result))}</p>`;
  }

  if (result.model_used) {
    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = `${result.model_used} · ${result.elapsed_seconds}s`;
    bubble.appendChild(meta);
  }

  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();

  const trace = bubble.querySelector(".trace-toggle");
  if (trace) {
    trace.addEventListener("click", () => {
      const body = bubble.querySelector(".trace-body");
      body.classList.toggle("open");
      trace.textContent = body.classList.contains("open") ? "Hide reasoning" : "Show reasoning";
    });
  }
}

function traceToggleHtml(trace) {
  if (!trace) return "";
  return `<span class="trace-toggle">Show reasoning</span>
          <div class="trace-body">${escapeHtml(trace)}</div>`;
}

// ---- Citation viewer (native browser PDF rendering via #page=N) ----
//
// This jumps to the cited page (Chrome/Firefox/Edge's built-in PDF viewer
// honors the #page=N URL fragment) but does not highlight the exact passage
// on the page — that needs full PDF.js text-layer integration, which is
// flagged as a follow-up rather than built into this first pass. The exact
// passage text is already shown inline in the citation (see cite-via above)
// as the practical stand-in.

function openViewer(citation) {
  viewerTitle.textContent = `${citation.doc_name} — page ${citation.page_number}`;
  viewerFrame.src = `/api/document/${citation.doc_id || ""}/pdf#page=${citation.page_number}`;
  viewer.classList.remove("hidden");
}

closeViewer.addEventListener("click", () => {
  viewer.classList.add("hidden");
  viewerFrame.src = "";
});

// ---- utils ----

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

loadDocuments();
