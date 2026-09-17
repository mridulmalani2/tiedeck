# TieOut

Deterministic, offline PowerPoint QA for investment banking decks, with
zero-config client onboarding derived from a single reference deck.

Upload one deck the client has already approved. TieOut derives their house
style from it — palette, typefaces, size bands per text role, logo placement per
slide type, footer conventions, margins, alignment grid, typographic
conventions, terminology — and writes it out as an editable YAML profile where
every value carries the evidence behind it. Check every deck after that against
that profile. Three further rules need no profile at all: they read the deck's
own tables and report a figure that disagrees with itself, a figure stated in two
different units, and a total row that does not sum.

```bash
tieout learn project_meridian_final.pptx --client acme
tieout check new_draft.pptx --client acme --format html --out report.html
```

**No LLM calls. No network access of any kind. No telemetry.** TieOut runs
air-gapped, and `tests/test_no_network.py` walks the abstract syntax tree of
every runtime module to keep it that way — the guarantee is structural, not a
claim about one code path.

There is an optional semantic layer, `tieout-review`, which does call a model.
It is a **separate package installed separately** precisely so the sentence
above stays true of `tieout` itself rather than becoming a claim about an import
guard: the walk treats `tieout_review` as a forbidden import, so a core module
reaching for it is a test failure. Every identifying term is replaced before
anything leaves the machine, and nothing is sent while the redaction has items
outstanding. See [Optional: semantic review](#optional-semantic-review).

---

## Contents

- [Install](#install)
- [A worked onboarding](#a-worked-onboarding)
- [How derivation works](#how-derivation-works)
- [The profile](#the-profile)
- [The rule catalogue](#the-rule-catalogue)
- [Optional: semantic review](#optional-semantic-review)
- [Optional: the local UI](#optional-the-local-ui)
- [Deploying it](#deploying-it)
- [Command reference](#command-reference)
- [Known limitations](#known-limitations)
- [Development](#development)

---

## Install

Python 3.11 or later. This is the development install, from a clone; to put
TieOut on someone else's machine see [Deploying it](#deploying-it).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip      # -e needs pip 21.3 or newer
.venv/bin/python -m pip install -e ".[dev]"
```

The pip upgrade is not optional for an editable install: the pip bundled with an
older Python is 21.2, which cannot install a `pyproject.toml`-only project in
editable mode and says so obscurely — *"editable mode currently requires a
setuptools-based build"*. Plain installs need no upgrade.

That puts `tieout` on the path. Nine runtime dependencies, all of them local:
`python-pptx`, `lxml`, `pydantic`, `typer`, `rich`, `jinja2`, `Pillow`,
`pyyaml`, `ruamel.yaml`. None of them can open a socket.

The optional semantic layer is a separate install and adds exactly one
dependency, the Anthropic SDK:

```bash
.venv/bin/python -m pip install -e ".[review]"
```

That puts `tieout-review` on the path as a second command. Install it and
`tieout` is unchanged: it still has no way to reach a network, and the test that
proves that treats the review package as a forbidden import.

There is also a local web UI, likewise separate:

```bash
.venv/bin/python -m pip install -e ".[ui]"
```

It serves one page on `127.0.0.1` and refuses to bind anything else. See
[Optional: the local UI](#optional-the-local-ui). Everything works without it.

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

0 findings from 38 rules across 26 slides.

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
holds all 39 rules to it, individually, on every commit.

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

slide 7
    shape              message
────────────────────────────────────────────────────────────────────────────
    Recap table        '2025a' / 'ebitda' is 375 here but 351 in Financial
                       table on slide 6
...

47 finding(s): 6 blocker, 23 major, 18 minor — from 38 rules across 26 slides.
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

### The reference deck review

`learn` finishes by running the whole rule catalogue against the deck it just
learned from, and reporting what the profile does not cover.

```console
Reference deck review — 12 finding(s) the profile does not cover
  Either fix these in the reference deck before it becomes the house standard,
  or accept them: every one is about to be the bar every future deck is
  measured against.
  [blocker] BR-005 ×1 on slide(s) 8: 2 runs in 2 shapes set in Arial, which is
  not an approved typeface
  [minor] TY-002 ×3 on slide(s) 5, 9, 20: 10 spacing defects on this slide
```

A profile derived from a deck and then run against that same deck should find
nothing. That is the tool's own acceptance criterion, and every deviation is one
of exactly two things.

Either **the profile is wrong** — it derived a central value where the deck holds
a range, and now reports the deck's own spread. That is a defect in TieOut and
belongs in its test suite, not in your report.

Or **the reference deck really does contain what the rule says**: a typeface
nobody meant to use, a colour used once, a double space. That is worth knowing
*before* the profile goes into service, because every one of those defects is
about to become the standard every future deck is measured against, or a finding
on every future deck that inherits it.

So it is reported, never absorbed. Widening the profile to cover a defect would
make the criterion hold by making the tool useless, and recording blanket
exemptions would do the same more quietly.

A rule that *raised* while reviewing is called out separately, in red, and the
review is not clean however few findings came back: a rule that crashed checked
nothing, and counting it among the rules that ran claims coverage that does not
exist.

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

That last claim only holds while the grid stays *selective*, and on a real deck
one axis of it did not. Rows and columns are derived by the same thresholds, but
a deck is not symmetric about them: it has a handful of real columns that repeat
slide after slide, and a great many distinct vertical positions, because
vertical placement follows the length of the content above it rather than a
template. The deck that prompted this derived 27 columns and **53 rows** on a
960 × 540pt canvas. At near-miss width that put 56% of the vertical canvas
inside the window of *some* row against 19% horizontally, and every remaining
LO-003 finding on that deck was on the y axis. 53 rows on 540pt is not a grid,
it is a transcript of every y-coordinate the deck uses, and a shape dropped at
random would have tripped the rule.

So each axis is measured after it is derived. If the bands within near-miss
distance of its lines cover more than **25%** of that axis, the requirement to
recur across slides is raised — a step at a time, only on the axis that needs it
— until the axis is selective again. An axis that never gets there is not
emitted at all, and `learn` says so in `not_learned`:

```
layout.grid.rows_pt: 24 horizontal edge clusters have the support to be grid
lines, but they saturate the canvas: ... no horizontal grid is emitted and
LO-003 will not run on the y axis
```

Reported rather than guessed at, for the same reason as everything else here: a
rule that cannot be derived honestly should skip, and a user who reads an empty
LO-003 needs to know whether that means a clean deck or a rule that never ran.
On a deck whose rows *are* a grid, nothing is tightened and nothing is dropped.

Merging inherits the same test. Each deck's own grid comes in under the limit by
construction, but the union of two need not, so `learn --add` declines to widen
an axis past it and says the two decks do not share that axis.

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

46 rules across six categories. Each one is an independent class — rules never
import each other, and none may touch `python-pptx` — with a docstring stating
exactly what it measures and its known false-positive mode. `tieout rules`
prints this table for your own installation, including which rules your client's
profile has turned off and which are not running because their input was never
learned.

Severity drives `--fail-on`. "Default" is whether the rule runs out of the box.
"Expectation derived from" is the profile path the rule reads: **a rule whose
input was never learned does not run at all**, because the only findings it could
produce would be measured against a default the client never agreed to.

### Brand (11 rules)

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
| **BR-011** | major | on | Chart series drawn in a colour off the learned palette | `brand.palette_hex` |

### Layout (9 rules)

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
| **LO-009** | minor | on | Bullets at one level of a list do not share an indent | the list itself |

### Typography (9 rules)

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **TY-001** | minor | on | Quote or apostrophe style contradicts the learned convention | `typography.quotes` |
| **TY-002** | minor | on | Double space, trailing whitespace, space before punctuation or mixed non-breaking spaces | not learned; fixed behaviour |
| **TY-003** | minor | on | Bullet terminal punctuation inconsistent with the learned convention | `typography.bullet_terminal_punctuation` |
| **TY-004** | minor | on | Slide title capitalisation deviates from the learned convention | `typography.title_case` |
| **TY-005** | major | on | A non-canonical variant of a learned term appears | `typography.canon_terms`, `typography.canon_accepted` |
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

### Chart (5 rules)

Charts get their own category because in a banking deck they carry the
argument, and because **these expectations are not learned from a reference
deck**. The rest of the catalogue derives its expectation from the client's own
approved material, which is right for a palette: there is no universal correct
navy. There is a universal correct answer to *can the reader tell what these
bars are measured in*, and deriving that from one deck would let a deck that
omits units everywhere teach the tool that omitting units is the house style.

Every one of them takes **multiple pathways to the same fact and fires only
when none of them leads anywhere** — which is what makes a craft standard safe
to assert against decks built in different styles. A chart's units may be in
its axis title, its own title, a caption above it, the slide headline, the data
labels or a footnote. On a real deck the chart had no title and no axis title;
the caption above it read `REVENUE AND EBITDA (EUR M)`, which is a house style
rather than a defect, and a rule that cannot see that is a rule that gets
switched off.

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **CH-001** | major | on | A chart is not named by anything on the slide | the craft |
| **CH-002** | major | on | A chart's units or scale are stated nowhere on the slide | the craft |
| **CH-003** | major | on | A bar chart's value axis does not start at zero | the craft |
| **CH-004** | major | on | A multi-series chart gives no way to tell the series apart | the craft |
| **CH-005** | minor | on | Series in one chart label their values to different precision | the craft |

### Consistency (3 rules)

The one category that reads the deck's *content* rather than its form, and it
does so arithmetically: every finding is a comparison between two numbers the
deck itself states. No profile input, no key, no network. These are the mistakes
that survive four turns of a deck because each page is internally correct.

| Rule | Severity | Default | What it measures | Expectation derived from |
|---|---|---|---|---|
| **CO-001** | major | on | The same labelled figure differs between tables | not learned; fixed behaviour |
| **CO-002** | major | on | The same figure appears in two different scales | not learned; fixed behaviour |
| **CO-003** | major | on | A row labelled as a total does not sum its column | not learned; fixed behaviour |

Each one keys a number on the pair (row label, column header) so the column
disambiguates the scope, ignores labels too generic to identify a metric
(`total`, `value`, `other`), and treats a label bearing a footnote marker as the
same label without it. CO-003 reads a total row four ways — the block since the
previous total, everything above it, all line items, the subtotals above it —
and only reports when no reading sums.

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
tieout check deck.pptx --client acme --accept BR-002@slide7 \
  --accept-note "the sponsor logo is contractually this colour"
```

Appends to `profiles/acme.suppress.yaml`. Accept the same rule three times and
TieOut says what it thinks is really happening:

```
BR-002 has been accepted 3 times. It is probably miscalibrated. Fold the evidence in with:
  tieout learn --add THAT_DECK.pptx --client acme
  accepted because: the sponsor logo is contractually this colour
```

`--accept-note` is optional but close to the point of the count. Whoever reaches
three acceptances is rarely whoever made them, and a bare count of three says
nothing about what to fold in. Notes from repeated acceptances accumulate rather
than overwrite — a second reason is evidence, not a correction of the first —
and a run without one is told what is missing.

**Scope an acceptance to a shape where you can.** `RULE@slideN` accepts the rule
for the whole slide, which is what it says and rarely what you mean: the next
turn of the deck can introduce a genuine defect of the same rule on the same
slide, and it will be filed as already accepted and never shown. Naming the
shape narrows it:

```bash
tieout check deck.pptx --client acme \
  --accept "LO-003@slide10:Quadrant dot 3" \
  --accept-note "plotted position, not a misalignment"
```

A shape-scoped acceptance stops matching if the shape is renamed, which errs
towards reporting — the safe direction for a check.

---

## Optional: semantic review

Everything above is deterministic and offline. This is neither, which is why it
is a separate package and a separate command.

### Why it exists at all

TieOut's own consistency rules compare labelled figures between tables
arithmetically: `CO-001` catches the same figure stated two ways, `CO-002` a
factor-of-a-thousand unit error, `CO-003` a total that does not sum. What they
cannot do is read. A headline claiming 20% growth over a table showing 8% is a
sentence, not a sum, and no amount of parsing gets there.

So the deterministic layer was built first, and deliberately. Every question it
can answer is one the model never needs to see, which is the cheapest possible
way to keep material out of a third party's hands.

### The bargain

The model may see the figures. It may not see whose they are.

```bash
tieout-review redact deck.pptx --client acme --forbid "Meridian Capital,Project Atlas"
```

`redact` needs no key, makes no request, and prints exactly what would be sent.
Run it first.

```console
$ tieout-review redact decks/reference_clean.pptx --client demo \
    --forbid "Ashcombe Partners,Project Meridian"

10 term(s) redacted across 12,262 characters from 26 slides.
redacted
 placeholder    kind       uses  original                            from
 ─────────────────────────────────────────────────────────────────────────────────────
 [CODENAME_1]   codename      1  Meridian                            detected:codename
 [COMPANY_1]    company       1  Ravensworth Group                   detected:company
 [TERM_1]       custom        1  Project Meridian                    blocklist
 [TERM_2]       custom       23  Ashcombe Partners                   blocklist
 [TERM_3]       custom       25  Strictly Private and Confidential   profile:brand.footer.boilerplate
 [TERM_4]       custom        1  Halloway                            profile:hygiene.dictionary
 ...

term list assembled from:
  blocklist                         2 custom term(s)
  profile:brand.footer.boilerplate  1 custom term(s)
  profile:client                    1 company term(s)
  profile:hygiene.dictionary        7 custom term(s)

could not be cleared (2)
 line  uses  text              why
 ──────────────────────────────────────────────────────────────────────────────
   98     1  Bodoni Sixtysix   a capitalised phrase containing a word that is
                               not ordinary English
  145     1  TBD Pre-tax       a capitalised phrase containing a word that is
                               not ordinary English

Each of these is a judgement only you can make. Add the ones that identify
someone to --forbid and run again; pass --yes once the list is one you are
content to send.
```

What went out of that 26-slide deck is the figures — `1,908`, `18.4%`,
`(42)` — and the argument around them with every name replaced. What a
contradiction then reads like on the way back:

> `[COMPANY_1]` margin is 15.6% on slide 4 and 15.8% on slide 12

which is a finding. Whose margin it is, the model never knew. The report you
read says "Ravensworth Group", because the placeholders are restored locally
after the answer arrives; the mapping is never transmitted.

### Where the terms come from

Four sources, in descending order of how much you control them.

| Source | What it contributes |
|---|---|
| `--forbid "a,b,c"` or `--forbid-file` | Whatever you type. Semicolons and newlines work as separators too, so a list pasted out of a spreadsheet lands correctly. |
| The client's profile | `client`, `hygiene.dictionary` (the proper nouns the learner already wrote down so `TY-009` would not flag them), `brand.footer.boilerplate` (a confidentiality line usually names the firm). |
| `docProps` | `creator`, `lastModifiedBy`, `Company`, `Manager`, `title`, `subject`, `keywords`, comment authors. `HY-004` reports two of those as a hygiene defect; here all of them are evidence. On the reference deck, `docProps/core.xml` carries the project codename in its title field — which no hygiene rule looks at. |
| The presets | Pattern detectors for what nobody can enumerate in advance: emails, phone numbers, URLs and bare hosts, local file paths, tickers with an exchange prefix, addresses and postcodes, company names carrying a legal or quasi-legal suffix, project codenames, and names adjacent to a role or an honorific. |

Two things the profile deliberately does **not** contribute.
`typography.canon_terms` records how to spell a term, not who it belongs to —
an early version pulled it in and removed "EBITDA" from the payload, which
deletes the one thing the model was shown the deck to reason about. And an
entry in `hygiene.dictionary` that is ordinary English ("Pre-tax", "run-rate")
is skipped for the same reason: it strips meaning for no privacy gain.

Detected names are also harvested as **short forms**, because the suffix is what
lets a detector recognise a name and the deck then uses the bare head
everywhere else. "Calderwood Holdings" on the cover makes "Calderwood" a term,
so the three table headers that use it bare are redacted too.

### Fail closed

The residual list is the control, and it is enforced structurally rather than by
convention. There is no function that takes a deck and returns findings, because
such a function would have to decide on its own that a payload was safe:

```python
prepared = prepare(deck, profile, forbidden=[...])   # offline, no key
prepared.plan.is_clear                               # or read the residuals
outcome = send(prepared.approve(), client)           # refuses otherwise
```

`send` refuses a payload with residuals outstanding, and then independently
re-verifies that every literal term it was given is absent from the outgoing
text. Two people would have to be wrong for a client name to leave the
building: whoever wrote the detectors, and whoever approved a list they had
read. A term surviving into the payload raises `RedactionFailed`, which is a bug
report rather than a user error, and nothing is sent.

Residuals are deliberately over-eager. Clearing one is a judgement, so it
persists: pass it to `--forbid` if it identifies someone, or to the profile's
allowlist if it does not.

### The questions it asks

Six, and the list is short on purpose. A model asked to "review the deck"
returns opinions, and opinions in a QA report train people to skim it.

| Rule | Severity | What it looks for |
|---|---|---|
| **SE-001** | major | A statement in prose contradicts the figures it describes |
| **SE-002** | minor | A quantified claim no figure in the deck supports |
| **SE-003** | major | Period or unit drift between two statements of the same measure |
| **SE-004** | minor | A footnote marker with no matching footnote, or the reverse |
| **SE-005** | minor | An enumeration that does not match its own count |
| **SE-006** | minor | A defined measure used inconsistently |

They arrive in a `semantic` category, and **a semantic finding never drives the
exit code.** A probabilistic finding gating a deterministic gate would make the
exit code mean something different from one run to the next, so adding this
layer to an existing pipeline cannot fail a build that used to pass. Ask for it
with `--fail-on-semantic` if you want it.

Every finding has to quote the text it is about and, where it is a
contradiction, the text it contradicts. One that quotes nothing, cites a slide
the deck does not have, or names a rule that does not exist is discarded — and
counted, so a prompt that needs fixing does not hide.

### Running it

```bash
export ANTHROPIC_API_KEY=...
tieout-review check deck.pptx --client acme --forbid "Meridian Capital" --format html --out report.html
```

`check` runs the full deterministic audit *and* the semantic pass and merges
them into one report, so there is one thing to read. The key is read from the
environment by the SDK, which means this code never sees it: it is not written
to a profile, a report or a cache, and the one object that can hold one
suppresses it from its own `repr`.

The payload for a 26-slide deck is about 12,000 characters, so it is one
request. Model: `claude-opus-5`, adaptive thinking, with the response shape
enforced as a JSON schema rather than only asked for in the prompt.

---

## Optional: the local UI

```bash
./run.sh            # macOS, Linux
.\run.ps1           # Windows
```

That is the whole thing. It finds a Python new enough, builds a virtual
environment, installs, and serves one page on `http://127.0.0.1:8765/` with a
browser open on it. Upload a deck, confirm what was derived from the client's
reference material, optionally turn on content review, and read the findings
slide by slide.

From nothing at all:

```bash
git clone https://github.com/mridulmalani2/tiedeck
cd tiedeck
git checkout claude/practical-volta-j1yct7      # until the PR is merged
./run.sh
```

Run it again any time. The first run sets up and takes a minute; every run
after it starts the server in about a second, and it reinstalls only when
`pyproject.toml` has changed. Flags pass straight through, so `./run.sh --port
9000` and `./run.sh --no-open` do what you would expect. `Ctrl-C` stops the
server and deletes every deck it was holding.

It changes nothing outside the checkout: everything lands in `.venv/`, and if
no usable Python is found it says so and exits without touching anything.

### Start to finish, on your own machine

Nothing here needs a key, a network or an account. `run.sh` does all of the
below; this is what it is doing, for anyone who would rather run it by hand or
is installing the development extra.

**First, check your Python.** This needs 3.11 or later, and the `python3` already
on a Mac is usually older — macOS ships 3.9 with Xcode's command line tools:

```bash
python3 --version
```

If that says 3.9 or 3.10, install a newer one (`brew install python@3.12` on
macOS, `apt install python3.12-venv` on Debian or Ubuntu, python.org on Windows)
and use it below in place of `python3`.

```bash
git clone https://github.com/mridulmalani2/tiedeck
cd tiedeck
git checkout claude/practical-volta-j1yct7      # until the PR is merged

python3 -m venv .venv
.venv/bin/python -m pip install ".[ui]"          # the UI extra; the core comes with it

.venv/bin/tieout-ui                              # then open http://127.0.0.1:8765/
```

Note there is no `-e` there. An editable install needs pip 21.3 or newer, and
the pip bundled with an older Python is 21.2 — which fails with *"editable mode
currently requires a setuptools-based build"*. A plain install works on every
pip that can read a `pyproject.toml`, and is what you want anyway unless you are
changing the code. If you are, upgrade pip first and then use `-e`:

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

On Windows the interpreter is `.venv\Scripts\python.exe` and the command
`.venv\Scripts\tieout-ui.exe`; everything else is identical.

`Ctrl-C` stops the server and deletes every deck it was holding. Useful flags:
`--port 9000` if 8765 is taken, `--no-open` to skip launching a browser.

**No deck to try it on?** The core CLI generates a set:

```bash
.venv/bin/tieout scaffold-reference --out decks
```

The two that matter are `decks/reference_clean.pptx`, a well-made deck to learn
a house style from, and `decks/reference_dirty.pptx`, the same deck with defects
seeded into it. Learn from the first in the UI, then open the second and check
it — you should get 47 findings. The rest are single-defect variants the test
suite uses, plus the YAML spec both decks are generated from.

**Want the slide images?** Install LibreOffice with its Impress filters
(`libreoffice-impress` on Debian or Ubuntu; the normal LibreOffice download on
macOS and Windows). Without it the rail and canvas show slide cards instead, and
no finding changes.

### It looks like PowerPoint on purpose

A ribbon of tabs, a slide rail down the left, the slide on a neutral canvas, and
a task pane on the right. That is not decoration. The people using this spend
their day in PowerPoint, and findings about a deck are easiest to act on in the
layout they already read decks in.

It is also why the findings are a **review note** rather than a wall of cards.
PowerPoint's own idiom for "someone has marked up your deck" is a comments task
pane, so that is where the note goes.

### Fixing what can be fixed

Each point in the note carries **Fix it** and **Set aside**, and the ribbon
carries **Undo** and **Export deck**. Applying a correction re-runs the audit and
says what changed — what it fixed, what is left, and anything it exposed that was
not reported before; undo steps back one correction at a time; export hands back
the deck with everything you accepted applied and nothing else changed.

**A fix is offered only where the change has exactly one right answer.** A colour
to the palette colour it is nearest, a typeface to the approved one, a variant
spelling to the canon term, a double space to one space, a document property to
nothing at all. Each is a substitution, and applying it cannot make the deck
worse.

**TieOut never decides where a shape goes, and that is a finding rather than a
policy.** Snapping the six shapes LO-003 reported on a real deck to their nearest
grid line was tried: it drove one text box into its neighbour — a `major` overlap
where there had been none — and turned a timetable column spaced evenly to the
point into one varying by five. The grid lines had been derived from that deck,
different shapes align to different ones, and pulling a group onto a line breaks
its relationship with everything around it. Which alignment matters is a
judgement about what the slide is for.

So there is no **Fix it** on LO-\*, the logo rules or BR-008, and no builder in
`tieout_fix` that could produce one. What there is instead is
[the position editor](#moving-a-shape-yourself): the judgement stays the
person's, and the tool supplies the precision.

**Nor is anything fixed where the answer is unknowable.** Two figures that
disagree, a total that does not sum, a placeholder that needs real words, a word
the dictionary does not know: the tool can see something is wrong and has no way
to know what is right. A value invented there would be wrong invisibly, inside a
file someone is about to send.

Nothing is edited in place. Each correction writes a new copy, which is what
makes undo a matter of putting a path back rather than of inverting an edit — and
an edit that cannot be inverted exactly cannot be undone honestly. The upload
itself is never written over. **Set aside** is held for the session only: deciding
to leave one deck's colour alone is not a decision about the client's house
style, and writing it to the profile would make it one. For that, `tieout check
--accept` writes a suppression.

### What each correction changed

A count that goes from 24 to 22 does not mean two things were fixed. It can mean
three were fixed and one was exposed — and the one that was exposed is the thing
worth knowing, because it is the only reason you would undo the correction.
So every correction and every undo reports three counts rather than a new total:

```
Recolour #B08D3F to the palette's #6B7280
  16 fixed     8 remain

Move Logo on slide 5 to 852.0, 24.0pt
  1 fixed      8 remain     2 new
  This correction exposed 2 findings that were not reported before:
    Slide 5 · LO-004 · major — Logo overlaps Footnote across 31% of the smaller shape
    Slide 5 · LO-002 · minor — Logo intrudes 4.0pt into the safe margin
  [ Undo this correction ]   Nothing else is changed by undoing it.
```

**Applying a correction can legitimately expose a finding that was masked**, and
that is not a defect in the correction. Moving a shape onto its grid line can put
it over its neighbour; that is precisely how automatic snapping damaged a real
deck. What would be a defect is letting it happen silently. So the new findings
are named rather than counted, linked to the slide they arrived on, tagged **new**
in the note where they are read, and the undo is offered beside them rather than
only on the ribbon.

Findings are matched between the two audits by the same identity the review note
groups on — the rule and its remedy, plus where it was found. Every rule states
its remedy in terms of the *expectation* rather than the measurement, so "Move
the logo to left 852pt, top 24pt" is the same sentence before and after the logo
moves: a finding keeps its identity while its measurement changes. An identity
built from the measurement would call every partial improvement one finding fixed
and a different finding arrived.

The counts always reconcile — `fixed + remaining` is the total before and
`remaining + new` the total after — and the whole log travels with the deck:
**Copy note** carries every correction applied and what each one cost, so the
person receiving the exported file reads the same account as the person who made
it. The ribbon keeps the running total: *3 corrections, 19 fixed, 2 exposed*.

Content review is deliberately outside this arithmetic. A semantic pass runs on an
explicit check and not on the re-audit after a correction, so counting its
findings would make a recolour appear to have fixed every one of them.

### Moving a shape yourself

Where a finding is about *where something sits* — `LO-001` through `LO-005`,
`LO-008`, `BR-002`, `BR-008` — the note offers **Move it** instead of **Fix it**,
and the outline already drawn over the shape becomes something you can pick up.

```
Off canvas · slide 21   X 36.0 │  Y 120.0 ─  (-864.0, +0.0pt)   ☑ Snap to grid
                                              [ Apply move ] [ Reset ] [ Done ]
        Drag, or use the arrow keys — 1pt, or 10pt with Shift.
```

- **Drag** it with the mouse, and the learned grid appears as guides. Come within
  the near-miss distance of a line and the shape is pulled onto it; the line
  lights up and the readout marks the axis that snapped. Turn **Snap to grid**
  off and the shape lands exactly where you left it.
- **Arrow keys** move by exactly one point, or ten with Shift, and never snap.
  A nudge that a magnet pulled straight back would be a nudge that did nothing,
  so the keyboard is the precise instrument of the two.
- **Apply move** writes it and re-audits, so the count moves as you work. **Undo**
  on the ribbon steps back through moves and corrections alike. `Esc` closes the
  editor without writing anything.

Two details worth knowing.

**The magnet reaches further than the grid's own tolerance, and has to.** LO-003
reports an edge that is *further* from a line than `layout.grid.tolerance_pt` —
that is what makes it off-grid rather than on it — and no further than
`layout.near_miss_alignment_pt.max`. A magnet the width of the tolerance could
therefore not reach a single shape the rule reports. The reach is the near-miss
window instead: the deck's own account of how far off a line a shape can be and
still be trying to sit on it. Wider than that would pull shapes onto lines they
are deliberately away from, which is the mistake auto-snapping made.

**A shape inside a group cannot be moved here**, and the editor says so rather
than quietly not offering. A grouped shape's offset is stored in its group's
coordinate space, which the group then translates and scales; writing a
slide-space number into it would move the shape somewhere nobody asked for.
Ungroup it in PowerPoint and TieOut will move it.

The coordinates written are the ones you produced — nothing is rounded toward a
rule, and the server consults no grid before writing them. That is the whole
reason this is allowed to write geometry when nothing else in TieOut is. It is
also why the arithmetic runs in whole EMU from the page to the file: a point is
12,700 of them, so ten nudges out and ten nudges back land on the offset they
started from exactly, rather than near it.

### The note answers three questions, in order

**Can I send it?** The note opens with a verdict — *Not ready to send*, *Fix
before sending*, *Ready to send* — because that is the question someone opens
this with, and "24 points to address" is a count rather than an answer.

**What do I actually have to do?** Findings are grouped by the fix behind them,
not listed one per slide. One off-palette gold used on four slides is one job;
read slide by slide it is four problems, and whoever corrects the theme colour
once then has to work out that the other three were the same thing. On a real
five-slide deck, 24 findings collapse to 8 things to do, worst first. **By
slide** is still there, because that is the order a deck actually gets corrected.

**Where is it, and what do I change it to?** Every finding carries a **fix** in
the imperative — *Recolour #B08D3F to the palette's #6B7280*, *Delete the
speaker notes before sending* — because the rule has usually already computed
the answer, and a Delta-E figure on its own leaves the reader to work it out
again. Where the fix is a judgement rather than a mechanical change — two
figures that disagree, a total that does not sum — it says what to weigh instead
of inventing an instruction. And clicking any finding outlines the shape it is
about on the slide, which is the difference between "Shape 16" and a box you can
see.

### What it is, and is not

It is a front end over the same functions the commands call — `learn`,
`run_rules`, `prepare`, `send` — and it computes nothing of its own. That is the
only way a second interface stays honest: a UI with its own idea of what a
finding is would be a second tool to keep true. The profile it writes is the
same `profiles/NAME.yaml` the CLI writes, for the same reason.

It is not a service. It binds a loopback address and **refuses anything else**:

```console
$ tieout-ui --host 0.0.0.0
error '0.0.0.0' is not a loopback address. This server has no authentication
beyond a session token and holds live deck material; it will only bind an
address reachable from this machine.
```

Enforced rather than defaulted, because "we default to localhost" is a weaker
promise than "it will not bind anything else". Every API call carries a session
token generated at startup, so another tab — or any other process that can
reach 127.0.0.1 — cannot drive an audit or read your deck. The token is
substituted into the document rather than passed in a link, and the page clears
it out of the address bar on load so it does not sit in browser history.

The page is one file with no external references at all: no CDN, no font
service, nothing fetched when it renders. The `Content-Security-Policy` the
server sends is `default-src 'none'` with `connect-src 'self'`, which enforces
that rather than asserting it — the same promise the HTML report makes, and for
the same reason. An uploaded deck lives in a temporary directory and is deleted
when the server stops.

### The six steps

1. **Deck.** Drag a `.pptx` in. Nothing is uploaded anywhere; the file is copied
   into a temporary directory on your machine. Deriving a house style from it
   starts immediately, in the background, so step 3 has an answer in it by the
   time you get there.
2. **House style.** One choice, not two panels: **Use existing profile** audits
   against a client already onboarded, **Create profile** names and keeps the
   style derived from this deck. Only the one you pick opens.
3. **Confirm.** Every derived fact, with the evidence for it — the palette as
   swatches, the size band per text role, the logo box per slide type, the safe
   margins, the grid, the typographic conventions — plus any questions the
   evidence did not settle. Untick anything that is not really a house rule, and
   correct anything that is. **Save changes** is lit only when something has
   changed, and tells you what it wrote and where.
4. **Content review** (optional, off by default). A key field and a forbidden
   words box, then **Show me what would be sent**: the redaction table, the
   residual list, and the payload verbatim. Nothing is sent until you have read
   the residuals and said so.
5. **Run.**
6. **Results.** A verdict, then the work, with **Fix it** on everything TieOut
   can correct exactly, **Move it** where the answer is a position rather than a
   substitution, and **Export deck** when you are done. Each correction reports
   what it fixed, what is left and what it exposed — see
   [What each correction changed](#what-each-correction-changed). **Copy note** puts the same thing on
   the clipboard as plain text, and the self-contained HTML report is the one
   `tieout check --format html` produces.

### Editing: dropping, and correcting

Step 3 lets you remove a derived fact, and lets you correct one.

**Dropping** is the safe edit. The rule that read the field stops running, and
`not_learned` records that a person decided so, with the field locked so a later
`learn --add` cannot quietly put it back. It is offered only where removing means
something — a list empties, an optional setting clears, a tolerance returns to its
default — and a required field with no default shows a dot instead of a checkbox.

**Correcting** is the other one, and it carries a risk the first does not. A
margin measured at 28.4pt is 30pt because someone decided it should be, and an
analyst who can see the measurement wants to round it. So each fact that a form
can carry gets one control per member — a margin set is four numbers, not one
string — prefilled with what is there now, validated against the schema before
anything lands, recorded in the provenance as *set by hand* rather than measured,
and locked. What cannot be typed into gets no control at all rather than a dead
one.

The risk is stated next to the controls rather than designed away: **a value the
client's own approved deck does not support will report their material as wrong**,
and everything downstream treats the profile as ground truth. That is a judgement
to put in front of the person making it. Refusing the edit does not remove the
judgement — it moves it into a text editor, where nothing validates the value and
nothing records that a person chose it.

A bad value is refused on its own. One mistyped number out of twelve does not
throw away the other eleven; the save reports exactly which one it was and why.

### Slide images

Thumbnails come from LibreOffice in headless mode, converted once to PDF and
rasterised with pdfium. It is entirely optional: a machine without LibreOffice
shows slide cards and a sentence saying why, and nothing about the audit
changes. A core-only LibreOffice install (`libreoffice-core` with no
`libreoffice-impress`) has no PowerPoint filter and fails the same way, which
the message says explicitly because the error LibreOffice itself gives —
"source file could not be loaded" — helps nobody.

Rendering happens on a background thread, so the upload returns immediately and
images appear when they are ready.

**Stated plainly: the LibreOffice conversion is not exercised by the test suite
or by CI**, because it is a subprocess call to an optional large dependency that
neither environment has. What is tested is every way it can fail, the pdfium
rasterisation half against a real PDF, and that the page degrades to cards. If
you are relying on the thumbnails, check them once on your own machine.

### The key

Accepted in the form, held for the one request that uses it, and dropped with
it. There is no field for it on any session object, it is never echoed in a
response, never written under the session directory, and the page keeps no
`localStorage`, `sessionStorage` or cookie of any kind. `tests/test_ui_server.py`
asserts each of those separately.

---

## Deploying it

There is no server to run and no service to operate. TieOut is a command-line
tool that reads a file and writes a report, so deployment means getting a wheel
onto the machines that need it and deciding where profiles live.

### Build a wheel

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
# dist/tieout-0.1.0-py3-none-any.whl
# dist/tieout-0.1.0.tar.gz
```

The wheel carries the three files that are not code and that the tool does not
work without: the HTML report template, the compressed wordlist TY-009 spells
against, and the UI's page. Nothing is fetched at runtime.

### One analyst, one laptop

```bash
python3 -m venv ~/.tieout
~/.tieout/bin/pip install tieout-0.1.0-py3-none-any.whl
~/.tieout/bin/tieout --help
```

Then, once, per client:

```bash
~/.tieout/bin/tieout learn approved_deck.pptx --client acme
```

and from then on, before every send:

```bash
~/.tieout/bin/tieout check draft.pptx --client acme
```

Put `~/.tieout/bin` on `PATH` and it is just `tieout`.

### An air-gapped desk

The case the whole design is pointed at, and the reason the core has nine local
dependencies rather than thirty. On a machine with network access:

```bash
pip download -d vendor dist/tieout-0.1.0-py3-none-any.whl
```

That collects the wheel and everything it needs — 21 wheels, about 18MB. Copy
the directory across, then on the isolated machine:

```bash
python3 -m venv ~/.tieout
~/.tieout/bin/pip install --no-index --find-links vendor tieout
```

`--no-index` is the point: pip is forbidden from reaching out, and the install
succeeds anyway. Nothing afterwards opens a socket either.

For the UI as well, put the extra on the wheel path — 30 wheels instead of 21:

```bash
pip download -d vendor 'dist/tieout-0.1.0-py3-none-any.whl[ui]'
```

Leave `[review]` off an air-gapped desk; the thing it does is make a network
call.

**Download on the same platform you install on.** `pip download` resolves
wheels for the machine it runs on, and several dependencies are compiled:
`lxml`, `Pillow`, `pydantic-core` and `pypdfium2` all arrive as
`manylinux_…_x86_64` or `macosx_…` builds. Vendoring on a Mac for a Linux desk
produces a directory that installs on neither. Either run `pip download` on a
machine matching the target, or pass `--platform`, `--python-version`,
`--implementation cp` and `--only-binary :all:` to name the target explicitly.

### A team

Profiles are the only durable state, and they are text. Two arrangements work:

**A shared drive.** Point everyone at one directory:

```bash
export TIEOUT_PROFILE_DIR=/Volumes/deals/tieout/profiles
```

Simple, and the profile a colleague learned is immediately yours. The risk is
that a profile is a document with authority over other people's work, and a
shared directory has no history — someone widens a tolerance to silence a
finding and nobody knows.

**Version control.** Keep `profiles/` in a small repository and have people pull
it. A profile diff is legible precisely because every value carries its `why:`
comment, so "widened the palette tolerance from 3.0 to 9.0" arrives with the
evidence it contradicts. This is the arrangement to prefer if more than two or
three people share a client.

Either way, one person should own a client's profile. `tieout profile lock`
exists for the fields that are decisions rather than measurements:

```bash
tieout profile lock --client acme --field brand.logo.per_archetype.content
```

A locked field survives `tieout learn --add`, so a later deck cannot quietly
overwrite a judgement someone made.

### A pre-send gate, or CI

`check` is built to be scripted. Exit `0` means nothing at or above the
threshold, `1` means findings, `2` means the run itself failed — and a gate that
cannot tell the last two apart will eventually wave a bad deck through.

```bash
tieout check draft.pptx --client acme --fail-on major --quiet || exit 1
```

For a pipeline, `--format json` gives a stable additive schema with `unchecked`
and `rules_skipped` at the top level, so a dashboard can show what was *not*
measured as well as what failed:

```bash
tieout check draft.pptx --client acme --format json --out findings.json
```

The JSON goes to stdout when `--out` is omitted, and nothing else does.

### Things pip cannot install

Two prerequisites are outside Python, and both are optional:

- **Metric-compatible fonts** for LO-006. The overflow rule measures against the
  real font file and records a shape as `unchecked` when it cannot resolve one —
  so without the fonts installed the rule passes by declining to measure. It
  ships off by default for this reason. On Linux, `fonts-liberation`.
- **LibreOffice** for slide thumbnails in the UI, and specifically the Impress
  filters: `libreoffice-core` alone has no PowerPoint filter and fails. On
  Debian or Ubuntu, `libreoffice-impress`. Without it the UI shows slide cards
  and says why, and no finding changes.

### The two optional commands

Each is a separate extra, and running one without its extra says so rather than
producing a traceback:

```console
$ tieout-ui
error the local UI needs its extra: fastapi is missing.
         Install it with pip install 'tieout[ui]'. The tieout and tieout-review
         commands are unaffected.
```

`tieout-review redact` is the exception — it works with the core install alone,
because inspecting what *would* be sent should not require installing the thing
that sends it.

### Deployment mistakes worth naming

- **Do not expose the UI.** It refuses to bind a non-loopback address, so this
  takes deliberate effort — a reverse proxy, an SSH tunnel someone forgets. The
  session token is not authentication for a network service, and the page holds
  a live deck.
- **Do not put an API key anywhere near the core.** `tieout` needs none, and CI
  running the core should assert that none is present; this repository's own
  workflow does exactly that, and fails if a key appears.
- **Do not deploy a suppression file as a way of going quiet.** Accepting the
  same finding three times makes TieOut say what it thinks is really happening,
  and that message is the useful output. A `*.suppress.yaml` with forty entries
  means the profile is wrong, not that the deck is fine.
- **Do not learn from the deck you are about to check.** It will report nothing,
  which is not the same as there being nothing wrong. Learn from material the
  client has already approved.

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
                       [--accept RULE@slideN[:Shape name]] [--accept-note TEXT]
                       [--fail-on blocker] [--quiet]

tieout rules [--client NAME]
tieout profile show --client NAME
tieout profile lock --client NAME --field brand.logo.per_archetype.content
tieout scaffold-reference --out DIR [--spec PATH] [--clean-only]
```

The optional semantic layer, installed separately and a separate command:

```
tieout-review redact DECK [--client NAME] [--profile PATH]
                          [--forbid "a,b,c"] [--forbid-file PATH]
                          [--include-notes] [--show-payload]

tieout-review check  DECK [--client NAME] [--profile PATH]
                          [--forbid "a,b,c"] [--forbid-file PATH] [--yes]
                          [--include-notes] [--model NAME]
                          [--format table|json|html] [--out PATH]
                          [--fail-on SEVERITY] [--fail-on-semantic] [--quiet]
```

`redact` exits `1` when something could not be cleared, so it scripts as a
pre-flight check. `check` refuses to send in that case unless `--yes` is passed,
and exits `2` rather than proceeding. Its diagnostic summary goes to stderr, so
`--format json` on stdout stays parseable.

Speaker notes are excluded from the payload unless `--include-notes` is passed.
They are where the price and the walk-away number live.

The local UI, also installed separately:

```
tieout-ui [--host 127.0.0.1] [--port 8765] [--open/--no-open]
```

`--host` accepts loopback addresses only and exits `2` on anything else.

**Exit codes.** `0` nothing at or above `--fail-on`; `1` findings at or above it;
`2` the run itself failed. A gate that cannot distinguish the last two will
eventually wave a bad deck through.

**A rule that crashes exits `2`, not `0`.** A rule that raises has examined
nothing, so an audit containing one does not cover what it claims to. It is
reported as a failed run rather than as findings, and the deck is named as not
fully checked. This used to print in red and exit zero, which is the one
combination a pre-send gate cannot survive: the deck ships because the checker
broke rather than because the deck was clean.

**Profiles** live in `./profiles/NAME.yaml`. Set `TIEOUT_PROFILE_DIR` to keep
them on a shared drive.

**Reports.** `table` for a terminal, grouped by slide then severity because that
is the order someone fixes a deck in. `json` for a pipeline, with a stable
additive schema and `unchecked`/`rules_skipped` at the top level. `html` for
everyone else: one self-contained file, no CDN, no external fonts, nothing
fetched at view time, with the provenance of every finding shown inline.

---

### Three fields worth knowing about

**`typography.canon_accepted`** holds the *other* spellings the reference deck
uses for a canonical term, and they are accepted rather than reported. A deck
sets the same term in caps in an eyebrow, in title case on the agenda and in
sentence case in prose, and all three are the house style precisely because the
approved deck contains them. This is evidence, not a blanket exemption for
capitalisation: a form the reference deck never carried is still reported, so
"Ashcombe partners" on a deck whose reference only ever wrote "Ashcombe
Partners" is caught. `canon_terms` wins where a form appears in both, which is
how the loser of an answered terminology question stays reportable.

**`hygiene.document_metadata_allowed`** holds the identifying `docProps` values
the reference deck carries. They belong to the client whose approved deck it is,
so HY-004 treats them as authorship rather than as a leak, and reports only
other names — a named individual, a counterparty, a codename. Without it the
rule blocks on the client's own name in the client's own deck, which tells you
only that TieOut cannot tell whose deck it is looking at.

**Chart series colours are read as drawn.** A series fill that every data point
overrides paints nothing: a doughnut whose three segments are set to navy, gold
and pale blue is not drawn in the series' own colour at all. BR-011 measures the
fill only where some point still takes it, counted against the point count the
series declares, so a chart recolouring three of five slices is still measured
on the other two.

**`brand.palette_tolerance_delta_e`** is derived from the distance *between*
palette entries, and then raised if it has to be to admit the widest cluster's
own members. Clustering gathers colours within 3 Delta-E of each other, so a
cluster can be wider than the tolerance measured against its representative, and
the rim of every cluster then fails a check against the palette it is part of.
The provenance says when admission is what raised it.

Opacity is not among them, because opacity is not a colour. A brand navy at 12%,
28%, 60% and 75% — concentric rings, a tint band, a highlight panel — is one
colour decision and four opacity decisions. TieOut records the colour as chosen
and carries the opacity beside it, rather than compositing each against the page
and reporting four colours that appear nowhere in the file.

---

## Known limitations

Stated plainly, because a QA tool that overstates its coverage is worse than one
that admits its edges.

**No semantic or argument-level review in the core tool.** `tieout` itself is
the mechanical layer only. There is an
[optional semantic layer](#optional-semantic-review), separately installed, that
does read — but it reads a redacted payload, its findings never drive the exit
code, and it has no view on whether the valuation is defensible either.

TieOut will tell you the logo is 18pt out of place, the EBITDA column mixes
decimal places, and that a figure stated twice disagrees with itself — but the
last of those is arithmetic, not comprehension. It has no view on whether the
valuation is defensible, whether the chart supports the headline above it, or
whether the story works. Nothing here substitutes for reading the deck.

**The semantic layer cannot redact a name made of ordinary English words.**
"Northern Trust", "General Electric" and "Meridian" are invisible to every rule
in `tieout_review.patterns` unless they carry a legal suffix, a role, an
honorific or a codename marker — which is why `--forbid` exists, why the
residual list is over-eager, and why nothing is sent until a person has read it.
Where a detected name reduces to an ordinary word, the bare form is reported as
a residual rather than either leaked or blanket-substituted. The failure mode to
be honest about is a one-word invented name that is also a dictionary word,
appearing only in places no detector fires: the blocklist is the only thing that
catches it.

**Merging a name's spellings can merge two companies.** The redactor gives
"Meridian Capital Partners LLP", "Meridian Capital" and "Meridian" one
placeholder, so the model can see that a margin quoted under one is the same
company as a margin quoted under another. The rule is narrow — whole-word
prefix, same kind — and it can still be wrong: "First Capital" and "First
Capital Partners of Texas LLC" need not be related. That error is visible (you
restore the real names and see two companies); the opposite error, splitting one
company across three placeholders, is a silent miss, and a miss is worse for a
tool whose job is to find things.

**Consistency findings compare labels, and two tables can mean different things
by one label.** CO-001 and CO-002 key a figure on its (row label, column header)
pair, which is usually enough to pin the scope — but a group table and a segment
table that both say "Revenue" under "FY24A" hold different, correct numbers, and
TieOut will report the pair. Generic labels are excluded and footnote markers
normalised to cut this down; it remains the category's false-positive mode, and
the reason its rules are `major` rather than `blocker`. CO-003 is narrower: it
only reports a total row when none of four plausible readings of "the rows above"
sums, and only when at least three addends are involved.

**Errors in the reference deck are learned as rules.** The whole design treats
the reference deck as ground truth, so a mistake that is *consistent* in it
becomes the expectation for every future deck. A logo 4pt out of place on all 26
slides is learned as the correct position. TieOut mitigates this where it can —
one-off colours are discarded rather than admitted to the palette, outliers in a
dominant value are queued as a question, and `learn` ends by
[reviewing the reference deck against its own profile](#the-reference-deck-review)
— but it cannot detect a systematic error. Learn from a deck you are confident
in, read the emitted profile, and correct and `lock` anything wrong.

**A shape whose position carries a number is recognised, but only by its
shape.** A dot on a competitive quadrant, a bar on a football field and a marker
on a timeline sit where their value puts them, and snapping one to a grid line
would move a competitor or restate a valuation. Nothing in the file says which
shapes are plotting, so TieOut infers it from two signatures — *bars*, of one
thickness and varying length with no shared starting edge, and *points*, of one
size and irregular on both axes — and exempts those from the geometry rules.

The inference can be wrong in both directions. A hand-drawn diagram of three
same-sized boxes placed freely reads as a scatter and stops being checked; a
bar chart of two series does not reach the three-member floor and keeps being
checked. Where it is wrong,
`tieout check --accept LO-003@slide10 --accept-note "quadrant dots"` records the
judgement with the reason for it.

**The saturation limit is a judgement, and it is fitted to one deck.** 25% of an
axis, measured at near-miss width, is where a grid was decided to have stopped
telling an aligned shape from a stray one. It separated the two axes of the deck
it came from cleanly — 19% horizontally against 56% vertically — but that is one
deck, and a house style with a genuinely dense horizontal rhythm could be
refused a grid it really has. The failure is visible rather than silent: the
axis lands in `not_learned` with the coverage it measured, so a profile that
should have a grid and does not says so on the way past. The escalating
slide-share requirement is the deliberately cheaper half of the design — it
tightens whichever axis is over-derived rather than assuming rows always are —
but a second real deck is the only thing that will show whether 25% is the right
place to draw the line.

A route that was measured and rejected, so nobody spends a day on it: deriving a
*pitch* — a baseline rhythm, the way a typographic grid actually works — rather
than positions. On the reference deck the modal row gap was 3.6pt and a 3.6pt
pitch explained only 34% of the rows; 7.2pt explained 15%. There was no vertical
rhythm in that deck to find.

**A single reference deck is thin evidence for deck-wide conventions.** Rules
scoped to an archetype with two or three slides, or deck-wide keys with few
observations, fall below the support thresholds and land in `not_learned` rather
than being derived. That is the correct behaviour, but it means one deck gives
you a partial ruleset. `tieout learn --add` with further approved decks is what
promotes dominant rules to invariant and fills the gaps.

**Text is measured where its typeface is available, and bounded where it is
not.** `tieout.model.fonts` resolves a run's face, or a metric-compatible
substitute — Carlito for Calibri, Caladea for Cambria, Liberation Sans for
Arial — and measures the real advance. Where nothing honest stands in, an upper
bound of 1.15 em per character is used instead, which can only ever fail to
narrow a box and so cannot hide a real defect. Since Calibri and Cambria cannot
be installed on a build machine for licensing reasons, **the bound is what most
deployments actually use**, and it is looser than a measurement: on text-heavy
decks LO-001, LO-002 and LO-004 will narrow less than they could. Installing
`fonts-crosextra-carlito` and `fonts-crosextra-caladea` is the cheapest way to
sharpen them.

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

**SmartArt is inspected for text only.** A `dgm` graphic frame's labels live in
a separate diagram part, and TieOut reads them, so a draft marker, a
non-canonical term or a misspelling inside SmartArt is reported like any other
text — which matters, because a process or structure slide is often made
entirely of SmartArt. Its *internal* geometry and colours are still not
modelled: they are produced by an algorithm in the diagram's layout part rather
than positioned by the author, so no layout or brand rule looks inside one, and
claiming to measure those boxes would be confident nonsense. The frame's own box
participates in canvas and margin checks, as it always did.

**Chart internals are partially inspected.** Chart text — series names, category
labels, titles, axis titles — is read and checked for typefaces, placeholder
markers and terminology. Chart series colours are *checked* by BR-011 but never
*learned*: they are not collected into the palette, because TieOut does not model
chart geometry and so has no area to weight them by. BR-011 measures only a
series with an explicit fill of its own — a series taking the theme's chart
colour cycle was coloured by the template rather than by the author, and
reporting a colour nobody in the deck chose would be confident nonsense.
Gridlines, plot area and data-label backgrounds are not measured. A house style
whose charts legitimately use a wider data palette than its slide palette should
add those colours to `brand.palette_hex` or disable BR-011.

**No slide rendering in the HTML report.** The UI renders thumbnails where
LibreOffice is installed, but the standalone report does not: nothing in it is
verified visually and no finding can be confirmed by eye inside it. The report
reserves a fixed-aspect slot per slide and carries a `data-bbox` attribute on
each finding, so images and overlays can be added later without template
changes.

**A hand-set value is no longer evidence.** Correcting a derived fact in the UI
replaces a measurement with a decision. TieOut records which is which — the
provenance says *set by hand*, and the field is locked against re-learning — but
it cannot tell you that the number you typed is wrong, and a rule reading it
reports the client's own approved deck as wrong if it does not support it. The
audit is only ever as good as the profile behind it.

**Auto-fix is deliberately partial.** The UI will apply a correction where the
change is a substitution with exactly one right answer — see
[Fixing what can be fixed](#fixing-what-can-be-fixed). It will not choose where a
shape goes, and it will not guess at anything the tool can see is wrong without
knowing what is right. Those stay judgements for a person: the first has
[an editor](#moving-a-shape-yourself) that supplies the precision and takes the
coordinates from your own mouse, and the rest stay instructions. The CLI applies
nothing at all.

**The position editor moves a shape and nothing else.** It does not resize,
rotate, reorder or regroup, and it cannot move a shape inside a group. Moving a
shape is also not checked against anything before it is written: put a shape
somewhere that overlaps its neighbour and the next audit reports the overlap,
which is the tool doing its job rather than refusing yours.

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
.venv/bin/python -m pytest              # 1,235 tests
.venv/bin/python -m pytest --cov=tieout --cov=tieout_review --cov=tieout_ui  # floor 85%
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
  the 39 rules individually against the clean deck so a regression names the
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
rules/      base + brand, layout, typography, hygiene, consistency
report/     console, json_out, html
fixtures/   spec + generator
```

The optional layer is a second top-level package, which is the whole of how the
air-gap guarantee survives its existence:

```
tieout_review/
  patterns.py  regex detectors for identifying text; no dependencies
  redact.py    the boundary: harvest, replace, verify, residuals, restore
  terms.py     assembling the term list from blocklist, profile and docProps
  extract.py   deck -> the smallest payload that can answer the question
  review.py    prepare -> hold -> send -> parse; the fail-closed sequence
  client.py    the only module in this repository that imports an HTTP client
  cli.py       tieout-review
```

`tests/test_review_airgap.py` asserts that shape rather than trusting it: no
module in `tieout` may import `tieout_review`, no module in `tieout_review`
except `client.py` may import anything network-capable, `redact.py` may import
nothing but the standard library and `tieout`, and importing either package must
not load the SDK.

The UI is a third:

```
tieout_fix/
  the corrections, and the refusals: a substitution with one right answer is
  applied, judgement is never guessed at, and the only geometry it writes is a
  position a person supplied with their own mouse

tieout_ui/
  render.py    LibreOffice -> PDF -> PNG, degrading to nothing on every failure
  session.py   uploaded decks, in memory and in a temp dir; never an API key
  view.py      profile and audit -> JSON, as a pure function of the model
  server.py    the routes, the token, the headers
  static/      one HTML file with no external references
  cli.py       tieout-ui, refusing any non-loopback bind
```

It may import a web framework, because serving a loopback socket is what one is
for; it may not import the SDK, and reaches the model only through
`tieout_review`'s single transport. That too is asserted rather than assumed.

`tieout_fix` is the fourth, and separate for the same reason: the core promises
never to write to a deck, and a module inside it that did would make that promise
a matter of reading the code rather than of its shape. Nothing in `tieout`
imports it.

Rules read the resolved model and nothing else. If a rule needs something the
model does not expose, the model gets extended — without that boundary every rule
reimplements inheritance slightly differently and the tool quietly disagrees with
itself.

### Deviations from the specification

Recorded for review rather than buried:

1. **46 rules, not 27.** Section 9's header and section 14 both say 27, but the
   catalogue itself lists 36 (brand 10, layout 8, typography 9, hygiene 9). The
   catalogue is treated as authoritative and all 36 are implemented, seeded and
   tested. A fifth category, **consistency** (3 rules), is then added beyond the
   specification: the deck's own tables are enough to catch a figure that
   disagrees with itself, which is the defect a banker most wants caught and the
   one no amount of formatting discipline surfaces.
2. **An optional semantic layer the specification does not mention.** Section 2
   forbids LLM calls and network access, and `tieout` still honours that
   absolutely. `tieout_review` is additive: a separate package, a separate
   install, a separate command, and a forbidden import as far as the air-gap
   walk is concerned. The deterministic consistency rules were built first
   specifically so that the questions a model gets asked are only the ones
   arithmetic cannot answer.
3. **A local web UI the specification does not mention.** Same shape as the
   deviation above: a separate package, a separate install, a separate command,
   and a forbidden import as far as the air-gap walk is concerned. It computes
   nothing of its own and writes the same profile the CLI writes.
4. **Five modules not in section 4's file list**: `model/color.py` (Delta-E is
   required throughout and needs a home), `model/furniture.py` (logo, footer and
   text-role identification, shared by the learner and the rules so the two
   cannot disagree), `cluster.py` and `text.py` (shared primitives the rules must
   reach without importing the learning engine), and `fixtures/spec.py` (the
   declarative specification the generator builds from, which section 11 requires
   as a file).
5. **A ceiling on the palette tolerance.** Section 8.3 derives it as half the
   minimum inter-cluster distance, floored at 2.0. With a well-separated
   four-colour palette that yields ~16 Delta-E, wide enough for a visibly wrong
   colour to pass, so a ceiling of 6.0 is applied and recorded in the profile's
   provenance.
6. **LO-003 fires only against a learned grid line.** A literal "any two edges
   0.5–4pt apart" reading produces dozens of false positives per deck. Section 8.3
   resolves it — "a shape 3pt off a real grid line is a defect while a shape 3pt
   off a one-off edge is not" — and that is what is implemented.
7. **Provenance is written twice**, as `why:` comments and as a generated
   `provenance` block. The single-copy design was built first and abandoned: YAML
   attaches a leading comment to the preceding sibling key, so recovering notes
   from comments mis-assigned them across section boundaries and a finding about
   the grid cited the margin derivation.
8. **Hygiene questions are conditional on evidence.** Section 8.4 lists four
   settings to ask about with a default; asked unconditionally they fire on every
   deck, and section 14's requirement that a clean reference deck produce zero
   questions would be unachievable. They are asked only where the deck shows
   contrary evidence.
9. **`p:defaultTextStyle` added to the inheritance chain** between the master's
   text styles and the theme. It is genuinely part of PowerPoint's resolution —
   a plain text box inherits from it — and omitting it resolved every text box to
   the theme default size.
10. **Margins are clamped to the tightest observed edge.** The 5th percentile
   exists to tolerate shapes outside the content frame, but every shape it
   tolerates is one LO-002 would then report on the deck the margin was learned
   from. The clamp makes the profile unable to fail its own reference deck.
11. **`rules.enabled` added to the profile.** Without an opt-in list a rule with
   `default_enabled = False` could never be switched on at all.
12. **`Rule.run(deck, profile)` keeps the specified signature**, with furniture
    reached through a memoised helper and `unchecked` collected from the rule
    instance, rather than changing the signature to pass a context object.

---

## Licence

Proprietary. All fixture content is invented; nothing in this repository is
derived from any real filing, bank or client material.
