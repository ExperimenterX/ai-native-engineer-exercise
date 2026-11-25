"""Summing recalled units, and being honest about how far the numbers can be trusted.

The loader stores `units` as the FIRST number found in `units_text`. That is right for most rows
("About 1,200") but not all:

- "About 75,400 X and about 119,000 Y" is stored as 75,400: the second product is dropped.
- "About 30,000 (including the 28,000 ... recalled in December 2022)" overlaps an earlier recall.
- "About 8,740 (In addition, about 350 were sold in Canada)" is fine for US units; the Canada
  and Mexico units are never counted here.

So a total is reported as: everything as stored, the part we are confident in, and the part that
needs a human look, with examples.
"""
from __future__ import annotations

import re

# A parenthetical that only talks about Canada/Mexico sales: not US units, safe to ignore.
_NON_US_PAREN = re.compile(r"\([^()]*(?:canada|mexico)[^()]*\)", re.IGNORECASE)
_OVERLAP_WORDS = re.compile(r"previous|including|total|recalled|additional|in addition|combined|prior", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*")


def classify(units_text: str | None) -> str:
    """'clean' when the text holds exactly one quantity we can trust, else 'review'."""
    text = _NON_US_PAREN.sub("", units_text or "")
    if _OVERLAP_WORDS.search(text):
        return "review"
    return "clean" if len(_NUMBER.findall(text)) == 1 else "review"


def summarize(rows: list[dict], top: int = 5, examples: int = 10) -> dict:
    total = sum(r["units"] or 0 for r in rows)
    clean = [r for r in rows if classify(r["units_text"]) == "clean"]
    review = [r for r in rows if classify(r["units_text"]) == "review"]
    clean_total = sum(r["units"] or 0 for r in clean)

    def brief(r: dict) -> dict:
        return {
            "recall_number": r["recall_number"],
            "title": (r["title"] or "")[:120],
            "recall_date": r["recall_date"],
            "units": r["units"],
            "units_text": (r["units_text"] or "")[:160],
            "units_quality": classify(r["units_text"]),
        }

    return {
        "recall_count": len(rows),
        "units_total": total,
        "clean_units_total": clean_total,
        "clean_recalls": len(clean),
        "review_units_total": total - clean_total,
        "review_recalls": len(review),
        "review_examples": [brief(r) for r in review[:examples]],
        "top_recalls": [brief(r) for r in rows[:top]],
        "how_to_read": (
            "units_total sums the stored `units` (US units only; units sold in Canada/Mexico are never "
            "included). 'clean' recalls state a single quantity. 'review' recalls mention several products, "
            "an earlier recall, or an additional quantity, so their stored figure may be too low or overlap "
            "another recall; check the units_text of those before quoting units_total as exact."
        ),
    }
