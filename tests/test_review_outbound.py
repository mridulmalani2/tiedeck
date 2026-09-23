"""The approval binding, the outbound record, and the corpus that drives both.

Three separate claims live here, and they are here together because they are one
argument: *nothing leaves without a person naming what they approved, and
nothing leaves without a line in a log saying it did.*

1. **The binding.** An approval names a digest over the payload and its residual
   list. An approval that names something else is refused, and a refusal
   transmits nothing — asserted against what a fake transport actually received,
   never against a return value, because a return value is what a broken
   implementation is best at producing.
2. **The record.** One line per transmission, written before the transport is
   called, containing everything compliance needs and none of the payload.
3. **The corpus.** Decks built to defeat the redactor in the ways real decks do
   it by accident: a name split across two runs by a stray formatting change, a
   soft hyphen inside it, a non-breaking space between its words, a name that
   appears once and only in six-point footnote text, and a name that is also an
   ordinary English word.

The last is the one to read carefully. ``test_no_seeded_name_reaches_the_fake
_transport`` does not assert that every adversarial name was *redacted* — the
module docstring in :mod:`tieout_review.redact` is explicit that an invented
one-word name indistinguishable from prose cannot be, and the residual list
exists precisely because of it. It asserts the weaker and true thing: either the
name was redacted, or the payload was **held** and never handed over. Those are
the only two acceptable outcomes, and a deck that quietly took the third is what
this file is for.
"""

from __future__ import annotations

import json

import pytest
from pptx import Presentation
from pptx.util import Emu, Pt

from tieout.model.loader import load_deck
from tieout_review.attest import residual_digest, short_digest, text_digest
from tieout_review.outbound import (
    OUTBOUND_LOG_ENV,
    OutboundLogError,
    OutboundRecord,
    outbound_log_path,
    read_log,
)
from tieout_review.outbound import record as record_outbound
from tieout_review.redact import Residual
from tieout_review.review import (
    ApprovalMismatch,
    RedactionHeld,
    prepare,
    send,
)

#: The invisible characters the corpus hides a name inside, as a test-side
#: helper rather than an import of the thing under test. A test that normalises
#: with the module's own function cannot tell you that the module normalises.
_HIDDEN = "\u00ad\u180e\u200b\u200c\u200d\u2060\ufeff"


def _readable(value: str) -> str:
    """``value`` as a reader — or a model — actually sees it."""
    for character in _HIDDEN:
        value = value.replace(character, "")
    return value.casefold()


class FakeTransport:
    """Records everything it is handed and answers with nothing.

    The assertions in this file are made against :attr:`received`, not against
    what ``send`` returned. "Nothing was sent" is a claim about the transport,
    so the transport is what gets asked.
    """

    model = "fake-model"

    def __init__(self) -> None:
        self.received: list[tuple[str, str, dict[str, object]]] = []

    def complete(self, system, user, schema):
        self.received.append((system, user, schema))
        return json.dumps({"findings": []}), {"input_tokens": 1, "output_tokens": 1}

    @property
    def everything_seen(self) -> str:
        """All text that crossed the boundary, in one string to search."""
        return "\n".join(
            system + "\n" + user + "\n" + json.dumps(schema)
            for system, user, schema in self.received
        )


# --------------------------------------------------------------------------- #
# The adversarial corpus
# --------------------------------------------------------------------------- #
#
# Each deck is the smallest thing that contains one construction. They are built
# here rather than added to the shared fixture because the shared fixture is
# asserted against by name and count across the suite, and because what is being
# tested is exactly the stuff TieOut's own generator would never produce.

#: The name seeded into each deck, and the way it is broken up. Kept beside the
#: builders so a reader can see the whole corpus without scrolling.
SEEDED_NAME = "Thornbury"


def _presentation():
    presentation = Presentation()
    presentation.slide_width = Emu(960 * 12700)
    presentation.slide_height = Emu(540 * 12700)
    return presentation


def _slide(presentation):
    return presentation.slides.add_slide(presentation.slide_layouts[6])


def _runs(slide, left, top, width, height, pieces, size=12):
    """One paragraph whose text is split across several runs.

    A name arrives like this whenever someone bolded half of it, or pasted it
    and PowerPoint kept the source's run boundaries. The redactor sees the
    concatenated paragraph text, and this is the test that it does.
    """
    box = slide.shapes.add_textbox(Pt(left), Pt(top), Pt(width), Pt(height))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    for piece in pieces:
        run = paragraph.add_run()
        run.text = piece
        run.font.size = Pt(size)
        run.font.name = "Calibri"
    return box


def _deck(tmp_path, name, pieces, *, size=12, extra=()):
    presentation = _presentation()
    slide = _slide(presentation)
    _runs(slide, 60, 60, 600, 40, ["Transaction overview"], size=24)
    _runs(slide, 60, 140, 700, 60, pieces, size=size)
    for index, line in enumerate(extra):
        _runs(slide, 60, 220 + index * 30, 700, 28, [line])
    path = tmp_path / f"{name}.pptx"
    presentation.save(path)
    return load_deck(path)


@pytest.fixture
def adversarial(tmp_path):
    """Five decks, one construction each, all seeding the same name."""
    return {
        # Split across runs by a formatting change nobody remembers making.
        "split_runs": _deck(
            tmp_path, "split_runs", ["Thorn", "bury", " Holdings reported growth"]
        ),
        # A soft hyphen, which a word processor inserts and nothing renders.
        "soft_hyphen": _deck(
            tmp_path, "soft_hyphen", ["Thorn­bury Holdings reported growth"]
        ),
        # A non-breaking space, which is what you get from copying out of Excel.
        # A zero-width space, which arrives from a web paste and renders as
        # nothing at all.
        "zero_width": _deck(
            tmp_path, "zero_width", ["Thorn​bury Holdings reported growth"]
        ),
        "nbsp": _deck(tmp_path, "nbsp", ["Thornbury Holdings reported growth"]),
        # Named once, in footnote type, where nobody proofreads.
        "footnote_only": _deck(
            tmp_path,
            "footnote_only",
            ["Revenue grew 8% year on year"],
            extra=("(a) Source: Thornbury Holdings management accounts",),
        ),
        # A name that is also an ordinary word. No detector can tell it from
        # prose -- redact.py says so in its own first paragraph -- so the only
        # thing that saves it is the analyst having typed it into the blocklist,
        # and the property under test is that a literal term is replaced even
        # where it reads as an ordinary sentence.
        "ordinary_word": _deck(
            tmp_path, "ordinary_word", ["We rate Vantage ahead of the market"]
        ),
    }


@pytest.mark.parametrize(
    "construction",
    ["split_runs", "soft_hyphen", "zero_width", "nbsp", "footnote_only", "ordinary_word"],
)
def test_no_seeded_name_reaches_the_fake_transport(adversarial, construction):
    """Redacted, or held. There is no third outcome.

    The seeded name is on the blocklist in every case, which is the realistic
    setup: the analyst typed the client's name into the forbidden box. What
    varies is whether the deck's own construction lets the redactor find it.
    """
    deck = adversarial[construction]
    forbidden = ["Thornbury Holdings", "Vantage"]
    prepared = prepare(deck, None, forbidden=forbidden)
    transport = FakeTransport()

    try:
        send(prepared.approve(prepared.digest), transport)
    except RedactionHeld:
        assert transport.received == [], "a held payload must not have been handed over"
        return

    # Searched with the invisible characters taken out, which is how a model
    # reads it. Searching the raw string is what let the soft-hyphen leak sit
    # here passing: "thorn\u00adbury" does not contain "thornbury", so the
    # assertion was true and the name went out anyway.
    seen = _readable(transport.everything_seen)
    for term in ("thornbury", "vantage"):
        assert term not in seen, (
            f"{construction}: {term!r} crossed the boundary in the "
            f"{len(transport.received)} message(s) the transport received"
        )


def test_the_corpus_is_not_trivially_clean(adversarial):
    """A guard on the test above, not on the code.

    If the builders ever stopped putting the name in the payload at all, every
    assertion above would pass for the wrong reason and nobody would notice. So
    assert the raw, unredacted payload does contain it.
    """
    from tieout_review.extract import extract

    for construction in ("split_runs", "soft_hyphen", "nbsp", "footnote_only"):
        raw = extract(adversarial[construction]).render()
        normalised = raw.replace("­", "").replace(" ", " ").casefold()
        assert "thornbury" in normalised, construction


# --------------------------------------------------------------------------- #
# The digest
# --------------------------------------------------------------------------- #


def _residual(text="Halvorsen", line=3):
    return Residual(text=text, reason="capitalised, not a known word", occurrences=1,
                    first_line=line)


def test_the_digest_covers_the_payload_and_not_only_the_residuals():
    """The empty-residual case, which is the one that matters.

    A digest over the residual list alone hashes "nothing outstanding" to one
    constant for every deck in the world, and an approval granted for one clean
    deck would then validate a send of any other. "Clear" would become the way
    around the check.
    """
    one = residual_digest("slide one text", [])
    two = residual_digest("slide two text", [])
    assert one != two


def test_the_digest_covers_the_residuals_and_not_only_the_payload():
    same_text = "identical payload"
    assert residual_digest(same_text, []) != residual_digest(same_text, [_residual()])


def test_the_digest_does_not_depend_on_the_order_residuals_were_found_in():
    """Otherwise an unrelated change to the scan order looks, to whoever is
    holding an approval, exactly like the deck being tampered with."""
    a, b = _residual("Alpha", 1), _residual("Beta", 9)
    assert residual_digest("text", [a, b]) == residual_digest("text", [b, a])


def test_the_digest_is_stable_across_calls():
    assert residual_digest("text", [_residual()]) == residual_digest("text", [_residual()])


def test_the_version_is_part_of_the_hashed_material(monkeypatch):
    """So a change to the canonical form invalidates old digests rather than
    silently matching a payload they were never computed over."""
    import tieout_review.attest as attest

    before = residual_digest("text", [_residual()])
    monkeypatch.setattr(attest, "DIGEST_VERSION", attest.DIGEST_VERSION + 1)
    assert residual_digest("text", [_residual()]) != before


def test_short_digest_says_so_when_there_is_no_digest():
    assert short_digest(None) == "no digest"
    assert short_digest("") == "no digest"
    assert short_digest("a" * 64) == "a" * 16


# --------------------------------------------------------------------------- #
# The binding
# --------------------------------------------------------------------------- #


@pytest.fixture
def held(tmp_path):
    """A prepared payload with something outstanding, so the hold is live."""
    deck = _deck(tmp_path, "held", ["We rate Halvorsen Estates the strongest of the three"])
    prepared = prepare(deck, None)
    assert prepared.plan.residuals, "this deck is expected to leave a residual"
    return prepared


def test_an_absent_approval_refuses_and_transmits_nothing(held):
    transport = FakeTransport()
    with pytest.raises(RedactionHeld):
        send(held, transport)
    assert transport.received == []


def test_a_stale_digest_is_refused_by_name(held):
    """The message is the deliverable here. Whoever sees it did read a list and
    did agree to it; what they need to be told is that it was another list."""
    with pytest.raises(ApprovalMismatch) as excinfo:
        held.approve("0" * 64)
    assert "the deck changed since you approved this" in str(excinfo.value)
    assert short_digest(held.digest) in str(excinfo.value)


def test_a_stale_digest_transmits_nothing(held):
    """Asserted against the transport, because that is the claim."""
    transport = FakeTransport()
    with pytest.raises(ApprovalMismatch):
        send(held.approve("0" * 64), transport)
    assert transport.received == []


def test_an_approval_from_a_different_deck_does_not_carry_over(tmp_path):
    """The realistic version of a stale digest: two decks open in one session."""
    one = prepare(_deck(tmp_path, "one", ["We rate Vantage ahead"]), None)
    two = prepare(_deck(tmp_path, "two", ["We rate Meridian ahead"]), None)
    assert one.digest != two.digest
    with pytest.raises(ApprovalMismatch):
        two.approve(one.digest)


def test_an_approval_does_not_survive_an_edited_blocklist(tmp_path):
    """§6's case: the blocklist changes between approving and sending."""
    deck = _deck(tmp_path, "blocklist", ["We rate Halvorsen Estates the strongest"])
    before = prepare(deck, None, forbidden=["Halvorsen Estates"])
    after = prepare(deck, None, forbidden=[])
    assert before.digest != after.digest
    with pytest.raises(ApprovalMismatch):
        after.approve(before.digest)


def test_a_matching_approval_sends(held):
    transport = FakeTransport()
    send(held.approve(held.digest), transport)
    assert len(transport.received) == 1


def test_approving_does_not_mutate_in_place(held):
    approved = held.approve(held.digest)
    assert held.approved_digest is None
    assert approved.approved_digest == held.digest


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #


def test_a_send_writes_exactly_one_line(held, outbound_log):
    send(held.approve(held.digest), FakeTransport())
    assert len(outbound_log.read_text(encoding="utf-8").splitlines()) == 1


def test_the_line_says_what_compliance_needs(held, outbound_log):
    send(held.approve(held.digest), FakeTransport())
    (entry,) = read_log(outbound_log)
    assert entry.deck == "held.pptx"
    assert entry.model == "fake-model"
    assert entry.characters == held.characters
    assert entry.redactions == len(held.plan.redactions)
    assert entry.residuals == len(held.plan.residuals)
    assert entry.digest == held.digest
    assert entry.text_sha256 == text_digest(held.plan.text)
    assert entry.sent_at.endswith("+00:00"), entry.sent_at


def test_the_log_never_holds_the_payload(held, outbound_log):
    """A log that quotes the payload is a second copy of the thing being
    protected, in plaintext, somewhere nobody is thinking about."""
    send(held.approve(held.digest), FakeTransport())
    written = outbound_log.read_text(encoding="utf-8")
    assert "Halvorsen" not in written
    for line in held.plan.text.splitlines():
        stripped = line.strip()
        if len(stripped) > 12:
            assert stripped not in written, stripped


def test_the_log_records_only_the_filename_and_not_the_directory(tmp_path, outbound_log):
    """The containing folder is often a matter number or a codename."""
    secret = tmp_path / "Project Atlas"
    secret.mkdir()
    deck = _deck(secret, "held", ["We rate Vantage ahead of the market"])
    prepared = prepare(deck, None)
    send(prepared.approve(prepared.digest), FakeTransport())
    written = outbound_log.read_text(encoding="utf-8")
    assert "Project Atlas" not in written
    assert "held.pptx" in written


def test_sends_accumulate_rather_than_replacing_each_other(held, outbound_log):
    for _ in range(3):
        send(held.approve(held.digest), FakeTransport())
    assert len(read_log(outbound_log)) == 3


def test_a_refused_send_writes_nothing(held, outbound_log):
    with pytest.raises(RedactionHeld):
        send(held, FakeTransport())
    assert read_log(outbound_log) == ()


def test_a_send_that_cannot_be_recorded_does_not_happen(held, tmp_path, monkeypatch):
    """Fail closed. A best-effort audit trail is worse than none, because it
    looks like evidence while being silent exactly when the disk is full."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("this is a file, so nothing can be created beneath it")
    monkeypatch.setenv(OUTBOUND_LOG_ENV, str(blocked / "outbound.jsonl"))

    transport = FakeTransport()
    with pytest.raises(OutboundLogError) as excinfo:
        send(held.approve(held.digest), transport)
    assert transport.received == [], "nothing may be sent when it cannot be recorded"
    assert OUTBOUND_LOG_ENV in str(excinfo.value)


def test_a_truncated_final_line_does_not_make_the_history_unreadable(tmp_path):
    path = tmp_path / "outbound.jsonl"
    good = OutboundRecord.now(
        deck_path="/somewhere/deck.pptx",
        model="m",
        characters=1,
        redactions=0,
        residuals=0,
        digest="d",
        text_sha256="t",
    )
    record_outbound(good, path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"sent_at": "2026-0')
    (entry,) = read_log(path)
    assert entry.deck == "deck.pptx"


def test_an_unknown_field_is_skipped_rather_than_crashing(tmp_path):
    """A log written by a later version must still be readable by this one."""
    path = tmp_path / "outbound.jsonl"
    path.write_text(json.dumps({"sent_at": "x", "something_new": 1}) + "\n")
    assert read_log(path) == ()


def test_a_missing_log_reads_as_empty(tmp_path):
    assert read_log(tmp_path / "never-written.jsonl") == ()


def test_the_environment_variable_moves_the_log(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere" / "compliance.jsonl"
    monkeypatch.setenv(OUTBOUND_LOG_ENV, str(target))
    assert outbound_log_path() == target


def test_the_default_sits_beside_the_profiles(tmp_path, monkeypatch):
    monkeypatch.delenv(OUTBOUND_LOG_ENV, raising=False)
    monkeypatch.setenv("TIEOUT_PROFILE_DIR", str(tmp_path))
    assert outbound_log_path() == tmp_path / "outbound.jsonl"
