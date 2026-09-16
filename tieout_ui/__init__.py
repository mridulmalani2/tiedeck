"""A local web UI for TieOut.

Upload a deck, confirm what was derived from the client's reference material,
optionally turn on content review, and read the findings slide by slide.

It is a front end over the same functions the commands call and computes nothing
of its own, which is the only way a second interface stays honest. It binds a
loopback address and refuses anything else, serves one page with no external
references, and gates every request on a session token.

Installed by ``pip install tieout[ui]``. The core package is unaffected: nothing
in ``tieout`` imports this, and the air-gap walk treats it as forbidden.
"""

from __future__ import annotations

from tieout_ui.render import Renderer, RenderResult, soffice_path
from tieout_ui.session import Deck, SessionStore
from tieout_ui.view import audit_view, profile_view, rules_view

__all__ = [
    "Deck",
    "RenderResult",
    "Renderer",
    "SessionStore",
    "audit_view",
    "profile_view",
    "rules_view",
    "soffice_path",
]

__version__ = "0.1.0"
