"""The README has to stay true.

Documentation that has drifted from the code is worse than none: it is confident
and wrong. The rule table in particular is the thing a user consults before
deciding whether to trust a finding, so a rule missing from it, or listed with
the wrong severity, is a real defect rather than an untidiness.

Section 14 also requires the README to state a specific set of limitations. Those
are asserted individually, because the temptation when a limitation is
embarrassing is to quietly drop it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tieout.rules.base import load_all_rules

README = Path(__file__).resolve().parent.parent / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def catalogue(readme: str) -> str:
    """Just the deterministic rule catalogue.

    The optional semantic layer documents its own table of SE-nnn rules further
    down, and those are not in the registry. Scoping keeps each table asserted
    against the right source of truth instead of one test failing on the other's
    rows.
    """
    after = readme.split("## The rule catalogue", 1)[1]
    return after.split("## Optional: semantic review", 1)[0]


def test_the_readme_exists_and_is_substantial(readme):
    assert len(readme) > 10_000, "section 14 asks for a genuinely documented tool"


def test_the_readme_covers_the_sections_section_14_requires(readme):
    for heading in (
        "## Install",
        "## A worked onboarding",
        "## How derivation works",
        "## The rule catalogue",
        "## Deploying it",
        "## Known limitations",
    ):
        assert heading in readme, f"missing {heading}"


def test_every_rule_appears_in_the_rule_table(catalogue):
    documented = set(re.findall(r"^\| \*\*([A-Z]{2}-\d{3})\*\*", catalogue, re.MULTILINE))
    registered = set(load_all_rules())
    assert documented == registered, (
        f"undocumented: {sorted(registered - documented)}; "
        f"documented but not registered: {sorted(documented - registered)}"
    )


def test_the_rule_table_states_each_severity_correctly(catalogue):
    rows = dict(
        re.findall(
            r"^\| \*\*([A-Z]{2}-\d{3})\*\* \| (blocker|major|minor|info) \|",
            catalogue,
            re.MULTILINE,
        )
    )
    registry = load_all_rules()
    wrong = {
        rule_id: (documented, registry[rule_id].severity)
        for rule_id, documented in rows.items()
        if documented != registry[rule_id].severity
    }
    assert not wrong, f"severity mismatches (documented, actual): {wrong}"


def test_the_rule_table_marks_the_off_by_default_rules(catalogue):
    rows = dict(
        re.findall(
            r"^\| \*\*([A-Z]{2}-\d{3})\*\* \| \w+ \| (\*\*off\*\*|on) \|",
            catalogue,
            re.MULTILINE,
        )
    )
    registry = load_all_rules()
    for rule_id, documented in rows.items():
        expected = "on" if registry[rule_id].default_enabled else "**off**"
        assert documented == expected, (
            f"{rule_id} is documented as {documented} but ships {expected}"
        )


def test_the_semantic_rules_are_documented_exactly_as_they_ship(readme):
    """The optional layer's table has the same duty as the catalogue above it."""
    from tieout_review.review import SEMANTIC_RULES

    section = readme.split("## Optional: semantic review", 1)[1]
    rows = dict(
        re.findall(
            r"^\| \*\*(SE-\d{3})\*\* \| (blocker|major|minor|info) \|",
            section,
            re.MULTILINE,
        )
    )
    assert set(rows) == set(SEMANTIC_RULES), "the semantic table has drifted"
    for rule_id, severity in rows.items():
        assert severity == SEMANTIC_RULES[rule_id].severity, rule_id


def test_the_readme_states_that_semantic_findings_do_not_gate(readme):
    """The property someone adding this to a pipeline most needs to know."""
    # Normalised, because the README is hard-wrapped and the phrase straddles a
    # line break.
    section = " ".join(readme.split("## Optional: semantic review", 1)[1].split())
    assert "a semantic finding never drives the exit code" in section


def test_the_air_gap_qualification_is_next_to_the_air_gap_promise(readme):
    """A promise stated without its one exception is a promise that misleads."""
    header = readme.split("## Contents", 1)[0]
    assert "tieout-review" in header
    assert "separate package installed separately" in header


def test_the_rule_counts_per_category_are_stated_correctly(readme):
    registry = load_all_rules()
    actual: dict[str, int] = {}
    for rule in registry.values():
        actual[rule.category] = actual.get(rule.category, 0) + 1
    for category, count in actual.items():
        heading = f"### {category.capitalize()} ({count} rules)"
        assert heading in readme, f"expected heading {heading!r}"


def test_the_stated_rule_total_matches_the_registry(readme):
    categories = {rule.category for rule in load_all_rules().values()}
    words = {4: "four", 5: "five", 6: "six"}
    expected = (
        f"{len(load_all_rules())} rules across "
        f"{words.get(len(categories), len(categories))} categories"
    )
    assert expected in readme, f"the README should say {expected!r}"


@pytest.mark.parametrize(
    ("topic", "needle"),
    [
        ("no semantic review", "No semantic or argument-level review"),
        ("reference deck errors", "Errors in the reference deck are learned as rules"),
        ("single deck evidence", "A single reference deck is thin evidence"),
        ("overflow approximate", "Overflow detection is approximate and off by default"),
        ("spell check off", "Spell check is off by default"),
        ("smartart", "SmartArt is not inspected"),
    ],
)
def test_each_required_limitation_is_stated(readme, topic, needle):
    """Section 14 names these explicitly. They are the ones a user most needs to
    know and the ones most tempting to leave out."""
    limitations = readme.split("## Known limitations", 1)[1]
    assert needle in limitations, f"the {topic} limitation is missing"


def test_the_air_gap_promise_is_stated_prominently(readme):
    header = readme.split("## Contents", 1)[0]
    assert "No network access of any kind" in header
    assert "air-gapped" in header


@pytest.fixture(scope="module")
def deployment(readme: str) -> str:
    return readme.split("## Deploying it", 1)[1].split("\n## ", 1)[0]


def test_the_deployment_section_names_the_real_entry_points(deployment):
    """These are commands people paste. A stale one wastes an afternoon."""
    import tomllib

    with (README.parent / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    for command in config["project"]["scripts"]:
        assert command in deployment, f"{command} is not mentioned"


def test_the_offline_install_is_documented_with_the_flag_that_makes_it_offline(
    deployment,
):
    """``--no-index`` is the whole point of the air-gapped recipe: it is what
    forbids pip from reaching out. A recipe missing it would appear to work on a
    connected machine and fail on the one it is for."""
    assert "pip download" in deployment
    assert "--no-index" in deployment
    assert "--find-links" in deployment


def test_the_deployment_section_states_the_exit_codes_it_relies_on(deployment):
    from tieout.cli import EXIT_ERROR, EXIT_FINDINGS

    assert f"`{EXIT_FINDINGS}` means findings" in deployment
    assert f"`{EXIT_ERROR}` means the run itself failed" in deployment


def test_the_shared_profile_variable_is_spelled_correctly(deployment):
    from tieout.profile.loader import PROFILE_DIR_ENV

    assert PROFILE_DIR_ENV in deployment


def test_the_non_python_prerequisites_are_both_named(deployment):
    """Neither is installable by pip, and both are silent when absent: LO-006
    passes by declining to measure, and the UI falls back to slide cards."""
    assert "fonts-liberation" in deployment
    assert "libreoffice-impress" in deployment


def test_the_deployment_section_warns_against_exposing_the_ui(deployment):
    normalised = " ".join(deployment.split())
    assert "Do not expose the UI" in normalised


def test_the_command_reference_covers_every_cli_verb(readme):
    reference = readme.split("## Command reference", 1)[1]
    for command in (
        "tieout learn",
        "tieout learn --add",
        "tieout learn --review",
        "tieout check",
        "tieout rules",
        "tieout profile show",
        "tieout profile lock",
        "tieout scaffold-reference",
    ):
        assert command in reference, f"{command} is undocumented"


def test_the_exit_codes_are_documented(readme):
    from tieout.cli import EXIT_ERROR, EXIT_FINDINGS

    reference = readme.split("## Command reference", 1)[1]
    assert f"`{EXIT_FINDINGS}` findings at or above it" in reference
    assert f"`{EXIT_ERROR}` the run itself failed" in reference


def test_the_deviations_from_the_specification_are_recorded(readme):
    """Deviations belong in the open. A reviewer comparing the build against the
    specification should not have to find them by reading the diff."""
    deviations = readme.split("### Deviations from the specification", 1)
    assert len(deviations) == 2, "the deviations section is missing"
    body = deviations[1]
    for topic in (
        f"{len(load_all_rules())} rules, not 27",
        "palette tolerance",
        "LO-003",
        "Provenance is written twice",
        "Hygiene questions are conditional",
        "defaultTextStyle",
    ):
        assert topic in body, f"the {topic!r} deviation is not recorded"


def test_the_documented_test_count_is_not_wildly_stale(readme):
    """A loose bound: the number is illustrative, but an order of magnitude out
    would mislead."""
    match = re.search(r"pytest\s+#\s+(\d[\d,]*) tests", readme)
    assert match, "the test count is not stated"
    documented = int(match.group(1).replace(",", ""))
    assert 200 <= documented <= 2000


def test_the_coverage_floor_matches_the_configuration(readme):
    import tomllib

    with (README.parent / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    floor = config["tool"]["coverage"]["report"]["fail_under"]
    assert f"floor {floor}%" in readme


def test_the_runtime_dependencies_match_the_packaging(readme):
    """A README listing a dependency the package does not declare, or omitting one
    it does, is the kind of drift that wastes an afternoon at install time."""
    import tomllib

    with (README.parent / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    declared = {
        re.split(r"[><=!\[]", entry)[0].strip().lower()
        for entry in config["project"]["dependencies"]
    }
    install = readme.split("## Install", 1)[1].split("---", 1)[0]
    documented = {name.lower() for name in re.findall(r"`([a-zA-Z0-9_.-]+)`", install)}
    missing = {name for name in declared if name not in documented}
    assert not missing, f"undocumented runtime dependencies: {sorted(missing)}"

    # The count is stated in words, which reads better in prose than a numeral.
    words = {
        1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
        6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten",
    }
    stated = {str(len(declared)), words.get(len(declared), "")}
    assert any(
        f"{form} runtime dependencies" in install for form in stated if form
    ), f"the README should state that there are {len(declared)} runtime dependencies"


def test_the_profile_example_in_the_readme_is_valid(readme):
    """The worked profile is the document a user copies from, so it has to load."""
    import yaml

    block = readme.split("```yaml", 1)[1].split("```", 1)[0]
    parsed = yaml.safe_load(block)
    assert parsed["client"] == "demo"
    assert parsed["slide"]["width_pt"] == 960.0
    assert "not_learned" in parsed


def test_the_offline_recipe_warns_about_the_platform_trap(deployment):
    """`pip download` resolves wheels for the machine it runs on, and lxml,
    Pillow, pydantic-core and pypdfium2 are all compiled. Vendoring on a Mac for
    a Linux desk produces a directory that installs on neither, and the failure
    happens on the machine that cannot easily be debugged."""
    normalised = " ".join(deployment.split())
    assert "Download on the same platform you install on" in normalised
    assert "--only-binary" in deployment
    for compiled in ("lxml", "Pillow", "pydantic-core", "pypdfium2"):
        assert compiled in deployment, compiled
