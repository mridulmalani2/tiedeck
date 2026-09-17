# TieOut handoff

Written 2026-09-17, at the close of the session that merged
[#1](https://github.com/mridulmalani2/tiedeck/pull/1) (`cfb6a27` on `main`),
and revised the same day when the grid work in §2 was implemented on this
branch. It exists so a fresh chat can pick the work up without re-deriving what
was already established. Read it top to bottom once; after that, treat §7 as
the queue.

**What changed since the first draft.** §2's grid problem is no longer open: the
saturation guard and the escalating support requirement are implemented, tested
and documented, on this branch. §5's `--accept-note` gap is closed. What is
*not* done is the only thing that would prove either: neither has been run
against a real deck, because the deck is gone with the container (§6). Both
sections below say so where it matters.

---

## 1. Where things stand

`main` is `cfb6a27`. It carries the whole tool plus one round of correctness
work driven by a real deck. This branch (`claude/practical-volta-j1yct7`,
[#2](https://github.com/mridulmalani2/tiedeck/pull/2)) sits on top of it and is
still open — extend it, or branch from `main` once it merges. The previous
working branch from #1 is merged and must not be extended.

```
git fetch origin main && git checkout -B <new-branch> origin/main
.venv/bin/python -m pytest -q        # 1346 tests
.venv/bin/ruff check . && .venv/bin/mypy .
```

The CI gate is ruff **and** mypy **and** pytest with coverage. Running only
ruff and pytest has already put a red build on the remote once; run all three
before pushing.

### What the merged work changed

The session started from a paradox worth restating, because it is the thing
that shapes the whole design: the user built a house-style profile *from* a
deck, checked that same deck *against* that profile, and got 168 findings.

The root cause: `learn` derived **central tendencies**, and `check` flagged
**anything not equal to the centre**. The reference deck's own internal
spread therefore became findings against itself. The tool was measuring its
own shadow.

The invariant that falls out of this, and which every future change should be
tested against, is:

> `check(D, learn(D))` must be empty, or every remaining finding must be
> something a human would agree is a real defect in `D`.

Against the user's deck the count went **168 → 37** from pipeline fixes
alone, and **37 → 4** once the genuine deck defects were corrected. All four
remaining are `info`, and all four are the deliberate decorative bleeds on
the title cover.

The substantive fixes, so you don't re-solve them:

| Area | What was wrong | Where it lives now |
| --- | --- | --- |
| Text overflow | Measured the text *frame*, not the ink. PowerPoint frames are routinely wider than their glyphs. | `tieout/model/extent.py` — provable upper bounds (`MAX_ADVANCE_EM = 1.15`, `MAX_LINE_EM = 1.5`) narrow a frame only where the ink is provably inside. |
| Font metrics | No real metrics at all. | `tieout/model/fonts.py`, with metric-compatible substitution (Carlito↔Calibri, Caladea↔Cambria, Liberation Sans↔Arial). |
| Decorative bleed | Every shape crossing a canvas edge was a blocker. | `is_decorative_bleed()` in `extent.py`; LO-001 downgrades to `info`. |
| Opacity read as colour | `a:alpha` was composited into the hex, turning one brand colour into N off-palette colours. | `tieout/model/inherit.py` — alpha is now carried separately on `ResolvedFill`/`ResolvedLine`. |
| Chart series colour | A series fill that every `c:dPt` overrides draws nothing, but was still audited. | `_every_point_overrides()` in `tieout/model/loader.py`. |
| Data marks | Bars and plotted points were flagged as misaligned shapes. Where a shape sits *is the reading*. | `_data_series_axes()` in `tieout/rules/layout.py`. |
| Silent rule crashes | A rule that raised was filed as a *skip*, so a crashed catalogue read as "0 findings from 37 rules". | `RuleSkipped.failed` in `tieout/rules/base.py`. A crash can no longer read as a pass. |
| Reference blindness | Nothing told you what the profile failed to capture. | `review_reference()` in `tieout/learn/__init__.py` — `learn` now runs the full catalogue against the deck it learned from and **reports** the gap. It never absorbs it. |

That last one is the structural answer to the paradox. It is deliberately a
report and not a suppression: absorbing the gap would make the tool agree
with itself by construction, which is exactly the failure mode it exists to
catch.

---

## 2. The grid — diagnosed, then fixed

This was the user's named concern ("a lot of glitches ... especially related to
grid positions"), and the evidence said they were right. **The fix is now in.**
The diagnosis is kept below because the numbers are not reproducible from this
repository alone and would otherwise be lost.

### The numbers, against `Project_Falcon_Halyard_Clean.pptx`

* The learned grid had **27 columns and 53 rows** on a 960 × 540pt canvas.
* Grid tolerance is 2.0pt; the near-miss window is 0.5–4.0pt.
* **34 of the 52 row gaps were under 8pt** — under twice the near-miss
  maximum. Actual gaps included 2.16, 2.88, 3.6, 4.32.
* Canvas coverage: **37% of the vertical canvas was inside on-grid tolerance
  of some row, and 56% was inside the near-miss window.** Horizontally the
  same figures were 11% and 19%.
* **All 6 remaining LO-003 findings were on the y axis.** Not one was
  horizontal.

53 rows on 540pt is not a grid. It is a transcript of every y-coordinate the
deck happens to use. When more than half the vertical canvas is inside the
near-miss window of *something*, "this edge just misses a grid line" carries
almost no information — a shape placed at random would trip it.

The mechanical cause was that **rows and columns shared one derivation
threshold** (`GRID_MIN_SUPPORT = 5`, `GRID_MIN_SLIDES = 2`,
`GRID_TOLERANCE_PT = 2.0`, all in `tieout/learn/derive_layout.py`). But decks
are not symmetric: a deck has a handful of real columns that repeat slide after
slide, and a great many distinct vertical positions, because vertical placement
follows content length rather than a template. One threshold cannot serve both.

### What was implemented

Routes 2 and 1 of the four set out in the first draft, composed — the
combination the draft argued for.

**Route 2, the saturation guard, is the trigger.** `coverage_share()` in
`tieout/cluster.py` measures the share of an axis lying within near-miss
distance of some learned line, merging overlapping bands and clipping to the
canvas. `GRID_SATURATION_LIMIT = 0.25` is where an axis is judged to have
stopped distinguishing an aligned shape from a stray one.

**Route 1, a stricter threshold, is the remedy — but derived, not fitted.**
Rather than hand-pick a stricter constant for rows, `_unsaturated()` raises the
requirement to recur *across slides* one step at a time
(`GRID_SLIDE_SHARE_STEPS`, 0% → 80%) until the axis comes back under the limit.
This matters for the `n=1` problem in §4: the asymmetry is discovered per deck
rather than assumed, so a deck whose rows are a genuine grid is not tightened at
all, and a deck whose *columns* are the over-derived axis gets the same
treatment without anyone editing a constant.

**An axis that never gets under the limit is not emitted**, and `learn` says so
in `not_learned`, naming the rule that will not run:

```
layout.grid.rows_pt: 24 horizontal edge clusters have the support to be grid
lines, but they saturate the canvas: ... no horizontal grid is emitted and
LO-003 will not run on the y axis
```

Reported rather than absorbed, for the same reason as `review_reference` in §1.

**Merging inherits the test.** Each deck's grid is under the limit by
construction, but the union of two need not be, so `_merge_layout` declines to
widen an axis past the limit and records why. That was the one place the
property could come back.

Route 4 (derive a pitch instead of positions) was measured and rejected before
any of this: the modal row gap was 3.6pt, a 3.6pt pitch explained 34% of the
rows and 7.2pt explained 15%. There is no vertical rhythm in that deck to
derive. Recorded in the README so nobody spends a day on it. Route 3 (drop
deck-wide rows entirely) was not needed once the guard could drop them per deck.

### What is *not* proven, and how to prove it

The acceptance test the first draft named — re-run `check(D, learn(D))` on the
real deck and confirm the six vertical LO-003 findings go without silencing
genuine misalignment — **has not been run, because the deck is gone** (§6).
What exists instead is `tests/test_grid_saturation.py`, 15 tests that reproduce
the failure mode synthetically:

* a deck whose row axis saturates has no row grid emitted, says why, and keeps
  its columns;
* the same deck, with shapes placed a few points off a row nobody meant to
  exist, produces spurious LO-003 findings with the guard lifted and none with
  it — the invariant from §1, in miniature;
* **a shape dragged 3pt off a true column is still reported** — the guard the
  first draft asked for alongside the change;
* a deck whose rows are a real grid is not tightened, and one whose rows are
  partly real keeps the rows every slide uses and loses the ones a single pair
  of slides uses.

**First thing to do when the deck is re-attached**: run `learn` then `check`
against it and compare with the numbers above. Expect the row axis to be
dropped and the six LO-003 findings to go. If it is *not* dropped, the 25%
limit is in the wrong place and that is the number to move — not the mechanism.

## 3. Two deck defects, unfixed

These are defects in `Project_Falcon_Halyard_Clean.pptx` itself, not in the
tool. Both were found, diagnosed, and left alone when the session was
redirected.

* **Slide 9** — the ring stroke runs through the "$8.6bn SAM" label. I was
  mid-inspection of the geometry when redirected; the fix is presumably a
  small radial nudge of the label or a gap in the stroke, but I did not
  confirm which reads better. Verify visually before and after.
* **Slide 12** — two logo lockups on the section divider. The obvious fix is
  to remove the chrome one, but that risks tripping BR-010 (boilerplate),
  which expects the chrome lockup present. Check what BR-010 actually asserts
  before removing either.

Render with LibreOffice to check visually (`libreoffice-impress` +
`poppler-utils` — both need installing in a fresh container):

```
soffice --headless --convert-to pdf --outdir <dir> <deck.pptx>
pdftoppm -png -r 80 <dir>/<deck>.pdf <dir>/slide
```

---

## 4. Standing limitations — flag these, don't quietly inherit them

Each is a real shortcoming of the merged work, not a nitpick.

* **Ink measurement is a bound, not a measurement, on most machines.** Where
  Carlito or Caladea is installed, text is measured with real metrics. Where
  it is not — which is most deployments — `extent.py` falls back to the
  `1.15em` advance and `1.5em` line bounds. Those bounds are provable, so
  they never produce a false positive, but they are loose, so they *will*
  miss real overflow. The README documents the two regimes; keep it honest if
  you change either.
* **Data-mark inference can be wrong in both directions.** `_data_series_axes`
  decides that a shape's position encodes a value from support ≥ 3 and a
  thickness tolerance of 0.5pt. That tolerance was tightened from 2.0pt
  precisely because the deck's own chrome (a 24pt rule, a 24pt eyebrow, a
  22pt wordmark) clustered as a false "series". A deck with three genuinely
  misaligned same-size shapes will be read as a chart and excused; a chart
  with varying bar thicknesses will be audited as misplacement. There is no
  test deck for either case.
* **One real deck.** `tests/test_real_world_constructions.py` adds a
  six-slide synthetic deck, but every tuned constant in this codebase was
  tuned against a single real deck from a single house style. Treat the
  constants as fitted to `n=1` until a second real deck has been through.
* **`GRID_SATURATION_LIMIT` is the newest of those constants and the least
  tested.** 25% of an axis at near-miss width separated the reference deck's
  two axes cleanly (19% horizontal against 56% vertical), but that separation
  is the only evidence for the number, and it has not been re-measured against
  the deck since the guard was written (§2). A house style with a genuinely
  dense horizontal rhythm could be refused a grid it really has. The failure is
  visible rather than silent — the axis lands in `not_learned` with its
  measured coverage — but visible is not the same as correct. The escalating
  slide-share requirement beside it is the more defensible half: it discovers
  which axis is over-derived instead of assuming rows always are.
* **`canon_accepted` inherits the reference deck's inconsistencies.** TY-005
  learns accepted spellings from the reference. If that deck spells a term
  two ways, both become canon and the rule stops catching either.
* **The learn/check asymmetry is mitigated, not solved.** `review_reference`
  reports the gap; it does not close it. Anyone reading `learn` output must
  actually read that section, or the paradox returns in a quieter form.

---

## 5. Housekeeping — done

* **`--accept` could not record a note.** `Suppression.note` existed in the
  schema with no CLI path able to write it, so the field was dead. It is now
  wired up: `tieout check --accept RULE@slideN --accept-note "why"`. Notes from
  repeated acceptances accumulate rather than overwrite — a second reason is
  evidence, not a correction of the first — and `_suggest_relearn` prints the
  reason alongside the count, or says the reason is missing when there is none.
  That was the whole point: three acceptances means the *rule* is miscalibrated
  rather than the deck, and whoever reads that is rarely whoever made them.
* Suppression paths are already `profiles/<client>.suppress.yaml` in-repo
  (`suppression_path`, asserted by `tests/test_cli.py`). An earlier note
  suggested renaming `falcon.suppressions.yaml`; that was a scratch file from
  the session, not repo content. **Nothing to rename.**

---

## 6. Gone with the container

The previous session's scratch directory held `Falcon_Clean.pptx`,
`Falcon_v3.pptx` (the corrected deck), `Falcon_autofix.pptx`, the derived
profiles (`o2.yaml`, `dm.yaml`), the deck-repair scripts (`fix_deck.py`,
`fix_geometry.py`, `run_fix.py`) and the rendered PDFs. **None of it was
committed and all of it is lost when the container is reclaimed.**

To continue the deck work, ask the user to re-attach
`Project_Falcon_Halyard_Clean.pptx`. The corrected v3 deck will have to be
rebuilt; the edits it contained are described in the merged commits, but the
file itself is not recoverable.

Do not commit the deck to the repo — it is client material.

---

## 7. Suggested order of work

Everything left needs the same thing first, and it is not code.

1. **Get `Project_Falcon_Halyard_Clean.pptx` re-attached.** Without it, (2),
   (3) and (4) below cannot be started, and nothing more can honestly be
   claimed about the grid.
2. **Validate the grid fix against it** (§2, "What is *not* proven"). Expect
   the row axis to be dropped and the six vertical LO-003 findings to go. If
   the axis survives, move `GRID_SATURATION_LIMIT`, not the mechanism.
3. **Close slides 9 and 12** (§3).
4. **A second real deck** (§4), which is the only thing that will show whether
   any of the fitted constants generalise — the 25% saturation limit now among
   them.

Nothing in this list is blocked on a decision. (2) and (4) are blocked on
material; (3) is blocked on both material and a visual judgement that needs a
human to look at a render.
