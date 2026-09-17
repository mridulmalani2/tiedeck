# TieOut handoff

Written 2026-09-17, at the close of the session that merged
[#1](https://github.com/mridulmalani2/tiedeck/pull/1) (`cfb6a27` on `main`).
It exists so a fresh chat can pick the work up without re-deriving what was
already established. Read it top to bottom once; after that, treat the
**Open work** section as the queue.

---

## 1. Where things stand

`main` is `cfb6a27`. It carries the whole tool plus one round of correctness
work driven by a real deck. Start any new branch from `main` — the previous
working branch is merged and must not be extended.

```
git fetch origin main && git checkout -B <new-branch> origin/main
.venv/bin/python -m pytest -q        # 1327 tests
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

## 2. The grid — the main open problem

This is the user's named concern ("a lot of glitches ... especially related
to grid positions"), and the evidence says they are right. Here are the
numbers, gathered against `Project_Falcon_Halyard_Clean.pptx`:

* The learned grid has **27 columns and 53 rows** on a 960 × 540pt canvas.
* Grid tolerance is 2.0pt; the near-miss window is 0.5–4.0pt.
* **34 of the 52 row gaps are under 8pt** — under twice the near-miss
  maximum. Actual gaps include 2.16, 2.88, 3.6, 4.32.
* Canvas coverage: **37% of the vertical canvas is inside on-grid tolerance
  of some row, and 56% is inside the near-miss window.** Horizontally the
  same figures are 11% and 19%.
* **All 6 remaining LO-003 findings are on the y axis.** Not one is
  horizontal.

### The diagnosis

53 rows on 540pt is not a grid. It is a transcript of every y-coordinate the
deck happens to use. When more than half the vertical canvas is inside the
near-miss window of *something*, "this edge just misses a grid line" carries
almost no information — a shape placed at random would trip it.

The mechanical cause is that **rows and columns share one derivation
threshold** (`GRID_MIN_SUPPORT = 5`, `GRID_MIN_SLIDES = 2`,
`GRID_TOLERANCE_PT = 2.0`, all in `tieout/learn/derive_layout.py`). But decks
are not symmetric: a deck has a handful of real columns that repeat slide
after slide, and a great many distinct vertical positions, because vertical
placement follows content length rather than a template. One threshold cannot
serve both.

I also tested whether a baseline rhythm exists that the derivation is simply
failing to find. It does not. The modal row gap is 3.6pt (10 occurrences),
and a 3.6pt pitch explains only **34% of the rows**; 7.2pt explains 15%.
There is no clean vertical pitch in this deck to derive.

### Suggested direction (not yet implemented, not yet agreed)

Treat it as a **design question to settle with the user before coding**, since
several routes are defensible:

1. **Separate the derivations.** Give rows their own, much stricter support
   threshold — or require a row to recur across a larger share of slides
   than a column does. Cheapest change; keeps the concept.
2. **Add a saturation guard.** Refuse to emit a grid axis whose learned lines
   cover more than some share of the canvas (say 25% at near-miss width), and
   have `learn` say so out loud: *"no vertical grid could be derived; LO-003
   will not run on the y axis."* A rule that cannot be derived honestly
   should skip, not guess. This composes with (1) and is the option I would
   argue for.
3. **Drop deck-wide rows entirely** and keep only the per-slide local
   alignment logic (`_local_alignments` in `tieout/rules/layout.py`), which
   already handles "these eight shapes share an edge the deck grid never saw".
4. **Derive a pitch instead of positions** — a baseline rhythm, the way a
   typographic grid actually works. Principled, but this deck has no such
   rhythm, so it would derive nothing here. Worth knowing before investing.

Whatever is chosen, the acceptance test is the invariant in §1: re-run
`check(D, learn(D))` and confirm the six vertical LO-003 findings go away
*without* silencing genuine misalignment. A synthetic deck with one shape
deliberately dragged 3pt off a true column is the guard to write alongside it.

---

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
* **`canon_accepted` inherits the reference deck's inconsistencies.** TY-005
  learns accepted spellings from the reference. If that deck spells a term
  two ways, both become canon and the rule stops catching either.
* **The learn/check asymmetry is mitigated, not solved.** `review_reference`
  reports the gap; it does not close it. Anyone reading `learn` output must
  actually read that section, or the paradox returns in a quieter form.

---

## 5. Housekeeping

Small, uncontroversial, safe to batch:

* **`--accept` cannot record a note.** `Suppression` has a `note: str` field
  (`tieout/profile/schema.py:380`), but `_accept` in `tieout/cli.py` only ever
  writes `rule_id`, `slide_index` and `count` — there is no `--accept-note`
  option, so the field is dead. This matters because of `_suggest_relearn`:
  three acceptances is meant to signal that the *rule* is miscalibrated rather
  than the deck. Without a note, whoever hits that threshold has no record of
  *why* it was accepted three times, which is the only thing that makes the
  signal actionable. Either wire the field up or delete it.
* Suppression paths are already `profiles/<client>.suppress.yaml` in-repo
  (`suppression_path`, asserted by `tests/test_cli.py:298`). An earlier note
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

1. Settle the grid direction with the user (§2), then implement and prove it
   against the invariant. This is the largest source of remaining noise and
   the thing the user actually named.
2. Get the deck re-attached and close slides 9 and 12 (§3).
3. Housekeeping (§5) — bundle into whichever branch is open.
4. A second real deck (§4), which is the only thing that will show whether
   the fitted constants generalise.
