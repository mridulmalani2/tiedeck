"""Shared fixtures.

Every deck used by the suite is generated, never committed: section 12 forbids
binary fixtures, and a generated deck is the only kind whose parameters the
round-trip test can assert against.

The decks are built once per session because building twenty-six slides with
tables and charts is slow enough to notice, and nothing in the suite mutates
them on disk.

:func:`reference_profile` is hand-built rather than learned. Per-rule tests must
fail when a rule is wrong, not when the learner is wrong, so the two are kept
independent. ``test_learn_roundtrip`` and ``test_clean_deck`` are the tests that
exercise the learner's output.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tieout.fixtures.generator import BuildResult, _logo_png, build_all
from tieout.fixtures.spec import (
    CONTENT_LEFT_PT,
    CONTENT_WIDTH_PT,
    FOOTNOTE_TOP_PT,
    ReferenceSpec,
    default_spec,
)
from tieout.model.archetype import CONTENT_ARCHETYPES
from tieout.model.deck import DeckModel
from tieout.model.loader import load_deck
from tieout.profile.schema import (
    BoilerplateEntry,
    Box,
    BrandProfile,
    FontRole,
    FontsProfile,
    FooterProfile,
    GridProfile,
    HygieneProfile,
    LayoutProfile,
    LogoProfile,
    Margins,
    NearMissWindow,
    NotLearned,
    PageNumberProfile,
    Profile,
    RecurringElement,
    RulesProfile,
    SlideProfile,
    TypographyProfile,
)
from tieout.rules.base import clear_caches


@pytest.fixture(autouse=True)
def _clear_rule_caches() -> Iterator[None]:
    """Drop the rule engine's per-deck memoisation around every test.

    The fixture decks are session-scoped and the profiles are function-scoped, so
    without this a test could read furniture derived from the previous test's
    profile.
    """
    clear_caches()
    yield
    clear_caches()


@pytest.fixture(scope="session")
def spec() -> ReferenceSpec:
    return default_spec()


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory, spec: ReferenceSpec) -> BuildResult:
    """Build every fixture deck once for the whole session."""
    directory = tmp_path_factory.mktemp("tieout-fixtures")
    return build_all(directory, spec)


@pytest.fixture(scope="session")
def clean_path(fixtures: BuildResult) -> Path:
    return fixtures.clean


@pytest.fixture(scope="session")
def dirty_path(fixtures: BuildResult) -> Path:
    return fixtures.dirty


@pytest.fixture(scope="session")
def clean_deck(clean_path: Path) -> DeckModel:
    return load_deck(clean_path)


@pytest.fixture(scope="session")
def dirty_deck(dirty_path: Path) -> DeckModel:
    return load_deck(dirty_path)


@pytest.fixture(scope="session")
def variant_decks(fixtures: BuildResult) -> dict[str, DeckModel]:
    """The single-defect decks, keyed by variant name."""
    return {name: load_deck(path) for name, path in fixtures.variants.items()}


@pytest.fixture(scope="session")
def logo_sha1(spec: ReferenceSpec) -> str:
    return hashlib.sha1(_logo_png(spec.brand), usedforsecurity=False).hexdigest()


@pytest.fixture
def reference_profile(spec: ReferenceSpec, logo_sha1: str) -> Profile:
    """A profile that exactly describes the clean deck.

    Function-scoped, not session-scoped: several rule tests mutate a profile to
    check a rule's behaviour when an expectation is absent, and a shared mutable
    profile would leak that between tests.
    """
    return build_reference_profile(spec, logo_sha1)


def build_reference_profile(spec: ReferenceSpec, logo_sha1: str) -> Profile:
    """Construct the profile the clean deck should produce.

    Kept as a plain function so ``test_learn_roundtrip`` can compare a learned
    profile against it field by field.
    """
    brand = spec.brand
    content_box = Box(
        left=brand.logo_box_pt[0],
        top=brand.logo_box_pt[1],
        width=brand.logo_box_pt[2],
        height=brand.logo_box_pt[3],
        tolerance_pt=2.0,
    )
    divider_box = Box(
        left=brand.logo_divider_box_pt[0],
        top=brand.logo_divider_box_pt[1],
        width=brand.logo_divider_box_pt[2],
        height=brand.logo_divider_box_pt[3],
        tolerance_pt=2.0,
    )

    profile = Profile(
        client=spec.client,
        version=1,
        sources=["reference_clean.pptx (26 slides)"],
        slide=SlideProfile(width_pt=float(spec.width_pt), height_pt=float(spec.height_pt)),
        archetypes={k: sorted(v) for k, v in spec.archetype_assignment.items()},
        brand=BrandProfile(
            fonts=FontsProfile(
                allowed=list(brand.fonts_allowed),
                roles={
                    "title": FontRole(exact_pt=[brand.title_pt]),
                    "subtitle": FontRole(exact_pt=[10.0, 12.0, 14.0]),
                    "body": FontRole(
                        min_pt=min(brand.body_sizes_pt), max_pt=max(brand.body_sizes_pt)
                    ),
                    "table": FontRole(exact_pt=list(brand.table_sizes_pt)),
                    "footnote": FontRole(exact_pt=[brand.footnote_pt]),
                },
            ),
            palette_hex=list(brand.palette_hex),
            palette_tolerance_delta_e=6.0,
            logo=LogoProfile(
                image_sha1=[logo_sha1],
                per_archetype={
                    "content": content_box,
                    "table_heavy": content_box,
                    "chart_heavy": content_box,
                    "agenda": content_box,
                    "section_divider": divider_box,
                    "appendix_divider": divider_box,
                    "title": "exempt",
                    "disclaimer": "exempt",
                },
            ),
            footer=FooterProfile(
                page_number=PageNumberProfile(
                    required_on=sorted(CONTENT_ARCHETYPES),
                    regex=r"^\d+$",
                    box_pt=Box(
                        left=brand.page_number_box_pt[0],
                        top=brand.page_number_box_pt[1],
                        width=brand.page_number_box_pt[2],
                        height=brand.page_number_box_pt[3],
                        tolerance_pt=2.0,
                    ),
                ),
                boilerplate=[
                    BoilerplateEntry(
                        text=brand.confidentiality_text,
                        required_on=sorted(spec.archetype_assignment),
                        is_confidentiality=True,
                    )
                ],
            ),
        ),
        layout=LayoutProfile(
            safe_margin_pt={
                archetype: Margins(top=36.0, right=36.0, bottom=48.0, left=36.0)
                for archetype in sorted(CONTENT_ARCHETYPES)
            },
            grid=GridProfile(
                columns_pt=[36.0, 240.0, 468.0, 492.0, 924.0],
                rows_pt=[36.0, 84.0, 96.0, 120.0, 456.0, 468.0, 480.0, 492.0],
            ),
            near_miss_alignment_pt=NearMissWindow(min=0.5, max=4.0),
            recurring=[
                RecurringElement(
                    key="name:footnote",
                    box_pt=Box(
                        left=float(CONTENT_LEFT_PT),
                        top=float(FOOTNOTE_TOP_PT),
                        width=float(CONTENT_WIDTH_PT),
                        height=12.0,
                        tolerance_pt=2.0,
                    ),
                    archetypes=sorted(CONTENT_ARCHETYPES),
                    support=17,
                ),
            ],
        ),
        typography=TypographyProfile(
            quotes=spec.typography.quotes,
            title_case=spec.typography.title_case,
            bullet_terminal_punctuation=spec.typography.bullet_terminal_punctuation,
            thousands_separator=spec.typography.thousands_separator,
            negative_style=spec.typography.negative_style,
            decimal_places_by_column=spec.typography.decimal_places_by_column,
            date_format=spec.typography.date_format,
            currency_pattern=r"^(US\$|\$)\s?[\d(]",
            canon_terms={spec.advisor_mark: []},
        ),
        hygiene=HygieneProfile(
            dictionary=[
                "Meridian",
                "Ashcombe",
                "Halloway",
                "Calderwood",
                "Pemberton",
                "Thorne",
                "Ravensworth",
                "Ellesmere",
            ]
        ),
        rules=RulesProfile(disabled=["TY-009"]),
        not_learned=[
            NotLearned(
                key="brand.fonts.roles.chart_label",
                reason="only 2 charts in the reference deck, below min_support of 3",
            )
        ],
    )

    profile.set_provenance("slide", "observed, invariant across 26 slides", "high")
    profile.set_provenance(
        "brand",
        "derived from the reference deck listed in sources",
        "high",
    )
    profile.set_provenance(
        "brand.title_geometry_tolerance_pt",
        "default 2pt: a title is expected to sit where its own layout places it, "
        "which is not inferred from the reference deck",
        "medium",
    )
    profile.set_provenance(
        "layout.recurring",
        "the footnote block observed at one position on 17 of 26 slides",
        "high",
    )
    profile.set_provenance(
        "brand.fonts.allowed",
        "96.8% and 3.2% of characters across 26 slides",
        "high",
    )
    profile.set_provenance(
        "brand.fonts.roles",
        "character-weighted sizes per text role across 26 slides",
        "high",
    )
    profile.set_provenance(
        "brand.palette_hex",
        "area and character weighted colour clusters across 26 slides",
        "high",
    )
    profile.set_provenance(
        "brand.logo", "one image present on 23 of 26 slides", "high"
    )
    profile.set_provenance(
        "brand.logo.per_archetype",
        "logo geometry invariant within each archetype across 23 slides",
        "high",
    )
    profile.set_provenance(
        "brand.footer.page_number",
        "17 ascending bare integers in the bottom-left corner",
        "high",
    )
    profile.set_provenance(
        "brand.footer.boilerplate",
        "repeated verbatim on 26 of 26 slides",
        "high",
    )
    profile.set_provenance(
        "layout.safe_margin_pt",
        "5th percentile of content bounding box edges per archetype",
        "high",
    )
    profile.set_provenance(
        "layout.grid", "shape edge clusters with support of 5 or more", "high"
    )
    profile.set_provenance("typography", "dominant convention, no exceptions", "high")
    profile.set_provenance("hygiene", "default, not inferred", "medium")
    return profile


# --------------------------------------------------------------------------------------
# Assertion helpers
# --------------------------------------------------------------------------------------


def findings_for(result: Any, rule_id: str) -> list[Any]:
    """Findings from one rule, for per-rule assertions."""
    return [f for f in result.findings if f.rule_id == rule_id]


def slide_indices(findings: list[Any]) -> set[int]:
    return {f.slide_index for f in findings}


def assert_silent_on_clean(result: Any, rule_id: str) -> None:
    """A rule must say nothing about the clean deck.

    This is the assertion that matters most in the whole suite. A rule that
    catches its seeded defect but also fires on correct slides is worse than no
    rule, because it trains the user to ignore the report.
    """
    noise = findings_for(result, rule_id)
    assert not noise, (
        f"{rule_id} produced {len(noise)} finding(s) on the clean deck: "
        + "; ".join(f"slide {f.slide_index}: {f.message}" for f in noise[:5])
    )
