#!/usr/bin/env python3
"""
Generates a small synthetic 10-K-style PDF (fictional "Acme Corp") purely for
pipeline testing — no real financial data involved. Structured across 4 pages
with a known figure, a known table, and one deliberately unanswerable
question, so retrieval accuracy and "not found" behavior (DNA doc §13) can
both be checked against ground truth.
"""
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

OUT_PATH = Path(__file__).resolve().parent / "sample_docs" / "acme_corp_10k_test.pdf"

styles = getSampleStyleSheet()
heading_style = ParagraphStyle("Heading", parent=styles["Heading1"], fontSize=14, spaceAfter=12)
body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, spaceAfter=10, leading=14)

doc = SimpleDocTemplate(str(OUT_PATH), pagesize=letter)
story = []

# --- Page 1: cover / business overview ---
story.append(Paragraph("Acme Corp — Annual Report, Fiscal Year 2024", heading_style))
story.append(Paragraph("Item 1. Business Overview", heading_style))
story.append(Paragraph(
    "Acme Corp is a fictional company created solely to test a document ingestion pipeline. "
    "It designs and sells industrial adhesives and fasteners across North America and Europe. "
    "The company was founded in 1998 and operates three manufacturing facilities. "
    "This document contains no real financial data and describes no real company.",
    body_style,
))
story.append(Paragraph(
    "The company's fiscal year ends December 31. Management believes the business remains "
    "well positioned in its core markets, supported by long-term supply agreements with "
    "several large industrial customers.",
    body_style,
))
story.append(PageBreak())

# --- Page 2: MD&A with a specific revenue figure ---
story.append(Paragraph("Item 7. Management's Discussion and Analysis", heading_style))
story.append(Paragraph(
    "Total revenue for fiscal year 2024 was $22.1 million, an increase of 8% from the prior "
    "year's total revenue of $20.5 million. The increase was primarily driven by higher unit "
    "volumes in the industrial fasteners segment, partially offset by pricing pressure in "
    "adhesives.",
    body_style,
))
story.append(Paragraph(
    "Operating expenses increased modestly year over year, reflecting continued investment in "
    "the company's sales organization. Management does not expect a material change in the "
    "cost structure over the next twelve months.",
    body_style,
))
story.append(PageBreak())

# --- Page 3: Financial statements with a table (incl. a DTI-style ratio) ---
story.append(Paragraph("Item 8. Financial Statements", heading_style))
story.append(Paragraph(
    "The following table summarizes selected balance sheet metrics for fiscal years 2024 and 2023:",
    body_style,
))
table_data = [
    ["Metric", "FY2024", "FY2023"],
    ["Total Assets", "$145.3M", "$132.7M"],
    ["Total Liabilities", "$67.2M", "$61.4M"],
    ["Total Equity", "$78.1M", "$71.3M"],
    ["Debt-to-Income Ratio", "34%", "31%"],
]
t = Table(table_data, colWidths=[180, 100, 100])
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
]))
story.append(t)
story.append(Spacer(1, 12))
story.append(Paragraph(
    "The Debt-to-Income Ratio increased slightly year over year, which management attributes "
    "to financing for the expansion of the third manufacturing facility.",
    body_style,
))
story.append(PageBreak())

# --- Page 4: unrelated filler, deliberately has no compensation data ---
story.append(Paragraph("Item 9A. Controls and Procedures", heading_style))
story.append(Paragraph(
    "Management, including the principal executive and financial officers, evaluated the "
    "effectiveness of the company's disclosure controls and procedures as of the end of the "
    "period covered by this report. Based on that evaluation, management concluded that the "
    "company's disclosure controls and procedures were effective.",
    body_style,
))
story.append(Paragraph(
    "There were no changes in internal control over financial reporting during the fiscal "
    "year that materially affected, or are reasonably likely to materially affect, internal "
    "control over financial reporting. This document does not disclose executive compensation "
    "figures.",
    body_style,
))

doc.build(story)
print(f"Wrote {OUT_PATH}")
