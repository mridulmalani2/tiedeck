"""The declarative specification a reference deck is built from.

This file is the ground truth for ``test_learn_roundtrip``. The generator builds
a deck from a :class:`ReferenceSpec`; the learner then reads that deck back and
must recover the same parameters. If it cannot recover values the deck was
literally constructed from, the learning engine is broken and no amount of
plausible-looking output redeems it.

Two design decisions that keep the round trip honest:

* Every coordinate is a multiple of :data:`GRID_UNIT_PT`. Real decks are not
  built this way, but a fixture on a coarse grid guarantees that any two shape
  edges either coincide exactly or differ by more than the near-miss window, so
  LO-003 has nothing spurious to report on the clean deck.
* Nothing is left to inheritance by accident. Fonts, sizes and colours are set
  explicitly on every run, and the theme and master text styles are rewritten to
  agree with them, so a resolution bug shows up as a mismatch rather than being
  masked by a fallback that happens to be right.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Final

import yaml

#: Every generated coordinate is a multiple of this, in points.
GRID_UNIT_PT: Final[int] = 12

SLIDE_WIDTH_PT: Final[int] = 960
SLIDE_HEIGHT_PT: Final[int] = 540

# Horizontal frame.
MARGIN_LEFT_PT: Final[int] = 36
MARGIN_RIGHT_PT: Final[int] = 36
CONTENT_LEFT_PT: Final[int] = MARGIN_LEFT_PT
CONTENT_RIGHT_PT: Final[int] = SLIDE_WIDTH_PT - MARGIN_RIGHT_PT  # 924
CONTENT_WIDTH_PT: Final[int] = CONTENT_RIGHT_PT - CONTENT_LEFT_PT  # 888

# Vertical frame.
TITLE_TOP_PT: Final[int] = 36
TITLE_HEIGHT_PT: Final[int] = 48
TITLE_RULE_TOP_PT: Final[int] = 96
BODY_TOP_PT: Final[int] = 120
BODY_BOTTOM_PT: Final[int] = 456
FOOTNOTE_RULE_TOP_PT: Final[int] = 468
FOOTNOTE_TOP_PT: Final[int] = 480
FOOTER_TOP_PT: Final[int] = 504
FOOTER_HEIGHT_PT: Final[int] = 12

#: Column grids, all on the 12pt unit. Two, three and four column layouts share
#: the same outer edges, which is what makes a learnable grid rather than noise.
COLUMNS_2_PT: Final[tuple[tuple[int, int], ...]] = ((36, 432), (492, 432))
COLUMNS_3_PT: Final[tuple[tuple[int, int], ...]] = ((36, 288), (336, 288), (636, 288))
COLUMNS_4_PT: Final[tuple[tuple[int, int], ...]] = (
    (36, 204),
    (264, 204),
    (492, 204),
    (720, 204),
)


@dataclass(frozen=True)
class FontSpec:
    """A typeface, size and colour, applied explicitly to every run that uses it."""

    name: str
    size_pt: float
    color_hex: str
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class BrandSpec:
    """The house style the deck is built in and the learner must recover."""

    primary_font: str = "Gill Sans MT"
    figure_font: str = "Arial"

    ink: str = "#000000"
    paper: str = "#FFFFFF"
    house_navy: str = "#1F3864"
    rule_grey: str = "#A6A6A6"
    accent_red: str = "#C00000"

    title_pt: float = 20.0
    subtitle_pt: float = 14.0
    body_sizes_pt: tuple[float, ...] = (10.0, 11.0, 12.0, 14.0)
    table_sizes_pt: tuple[float, ...] = (9.0, 10.0)
    chart_label_pt: float = 9.0
    footnote_pt: float = 7.0

    #: Top-right logo box on content-bearing archetypes.
    logo_box_pt: tuple[int, int, int, int] = (852, 24, 72, 24)
    #: Centred logo box on divider archetypes.
    logo_divider_box_pt: tuple[int, int, int, int] = (420, 240, 120, 40)
    #: Native pixel size of the logo image, chosen so effective DPI clears 150
    #: comfortably at both rendered sizes.
    logo_pixels: tuple[int, int] = (288, 96)

    page_number_box_pt: tuple[int, int, int, int] = (36, 504, 48, 12)
    confidentiality_text: str = "Strictly Private and Confidential"
    confidentiality_box_pt: tuple[int, int, int, int] = (708, 504, 216, 12)

    @property
    def palette_hex(self) -> tuple[str, ...]:
        return (
            self.ink,
            self.paper,
            self.house_navy,
            self.rule_grey,
            self.accent_red,
        )

    @property
    def fonts_allowed(self) -> tuple[str, ...]:
        return (self.primary_font, self.figure_font)

    def title_font(self) -> FontSpec:
        return FontSpec(self.primary_font, self.title_pt, self.house_navy, bold=True)

    def subtitle_font(self) -> FontSpec:
        return FontSpec(self.primary_font, self.subtitle_pt, self.ink)

    def body_font(self, size_pt: float | None = None) -> FontSpec:
        return FontSpec(self.primary_font, size_pt or self.body_sizes_pt[1], self.ink)

    def figure_cell_font(self, size_pt: float | None = None) -> FontSpec:
        return FontSpec(self.figure_font, size_pt or self.table_sizes_pt[0], self.ink)

    def header_cell_font(self) -> FontSpec:
        return FontSpec(
            self.primary_font, self.table_sizes_pt[1], self.paper, bold=True
        )

    def footnote_font(self) -> FontSpec:
        return FontSpec(self.primary_font, self.footnote_pt, self.rule_grey)

    def chart_label_font(self) -> FontSpec:
        return FontSpec(self.primary_font, self.chart_label_pt, self.ink)


@dataclass(frozen=True)
class TypographySpec:
    """Conventions the deck obeys without exception, so they are learnable."""

    quotes: str = "curly"
    title_case: str = "sentence"
    bullet_terminal_punctuation: str = "none"
    thousands_separator: str = ","
    negative_style: str = "parentheses"
    date_format: str = "%d-%B-%Y"
    currency_prefix: str = "US$"
    decimal_places_by_column: str = "consistent_within_column"


@dataclass(frozen=True)
class SlideSpec:
    """One slide's intent. ``kind`` drives which builder runs."""

    index: int
    archetype: str
    kind: str
    title: str = ""
    subtitle: str = ""
    #: Free-form payload interpreted by the builder for this ``kind``.
    payload: dict[str, Any] = field(default_factory=dict)
    has_logo: bool = True
    logo_centred: bool = False
    has_page_number: bool = True
    has_confidentiality: bool = True


@dataclass(frozen=True)
class SeededDefect:
    """One deliberate violation, tagged with the rule it must provoke.

    ``variant`` is set when the defect cannot coexist with the others: a wrong
    slide size invalidates every geometric seed on the same deck, and the
    package-level hygiene defects are applied by rewriting the zip after
    python-pptx has finished with it.
    """

    rule_id: str
    description: str
    slide_index: int | None = None
    variant: str | None = None


@dataclass(frozen=True)
class ReferenceSpec:
    """The whole fixture specification."""

    client: str = "meridian"
    project_codename: str = "Project Meridian"
    advisor_mark: str = "Ashcombe Partners"
    deck_date: str = "14-September-2026"
    width_pt: int = SLIDE_WIDTH_PT
    height_pt: int = SLIDE_HEIGHT_PT
    brand: BrandSpec = field(default_factory=BrandSpec)
    typography: TypographySpec = field(default_factory=TypographySpec)
    slides: tuple[SlideSpec, ...] = ()
    defects: tuple[SeededDefect, ...] = ()

    def to_yaml(self) -> str:
        return yaml.safe_dump(asdict(self), sort_keys=False, width=100)

    def slide(self, index: int) -> SlideSpec | None:
        for candidate in self.slides:
            if candidate.index == index:
                return candidate
        return None

    @property
    def archetype_assignment(self) -> dict[str, list[int]]:
        """The archetype the classifier is expected to recover for each slide."""
        out: dict[str, list[int]] = {}
        for slide in self.slides:
            out.setdefault(slide.archetype, []).append(slide.index)
        return out

    def logo_expectation(self) -> dict[str, tuple[int, int, int, int] | None]:
        """Archetype -> expected logo box, or None where the logo is absent."""
        out: dict[str, tuple[int, int, int, int] | None] = {}
        for slide in self.slides:
            if not slide.has_logo:
                out.setdefault(slide.archetype, None)
                continue
            box = (
                self.brand.logo_divider_box_pt
                if slide.logo_centred
                else self.brand.logo_box_pt
            )
            out[slide.archetype] = box
        return out

    def page_number_archetypes(self) -> tuple[str, ...]:
        return tuple(
            sorted({s.archetype for s in self.slides if s.has_page_number})
        )


# --------------------------------------------------------------------------------------
# The 26-slide reference deck
# --------------------------------------------------------------------------------------
#
# Invented codenames and invented figures throughout. Nothing here is taken from
# any real filing or any real client material.

_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("I", "Situation assessment"),
    ("II", "Valuation perspectives"),
    ("III", "Strategic alternatives"),
    ("IV", "Process considerations"),
)


def _content(index: int, title: str, **payload: Any) -> SlideSpec:
    return SlideSpec(
        index=index, archetype="content", kind="content", title=title, payload=payload
    )


def _table_slide(index: int, title: str, **payload: Any) -> SlideSpec:
    return SlideSpec(
        index=index,
        archetype="table_heavy",
        kind="table",
        title=title,
        payload=payload,
    )


def _chart_slide(index: int, title: str, **payload: Any) -> SlideSpec:
    return SlideSpec(
        index=index,
        archetype="chart_heavy",
        kind="chart",
        title=title,
        payload=payload,
    )


def _divider(index: int, numeral: str, heading: str) -> SlideSpec:
    return SlideSpec(
        index=index,
        archetype="section_divider",
        kind="divider",
        title=f"Section {numeral}",
        subtitle=heading,
        logo_centred=True,
        has_page_number=False,
    )


def default_slides() -> tuple[SlideSpec, ...]:
    """The 26-slide running order described in section 11 of the specification."""
    return (
        SlideSpec(
            index=1,
            archetype="title",
            kind="title",
            title="Project Meridian",
            subtitle="Discussion materials for the Board of Directors",
            has_logo=False,
            has_page_number=False,
        ),
        SlideSpec(
            index=2,
            archetype="agenda",
            kind="agenda",
            title="Table of Contents",
            has_page_number=False,
            payload={"sections": [f"{n}. {h}" for n, h in _SECTIONS]},
        ),
        _divider(3, "I", _SECTIONS[0][1]),
        _content(
            4,
            "Meridian has outgrown its regional footprint",
            columns=2,
            bullets=[
                [
                    "Revenue has compounded at twenty one per cent since 2022",
                    "Four of the six regional depots are at capacity",
                    "The Halloway contract renews in the first quarter of 2027",
                ],
                [
                    "Unit economics improve materially above forty depots",
                    "Working capital absorbs most of the operating cash flow",
                    "Management bandwidth is the binding constraint",
                ],
            ],
            footnote="Source: Company management, Ashcombe Partners analysis.",
        ),
        _content(
            5,
            "Three constraints shape the opportunity set",
            columns=3,
            bullets=[
                ["Capital", "Growth requires US$240 million of new capital"],
                ["Capability", "Depot automation is not an in-house competence"],
                ["Coverage", "No presence in the two fastest growing corridors"],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _table_slide(
            6,
            "Historical and projected financial performance",
            columns=["Fiscal year", "Revenue", "EBITDA", "Margin", "Free cash flow"],
            rows=[
                ["2023A", "1,284", "196", "15.3%", "(42)"],
                ["2024A", "1,562", "263", "16.8%", "(18)"],
                ["2025A", "1,908", "351", "18.4%", "27"],
                ["2026E", "2,314", "446", "19.3%", "94"],
                ["2027E", "2,760", "552", "20.0%", "168"],
            ],
            footnote="Source: Company management. Figures in US$ millions.",
        ),
        _content(
            7,
            "The automation thesis rests on two assumptions",
            columns=2,
            bullets=[
                [
                    "Throughput per depot rises by a third within eighteen months",
                    "Labour intensity falls by two fifths at steady state",
                ],
                [
                    "Capital cost per depot of US$4 million is held flat",
                    "No material disruption during the retrofit window",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _chart_slide(
            8,
            "Revenue and margin trajectory",
            categories=["2023A", "2024A", "2025A", "2026E", "2027E"],
            series=[("Revenue", [1284, 1562, 1908, 2314, 2760])],
            footnote="Source: Company management. Figures in US$ millions.",
        ),
        _divider(9, "II", _SECTIONS[1][1]),
        _content(
            10,
            "Valuation anchors on three methodologies",
            columns=3,
            bullets=[
                ["Trading comparables", "Eight point two to nine point six times EBITDA"],
                ["Precedent transactions", "Nine point one to eleven point four times"],
                ["Discounted cash flow", "Implies US$3.4 to US$4.1 billion"],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _content(
            11,
            "The discounted cash flow is sensitive to two inputs",
            columns=2,
            bullets=[
                [
                    "A one point move in the discount rate shifts value by nine per cent",
                    "Terminal growth above two and a half per cent is hard to defend",
                ],
                [
                    "Steady state margin is the single largest swing factor",
                    "Capital intensity normalises only after the retrofit completes",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _table_slide(
            12,
            "Selected trading comparables",
            columns=["Company", "Enterprise value", "EBITDA", "Multiple", "Margin"],
            rows=[
                ["Calderwood Logistics", "8,420", "912", "9.2x", "17.4%"],
                ["Pemberton Freight", "6,180", "674", "9.2x", "16.1%"],
                ["Thorne Distribution", "4,935", "602", "8.2x", "18.9%"],
                ["Ravensworth Group", "3,710", "387", "9.6x", "15.2%"],
                ["Ellesmere Transport", "2,486", "271", "9.2x", "14.8%"],
            ],
            footnote="Source: Public filings as at 14-September-2026. Figures in US$ millions.",
        ),
        _chart_slide(
            13,
            "Implied enterprise value by methodology",
            categories=["Trading comparables", "Precedent transactions", "Discounted cash flow"],
            series=[("Low", [3210, 3580, 3400]), ("High", [3760, 4480, 4100])],
            footnote="Source: Ashcombe Partners analysis. Figures in US$ millions.",
        ),
        _divider(14, "III", _SECTIONS[2][1]),
        _content(
            15,
            "Four alternatives merit consideration",
            columns=4,
            bullets=[
                ["Status quo", "Fund growth from operating cash flow"],
                ["Minority stake", "Sell twenty five per cent to a financial sponsor"],
                ["Full sale", "Run a targeted process with six parties"],
                ["Merger", "Combine with a complementary regional operator"],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _content(
            16,
            "A minority stake preserves optionality",
            columns=2,
            bullets=[
                [
                    "Capital need is met without ceding control",
                    "Governance can be limited to two board observer seats",
                    "A later sale retains the full buyer universe",
                ],
                [
                    "Valuation is set at a minority rather than a control level",
                    "A sponsor will require a defined liquidity path",
                    "Process duration is roughly four months",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _content(
            17,
            "A full sale maximises proceeds but forecloses upside",
            columns=2,
            bullets=[
                [
                    "Control premia in the sector have averaged twenty eight per cent",
                    "Six parties would be credible at the indicated range",
                ],
                [
                    "The automation benefit accrues to the buyer, not the seller",
                    "Employee retention becomes the principal execution risk",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _table_slide(
            18,
            "Alternatives assessed against the Board's stated objectives",
            columns=["Objective", "Status quo", "Minority stake", "Full sale", "Merger"],
            rows=[
                ["Capital raised", "(240)", "240", "n.a.", "120"],
                ["Control retained", "Full", "Full", "None", "Shared"],
                ["Value realised", "Deferred", "Partial", "Full", "Partial"],
                ["Execution risk", "Low", "Moderate", "High", "High"],
                ["Time to close", "n.a.", "4 months", "6 months", "9 months"],
            ],
            footnote="Source: Ashcombe Partners analysis. Capital in US$ millions.",
        ),
        _divider(19, "IV", _SECTIONS[3][1]),
        _content(
            20,
            "A targeted process is preferable to a broad auction",
            columns=2,
            bullets=[
                [
                    "Six parties can be approached without signalling to the market",
                    "Confidentiality is the Board's stated first priority",
                ],
                [
                    "A broad process adds perhaps four per cent of value",
                    "It materially raises the risk of a leak",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _content(
            21,
            "Indicative timetable spans nineteen weeks",
            columns=3,
            bullets=[
                ["Weeks one to four", "Preparation and materials"],
                ["Weeks five to twelve", "Outreach and first round"],
                ["Weeks thirteen to nineteen", "Confirmatory work and signing"],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        _content(
            22,
            "Next steps for the Board's decision",
            columns=2,
            bullets=[
                [
                    "Confirm the preferred alternative at the October meeting",
                    "Authorise preparation of the information memorandum",
                ],
                [
                    "Agree the approach list and the contact sequencing",
                    "Appoint a working group of three directors",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        SlideSpec(
            index=23,
            archetype="appendix_divider",
            kind="divider",
            title="Appendix",
            subtitle="Supporting analysis",
            logo_centred=True,
            has_page_number=False,
        ),
        _content(
            24,
            "Weighted average cost of capital build",
            columns=2,
            bullets=[
                [
                    "Risk free rate of four point one per cent",
                    "Equity risk premium of five point five per cent",
                    "Unlevered beta of zero point nine two",
                ],
                [
                    "Target capital structure of thirty per cent debt",
                    "Pre-tax cost of debt of six point three per cent",
                    "Marginal tax rate of twenty five per cent",
                ],
            ],
            footnote="Source: Ashcombe Partners analysis.",
        ),
        SlideSpec(
            index=25,
            archetype="disclaimer",
            kind="disclaimer",
            title="Disclaimer",
            has_logo=False,
            has_page_number=False,
            payload={"paragraph_count": 4},
        ),
        SlideSpec(
            index=26,
            archetype="disclaimer",
            kind="disclaimer",
            title="Important Notice",
            has_logo=False,
            has_page_number=False,
            payload={"paragraph_count": 4},
        ),
    )


def default_defects() -> tuple[SeededDefect, ...]:
    """One seeded violation per rule, tagged so tests can assert precisely.

    Defects marked with a ``variant`` are built into their own single-defect deck
    because they cannot coexist with the rest: a wrong slide size invalidates
    every geometric seed, and the package-level hygiene defects require rewriting
    the archive after python-pptx has closed it.
    """
    return (
        # -- brand ---------------------------------------------------------------
        SeededDefect("BR-001", "logo removed from a content slide", 4),
        SeededDefect("BR-002", "logo shifted 18pt left of its learned box", 5),
        SeededDefect("BR-003", "logo stretched, breaking its aspect ratio", 7),
        SeededDefect("BR-004", "off-palette teal fill on a callout box", 10),
        SeededDefect("BR-005", "an unapproved typeface on one run", 11),
        SeededDefect("BR-006", "page number replaced with a non-conforming string", 15),
        SeededDefect("BR-007", "page numbers put out of ascending order", 16),
        SeededDefect("BR-008", "title placeholder dragged off its layout position", 17),
        SeededDefect("BR-009", "slide size changed to 4:3", None, "wrong_slide_size"),
        SeededDefect("BR-010", "confidentiality line deleted from a content slide", 20),
        # -- layout --------------------------------------------------------------
        SeededDefect("LO-001", "a shape pushed off the right edge of the canvas", 21),
        SeededDefect("LO-002", "a shape intruding into the learned left margin", 22),
        SeededDefect("LO-003", "a column nudged 3pt off its learned grid line", 24),
        SeededDefect("LO-004", "two text shapes overlapped", 4),
        SeededDefect("LO-005", "the footnote moved away from its modal position", 5),
        SeededDefect("LO-006", "a long string forced into a narrow fixed-size box", 7),
        SeededDefect("LO-007", "body text set at 18pt, outside the learned band", 10),
        SeededDefect("LO-008", "uneven gutters across a four column row", 15),
        # -- typography ----------------------------------------------------------
        SeededDefect("TY-001", "straight apostrophe against a curly convention", 11),
        SeededDefect("TY-002", "a double space and a space before a full stop", 16),
        SeededDefect("TY-003", "a full stop on one bullet in an unpunctuated list", 17),
        SeededDefect("TY-004", "a Title Cased Slide Heading against sentence case", 20),
        SeededDefect("TY-005", "a non-canonical spelling of the advisor mark", 21),
        SeededDefect("TY-006", "mixed decimal places within one table column", 6),
        SeededDefect("TY-007", "currency written as USD rather than US$", 12),
        SeededDefect("TY-008", "a date written in a second format", 18),
        SeededDefect("TY-009", "a misspelled word", 22),
        # -- hygiene -------------------------------------------------------------
        SeededDefect("HY-001", "a TBD placeholder marker left in the body", 24),
        SeededDefect("HY-002", "speaker notes left on a slide", 5),
        SeededDefect("HY-003", "a hidden slide left in the deck", 7),
        SeededDefect("HY-004", "creator and company metadata left populated", None, "metadata"),
        SeededDefect("HY-005", "a PowerPoint comment left in the package", None, "comments"),
        SeededDefect("HY-006", "an empty body placeholder left visible", 10),
        SeededDefect("HY-007", "an external relationship to a local drive path", None, "external_rel"),
        SeededDefect("HY-008", "a low resolution image below 150 effective DPI", 11),
        SeededDefect("HY-009", "a non-standard, non-embedded typeface", 16),
    )


def default_spec() -> ReferenceSpec:
    """The shipped reference specification."""
    return ReferenceSpec(slides=default_slides(), defects=default_defects())


#: Defect variants that need their own single-defect deck.
VARIANT_NAMES: Final[tuple[str, ...]] = (
    "wrong_slide_size",
    "metadata",
    "comments",
    "external_rel",
)
