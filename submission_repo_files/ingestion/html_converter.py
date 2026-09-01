"""
HTML -> PDF conversion for SEC EDGAR filings.

Real 10-K/10-Q filings from EDGAR are usually distributed as raw .htm, not
PDF — the DNA doc's ingestion pipeline is spec'd for born-digital PDFs only
(§3, §10), so this is a pre-processing step, not a parser replacement. It
converts the filing to a real PDF once, up front; ingestion/parser.py never
needs to know an HTML step happened.

Uses Playwright (pre-installed in this environment, Chromium already
available at /opt/pw-browsers/chromium) to actually render the filing in a
browser engine and print it — not a text-only HTML-to-PDF converter — because
EDGAR filings lean heavily on CSS-positioned tables that a naive converter
mangles. This also means page breaks come from real layout, matching what a
human would see if they printed the filing, not an arbitrary chunker's
guess at where a "page" should end.

EDGAR's raw submission files wrap the actual HTML in an SGML envelope
(<DOCUMENT><TYPE>10-K>...<TEXT> ... </TEXT></DOCUMENT>) — strip_sgml_wrapper()
isolates the <html>...</html> span before rendering.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def strip_sgml_wrapper(html_path: str | Path) -> str:
    """Returns clean HTML text with any EDGAR SGML envelope removed."""
    html_path = Path(html_path)
    content = html_path.read_text(errors="replace")

    start = content.lower().find("<html")
    if start == -1:
        return content  # no <html> tag found — hand back as-is, let the caller decide

    end = content.lower().find("</html>")
    end = end + len("</html>") if end != -1 else len(content)
    return content[start:end]


def convert_html_to_pdf(html_path: str | Path, pdf_path: str | Path, timeout_ms: int = 300_000) -> Path:
    """Render an HTML filing to PDF via a real browser engine (Playwright +
    Chromium), so CSS-positioned tables and page layout come out the way a
    human reading the filing would see them."""
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path)
    pdf_path = Path(pdf_path)

    clean_html = strip_sgml_wrapper(html_path)
    clean_path = html_path.with_suffix(".clean.html")
    clean_path.write_text(clean_html)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        try:
            page = browser.new_page()
            page.set_default_navigation_timeout(timeout_ms)
            page.set_default_timeout(timeout_ms)
            page.goto(f"file://{clean_path.resolve()}", wait_until="load")
            page.emulate_media(media="print")
            page.pdf(
                path=str(pdf_path),
                format="Letter",
                print_background=True,
                margin={"top": "0.6in", "bottom": "0.6in", "left": "0.6in", "right": "0.6in"},
            )
        finally:
            browser.close()

    logger.info("Converted %s -> %s", html_path.name, pdf_path.name)
    return pdf_path
