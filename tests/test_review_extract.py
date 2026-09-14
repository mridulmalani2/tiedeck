"""What goes into the payload, and what stays out of it.

The payload is the attack surface: everything in it is something the redactor
has to get right. So the tests here are as much about exclusion as inclusion,
and the speaker-notes test is the one that matters — notes are where the price
and the walk-away number live.
"""

from __future__ import annotations

from tieout_review.extract import extract


def test_the_payload_covers_every_slide(clean_deck):
    payload = extract(clean_deck)
    assert payload.slide_count == clean_deck.slide_count
    assert {item.slide_index for item in payload.items} == {
        slide.index for slide in clean_deck.slides
    }


def test_a_line_names_its_slide_archetype_and_role(clean_deck):
    payload = extract(clean_deck)
    first = payload.items[0]
    assert first.render().startswith(f"[s{first.slide_index}|{first.archetype}|{first.role}]")


def test_speaker_notes_are_excluded_by_default(dirty_deck):
    """Notes are where the sensitive material is. Including them is a flag."""
    assert not any(item.role == "notes" for item in extract(dirty_deck).items)


def test_speaker_notes_are_included_only_when_asked(dirty_deck):
    with_notes = extract(dirty_deck, include_notes=True)
    assert any(item.role == "notes" for item in with_notes.items)
    assert with_notes.characters > extract(dirty_deck).characters


def test_a_table_keeps_its_grid(clean_deck):
    """A number without its row label and column header cannot be compared with
    anything, and comparing numbers is the only reason the payload exists."""
    rows = [item for item in extract(clean_deck).items if item.role.startswith("table_")]
    assert rows
    header = next(row for row in rows if "Revenue" in row.text)
    assert header.text.count(" | ") >= 3


def test_a_table_row_appears_once_per_row(clean_deck):
    """A spanned cell must not repeat itself across the rows it covers."""
    payload = extract(clean_deck)
    for slide in clean_deck.slides:
        capacity = sum(
            child.table.row_count
            for shape in slide.shapes
            for child in shape.walk()
            if child.table is not None
        )
        rows = [
            item
            for item in payload.item_for_slide(slide.index)
            if item.role.startswith("table_")
        ]
        assert len(rows) <= capacity


def test_chart_text_is_carried(clean_deck):
    roles = {item.role for item in extract(clean_deck).items}
    assert roles & {"chart_title", "chart_categories", "chart_series", "chart_axis"}


def test_every_item_is_one_line(clean_deck):
    """Lines are the addressing scheme: a residual is reported as "line 214"."""
    payload = extract(clean_deck)
    assert len(payload.render().splitlines()) == len(payload.items)
    for item in payload.items:
        assert "\n" not in item.text


def test_the_payload_is_small_enough_to_send_in_one_request(clean_deck):
    """A 26-slide deck should be a few tens of thousands of characters, not a
    few hundred thousand: the size is what keeps this one request."""
    assert extract(clean_deck).characters < 60_000


def test_the_payload_is_stable_across_two_extractions(clean_deck):
    assert extract(clean_deck).render() == extract(clean_deck).render()


def test_figures_reach_the_payload_verbatim(clean_deck):
    """Whatever else is dropped, the numbers are the point."""
    rendered = extract(clean_deck).render()
    assert "1,908" in rendered
    assert "18.4%" in rendered
