"""Decide, per page, whether the page needs Parse.

Embed reads the page text (and the page image), so a page only needs Parse when the text we already
have for it is not good enough to embed:

  low_text     almost no usable text (scans, faxes, photos, image-only pages). Evidence: on simulated
               scans, answers from Parse text were right where answers from the page image were wrong.
  table_heavy  most of the page is tables. Evidence is weaker: for finding the page, Parse and the
               dataset's own text performed about the same; Parse helped exact-figure answers a little.
               Switch this off to keep Parse for scans only.

Everything else keeps the text that ships in the bundle. The function is pure so it can be tested
without AWS.
"""
import re

# Parse writes HTML tables; the dataset's own text and most OCR tools write markdown pipe rows.
TABLE_RE = re.compile(r"<table.*?</table>", re.S | re.I)
PIPE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.M)

LOW_TEXT = "low_text"
TABLE_HEAVY = "table_heavy"
TEXT_OK = "text_ok"


def page_features(text):
    """Length of the page text and the share of it that sits inside tables (HTML or markdown)."""
    text = (text or "").strip()
    chars = len(text)
    html_tables = TABLE_RE.findall(text)
    pipe_rows = PIPE_ROW_RE.findall(TABLE_RE.sub("", text))
    table_chars = sum(len(m) for m in html_tables) + sum(len(m) for m in pipe_rows)
    return {"chars": chars, "table_ratio": (table_chars / chars) if chars else 0.0}


def route(text, *, route_low_text=True, route_tables=True, min_chars=600, table_ratio=0.5):
    """Returns (needs_parse, reason). `reason` is stored on the page so the choice can be audited."""
    f = page_features(text)
    if route_low_text and f["chars"] < min_chars:
        return True, LOW_TEXT
    if route_tables and f["table_ratio"] >= table_ratio:
        return True, TABLE_HEAVY
    return False, TEXT_OK
