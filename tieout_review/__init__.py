"""Optional LLM content review for TieOut, with redaction on the way out.

``tieout`` proper makes a structural promise: no network, no telemetry, no model
calls, enforced by an abstract-syntax-tree walk over every runtime module. This
package is separate, and installed separately (``pip install tieout[review]``),
so that promise stays checkable rather than becoming a claim about a code path.

What it adds is narrow. TieOut's own consistency rules already compare labelled
figures between tables arithmetically. What they cannot do is read: a headline
claiming 20% growth over a table showing 8% is a sentence, not a sum. This layer
sends the deck's text — and only its text, with every identifying term replaced
by a stable placeholder — to a model and asks that one class of question.

The redaction is the point, and it fails closed. See
:mod:`tieout_review.redact` for the boundary itself and
:mod:`tieout_review.review` for why nothing can be transmitted without a person
having read what the redactor could not clear.
"""

from __future__ import annotations

from tieout_review.extract import DeckPayload, TextItem, extract
from tieout_review.redact import (
    FINANCE_VOCABULARY,
    Redacted,
    Redaction,
    Redactor,
    Residual,
    TermSource,
)
from tieout_review.review import (
    SEMANTIC_CATEGORY,
    SEMANTIC_RULES,
    Prepared,
    RedactionFailed,
    RedactionHeld,
    ResponseError,
    ReviewOutcome,
    SemanticRule,
    prepare,
    send,
)
from tieout_review.terms import assemble, blocklist_from_file, blocklist_from_text

__all__ = [
    "FINANCE_VOCABULARY",
    "SEMANTIC_CATEGORY",
    "SEMANTIC_RULES",
    "DeckPayload",
    "Prepared",
    "Redacted",
    "Redaction",
    "RedactionFailed",
    "RedactionHeld",
    "Redactor",
    "Residual",
    "ResponseError",
    "ReviewOutcome",
    "SemanticRule",
    "TermSource",
    "TextItem",
    "assemble",
    "blocklist_from_file",
    "blocklist_from_text",
    "extract",
    "prepare",
    "send",
]

__version__ = "0.1.0"
