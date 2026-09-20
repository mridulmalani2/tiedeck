# TieOut — tying and ticking, and the confidentiality boundary

Written 2026-09-20, replacing the previous contents of this file — "what it is,
what it has to become, and the work between" — whose queue is spent. §2 accounts
for every section of that document and what it became; it is deleted because it
was built, not because it was abandoned. This is the only build document. Start
here.

---

## 1. The one-sentence version

**TieOut checks how a deck looks far better than it checks what it says.**
Forty-six rules, forty-four on by default, and **three** of them read a figure
and compare it to another figure — all three from tables only. The deck can be
pixel-perfect and still say 8% on page 4 and 20% on page 12.

The other half: the layer that sends text to a model is the safest thing in the
codebase, and its one weakness is that the *approval* to send is a claim the
browser makes rather than a fact the server can check.

---

## 2. What the previous plan asked for, and where it landed

Every item verified against code and tests before its section was deleted.

| Previous section | Asked for | Where it landed |
| --- | --- | --- |
| §3.1 / §5.3 | Canvas never re-rendered after a correction; `thumbnails.version` never incremented | The live surface redraws from the model on every correction, which is the verify step; `session.py:281` bumps `thumbnails_version`, and `/api/move`, `/api/resize`, `/api/edit-text` and `/api/undo` force the rail's preview to re-render with it. `/api/fix` deliberately does not — see §9. The shape a correction touched flashes on the slide, held in state so a background redraw cannot cut it short |
| §3.2 | Marker has no dismiss | Escape closes the editor, a second Escape or a click on the canvas clears the marker; driven and confirmed |
| §3.3 | Canvas is a picture; nothing on it can be touched | `tieout_ui/canvas.py` serialises the shape tree; shapes are real elements — click-select, eight rotation-aware handles, drag, double-click to edit text, arrow-key nudge |
| §3.4 | `select(NaN)` from cards carrying no `data-slide` | Guarded; only a card naming a slide navigates |
| §5.1 | Live surface: DOM not canvas, model as source of truth, LibreOffice out of the edit path, honest placeholders | Built. LibreOffice is now only the rail's photographic preview; charts/SmartArt/OLE/media draw a labelled placeholder at true geometry with a stated reason |
| §5.2 | Selection, handles, drag, nudge, snapping, data-mark guardrail | Built. `data_mark_uids()` is the shared primitive; a data-encoding shape warns and requires confirmation before a drag |
| §5.4 | Text editing preserving font, language and place in the paragraph | `retext_fix` addresses a run by paragraph + run index and rewrites only its `a:t`; `rPr` is untouched. Breaks and fields are refused rather than guessed |
| §6 | Groups, rotation, tables/charts, SmartArt, pictures, concurrency, odd slide sizes, 100+ slides, `uid` not `shape_id` | All built and tested; group write-back composes scale through nested groups and refuses rotated or flipped ones |
| §9 | `LO-006` unreachable as documented | Fixed; naming it on the command line now reaches it |

Two items were carried, not completed, and appear again below: **a deck whose
figures restate each other, so the consistency rules are proven on real
material**, and pointing the render oracle at a real client deck (still blocked —
client decks are never committed).

Four defects were found afterwards by driving the surface rather than reading
it, and are already fixed: text sized in absolute points inside a
percentage-scaled slide, text clipped to its shape (which hid `LO-006`'s own
seeded defect), a placeholder's offset and extent not inheriting separately, and
a correction flash cut short by a background redraw.

---

## 3. Where the tie-out actually stands

Measured 2026-09-20 against `reference_dirty.pptx`.

```
rules defined                   46
rules on by default             44
consistency rules                3      CO-001, CO-002, CO-003
of those, reading anything
other than a table               0
```

`tieout/rules/consistency.py` states its own scope in its first paragraph:
"Everything here is derived from the deck's own tables." That is what makes it
sound, and it is also the ceiling.

| What is not tied today | The error it lets through |
| --- | --- |
| Numbers in prose, headlines and footnotes | "Revenue grew to $412m" over a table reading 408. `CO-001` needs the figure in **two tables**; a headline is neither |
| Chart values | `ChartModel` carries `point_count`, categories and label flags — **no numeric values at all**. A chart contradicting the table beside it cannot be seen |
| Derived figures | A margin, a growth rate, a CAGR or an **EV/EBITDA multiple** is never recomputed from the inputs the deck itself gives |
| Units, currency, scale | Beyond `CO-002`'s clean ×100/×1000 inside a table: $m against $bn across two pages is invisible |
| As-of dates | "as at 14-Sep" on one page and another date on the next, over the same figures |

What already exists and must be reused rather than rebuilt: `tieout/text.py`'s
`parse_number` returns a `NumberReading` carrying value, decimals, thousands
separator, negative style, **currency and suffix** — and the suffixes it already
understands are the multiple, percentage and basis-point forms banking tables
use. The primitive for all of this is built. It is only ever fed table cells.

---

## 4. The confidentiality boundary

This is in better shape than the tie-out, and the work here is hardening, not
building.

**What holds, and must survive.** `tieout` proper cannot reach a network, proved
by an AST walk over every module; `tests/test_review_airgap.py` proves the review
layer cannot leak back into it. `prepare()` is offline and needs no key, so what
would leave can be read before anything leaves. `send()` refuses an unapproved
residual list, then independently re-verifies that no literal term survives into
the outgoing text and raises `RedactionFailed` — worded as a defect report, not a
user error — rather than transmitting. The UI enforces the same hold, and shows
the redaction table and the residual list before offering the approval. Forty-four
redaction tests, thirty-three findings tests.

**The gap.** `approved` is an unbound assertion. The browser posts
`approved: true`; the server cannot tell whether that approval corresponds to the
residual list it displayed, or to any list at all. If the deck, the blocklist or
the forbidden terms change between `/api/redact` and `/api/check`, a stale
approval silently applies to a different payload.

**The property worth locking down.** There is exactly one call site of
`client.complete()` in the whole package — `review.py:247`, inside `send()`,
after both checks. That is the shape of the safety argument, and nothing today
stops a second one being added.

---

## 5. What has to be built

### 5.1 Bind the approval, and record what left

- `/api/redact` returns a **residual digest**: SHA-256 over the canonical residual
  list together with a hash of the exact payload text. The approval echoes it
  back; the server recomputes and refuses on mismatch with "the deck changed
  since you approved this". `Prepared.approve()` takes the digest it is approving,
  so the binding exists in the library and not only in the web layer.
- An **append-only outbound log**, written wherever the person can find it and
  hand it to compliance. One line per transmission: timestamp, deck filename,
  model, character count, redaction count, residual count, the digest, and a hash
  of the transmitted text. Never the text itself — a log that quotes the payload
  is a second copy of the thing being protected.
- The CLI enforces the same binding, or the browser is simply the loose door.

**Tests, which are the point of this section.**
- A structural test that `client.complete()` has exactly one call site and that
  it sits inside `send()` after the two checks — the same kind of proof the air-gap
  test already makes, and for the same reason: a test that one code path sent
  nothing proves nothing about the next one.
- A fake client that records everything it is handed, driven over a corpus of
  adversarial decks — names split across runs, soft hyphens, non-breaking spaces,
  a name appearing only in a footnote, a name that is also an ordinary word — and
  asserting that no seeded term appears in anything it received.
- A test that a stale or absent digest refuses, and that a refusal transmits
  nothing (asserted against the fake client, not against a return value).

**Not in scope:** masking the figures themselves. The model has to see numbers to
check numbers. That trade is stated here so it is a decision rather than an
oversight.

### 5.2 One figure index for the whole deck

A new air-gapped module, `tieout/figures.py`, walking a `DeckModel` and returning
every figure in it with enough identity to compare against another:

- **the reading** — the existing `NumberReading`, unchanged;
- **where it is** — slide, shape `uid`, and the address needed to write it back:
  `(row, column)` for a table cell, `(paragraph, run)` for text, `(series, point)`
  for a chart. Carry the shape's `shape_id` alongside its `uid`: identity and
  de-duplication key on `uid`, because `cNvPr@id` is not unique in practice, but
  `retext_fix` writes by `shape_id` and `(paragraph, run)` — which is exactly the
  address a text figure already has, so a fix needs no new write path;
- **what it is of** — a folded metric label, from the row and column for a table,
  the surrounding noun phrase for prose, the series and category for a chart;
- **the period** — FY24, Q1 2026, LTM, parsed from the label or the column header;
- **the unit** — currency, scale and suffix, including a scale stated once for a
  whole table ("in US$ millions") and inherited by its cells.

`CO-001`, `CO-002` and `CO-003` are then **refactored onto the index** rather than
walking tables themselves. That is the point of the module: the rule that catches
a contradiction between two tables becomes the rule that catches it between a
table and a headline, without a second implementation of what "the same figure"
means.

**The false-positive discipline, non-negotiable.** The existing rules already
state the failure mode — two tables can label genuinely different things the same
way — and answer it by keying on (row label, column header) and rejecting generic
labels. The index inherits that and adds a confidence ladder:

| Evidence | Confidence |
| --- | --- |
| Two table cells, specific label, same period | `high` |
| A table cell and a chart point | `high` |
| Anything anchored on prose | `medium` — the label came from surrounding words |
| Generic metric, or no period on either side | **not reported at all** |

A tie-out that cries wolf is worse than none: it will be switched off, and the
formatting rules will be switched off with it.

### 5.3 Chart values in the model

`ChartSeries` gains its values, read from `c:val/c:numRef/c:numCache/c:pt` and
from `c:numLit` where the series is literal. The loader already reaches into
`c:val//c:ptCount` for the point count, so the path is known and the change is
narrow.

Read the **cache**, deliberately: it is what PowerPoint draws, and therefore what
the reader sees. A cache that disagrees with the embedded workbook is a different
and more alarming defect; out of scope here, noted so it is not mistaken for this.

### 5.4 The derived checks

Each recomputes from figures the deck itself supplies, so every finding is
arithmetic and reproducible, and each states its inputs in the evidence line —
"9.4x stated, 8.7x from EV 4,180 (slide 12) over EBITDA 480 (slide 6)".

| Rule | Reports |
| --- | --- |
| `CO-004` | A stated margin or ratio that does not equal its inputs |
| `CO-005` | A stated multiple (EV/EBITDA, P/E) that does not equal its inputs |
| `CO-006` | A stated growth rate or CAGR that does not equal the series it describes |
| `CO-007` | A bridge or waterfall whose steps do not carry opening to closing |
| `CO-008` | The same metric and period given in a different scale or currency, with no stated conversion |
| `CO-009` | More than one as-of date governing the same figures |

Each carries a rounding tolerance derived the way `CO-003`'s already is — a
correctly-rounded total is not a defect — and each refuses rather than guesses
where an input is missing.

**Out of scope, stated:** cross-references ("see page 12") are not checked. Worth
doing, not now.

### 5.5 Findings in the review panel, with Edit and Fix

The findings arrive in the existing note, grouped by the fix behind them like
every other finding. What is new is that the tie-out findings can act on the
surface built last, and **the split between the two buttons is the whole
argument**:

- **Fix it** — offered *only* where the figure is derived and therefore has one
  right answer: a margin that must equal its inputs, a multiple that must equal
  its inputs, a total that must equal its column, a bridge's closing figure. The
  correction is arithmetic, so TieOut is not choosing anything. It writes through
  `retext_fix`, which already exists, already addresses a run positionally, and
  already routes through the versioned-file path, so undo and the audit trail keep
  working with no new machinery.
- **Edit it** — offered where two *stated* figures disagree. Which of them is
  right is a judgement about the deal, not arithmetic, and TieOut does not make
  it. The button opens the run in the in-place text editor on the slide surface —
  double-click a run, type, Enter to write, Escape to leave it alone — with the
  counterpart figure and its slide shown beside it, so the person deciding has
  both numbers in front of them.

**A replacement must be written in the original's own format.** `NumberReading`
carries decimals, thousands separator, negative style, currency and suffix; a fix
that corrects 23.1 to 23.4 and drops a currency prefix or a decimal place has
introduced a formatting defect while fixing an arithmetic one — and the
typography rules will then report it. This is the detail most likely to be
skipped and most likely to be noticed.

Never: inventing a figure where nothing in the deck determines it. That promise
is in the README and it survives this.

### 5.6 A deck that restates its own figures

The reference generator gains seeded tie-out defects, which closes the item the
previous plan carried twice: a headline contradicting its table, a chart cache
contradicting the table beside it, a margin that does not compute, a multiple
that does not tie, a scale slip between $m and $bn, and a second as-of date.
Each seeded under its own rule id, the way every existing defect is, so the
suite can assert that each rule catches its own and reports nothing else.

Until this exists, every rule in §5.4 is tested only against fixtures written by
the same person who wrote the rule.

---

## 6. Edge cases that will decide whether this is airtight

**Reading figures**
- A figure split across runs by formatting — "$4" and "12m" in two runs of one
  paragraph — must read as one number, and must address back to the run that
  holds the digits.
- Footnote markers attached to figures ("58.1 (a)", "263*") — already handled for
  table cells; prose needs the same.
- Ranges ("8–10x"), approximations ("c.400"), and "n.a." — read, and excluded from
  comparison rather than coerced.
- A percentage of something that is itself a percentage; basis points against
  percentage points.
- Negative figures in parentheses, and a bridge step that is a reduction.

**Deciding two figures are the same figure**
- The same metric for the group and for a segment, both correct.
- The same metric on a restated and an unrestated basis, both correct — if the
  deck says so, and the deck usually says so in a footnote.
- Pro forma against actual; LTM against FY.
- A figure repeated on a divider or summary page, which is the case this is most
  useful for and also the one most likely to carry a shortened label.

**Acting**
- A fix whose run is inside a group, or a table cell — the write path for a table
  cell does not exist yet and must refuse, visibly, rather than appearing to work.
- A fix that corrects a figure another finding also names; the second finding must
  re-measure rather than go stale.
- Undo of a figure fix, and undo of a fix whose correction exposed a new tie-out
  finding.
- Two tie-out fixes in flight — the per-deck lock already serialises corrections.

**Confidentiality**
- A deck re-uploaded between approving and sending.
- A blocklist edited between approving and sending.
- A residual list that is empty — the digest must still bind, or "clear" becomes
  the way around the check.
- A model returning a finding that quotes a placeholder that was never issued.

---

## 7. Order of work

1. **Bind the approval, log what left, and prove the single call site** (5.1).
   Small, independent, and it is a safety property — it goes first.
2. **The figure index**, with `CO-001`/`002`/`003` refactored onto it (5.2).
   Everything after this depends on it.
3. **Chart values in the model** (5.3).
4. **The derived checks** (5.4), one rule at a time, each with its seeded defect.
5. **Edit and Fix in the panel** (5.5).
6. **The seeded reference deck** (5.6) — written alongside 4, not after it.

---

## 8. How to work on this

Unchanged, because it is what has worked.

**Reproduce against a purpose-built deck before fixing, and check the new test
fails on the previous code.** Twice a test has passed for the wrong reason. The
third will be a tie-out rule that agrees with a bug in the index, which is why
every rule in §5.4 gets a seeded defect it must catch and a clean deck it must
stay silent on.

**Drive the app, with intent.** The four defects found after the previous plan
was finished — text at the wrong scale, text clipped, a title with no size, a
flash cut short — were invisible to 1,473 passing tests and to every screenshot,
because a screenshot proves what a thing looks like and not what it does.

**Quote a number with the conditions it was measured under.**

The gate is `ruff` **and** `mypy` **and** `pytest` (85% floor, 1,473 tests).

```
.venv/bin/ruff check . && .venv/bin/mypy . && .venv/bin/python -m pytest
./run.sh                 # the UI on http://127.0.0.1:8765/
```

For thumbnails and the render oracle, installed one at a time — a single
`apt-get` aborts the whole transaction on one 404 and leaves a core-only
LibreOffice behind, which fails in a way that reads as a bad deck:

```
apt-get install -y --no-install-recommends libreoffice-impress
apt-get install -y --no-install-recommends poppler-utils
apt-get install -y --no-install-recommends fonts-crosextra-carlito fonts-crosextra-caladea
```

Client decks are never committed; CI asserts no `.pptx` is tracked.

---

## 9. Standing limitations to carry forward, not rediscover

* Ink is a bound, not a measurement, wherever Carlito/Caladea are absent. The
  bounds are sound for overflow (they under-report) and unsound for `LO-001`,
  which over-reports a frame whose text is on the canvas.
* A logo drawn as text alone, with no badge and no image, is not learned, so its
  position and size go unchecked. Deliberate: nothing separates it from any other
  repeated line.
* Where the reference deck bleeds, a structurally-identified decorative bleed is
  silent, so a misplaced text-free background graphic is not reported.
* `LOCKUP_PLATE_AREA_RATIO` 4.0, `LOCKUP_GAP_HEIGHTS` 1.0, `TITLE_BAND_SHARE`
  0.5, and every other tuned constant, are fitted to two house styles.
* Rotated shapes are drawn, selected and resized correctly by construction and by
  unit test, but no reference deck contains one, so that path has never been
  driven against real material.
* The render oracle has still never been pointed at a real client deck, because a
  client deck cannot be committed.
* The model layer composes only offset and scale when flattening a group's
  children into slide space; a rotated group's children are positioned as though
  the group were not rotated. Geometry writes refuse such a group rather than
  guessing, so the two are consistent, but the model is the weaker of the two.
* The rail's photographic thumbnail re-renders after a move, a resize, a text
  edit and an undo, but not after `/api/fix`: `_reload()` leaves it alone, on the
  argument that a LibreOffice conversion should not sit between a click and its
  result for a recoloured fill. Defensible now that the canvas is drawn from the
  model and is the thing actually being verified — but it is an inconsistency
  left standing rather than a decision anyone took after the surface landed.
