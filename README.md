# TieOut

Deterministic, offline PowerPoint QA for investment banking decks, with
zero-config client onboarding derived from a single reference deck.

Upload one deck the client has already approved. TieOut derives their house
style from it — palette, typefaces, size bands per text role, logo placement per
slide type, footer conventions, margins, alignment grid, typographic
conventions, terminology — and writes it out as an editable YAML profile where
every value carries the evidence behind it. Check every deck after that against
that profile.

```bash
tieout learn project_meridian_final.pptx --client acme
tieout check new_draft.pptx --client acme --format html --out report.html
```

**No LLM calls. No network access of any kind. No telemetry.** TieOut runs
air-gapped, and `tests/test_no_network.py` walks the abstract syntax tree of
every runtime module to keep it that way — the guarantee is structural, not a
claim about one code path.

---

## Contents

- [Install](#install)
- [A worked onboarding](#a-worked-onboarding)
- [How derivation works](#how-derivation-works)
- [The profile](#the-profile)
- [The rule catalogue](#the-rule-catalogue)
- [Command reference](#command-reference)
- [Known limitations](#known-limitations)
- [Development](#development)

---

## Install

Python 3.11 or later.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

That puts `tieout` on the path. Nine runtime dependencies, all of them local:
`python-pptx`, `lxml`, `pydantic`, `typer`, `rich`, `jinja2`, `Pillow`,
`pyyaml`, `ruamel.yaml`.

---

## A worked onboarding

TieOut ships a synthetic reference deck so you can watch the whole cycle on a
deck whose every parameter is known, before pointing it at real client material.
Everything in it is invented — no text, figure, company name or codename comes
from any real filing.

### 1. Generate a deck to learn from

```console
$ tieout scaffold-reference --out decks
Built reference_clean.pptx (clean)
       reference_dirty.pptx (one seeded defect per rule)
       reference_comments.pptx (comments)
       reference_external_rel.pptx (external rel)
       reference_metadata.pptx (metadata)
       reference_wrong_slide_size.pptx (wrong slide size)
       reference_spec.yaml (the parameters it was built from)
```

A 26-slide deck in bulge-bracket house style: title slide, table of contents,
four numbered sections behind dividers, dense tables with right-aligned figures
and parenthesised negatives, two charts, an appendix divider and two disclaimer
slides of 7pt legal type.

### 2. Learn

```console
$ tieout learn decks/reference_clean.pptx --client demo

Learned profiles/demo.yaml
  No questions: every rule was settled from the evidence.
6 rule(s) deliberately not derived:
  brand.fonts.roles.chart_label: only 2 observations, below min_support of 3
  layout.safe_margin_pt.section_divider: section_divider slides carry too little content for a margin to be meaningful
  layout.recurring.name:column: name:column appears on 29 shapes but at no consistent position, deck-wide or within any archetype
  layout.gutter_pt: no consistent gutter found, spread 12pt to 24pt
  ...
```

Two things to notice.

**It asked nothing.** The evidence settled every rule it emitted. TieOut only
asks about things it genuinely cannot infer, capped at twelve questions ranked by
how many future findings depend on the answer, and every question has a safe
default so the whole interview can be accepted with one keystroke.

**It says what it refused to derive.** Two charts is below the support threshold
for a deck-wide font role, so the chart-label rule is not invented from two
observations — it is recorded in `not_learned`, with the reason, and the
corresponding rule does not run. A tool that silently skipped it would leave you
believing a check exists that does not.

### 3. Check the deck you learned from

```console
$ tieout check decks/reference_clean.pptx --client demo

╭─────────────── tieout ───────────────╮
│ deck    decks/reference_clean.pptx   │
│ client  demo  profile v1  slides  26 │
╰──────────────────────────────────────╯

No findings.

0 findings from 34 rules across 26 slides.

rules not run
  LO-006  disabled in the profile
  TY-009  disabled in the profile

not checked (34 shape(s))
  LO-002  no safe margin was learned for the section_divider archetype (4 shapes on 4 slides)
  LO-007  no size band was learned for the chart_label role (2 shapes on 2 slides)
  TY-004  a section_divider heading is a fixed document label rather than a prose headline (4 shapes on 4 slides)
  ...
```

Exit code 0. This is the property everything else rests on: **learn from a deck,
then check that deck, and get nothing.** Anything reported here would be the tool
disagreeing with the material it was taught from. `tests/test_clean_deck.py`
holds all 36 rules to it, individually, on every commit.

Note the footer. "Not checked" is as important as the finding list — it is what
lets you tell *nothing is wrong* from *nothing was looked at*.

### 4. Check a deck with problems

```console
$ tieout check decks/reference_dirty.pptx --client demo
...
slide 5
    shape              message
────────────────────────────────────────────────────────────────────────────
    Logo               logo sits 18.0pt left of its expected position
                       one image present on 23 of 26 slides
    Footnote           Footnote sits 36.0pt from the position it holds
                       elsewhere in the deck (left 36pt, top 480pt,
                       888x12pt (+/-2pt), support 17)

44 finding(s): 6 blocker, 20 major, 18 minor — from 34 rules across 26 slides.
$ echo $?
1
```

Every finding states the evidence behind the value it expected. "The logo should
be at 852pt" invites an argument; "on 23 of your 26 slides it is" does not.

### 5. Use it as a pre-send gate

`check` exits 1 when anything at or above `--fail-on` is found, and 2 when the
run itself failed — so a gate can tell a bad deck from a broken tool.

```bash
tieout check draft.pptx --client acme --fail-on major --quiet || exit 1
```

### 6. Add a second deck as more evidence arrives

```console
$ tieout learn --add another_approved_deck.pptx --client demo

Merged another_approved_deck.pptx into profiles/demo.yaml
4 change(s) from the added deck:
  brand.fonts.roles.body: widened, 10pt to 14pt -> 10pt to 18pt (the added deck uses sizes the profile did not permit)
  brand.logo.per_archetype.content: unchanged, locked (logo box on content slides)
  typography.quotes: widened, curly -> not learned (the added deck uses 'straight' where the profile expected 'curly')
  typography.canon_terms.Ashcombe Partners: narrowed, not checked -> Ashcombe Partners (the added deck establishes this term)
    consequence: a deck spelling 'Ashcombe Partners' differently will now be reported by TY-005

1 change(s) made a rule stricter. A deck that passed before may now fail; each consequence is listed above.
```

Three behaviours worth knowing:

- **Locked fields are never overwritten.** A lock is a decision a person made,
  and evidence does not outvote it. `tieout profile lock --client demo --field
  brand.palette_hex` after you correct something by hand; answering an interview
  question locks its field automatically.
- **Narrowing is never silent.** Any change that makes a rule stricter carries
  the sentence saying what would newly fail.
- **Where two decks disagree, the rule is retired rather than voted on.** One
  deck using curly quotes and another straight means there is no convention, so
  the key moves to `not_learned`. Keeping a rule that half the client's own
  material breaks is worse than having no rule.

---

## How derivation works

Four stages, and the design principle running through all of them is from the
specification: **the engine must never invent a rule it cannot justify from
evidence. Silence is correct.**

### 1. Resolve, then measure

In a real deck a run's explicit font is almost always absent. Open any
banker-authored slide and the XML for a body bullet is frequently just
`<a:r><a:t>text</a:t></a:r>` — no font, no size, no colour. Those come from a
chain reaching up through the paragraph, the shape's list style, the layout
placeholder, the master placeholder, the master's text styles, the presentation
defaults and finally the theme's font scheme.

A tool that reads `run.font.name` gets `None`, concludes the font is not
approved, and reports a brand violation on **every correctly formatted slide in
the deck**. So TieOut resolves the full chain first (`model/inherit.py`),
including scheme-colour indirection through the master's colour map and the
`lumMod`/`lumOff`/`tint`/`shade` transforms, and nothing above the model layer is
permitted to read an unresolved property.

Colour comparison is CIE76 Delta-E in Lab space, never string equality. `#EB0A1E`
and `#EA0A1F` are different strings and the same colour to any reader.

### 2. Classify each slide

Archetypes are what make the rest work without interrogating you about
exceptions. "The logo is at x=852" is false for a deck; "the logo is top-right on
content slides and centred on section dividers" is true, and deriving the second
requires knowing which slides are which.

Ten archetypes — `title`, `section_divider`, `agenda`, `content`, `full_bleed`,
`table_heavy`, `chart_heavy`, `appendix_divider`, `disclaimer`, `unknown` —
assigned by an ordered cascade of explainable predicates over layout names and
types, placeholder signatures, text density, area shares and title patterns. No
machine learning. Every assignment records its reasons, is written into the
profile, and can be corrected there. Anything `unknown` is excluded from learning
so a slide TieOut cannot categorise cannot pollute a derived rule.

### 3. Observe, weighted and scoped

Every deriver speaks in typed observations carrying a key, a scope, a value and a
weight. The weighting matters: colours are weighted by the area they cover, fonts
by the characters they set. Counting shapes instead would let a 1.5pt hairline
rule outvote a full-bleed background.

Scope matters as much. Bullet punctuation counted per bullet across a deck gives
a number that means nothing, because one long list with stops on every line
outvotes ten short lists without them. Counted per list and then voted across
lists, it gives the convention a reader would describe.

### 4. Classify the observations

For each `(key, scope)` group:

| Class | Condition | Outcome |
|---|---|---|
| **Invariant** | one distinct value, support ≥ `min_support` | hard rule, high confidence |
| **Dominant** | top value ≥ 85% of weighted observations | rule at medium confidence, outliers queued as a question |
| **Multimodal** | two or three clusters each ≥ 15% | explained by archetype if possible, giving per-archetype rules; otherwise an enumerated allowed set |
| **Variable** | no structure, or entropy above threshold | **nothing emitted**, recorded in `not_learned` with the reason |

`min_support` is 3 observations for deck-wide keys, 2 for archetype-scoped keys,
1 for structural singulars like the canvas size. Continuous values are clustered
before classification — a logo placed by hand at 871.9pt and 872.1pt is at one
position — with tolerances of 2pt for geometry, 0pt for font sizes and Delta-E 3.0
for colour.

**Tolerances in emitted rules are derived, not hardcoded:**
`max(observed_spread × 1.5, floor)`. A client with sloppy but acceptable logo
placement gets a looser rule automatically rather than forty false positives; a
client with millimetre discipline gets a tight one.

### What each deriver produces

**Brand.** Palette clustered in Lab space, keeping clusters above 0.5% of the
deck's weighted colour. The discarded tail is reported to you as *"colours used
once, possibly errors in the reference deck"* and is deliberately **not** added
to the palette — a single `#1F3865` on slide 12 is almost certainly a typo for
`#1F3864`, and admitting it would licence the typo forever. The palette tolerance
is half the minimum inter-cluster distance, so it can never be wide enough to
merge two real brand colours.

Typefaces above 1% of characters become the approved set. Size bands are derived
per text role — title, subtitle, body, table, chart label, footnote — and a role
with fewer than four distinct observed sizes gets an exact allowed set rather
than a band, because a band invented from two observations permits sizes the
client has never used.

Logos are identified by hashing image bytes; any image on ≥40% of slides is a
candidate. Geometry is derived per archetype, and an archetype where the logo
never appears becomes an explicit **exemption** rather than an absence — "absent
by design" and "no evidence either way" must not produce the same finding.

**Layout.** Safe margins from the 5th percentile of content-box edges per
archetype, excluding the logo, the footer and full-bleed shapes. The alignment
grid comes from clustering every content-shape edge deck-wide and keeping
clusters with support of five or more; that is what turns "things should line up"
into something testable, and it is why near-miss alignment is only reported
against a *real* grid line — a shape 3pt off a learned grid line is a defect,
3pt off a one-off edge is not.

**Typography.** Quote style, title capitalisation, bullet punctuation, thousands
separators, negative style, per-column decimal consistency, currency notation and
date format, each counted at the scope that makes it meaningful. The deriver and
the rules that enforce it call the same parsing functions (`text.py`), so a
derived convention and its enforcement cannot disagree.

**Terminology.** Capitalised phrases occurring three or more times are grouped by
a normalisation key; a group with more than one surface form becomes a *question*
rather than a decision, because enforcing the wrong spelling of a client's own
name across every future deck is the most damaging kind of false confidence.
Variation explained purely by sentence-initial capitalisation is not even asked
about. The client's proper nouns are also seeded into the spell-check dictionary,
so TY-009's first run is not a list of their own company names.

---

## The profile

A readable, editable YAML document. Every derived value carries a `why:` comment
recording the evidence behind it.

```yaml
# Generated by tieout learn on 2026-09-14 from:
#   - reference_clean.pptx (26 slides)
# No questions: every rule was settled from the evidence.
client: demo
version: 1
# why: observed, invariant across 26 slides
slide:
  width_pt: 960.0
  height_pt: 540.0
brand:
  fonts:
    # why: character-weighted typefaces across 26 slides: Gill Sans MT 96.8%, Arial 3.2%
    allowed:
      - Gill Sans MT
      - Arial
    roles:
      # why: 4 distinct sizes observed across 72 runs on 13 slides (10, 11, 12, 14pt); emitted as a band
      body:
        min_pt: 10.0
        max_pt: 14.0
  # why: area and character weighted colour clusters across 26 slides: #A6A6A6 41.2%, ...
  palette_hex: ['#A6A6A6', '#000000', '#1F3864', '#FFFFFF']
  # why: half the minimum inter-cluster distance of 31.9 Delta-E between palette colours
  palette_tolerance_delta_e: 6.0
  # why: one image present on 23 of 26 slides
  logo:
    per_archetype:
      # why: logo geometry on 12 content slides, invariant with an observed spread of 0.0pt
      content: {left: 852.0, top: 24.0, width: 72.0, height: 24.0, tolerance_pt: 2.0}
      section_divider: {left: 420.0, top: 240.0, width: 120.0, height: 40.0, tolerance_pt: 2.0}
      title: exempt            # why: logo absent on all 1 title slides
# rules deliberately not derived, and why. Silence is correct when the evidence is thin
not_learned:
  - key: brand.fonts.roles.chart_label
    reason: only 2 observations, below min_support of 3
# field paths protected from re-learning
locks: []
```

Edit it freely. `ruamel.yaml` round-trips the document, so your own comments and
edits survive re-learning, and anything listed in `locks` is never overwritten.
`profiles/default.yaml` is a full worked example.

`tieout profile show --client acme` prints the evidence behind every value
without you reading the file.

---

## The rule catalogue

36 rules across four categories. Each one is an independent class — rules never
import each other, and none may touch `python-pptx` — with a docstring stating
exactly what it measures and its known false-positive mode. `tieout rules`
prints this table for your own installation, including which rules your client's
profile has turned off and which are not running because their input was never
learned.

Severity drives `--fail-on`. "Default" is whether the rule runs out of the box.
"Expectation derived from" is the profile path the rule reads: **a rule whose
input was never learned does not run at all**, because the only findings it could
produce would be measured against a default the client never agreed to.

### Brand (10 rules)

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **BR-001** | blocker | on | Logo absent from an archetype that requires it | `brand.logo` |
| **BR-002** | major | on | Logo outside its archetype's expected box | `brand.logo` |
| **BR-003** | major | on | Logo size deviates, or its aspect ratio is distorted | `brand.logo` |
| **BR-004** | major | on | Colour off the learned palette | `brand.palette_hex` |
| **BR-005** | blocker | on | Typeface outside the approved set | `brand.fonts.allowed` |
| **BR-006** | major | on | Page number missing, malformed or out of position | `brand.footer.page_number` |
| **BR-007** | major | on | Page numbers not strictly ascending | `brand.footer.page_number` |
| **BR-008** | minor | on | Title geometry drifts from its layout placeholder | `brand.title_geometry_tolerance_pt` |
| **BR-009** | blocker | on | Slide dimensions do not match the profile | `slide` |
| **BR-010** | major | on | Required boilerplate text absent | `brand.footer.boilerplate` |

### Layout (8 rules)

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **LO-001** | blocker | on | A shape extends outside the slide canvas | not learned; fixed behaviour |
| **LO-002** | minor | on | A shape intrudes into the learned safe margin | `layout.safe_margin_pt` |
| **LO-003** | minor | on | A shape edge sits just off a learned grid line | `layout.grid`, `layout.near_miss_alignment_pt` |
| **LO-004** | major | on | Two text-bearing shapes overlap | `layout.overlap_area_share` |
| **LO-005** | minor | on | A recurring element has moved off its learned position | `layout.recurring` |
| **LO-006** | major | **off** | Text likely overflows its shape | not learned; fixed behaviour |
| **LO-007** | major | on | A font size falls outside the learned band for its role | `brand.fonts.roles` |
| **LO-008** | minor | on | Sibling shapes in a row or column have uneven gutters | `layout.gutter_stdev_pt`, `layout.position_tolerance_pt` |

### Typography (9 rules)

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **TY-001** | minor | on | Quote or apostrophe style contradicts the learned convention | `typography.quotes` |
| **TY-002** | minor | on | Double space, trailing whitespace, space before punctuation or mixed non-breaking spaces | not learned; fixed behaviour |
| **TY-003** | minor | on | Bullet terminal punctuation inconsistent with the learned convention | `typography.bullet_terminal_punctuation` |
| **TY-004** | minor | on | Slide title capitalisation deviates from the learned convention | `typography.title_case` |
| **TY-005** | major | on | A non-canonical variant of a learned term appears | `typography.canon_terms` |
| **TY-006** | major | on | Number formatting is inconsistent within one table column | `typography.decimal_places_by_column` |
| **TY-007** | minor | on | Currency or unit notation deviates from the learned pattern | `typography.currency_pattern` |
| **TY-008** | minor | on | Date format deviates from the learned format | `typography.date_format` |
| **TY-009** | minor | **off** | Spelling against a bundled wordlist plus the per-client dictionary | not learned; fixed behaviour |

### Hygiene (9 rules)

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **HY-001** | blocker | on | Placeholder or draft marker text left in the deck | not learned; fixed behaviour |
| **HY-002** | major | on | Speaker notes present when the profile disallows them | not learned; fixed behaviour |
| **HY-003** | major | on | Hidden slides present when the profile disallows them | not learned; fixed behaviour |
| **HY-004** | blocker | on | Author or company metadata left in docProps | not learned; fixed behaviour |
| **HY-005** | blocker | on | PowerPoint comments present in the package | not learned; fixed behaviour |
| **HY-006** | major | on | Empty placeholder left visible on a slide | not learned; fixed behaviour |
| **HY-007** | blocker | on | External or broken relationship in the package | not learned; fixed behaviour |
| **HY-008** | major | on | Image effective resolution below the profile's floor | not learned; fixed behaviour |
| **HY-009** | minor | on | Font neither embedded nor a standard system font | not learned; fixed behaviour |

### Turning a rule on or off

Two rules ship **off**: `LO-006` because overflow detection is approximate and
needs the real font files, and `TY-009` because a spell checker is only useful
once it knows the client's vocabulary. Opt in through the profile:

```yaml
rules:
  disabled: []
  enabled: [LO-006, TY-009]
  severity_overrides:
    BR-008: info          # downgrade a rule you find noisy rather than disabling it
```

Naming a rule exactly on the command line also runs it —
`tieout check deck.pptx --client acme --rules LO-006` — while a glob such as
`LO-*` is a filter that leaves the defaults alone.

### Accepting a finding

```bash
tieout check deck.pptx --client acme --accept BR-002@slide7
```

Appends to `profiles/acme.suppress.yaml`. Accept the same rule three times and
TieOut says what it thinks is really happening:

```
BR-002 has been accepted 3 times. It is probably miscalibrated. Fold the evidence in with:
  tieout learn --add THAT_DECK.pptx --client acme
```

---

## Command reference

```
tieout learn REF.pptx [REF2.pptx ...] --client NAME [--interactive] [--out PATH]
tieout learn --add DECK.pptx --client NAME [--interactive]
tieout learn --review --client NAME              # answer deferred questions

tieout check DECK.pptx --client NAME [--profile PATH]
                       [--format table|json|html] [--out PATH]
                       [--severity blocker|major|minor|info]
                       [--rules BR-*,LO-003] [--exclude TY-009]
                       [--accept RULE@slideN] [--fail-on blocker] [--quiet]

tieout rules [--client NAME]
tieout profile show --client NAME
tieout profile lock --client NAME --field brand.logo.per_archetype.content
tieout scaffold-reference --out DIR [--spec PATH] [--clean-only]
```

**Exit codes.** `0` nothing at or above `--fail-on`; `1` findings at or above it;
`2` the run itself failed. A gate that cannot distinguish the last two will
eventually wave a bad deck through.

**Profiles** live in `./profiles/NAME.yaml`. Set `TIEOUT_PROFILE_DIR` to keep
them on a shared drive.

**Reports.** `table` for a terminal, grouped by slide then severity because that
is the order someone fixes a deck in. `json` for a pipeline, with a stable
additive schema and `unchecked`/`rules_skipped` at the top level. `html` for
everyone else: one self-contained file, no CDN, no external fonts, nothing
fetched at view time, with the provenance of every finding shown inline.

---

## Known limitations

Stated plainly, because a QA tool that overstates its coverage is worse than one
that admits its edges.

**No semantic or argument-level review.** This is the mechanical layer only.
TieOut will tell you the logo is 18pt out of place and the EBITDA column mixes
decimal places. It has no view on whether the valuation is defensible, whether
the chart supports the headline above it, or whether the story works. Nothing
here substitutes for reading the deck.

**Errors in the reference deck are learned as rules.** The whole design treats
the reference deck as ground truth, so a mistake that is *consistent* in it
becomes the expectation for every future deck. A logo 4pt out of place on all 26
slides is learned as the correct position. TieOut mitigates this where it can —
one-off colours are discarded rather than admitted to the palette, and outliers
in a dominant value are queued as a question — but it cannot detect a systematic
error. Learn from a deck you are confident in, read the emitted profile, and
correct and `lock` anything wrong.

**A single reference deck is thin evidence for deck-wide conventions.** Rules
scoped to an archetype with two or three slides, or deck-wide keys with few
observations, fall below the support thresholds and land in `not_learned` rather
than being derived. That is the correct behaviour, but it means one deck gives
you a partial ruleset. `tieout learn --add` with further approved decks is what
promotes dominant rules to invariant and fills the gaps.

**Overflow detection is approximate and off by default (LO-006).** Text
overflow is measured with Pillow against the resolved TrueType font, accounting
for wrapping, insets and autofit scaling. It cannot account for kerning pairs,
ligatures, hyphenation or PowerPoint's own line-breaking, so it is a close
estimate rather than a measurement. Where a font cannot be resolved on the host
the shape is recorded in `unchecked` and never guessed at. Metric-compatible
substitutes are used where they genuinely are metric-compatible (Liberation Sans
for Arial), which is a substitution rather than a guess but still not the real
font.

**Spell check is off by default (TY-009).** It is only useful once it knows the
client's vocabulary. The bundled dictionary is ~38,000 words — a frequency-ranked
list intersected with a full dictionary, plus corporate-finance vocabulary — and
the client's own proper nouns are seeded from the reference deck. Ticker
patterns, short all-caps acronyms and canon terms are skipped. It will still
flag unusual but legitimate words; add them to `hygiene.dictionary`.

**SmartArt is not inspected.** A `dgm` graphic frame is recognised as a shape and
its box participates in canvas and margin checks, but its internal geometry,
text and colours live in a diagram part TieOut does not model. Nothing inside a
SmartArt graphic is checked, and pretending otherwise would produce confident
nonsense.

**Chart internals are partially inspected.** Chart text — series names, category
labels, titles, axis titles — is read and checked for typefaces, placeholder
markers and terminology. Chart *series colours* are not collected into the
palette: TieOut does not model chart geometry, so there is no area to weight them
by, and they are set by the theme rather than typed by the author. A chart in
off-brand colours will not be reported by BR-004.

**No slide rendering.** There are no thumbnails, so nothing is verified visually
and no finding can be confirmed by eye inside the report. The HTML report
reserves a fixed-aspect slot per slide and carries a `data-bbox` attribute on
each finding, so images and overlays can be added later without template
changes.

**No auto-fix.** TieOut reports; it does not edit your deck.

**Ambiguous dates are resolved by order, not by intelligence.** `09/14/2026` is
unambiguous, but `05/06/2026` is not, and the first matching format in the
candidate list wins. If your house style uses a numeric date format, expect
TY-008 to be of limited use.

**Placeholder geometry inheritance is resolved, group transforms are applied,
rotation is accounted for — but 3-D rotation and shape effects are not.** A shape
with a large outer shadow or a 3-D transform may extend beyond the bounding box
TieOut measures.

---

## Development

```bash
.venv/bin/python -m pytest              # 385 tests
.venv/bin/python -m pytest --cov=tieout # coverage, floor 85%
.venv/bin/python -m ruff check .        # lint
.venv/bin/python -m mypy                # types, strict
```

All fixtures are generated; no binary `.pptx` is committed.

CI runs exactly these three commands on Python 3.11 and 3.12, so what you check
before pushing is what CI checks after. It also installs Liberation fonts — LO-006
records a shape as `unchecked` when it cannot resolve the real font file, so
without them the overflow path would be green in CI while never running — and
fails the build if a binary `.pptx` is ever committed.

### The tests that matter

- **`test_learn_roundtrip.py`** gates everything. It builds the reference deck
  from its declarative specification, learns from it, asserts the derived profile
  recovers the parameters the deck was literally constructed from — palette within
  Delta-E 2.0, font sets exactly equal, logo boxes within 1pt, margins within
  2pt, conventions exactly equal, archetype assignment exactly equal — and then
  checks the deck against its own profile and asserts zero findings. If the
  learner cannot recover values the deck was built from, it is broken however
  plausible its output looks.
- **`test_clean_deck.py`** is the ongoing false-positive guard, running each of
  the 36 rules individually against the clean deck so a regression names the
  rule. A rule that catches its seeded defect but also fires on a correct slide is
  worse than no rule, because a report with false positives in it teaches the
  reader to skim.
- **`test_no_network.py`** walks the AST of every runtime module for network
  imports, network calls and dynamic imports.
- **`test_inherit.py`** covers the eight inheritance cases, each isolating one
  level of the chain with hand-built OOXML so a failure names the level.

### Layering

```
model/      units, colour, package, inherit, archetype, deck, loader, furniture
              the only place python-pptx is imported (loader.py)
cluster.py  deterministic 1-D clustering, dominance, entropy
text.py     quote/case/number/currency/date/terminology parsing
learn/      observe -> classify -> derive_* -> interview -> emit / merge
profile/    schema (pydantic) and loader
rules/      base + brand, layout, typography, hygiene
report/     console, json_out, html
fixtures/   spec + generator
```

Rules read the resolved model and nothing else. If a rule needs something the
model does not expose, the model gets extended — without that boundary every rule
reimplements inheritance slightly differently and the tool quietly disagrees with
itself.

### Deviations from the specification

Recorded for review rather than buried:

1. **36 rules, not 27.** Section 9's header and section 14 both say 27, but the
   catalogue itself lists 36 (brand 10, layout 8, typography 9, hygiene 9). The
   catalogue is treated as authoritative and all 36 are implemented, seeded and
   tested.
2. **Five modules not in section 4's file list**: `model/color.py` (Delta-E is
   required throughout and needs a home), `model/furniture.py` (logo, footer and
   text-role identification, shared by the learner and the rules so the two
   cannot disagree), `cluster.py` and `text.py` (shared primitives the rules must
   reach without importing the learning engine), and `fixtures/spec.py` (the
   declarative specification the generator builds from, which section 11 requires
   as a file).
3. **A ceiling on the palette tolerance.** Section 8.3 derives it as half the
   minimum inter-cluster distance, floored at 2.0. With a well-separated
   four-colour palette that yields ~16 Delta-E, wide enough for a visibly wrong
   colour to pass, so a ceiling of 6.0 is applied and recorded in the profile's
   provenance.
4. **LO-003 fires only against a learned grid line.** A literal "any two edges
   0.5–4pt apart" reading produces dozens of false positives per deck. Section 8.3
   resolves it — "a shape 3pt off a real grid line is a defect while a shape 3pt
   off a one-off edge is not" — and that is what is implemented.
5. **Provenance is written twice**, as `why:` comments and as a generated
   `provenance` block. The single-copy design was built first and abandoned: YAML
   attaches a leading comment to the preceding sibling key, so recovering notes
   from comments mis-assigned them across section boundaries and a finding about
   the grid cited the margin derivation.
6. **Hygiene questions are conditional on evidence.** Section 8.4 lists four
   settings to ask about with a default; asked unconditionally they fire on every
   deck, and section 14's requirement that a clean reference deck produce zero
   questions would be unachievable. They are asked only where the deck shows
   contrary evidence.
7. **`p:defaultTextStyle` added to the inheritance chain** between the master's
   text styles and the theme. It is genuinely part of PowerPoint's resolution —
   a plain text box inherits from it — and omitting it resolved every text box to
   the theme default size.
8. **Margins are clamped to the tightest observed edge.** The 5th percentile
   exists to tolerate shapes outside the content frame, but every shape it
   tolerates is one LO-002 would then report on the deck the margin was learned
   from. The clamp makes the profile unable to fail its own reference deck.
9. **`rules.enabled` added to the profile.** Without an opt-in list a rule with
   `default_enabled = False` could never be switched on at all.
10. **`Rule.run(deck, profile)` keeps the specified signature**, with furniture
    reached through a memoised helper and `unchecked` collected from the rule
    instance, rather than changing the signature to pass a context object.

---

## Licence

Proprietary. All fixture content is invented; nothing in this repository is
derived from any real filing, bank or client material.
