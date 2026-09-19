# TieOut handoff

Written 2026-09-17, at the close of the session that merged
[#3](https://github.com/mridulmalani2/tiedeck/pull/3). It exists so a fresh chat
can pick the work up without re-deriving what was already established.

Read §1 and §2 once. After that, §6 is the queue and §3 is how to test.

---

## 1. Where things stand

`main` carries the whole tool, the correctness work from #1, the grid guard from
#2, and the precision work from #3. Start any new branch from `main`.

```
git fetch origin main && git checkout -B <new-branch> origin/main
.venv/bin/ruff check . && .venv/bin/mypy . && .venv/bin/python -m pytest
```

**1,394 tests.** The CI gate is ruff **and** mypy **and** pytest with coverage
(85% floor; it sits around 93%). Run all three before pushing — running only two
has put a red build on the remote before.

Note `pyproject.toml` already passes `-q` to pytest. Adding another `-q` on the
command line suppresses the summary line, which makes a green run look like a
run that printed nothing.

### The real deck has now been through

`Project_Falcon_Halyard_Clean_v2.pptx` — 20 slides, the client's own house
style — was run through `learn` and then `check` on 2026-09-17, which is the
first time anything here met real material. Three results worth keeping:

* **The invariant holds, and as of #5 it holds completely.** `check(D, learn(D))`
  reports **nothing at all**, from 41 rules across 20 slides. The four findings
  the first run produced were all false flags, and §4b says what each one was.
* **The grid guard from #2 works on real material.** That deck previously
  derived 27 columns and **53 rows**, and every remaining LO-003 finding sat on
  the y axis. It now derives 27 columns and **4 rows**, and the spurious
  vertical findings are gone. That was the open question #2 shipped with, and
  it is now answered on the deck that raised it.
* **The tool declines to learn a date format** from this deck, because it
  genuinely mixes `Sep 2026` and `September 2026` with neither dominant. TY-008
  is therefore skipped rather than guessing — correct behaviour, and a real
  inconsistency in the deck worth telling its author about.

### What #3 changed, in one paragraph

Thirteen defects, each reproduced against a purpose-built deck *before* it was
fixed and each now covered by a test that fails on the previous code. Seven were
ways the tool reported nothing about a deck nobody had checked; three were false
flags; two were found by a new rendering oracle; one was a crash. Alongside them:
the layout model is now checked against a real renderer, a finding's confidence
is computed from its evidence rather than declared per rule, and the report opens
with what to fix first.

---

## 2. The rendering oracle — read this before touching the layout model

`tests/test_render_oracle.py` is the most important thing added in #3, and the
thing most likely to be misunderstood by a future session.

It converts both reference decks with LibreOffice, extracts every word's bounding
box from the PDF with `pdftotext -bbox`, and asserts three things:

1. one PDF page per **visible** slide (LibreOffice omits hidden slides);
2. every scored word lands inside the ink rectangle `extent.py` predicted for its
   text frame;
3. every word's rendered height is explained by its resolved font size — the
   ratio is 1.163 to three decimal places across 1,235 words on the clean deck.

**Why it matters.** The suite had 1,365 tests and 92% coverage over `extent.py`
and could not have found either defect the oracle found on its first run, because
the fixture in `tieout/fixtures/generator.py` is built from the same assumptions
as the code. Its titles are left-aligned, so nothing noticed that the loader
spelled `centre` while the placer checked `center` and every centred paragraph
was therefore placed at the left margin. Coverage counts lines executed, not
inputs explored.

**What it is not.** The renderer is LibreOffice, not PowerPoint — a second
independent implementation of the same specification, not ground truth. One
divergence is known and excluded by name: LibreOffice centres a `wrap="none"`
text body on import where PowerPoint honours its `algn`. Table and chart text is
unscored, because no text frame contains it.

**It is skipped wherever `soffice` or `pdftotext` is missing, which includes CI.**
To run it you need both:

```
apt-get install -y --no-install-recommends libreoffice-impress poppler-utils
```

A core-only LibreOffice (`libreoffice-core` with no `libreoffice-impress`) has no
PowerPoint filter and reports only "source file could not be loaded".

---

## 3. How to test it against your own deck

```
git fetch origin main && git checkout main && git pull

# Onboard: derive a house style from one deck.
.venv/bin/python -m tieout.cli learn YOUR_REFERENCE_DECK.pptx --client acme

# The invariant: the deck you learned from should come back clean.
.venv/bin/python -m tieout.cli check YOUR_REFERENCE_DECK.pptx --client acme

# Check a later turn of the deck against that house style.
.venv/bin/python -m tieout.cli check A_LATER_DRAFT.pptx --client acme
```

`.venv/bin/tieout` is the same thing if the package is installed
(`pip install -e .`).

Useful flags while testing:

| Flag | What it does |
| --- | --- |
| `--format html --out report.html` | one self-contained file, nothing fetched at view time |
| `--format json --out report.json` | stable additive schema, `unchecked` and `rules_skipped` at the top level |
| `--severity major` | report only major and worse |
| `--rules CO-*` | run one family. Naming a rule *exactly* also opts into a default-off rule |
| `--gate-confidence low` | restore the pre-#3 exit-code behaviour (see below) |
| `--quiet` | the summary line only |

**Exit codes:** `0` nothing at or above `--fail-on`; `1` findings at or above it;
`2` the run itself failed — which now includes **a rule that crashed**. An audit
that did not cover the deck no longer exits 0.

### The defect deck

A companion deck was built from the clean one with **one unique seeded defect
per slide**, twenty in all, spanning every rule category. All twenty were
verified to fire on their own slide and nowhere else. It is the fastest way to
see the whole catalogue working, and the fastest way to notice a regression
that silences a rule.

It is not in the repository — it is client material — and the script that
builds it was a scratch file, gone with the container. Ask the repository owner
for `falcon_seeded.pptx` and the `seed_defects.py` that produced it; the table
below is what it seeds, one rule per slide, so it can be rebuilt from scratch
if both are lost.

| Slide | Rule | Seeded defect |
| --- | --- | --- |
| 1 | HY-004 | author and company left in `docProps` |
| 2 | HY-001 | a `TBD` draft marker left in the text |
| 3 | HY-002 | speaker notes left on the slide |
| 4 | BR-005 | Comic Sans MS against an allowed set of Calibri/Cambria |
| 5 | TY-001 | a curly apostrophe against a straight-quote convention |
| 6 | BR-004 | a `#E81123` fill, far off the learned palette |
| 7 | LO-004 | a text shape dragged on top of another |
| 8 | CH-003 | the bar chart's value axis starting at 40, not zero |
| 9 | LO-007 | 52pt body text against a learned 9–38pt band |
| 10 | LO-003 | a shape nudged 3pt off its learned grid column |
| 11 | CO-003 | the `Peer Median` row relabelled `Total`, stating 160.8 over rows summing to 779.2 |
| 12 | LO-001 | a text-bearing shape pushed off the right edge |
| 13 | CH-001 | the chart title and the slide headline both removed |
| 14 | TY-006 | `86` among one-decimal figures in the same column |
| 15 | BR-006 | the page number moved 60pt out of its learned box |
| 16 | TY-004 | a lower-case title against a title-case convention |
| 17 | TY-005 | `Cagr` where the canon is `CAGR` |
| 18 | TY-007 | `USD 1.35bn` against a learned `$` pattern |
| 19 | BR-007 | page number 7 between 18 and 20 |
| 20 | CO-001 | `EBITDA`/`FY26E` as 23.8 here and 37.4 on slide 14 |

On that deck the tool reports 26 findings — 4 blocker, 12 major, 7 minor, 3
info — and exits 1. The three `info` and one of the `minor`s are the clean
deck's own pre-existing bleeds, not seeded.

The sharpest single result is slide 12, where the *same rule* reports the
seeded text shape as a `blocker` and the pre-existing graphic as `info`: the
decorative-bleed logic telling intent from error on real material.

Two things that seeding taught, both worth repeating for anyone who builds a
similar fixture:

* **Pick the page number by its learned box, not by "the first digit-only
  shape".** Slide 19's timeline markers are `01`–`05` and come first in
  document order, so a text-based selector silently edited a timeline marker
  and left the page number alone — a seeded defect that corrupted something
  else and then did not fire.
* **A form the reference deck uses is not a defect.** `HALYARD` is in this
  deck's `canon_accepted` because the clean deck sets the name in caps in its
  eyebrows, so seeding `HALYARD` as a TY-005 variant tested nothing. The rule
  was right; the seed was wrong.

### What to look at first

The thing to judge is **the invariant**: `check(D, learn(D))` should be empty, or
every finding should be something you would agree is a real defect in `D`. That
is the whole design. If the reference deck comes back with findings you disagree
with, that is the bug report worth bringing back — ideally with the rule id, the
slide, and why you disagree.

Second, read the **`not checked`** block at the foot of the report. It lists what
the tool could not evaluate and why. A rule that fell silent because it could not
tell is the failure mode this session spent most of its effort on, and the block
is where you can see whether that is working.

---

## 4. What #3 actually fixed

Kept in detail because each is a class of error, not a one-off.

### Missed errors — a wrong deck ships

| Defect | Reproduction |
| --- | --- |
| A units error excused every contradiction under the same label. `_explained_by_scale` ran against the group's min and max and vetoed the whole group. | 263 / 275 / 263000 under one label → CO-001 reported **0** |
| Only the first disagreement was reported. | 263 / 275 / 290 → **1** finding; the 290 never mentioned |
| Two tables on one slide were never compared. The guard excluding repetition *within* a table was keyed on the slide. | two tables, one slide, 263 vs 275 → **0** |
| One footnoted figure disabled its whole column's arithmetic. `5 (a)` failed to parse and CO-003 abandoned the column. | 10+20+30+5 = 65 against a stated 99 → **0** |
| The ink bound was not an upper bound. Line count divided total advance by line width and rounded up; wrapping does not pack that tightly. | five words at half the line width: box 45pt where the text needs 75 — **40% short**. 3,304 such combinations in a small search |
| …and still was not, for anything bulleted, or with a right margin, or in columns. | a one-inch `marL`: box 30pt where the text needs 45 |
| One acceptance blinded a rule for a whole slide, permanently. `Suppression.shape_name` was declared and never read. | a genuine new defect on an accepted slide → filed as already accepted |
| A crashed rule exited 0. The console said so in red; the gate reads the exit code. | `--fail-on blocker` with a crashed rule → **exit 0** |

### False flags

| Defect | Reproduction |
| --- | --- |
| Correctly-rounded tables reported as not summing. The tolerance took the column's largest precision and applied it to every addend — the tightest of the per-figure bounds rather than their sum. | 10.04 + 20.4 + 30.4 against a stated 60.8 (the correct 1dp total of 60.84) → **flagged** |
| TY-008 was a coin flip on any US deck. `%d/%m/%Y` and `%m/%d/%Y` are indistinguishable when the day is ≤ 12, and each date resolved on its own. | days mostly ≤12 → format unlearned, **rule silently never ran**. Days mostly >12 → `03/04/2026` flagged **on the deck it learned from** |
| A quote after a figure was eaten with the figure. | `2023 "guidance"` → straight = **1**, should be 2 |

### Found by the oracle

| Defect | Effect |
| --- | --- |
| The loader spelled `centre`; `extent.py` checked `center`. | every centred paragraph placed at the left margin. A section title measured at x=36 while it renders at x≈480. The existing test for this *also* spelled `center` |
| `bodyPr` was read from the slide alone. Anchor, insets, wrap and autofit inherit slide → layout → master exactly as fonts do. | a title the master anchors mid-frame was modelled at the top, 15pt above where it renders |

### The crash

pypdfium2 wraps PDFium, which is not thread-safe, and each uploaded deck renders
on its own thread. **A lock was tried and was not enough**: pypdfium2 objects
carry finalizers, and CPython runs finalizers on whichever thread triggers
collection, so a second thread reached into PDFium regardless. Reproduced 6 of 6
under both `fork_exec` and `posix_spawn`. Never in CI, which has no LibreOffice
and never reaches the rasteriser; only on machines that have thumbnails at all.

`python -m tieout_ui.rasterise` now owns PDFium for the length of one PDF and
exits — the same arrangement LibreOffice already had. The UI test module went
from crashing two runs in three to passing three in three.

---

## 4a. What the real deck showed that the synthetic ones could not

Two findings from the first real-deck run. Neither is urgent; both change what
a future session should believe about coverage.

### The consistency rules are dormant on this deck

CO-001, CO-002 and CO-003 ran and were correctly silent, because there was
nothing for them to check:

* **no figure appears under the same (row label, column header) in two
  tables** — the deck's four tables share no keys at all, so CO-001 and CO-002
  have nothing to compare;
* **there is no `Total` row anywhere in the deck**, so CO-003 has no assertion
  to test.

This matters more than it sounds. These are the rules a banker actually relies
on — the ones that catch a margin quoted two ways — and they are the rules #3
spent the most effort fixing. On this deck they are inert, so a clean run says
nothing about whether they work. The defect deck (§3) exercises all three; the
real one does not.

**What to do about it:** ask for a deck that does restate figures across tables
— a financial summary plus a valuation page, say — before believing the
consistency category is proven on real material.

### Slide 12 is classified as `content`, not `section_divider`

**Fixed in #5, but not by the cause named here.** This section said the cause
was that "a one-character shape wins the title slot", and proposed reading a
single glyph inside a lockup as a monogram. That diagnosis was wrong, and acting
on it would have patched these two slides while leaving the defect live.

`title_shape` picked the right 29pt headline on 18 of the 20 slides. The `H` did
not beat the headline: the headline was never a candidate. The fallback searched
the top 30% of the canvas, and a title slide and a divider set their headline
down the page by design — 34% and 45% here — so the only text in the band was
the lockup's own. See §4b.

The lesson is worth more than the fix: the first explanation that accounts for
the symptom on the slide in front of you is not necessarily the cause. Check it
against the slides that *work* before acting on it.

---

## 4b. What #5 fixed, and what it cost

The client deck was run through the UI and came back with four findings. Every
one was a false flag, and they had two causes between them.

**The logo was invisible to the tool.** `detect_furniture` identified a logo by
the SHA1 of its image part, and this deck's mark is vector — a hexagon with the
monogram set on it and the wordmark beside it. `image_sha1_slide_support` is
empty, so `logo_shapes()` returned nothing on all twenty slides. Consequences:

* the badge entered `content_shapes`, so **the learned grid was partly a
  description of the logo** — columns at 808.78 and 827.5, rows at 28.8 and
  47.52 are the mark's own edges;
* on the divider, whose lockup is a different size, LO-003 then asked for the
  house mark to be nudged onto a grid the house mark had set;
* **BR-001, BR-002 and BR-003 never ran**, so nothing checked the logo's
  position or size on any slide.

A drawn shape that encloses a piece of chrome text and is of its order of size
is the plate that text is set on. That makes the badge chrome, and it makes a
vector mark identifiable: the plate seeds the lockup and the wordmark joins by
proximity.

**Overhangs the rule had already judged invisible were reported anyway.** Three
findings were LO-001 and LO-002 at `info` with the remedy "Nothing to do unless
this was not intended", on the deck that defines the house style. The UI renders
that as "3 things to do", two of which are things not to do. The ink case is now
silent on a proof; the bleed case is silent where `layout.decorative_bleed_slides`
records that the reference deck bled too.

Three further defects surfaced while fixing those:

* LO-002 restated LO-001 whenever a shape left the canvas — one misplacement,
  two findings, in two different numbers.
* `title_shape` searched the top 30% of the canvas, which is where a *content*
  slide puts its headline. See §4a.
* `_derive_logo_boxes` classifies each edge on its own, so a slide setting the
  mark twice can produce a box describing no placement the deck actually uses.
  Reachable before #5 by any deck with two logo images on a slide.

**Where things stand.** The clean deck reports nothing, from 41 rules. The defect
deck reports 20 findings for its 20 seeded defects and nothing else — it was 26.
A copy of the clean deck with the lockup dragged 40pt on one slide and scaled
25% on another is now reported; neither was visible before.

**What it cost.** With slide 12 correctly classified as `section_divider`, the
deck has one divider, which is not enough evidence to learn a safe margin or a
logo box for that archetype. LO-002 and BR-001/002/003 on slide 12 are now
reported as *not checked*. They were previously measured against the wrong
archetype's expectations and happened to pass. Read the `not checked` block.

---

## 5. Two structural changes worth knowing about

**Confidence is computed, not declared.** A rule used to declare one confidence
for every finding it made. That is wrong for the geometric rules, whose findings
inherit the layout model's error: text measured in its own typeface is a
measurement, text bounded at 1.15 em per character is a rectangle the ink is
somewhere inside, and two bounded boxes overlapping may be no overlap of ink at
all. `InkExtent.measured` and `ink_confidence()` say which; LO-002 and LO-004
weaken each finding by it, alongside the profile's derivation confidence.

**The gate reads it.** `--gate-confidence` (default `medium`) is the floor for
the exit code. **This is a behaviour change**: a `low`-confidence finding is
still reported but no longer fails the gate. `--gate-confidence low` restores the
old behaviour. Severity says how bad a finding is if true; confidence says how
likely it is to be true; a gate that reads only the first fails a deck on a
heuristic as readily as on arithmetic, and the person who is failed learns to
distrust both.

---

## 6. Open work, in the order I would take it

### 1. Run the *oracle* against the real deck

Half done. `learn` and `check` have been run against
`Project_Falcon_Halyard_Clean_v2.pptx` (§1) and the invariant holds. What has
**not** been run against real material is `tests/test_render_oracle.py` — every
word-position measurement so far is against the two synthetic fixtures, whose
layout the generator and the model agree about by construction.

Point it at the real deck by hand for one run: change the `rendered` fixture to
load that file instead of `build_clean`/`build_dirty`, on a machine with
LibreOffice and poppler installed (§2). Expect escapes, and read them before
assuming they are model defects — the clean deck's own `wrap="none"` footnotes
are already a known LibreOffice divergence, and a real deck will have more of
that kind.

This is the measurement that turns the tolerances in `extent.py` from "fitted
to synthetic decks" into something with an error distribution behind it.

### 2. Audit the hygiene and brand rule *logic*

`inherit.py`'s font chain and the colour transforms were checked in #3 and hold
up — the transforms land within ΔE 1.24 of Office's five documented Accent 1
variants. What was **not** audited is the rule logic that consumes them: most of
`tieout/rules/brand.py` and `tieout/rules/hygiene.py`, and most of
`tieout/rules/layout.py` beyond LO-002 and LO-004. Given the hit rate in
`consistency.py` and `extent.py`, assume defects of the same classes are there.

The method that worked: reproduce against a purpose-built deck first, fix second,
and write the test so it fails on the previous code. Check that last part — twice
in this session a test passed for the wrong reason.

### 3. Finish the silent-drop conversion

`chart.py` had five rules and no `unchecked` entries at all; two of its silences
turned out to be "could not tell" rather than "nothing to report". The same
census across `hygiene.py` (9 bare returns, 4 unchecked) and `layout.py` (9 bare
returns, 7 unchecked) has not been done. For a pre-send check, "I could not
verify this" and "this is fine" must never look the same.

### 4. ~~Fix the archetype classifier's monogram blindness~~ — done in #5

Done, though not as described. See §4b.

### 5. Get a deck whose tables restate figures

See §4a. The consistency rules are inert on the deck available today. Until a
deck with cross-table figures and a total row has been through, treat CO-001,
CO-002 and CO-003 as tested only against seeded material.

### 6. Model `lnSpcReduction`

The autofit line-spacing reduction is the one text-layout property still
unmodelled. Ignoring it makes the bound *loose* on a shrunk-to-fit shape, never
short — so it costs precision rather than soundness, which is why it waited.
`paragraph_available_width` and `_paragraph_line_height_bound` in `extent.py` are
where it goes.

### 7. A second real deck

Every tuned constant in this codebase was fitted to one real deck from one house
style. Treat them as `n=1` until a second has been through.

---

## 7. Standing limitations — flag these, do not quietly inherit them

* **Ink measurement is a bound, not a measurement, on most machines.** Where
  Carlito or Caladea is installed, text is measured with real metrics; where it
  is not — which is most deployments — `extent.py` falls back to the 1.15 em
  advance and 1.5 em line bounds. Those bounds are provable, so they never
  produce a false positive, but they are loose, so they *will* miss real
  overflow. This is now visible rather than silent: such findings carry `medium`
  confidence.
* **CO-003's tolerance is the correct rounding bound and therefore wider than
  before.** A total wrong by less than the sum of its addends' half-units will
  not be reported. That is the arithmetic, not a choice.
* **TY-008 has a deliberate miss.** A date genuinely written the other way round
  in an otherwise consistent deck now reads as the house convention. Nothing in
  the file distinguishes it from a correct one.
* **A shape-scoped acceptance stops matching when the shape is renamed**, which
  reports a previously-accepted finding again. That errs towards reporting — the
  safe direction — but it looks like a regression to anyone who renames shapes
  between turns.
* **Data-mark inference can be wrong in both directions.** `_data_series_axes`
  decides a shape's position encodes a value from support ≥ 3 and a thickness
  tolerance of 0.5pt. There is no test deck for either failure.
* **`canon_accepted` inherits the reference deck's inconsistencies.** TY-005
  learns accepted spellings from the reference; if that deck spells a term two
  ways, both become canon.
* **A logo drawn as text alone is not found.** #5 identifies a vector mark by the
  badge behind its monogram, and the wordmark joins that group by proximity. A
  house mark set as type with no badge and no image is therefore not learned, so
  BR-002 and BR-003 do not check its position or size. Deliberate: nothing
  separates such a mark from any other line the deck repeats, and the derivation
  records it in `not_learned` rather than guessing.
* **A bleed that is house style hides a misplaced background graphic.** Where the
  reference deck bleeds, LO-001 and LO-002 stop reporting any shape that is
  structurally a deliberate bleed — no text, behind the content, partly on the
  canvas. A genuinely misplaced *text-free* graphic behind the content is
  therefore not reported. Anything carrying text, or in front of the content, or
  wholly off the canvas, still blocks.
* **The consistency category is unproven on real material.** See §4a.
* **The learn/check asymmetry is mitigated, not solved.** `review_reference`
  reports the gap; it does not close it.

---

## 8. Gone with the container

Scratch decks, renders and probe scripts from this session were not committed and
are lost when the container is reclaimed. Nothing in the repository depends on
them: every defect above has a test that rebuilds its own deck.

Do not commit a client deck to the repository. CI asserts that no `.pptx` is
tracked, for exactly this reason.
