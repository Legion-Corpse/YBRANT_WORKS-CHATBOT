from __future__ import annotations

import re

from app.config import settings

CURRENCY_FIGURE = re.compile(
    r"(?:[$₹€£]\s?\d[\d,.]*)"
    r"|(?:\bRs\.?\s?\d[\d,.]*)"
    r"|(?:\b\d[\d,.]*\s?(?:USD|INR|EUR|GBP|dollars?|rupees?|lakhs?|crores?)\b)"
    r"|(?:\b\d[\d,.]*k?\s?(?:per\s+(?:hour|month|year|project))\b)",
    re.IGNORECASE,
)

# Spelled-out amounts paired with a currency word ("five thousand dollars",
# "ten lakh rupees"). Low false-positive because the currency word is required.
_NUM_WORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|"
    r"lakhs|crore|crores|million|billion)"
)
WORDED_FIGURE = re.compile(
    rf"\b(?:{_NUM_WORD}(?:[\s-]+(?:and[\s-]+)?{_NUM_WORD})*)\s+"
    r"(?:dollars?|rupees?|usd|inr|euros?|pounds?)\b",
    re.IGNORECASE,
)

# A bare number sitting next to an explicit price noun/verb ("costs 5000",
# "priced at 25,000", "fee of 1.5k"). Requires the price word so plain counts
# ("around 5 services") are not mistaken for money.
PRICE_CONTEXT_FIGURE = re.compile(
    r"\b(?:cost|costs|costing|priced|charges?|charged|fee|fees|"
    r"rate|rates|quote[ds]?|quoted)\b[^.\n]{0,24}?"
    r"(?:[$₹€£]\s?)?(\d[\d,.]*\s?k?)\b",
    re.IGNORECASE,
)

# Identity / "who are you" small-talk. These meta questions have no answer in the
# ingested documents; identity is a fixed, known fact about the assistant, so
# answer it deterministically without an API call.
IDENTITY_INTENT = re.compile(
    r"\b(who\s+are\s+you|what\s+are\s+you|who\s+is\s+this|"
    r"who\s+am\s+i\s+(?:talking|speaking|chatting)\s+to|introduce\s+yourself|"
    r"are\s+you\s+(?:a\s+)?(?:bot|robot|human|person|ai|real|chatbot)|"
    r"what\s+can\s+you\s+do|how\s+can\s+you\s+help)\b",
    re.IGNORECASE,
)

# Role + navigation only — deliberately makes no factual claim about the company
# beyond what the site's own nav implies, so the bot never asserts anything that
# isn't in the documents. Substantive facts come from retrieved content when the
# user asks a real question.
IDENTITY_ANSWER = (
    "I'm the YbrantWorks website assistant. I answer questions about YbrantWorks "
    "using content from our website — ask me about our services, company, blogs, "
    f"careers, or how to get in touch, or email {settings.contact_email}."
)

# Neutral replacement when the invented-figure guard fires. Pricing now flows
# through the normal pipeline, so this is NOT a pricing deflection — it's the
# honest "I can't back that number up from the documents" fallback for any
# fabricated figure (price or otherwise).
NOT_IN_DOCS_ANSWER = (
    "I don't have that information in the documents I can access, so I can't give "
    "you a reliable figure. Please reach out via our Contact page or email "
    f"{settings.contact_email} and the team can help."
)


def has_identity_intent(message: str) -> bool:
    return bool(IDENTITY_INTENT.search(message))


def _normalize_currency_figure(figure: str) -> str:
    normalized = figure.strip().lower()
    normalized = re.sub(r"\brs\.", "rs", normalized)
    # Drop the currency symbol so "$500" and a bare "500" (extracted from
    # "costs 500") compare equal — otherwise the same amount worded two ways in
    # the answer vs the context would false-trigger the guard.
    normalized = re.sub(r"[$₹€£]", "", normalized)
    normalized = re.sub(r"(?<=\d),(?=\d)", "", normalized)
    normalized = re.sub(r"[\s-]+", "", normalized)
    return normalized


def _extract_figures(text: str) -> set[str]:
    """All money-like figures in ``text``, normalized for comparison."""
    figures: set[str] = set()
    for pattern in (CURRENCY_FIGURE, WORDED_FIGURE):
        figures |= {_normalize_currency_figure(m.group(0)) for m in pattern.finditer(text)}
    # PRICE_CONTEXT_FIGURE carries a price word; compare on the captured number.
    figures |= {
        _normalize_currency_figure(m.group(1)) for m in PRICE_CONTEXT_FIGURE.finditer(text)
    }
    return figures


def answer_invents_figures(answer: str, context: str) -> bool:
    """True if the answer contains a money figure not present in the retrieved
    context. ``context`` is the concatenated text of the file_search results.

    Note: with an empty context we can't verify, so we do NOT block (avoids
    false positives when the retrieved chunk text isn't available); the caller
    still runs a final check against the completed response's context."""
    if not context.strip():
        # No retrieved text to verify against — don't block (avoids false
        # positives); the streaming caller re-checks once context is available.
        return False
    figures = _extract_figures(answer)
    if not figures:
        return False
    context_figures = _extract_figures(context)
    return not figures.issubset(context_figures)
