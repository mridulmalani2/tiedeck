# TieOut — tying and ticking, and the confidentiality boundary

Written 2026-09-20, replacing the previous contents of this file — "what it is,
what it has to become, and the work between" — whose queue is spent. §2 accounts
for every section of that document and what it became; it is deleted because it
was built, not because it was abandoned. This is the only build document. Start
here.

**Revised 2026-09-23.** §5.1 through §5.4 and §5.6 are built. The tie-out was
then pointed at a deck written the way a real one is written, which is what
§7 item 1 now records and what §10 is the result of. A second audit — read-only,
driven through the browser against a twenty-slide seeded deck and a
twenty-six-slide reference deck — is folded in at §11. Every number below was
measured on 2026-09-23 unless it says otherwise.

---

## 1. The one-sentence version

**TieOut now checks what a deck says, and the gap has moved to what it does
about it.** Fifty-two rules, **nine** of which read a figure and compare it to
another figure — from tables, from charts, and from prose grounded in the deck's
own vocabulary. A headline claiming 20% over a table reading 8% is visible; so
is a margin that does not equal its inputs, a multiple that does not tie, and a
bridge that does not carry.

Two things are true of that as of 2026-09-23, and the second is the live one:

1. Pointed at a deck written the way a real one is written, the tie-out
   **crashed and then disagreed with itself nine times** on a deck with nothing
   wrong in it. All ten are fixed and each is held by a test that fails without
   the fix. §10 is the account.
2. **Of the twenty findings a real deck produced in the UI, ten have no
   resolution path in the product, and both tie-out findings are among them.**
   The rules that are the product's reason to exist end at detection: the user
   is told two numbers disagree and is handed a tool for moving the table. §5.5
   is the remaining build item and §11 is the queue behind it.

The confidentiality layer is bound and logged (§5.1, built). Its one-call-site
property is now a structural test rather than an argument.

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

Two items were carried, not completed. The first — **a deck whose figures
restate each other, so the consistency rules are proven on real material** — was
carried twice more and is now done two ways: the generator seeds a defect per
rule (§5.6), and `tests/test_real_world_figures.py` does the harder half by
holding the rules to a deck that ties out (§10). The second, pointing the render
oracle at a real client deck, is still blocked, because client decks are never
committed.

Four defects were found afterwards by driving the surface rather than reading
it, and are already fixed: text sized in absolute points inside a
percentage-scaled slide, text clipped to its shape (which hid `LO-006`'s own
seeded defect), a placeholder's offset and extent not inheriting separately, and
a correction flash cut short by a background redraw.

---

## 3. Where the tie-out actually stands

Measured 2026-09-23. Rule counts from the registry; the number that *runs* on a
given deck is lower and profile-dependent, because 24 rules require a fact the
profile may not carry, so "rules on by default" is not a property of the code
and is not quoted here.

```
rules defined                   52
consistency rules                9      CO-001 .. CO-009
of those, reading a chart
or prose as well as a table      9
tests                        1,673      1,651 before, 22 added by §10
```

The ceiling §3 described on 2026-09-20 — "everything here is derived from the
deck's own tables" — is gone. `tieout/figures.py` is one index of every figure
in the deck, and all nine consistency rules read it rather than walking tables
themselves.

What replaced it is a narrower and more interesting limit, and §10 is how it was
found: **the index was only ever verified against decks the generator built, and
the generator builds what the index expects.**

| What is tied now | Where |
| --- | --- |
| Numbers in prose, headlines and footnotes | Grounded in the deck's own metric vocabulary, bound to the metric and period **next to** the figure — see §10, which is where taking one of each per sentence stopped being defensible |
| Chart values | `ChartSeries` carries its values, read from the cache, which is what PowerPoint draws and therefore what the reader sees |
| Derived figures | `CO-004` … `CO-007` recompute margins, multiples, growth rates and bridges from the inputs the deck itself gives, each stating them in its evidence line |
| Units, currency, scale | `CO-008`, which now stays silent where the deck states the conversion |
| As-of dates | `CO-009` |

| What is still not tied | The error it lets through |
| --- | --- |
| Cross-references | "see page 12" pointing at page 14. Stated out of scope in §5.4 and still is |
| A chart cache against its embedded workbook | A cache that disagrees with the workbook behind it. Deliberate: §5.3 reads the cache because that is what is drawn |
| A total labelled after its metric | "Total revenue" under three segments is not read as a total. Deliberate — see §9 |

`tieout/text.py`'s `parse_number` remains the primitive underneath all of it,
returning a `NumberReading` carrying value, decimals, thousands separator,
negative style, currency and suffix — which is what lets a fix write a
replacement in the original's own format. It is now fed table cells, chart
points and prose alike.

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

**Built.** `tieout_review/outbound.py` and `attest.py`; the structural
test that `client.complete()` has exactly one call site is in
`tests/test_review_outbound.py`. Kept below as the specification it was built to.

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

**Built.** `tieout/figures.py`. §10 is what happened when it was first
pointed at a deck the generator did not write.

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

**Built.**

`ChartSeries` gains its values, read from `c:val/c:numRef/c:numCache/c:pt` and
from `c:numLit` where the series is literal. The loader already reaches into
`c:val//c:ptCount` for the point count, so the path is known and the change is
narrow.

Read the **cache**, deliberately: it is what PowerPoint draws, and therefore what
the reader sees. A cache that disagrees with the embedded workbook is a different
and more alarming defect; out of scope here, noted so it is not mistaken for this.

### 5.4 The derived checks

**Built.** `tieout/rules/derived.py`, `CO-004` … `CO-009`.

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

**Outstanding, and now the binding item.** §11.2 measured what its absence
costs on a real deck: ten of twenty findings had no resolution path, and
both tie-out findings were among them.

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

**Built**, and then found insufficient on its own — see §10. A generated
deck seeded with defects proves a rule catches what it was written to catch;
it cannot prove the rule is silent on what a real deck does, because the
generator builds what the index expects. `tests/test_real_world_figures.py`
is the other half.

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

Marked ✓ where `tests/test_real_world_figures.py` now holds it. The rest are
still open, and are open on the evidence of §10 that this list was the right one.

**Reading figures**
- ✓ A figure split across runs by formatting — "$4" and "12m" in two runs of one
  paragraph — must read as one number, and must address back to the run that
  holds the digits.
- ✓ Footnote markers attached to figures ("58.1 (a)", "263*") — already handled
  for table cells; prose needs the same. *Prose did not have it; §10 #10.*
- ✓ Ranges ("8–10x"), approximations ("c.400"), and "n.a." — read, and excluded
  from comparison rather than coerced. *Ranges were not; §10 #4.*
- A percentage of something that is itself a percentage; basis points against
  percentage points. *Partly: a `bps` figure carries its own quantity and so is
  never weighed against a `%`, which is the safe half and not the useful one.*
- ✓ Negative figures in parentheses, and a bridge step that is a reduction.
  *And a bracketed restatement that is not a negative at all; §10 #5.*

**Deciding two figures are the same figure**
- ✓ The same metric for the group and for a segment, both correct. *Scoped by
  the table's corner cell; §10 #2, and the limits are in §9.*
- The same metric on a restated and an unrestated basis, both correct — if the
  deck says so, and the deck usually says so in a footnote. **Still open**, and
  the footnote is not read.
- Pro forma against actual; LTM against FY. *Periods separate `A`, `E`, `PF` and
  the relative windows, so these do not collide. A pro-forma figure labelled
  only in a footnote still will.*
- ✓ A figure repeated on a divider or summary page, which is the case this is
  most useful for and also the one most likely to carry a shortened label.
  *The executive summary in the real-world deck is exactly this.*

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

Items 1 to 4 and 6 of the 2026-09-20 list are built, and are struck through
rather than deleted so that what was promised can be checked against what
landed.

1. ~~Bind the approval, log what left, and prove the single call site (5.1).~~
   Built: `tieout_review/outbound.py`, `attest.py`, and a structural test that
   `client.complete()` has exactly one call site inside `send()`.
2. ~~The figure index, with CO-001/002/003 refactored onto it (5.2).~~
   Built: `tieout/figures.py`.
3. ~~Chart values in the model (5.3).~~ Built.
4. ~~The derived checks (5.4).~~ Built: `CO-004` … `CO-009`.
6. ~~The seeded reference deck (5.6).~~ Built, alongside 4.

What is left, in order:

1. **Point the tie-out at a deck written the way a real one is written**, and
   fix what it says. **Done 2026-09-23** — `tests/test_real_world_figures.py`,
   and §10 is the account of the ten defects it found. It is item 1 rather than
   item 5 because until it was done, every rule in §5.4 was tested only against
   decks the generator built, and *the generator builds what the index expects*.
   The same gap on the formatting rules produced 168 findings on a real
   twenty-slide deck. **Do not add a rule before repeating this for it.**
2. **Edit and Fix in the panel** (5.5) — the only item of the original queue
   still outstanding, and §11 is the evidence that it is now the binding one:
   ten of twenty findings on a real deck have no resolution path, and the two
   tie-out findings are among them.
3. **The UI queue** (§11), in the order §11 sets out. Item 11.1 is a single
   character and makes the surface the whole product asks you to judge on stop
   misrepresenting the deck.

---

## 8. How to work on this

Unchanged, because it is what has worked.

**Reproduce against a purpose-built deck before fixing, and check the new test
fails on the previous code.** Twice a test has passed for the wrong reason. The
third would have been a tie-out rule agreeing with a bug in the index, which is
why every rule in §5.4 gets a seeded defect it must catch and a clean deck it
must stay silent on — and why §10 exists, since the seeded defects and the clean
deck were both built by the same generator the index was written against.

The discipline paid on 2026-09-23: of the 22 tests §10 added, **17 fail on the
previous code**. The five that pass are regression guards for behaviour that was
already right and had to survive the change, which is the other half of the job.

**A refusal is not a pass.** Five of the ten defects in §10 were silent — a rule
recording "could not be recomputed" and moving on, or a figure indexed against
nothing — which on a deck with nothing wrong in it reads exactly like agreement.
`CO-005` refused every row of a comparables table and `CO-006` refused every
CAGR in the deck, and both silences survived 1,651 passing tests. Any new rule
is to be tested on what it *declined* to check as well as on what it reported.

**Drive the app, with intent.** The four defects found after the previous plan
was finished — text at the wrong scale, text clipped, a title with no size, a
flash cut short — were invisible to 1,473 passing tests and to every screenshot,
because a screenshot proves what a thing looks like and not what it does.

**Quote a number with the conditions it was measured under.**

The gate is `ruff` **and** `mypy` **and** `pytest` (85% floor, 1,673 tests as at
2026-09-23; 1,651 before §10 added 22).

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
* **A table's scope comes from its corner cell and from nowhere else.** A
  segment P&L headed "Analytics ($m)" is scoped to Analytics and no longer
  contradicts the group P&L; one headed only "$m", whose slide headline is the
  only thing naming the segment, still will. Reading the headline was tried and
  rejected: "Revenue by Segment" names a metric the deck uses and means nothing
  of the sort, and a scope read off a headline is a scope read off a sentence.
  The corner cell is also honoured only where it names something the deck's own
  tables already use, so that a table headed "Fiscal year" does not take that as
  a scope and stop matching the headline that restates it.
* **A total labelled after its metric is not read as a total.** "Total revenue"
  under three segments is a genuine total and `CO-003` skips it, because the
  label has to be a total word exactly or followed by a period. Deliberate, and
  the reason is in the rule: "Total addressable market" is a metric and
  "Total shareholder return" is a percentage, and widening this reports every
  TAM/SAM/SOM slide in banking. `CO-003` also skips a column with fewer than
  three addends, so a two-segment table is not checked at all.
* **Prose gives up a figure rather than guess what it measures.** A number in a
  sentence is indexed only where a metric the deck's own tables use sits
  immediately before or after it, or where an earlier figure in the same
  sentence is so bound and this one is the same quantity at the same scale.
  "An enterprise value of $4,180m" is therefore not indexed at all, because
  "enterprise value" is a scope in this deck and not a metric. That is the
  intended direction: §10 #3 is what the other one costs.
* **`CO-005` refuses on a statistics row** — median, mean, average, high, low
  and their kin — because the arithmetic relating the columns does not hold
  across one. "Total" and "sum" are deliberately not in that set: a total row is
  additive and its margin really is its own EBITDA over its own revenue.
* **A multiple that names no period takes its inputs' periods**, and only inside
  a named scope. That is what lets a comparables row divide an undated EV by an
  LTM EBITDA; it is also a relaxation of the exact-period rule `lookup` was
  written to enforce, and the scope is the only thing keeping it from reaching
  across to another company.
* **`lookup` narrows to the modal stated currency**, and figures stating none
  join it. A figure restated once in sterling no longer makes the deck's dollars
  ambiguous. A deck that genuinely reports half in one currency and half in
  another has no majority, and the tie is broken alphabetically — deterministic,
  so findings reproduce, and arbitrary, so the figure it computes from is the
  one whose currency code sorts first. That deck should be stating a
  presentation currency, and this is not a substitute for it.
* The tie-out has still never been pointed at a **real client deck**, for the
  same reason the render oracle has not: a client deck cannot be committed.
  `tests/test_real_world_figures.py` is a deck written to look like one, by the
  same person who wrote the fixes, which is better than the generator and is not
  the same thing as the real article.
* The rail's photographic thumbnail re-renders after a move, a resize, a text
  edit and an undo, but not after `/api/fix`: `_reload()` leaves it alone, on the
  argument that a LibreOffice conversion should not sit between a click and its
  result for a recoloured fill. Defensible now that the canvas is drawn from the
  model and is the thing actually being verified — but it is an inconsistency
  left standing rather than a decision anyone took after the surface landed.

---

## 10. What the tie-out said about a deck that ties out

Done 2026-09-23. `tests/test_real_world_figures.py` builds an eleven-slide
sell-side deck in the shape of a real one — a group P&L, a segment P&L under the
group's own row labels, a trading-comparables table ending in a median row, a
Low/Mid/High valuation, a revenue bridge, a chart, a CAGR, an executive summary
whose prose restates the tables, and a figure restated in a second currency.
Every figure in it agrees with every other statement of it: the margins equal
their inputs, the multiples equal EV over EBITDA, the segments sum to the group,
the bridge carries, the CAGR is the CAGR.

Pointed at it, the tie-out **crashed, and then disagreed with itself nine
times.** The crash is worth stating on its own:

> `FigureIndex.grouped()` keys on `(metric, scope, quantity)`, where `quantity`
> is `str | None`, and the caller sorted the keys. A deck that says "EBITDA of
> $480m" and "8.7x LTM EBITDA" on one page compares `None` with `'x'`, and
> **CO-001 and CO-002 both raise `TypeError` and check nothing.** That is every
> banking deck. It survived 1,651 passing tests because the generated decks
> never state one metric in two quantities.

All ten are fixed, each with a test that fails without the fix.

| # | Kind | What the tool did | Why the generated decks could not show it |
| --- | --- | --- | --- |
| 1 | crash | CO-001 and CO-002 raised and checked nothing | the generator never states one metric in two quantities |
| 2 | false positive | the Analytics segment P&L contradicted the group P&L on every row | the generator writes one table per metric; no deck of its has a segment P&L |
| 3 | false positive | "an enterprise value of $4,180m implies 8.7x LTM EBITDA" indexed the EV as EBITDA, so EBITDA was $4,180m on one slide and $480m on the next | the generator's prose names one metric per sentence |
| 4 | false positive | "8.0x - 9.5x", one statement of a range, was two multiples disagreeing with each other and with the 8.7x the deck proposes | the generator writes no ranges |
| 5 | false positive | "GBP 1,510m (US$1,935m)" read as revenue of **minus** 1,935, and CO-008 reported the deck's own stated conversion as unit drift | the generator writes one currency |
| 6 | **silent** | CO-005 refused every row of the comparables table: `EV ($m)` folded to the label `ev $m`, which no alias matches | the generator heads its columns without units |
| 7 | **silent** | behind that, the EBITDA column of a comparables table was indexed **upside down** — filed under the *company* as its metric, with no scope, while the columns either side were filed under the company as their scope. No row could find its own EBITDA | the generator dates every column or none |
| 8 | **silent** | CO-006 refused every CAGR in the deck, because two revenues answered to one period once the segment P&L was in it | as #2 |
| 9 | **silent** | every comparison sentence fell out of every rule: "revenue of $1,935m in FY25A, up from $1,352m in FY23A" gave **both** figures the range `FY2025A..FY2023A`, which agrees with no single period | the generator's prose names one period per sentence |
| 10 | latent | "Group EBITDA of $480m(1)" put a figure of **1** into the index, labelled `ebitda`. It cost no finding only because no real EBITDA is 1; what it cost was `lookup`, which then saw EBITDA stated two ways and refused | table cells have had footnote handling from the beginning; prose never did |

Two more were found while fixing those and are fixed with them:

* A comparables table's **median row** is a statistic of the rows above rather
  than one of them. The median multiple is the median of the multiples, not the
  median EV over the median EBITDA — so the moment #6 and #7 were fixed, CO-005
  reported 9.0x against a recomputed 8.0x on the one row of the table that
  cannot be wrong in the way the rule describes. It now refuses, and says why.
* **CO-007 recognised no bridge at all** on a bridge labelled the ordinary way.
  It looked for "Opening" and "Closing"; a revenue bridge is labelled "FY24A
  revenue → Volume → Price → FX → FY25A revenue". It now also recognises ends
  that name the same metric at two periods *with no period named on any step
  between them*, which is what separates a bridge from a year-by-year history
  table.

And one outside the tie-out, in `tieout/text.py`: `%d-%B-%y` was an accepted date
format and `%d-%b-%y` was not, so **"as at 30-Jun-25" parsed as no date at all**
and CO-009 saw no as-of date on the slide. The abbreviated month now has the
two-digit year its unabbreviated twin always had.

**The shape of the lesson, for the next rule.** One was a crash and four were
false positives; the other five were silent, and silence was the more expensive
half: a false positive is argued with, a refusal is believed. The three
assertions that matter in the new suite are therefore *reports nothing*,
*crashed nowhere*, and *left nothing unchecked without saying why* — and, because all three pass on a tie-out that does nothing at all, each
of the nine rules is also given the same deck with one figure changed and has to
catch it.

---

## 11. The UI queue, from the read-only audit

A second audit ran in parallel: read-only, driven through the browser as a user
would, against `falcon_seeded.pptx` (20 slides, 20 findings) and then
`reference_clean.pptx` checked against a foreign profile (26 slides, 300
findings collapsed into 24 jobs, which is the only way to see the grouping at
scale). Fifty findings. It is on the branch `claude/comprehensive-audit-be2242`
as `UI_AUDIT.md`, and that document is the detail; this is the queue.

It audited commit `4172c99`, which is behind this branch. Each item below says
whether it was re-verified against the current head on 2026-09-23. **Findings
that need the app driven were not re-driven in this session and are marked as
such — they are carried on the auditor's evidence, not re-measured.**

### 11.1 The canvas misrepresents the deck — verified live, one character

`tieout_ui/static/index.html:2266` builds a run's colour as
`` `color:#${f.color_hex}` ``, and `canvas_view()` emits `color_hex` **with the
`#` already on it**. Measured on this branch on 2026-09-23 rather than read:
`canvas_view()` over a test deck returns `['#000000', '#0F2A4A', '#6B7280',
'#C9A227']`. Every run is therefore styled `color:##0F2A4A`, which is invalid
CSS and is dropped, so **no text on the live canvas is ever drawn in its real
colour.** White-on-navy slides render dark on dark and cannot be read. Shape
*fills* go through `hexToRgba()`, which strips the `#` defensively, which is why
the gold logo box is right while the text beside it is not.

This is first because the canvas is the surface the workflow asks the user to
identify, judge and fix defects on, and anything colour-related is unjudgeable
while it holds. It is one character.

### 11.2 The fix affordances do not follow the remedies the rules compute

Verified in `tieout_ui/view.py:43`: `MOVABLE_RULES` is
`{BR-002, BR-008, LO-001..LO-005, LO-008}`.

* **BR-006 prints the exact target coordinates** — "Move the page number to left
  877.18pt, top 509.76pt" — and is not in the set, so it offers only *Set aside*
  under the caption "TieOut can see this is wrong and cannot know what is right",
  in the same card that states exactly what is right. The auditor cleared it by
  hand through the canvas, which proves the capability is there and only the
  affordance is missing.
* **CO-001 and CO-003 are in neither the movable nor the fixable set**, and a
  table cell has no write path, so clicking the offending figure selects the
  table and offers to *move* it. The product's headline capability ends at
  detection. This is §5.5, and it is the same work.
* The same "Yours to fix" sentence covers three different situations: an answer
  nobody can know (CO-001), an answer the tool knows and will not apply
  (BR-006), and an answer the tool knows and has no control for (LO-007 — where
  the sentence shown is about *moving shapes*, attached to a font size).

**Ten of the twenty findings on the seeded deck had no resolution path.**

### 11.3 Findings that name no object

* **HY-001 discards the shape it matched.** `tieout/rules/hygiene.py:207` emits
  `where=slide.index` only, so a **blocker** — placeholder text left in the deck
  — navigates to the slide and highlights nothing. Verified live. The rule has
  the shape in hand when it matches; it is thrown away before the UI sees it.
* HY-004 is a property of the file and is attributed to slide 1, badging and
  then ✓-ing a slide with nothing wrong on it. *Not re-driven.*
* LO-004 outlines one of the two overlapping shapes. *Not re-driven.*

### 11.4 Confidentiality and polish in the report

* **The HTML report is opened with the session token in the query string**
  (`index.html:1937`), so the credential that authorises reading live deck
  material is written into browser history by a normal button press — while the
  main page does substitute-and-clear exactly as designed. Verified live.
* **The report is titled with TieOut's internal working copy**, `v1-<name>.pptx`
  (`tieout/report/html.py:51`), and prints the absolute temp path in its footer.
  The artefact meant to be sent to someone else carries machine-local paths.
  Verified live.
* Every slide row in the report shows an empty "slide image" box although the
  app has rendered thumbnails for all of them at that moment. *Not re-driven.*

### 11.5 Wording that argues against the finding it is attached to

* **LO-002's evidence line ends "so the rule cannot fail this deck"**
  (`tieout/learn/derive_layout.py:271`) and was printed under 13 findings where
  the rule had just failed. True of the deck the profile was learned from, false
  of the deck being checked, and nothing in the wording says which. Verified
  live.
* **TY-007 gives its remedy as a raw regular expression** — "Write it to match
  the house pattern `^(\$)\s?[\d(]`" (`tieout/rules/typography.py:850`). The
  *By slide* view manages a human sentence for the same finding. Verified live.
* "Set the size to one of 10.5pt", and a required-boilerplate remedy reading
  "Add the footer text: 'H'" — the single letter of a logo badge. *Not
  re-driven.*

### 11.6 Interaction and state — carried on the auditor's evidence

Not re-driven in this session. Grouped by what they cost:

| Cost | Findings |
| --- | --- |
| You cannot see what you are judging | drag moves only the outline (#2); off-slide content is clipped away on the one rule about off-slide content (#34); overflow is painted onto the app background (#36); charts are grey hatching while a good raster sits 200px away (#33); badges cover the content the finding is about (#11) |
| You cannot act | resize is silently dead on shapes whose text overflows (#3); a small shape's handles cover its own text so a page number cannot be edited (#26); *By slide* has no buttons at all (#10) |
| The counts and state lie | *Set aside* leaves "20 things to do" at 20 (#13); undo labels a restored finding **NEW**, which is the product's signal for *newly exposed* (#14); re-running *Check deck* wipes the correction log while the ribbon still claims it (#15); a reload strands the deck and every correction on the server with no way back (#18); the profile chip goes stale on opening a second deck (#42) |
| Feedback is inconsistent | *Export deck* says nothing; *Copy note* reports success with nothing to copy (#23); a failed resize says nothing; corrections announce themselves well, which is the standard the rest should meet |
| Other | a native `window.confirm()` blocks the whole page for the data-mark guardrail, alone among every confirmation in the product (#24, verified live at `index.html:2470`); the profile picker pre-selects the alphabetically first profile, so one click audits against the wrong client (#30, verified live at `index.html:1079`); ticking *Content review* with no key blocks the offline audit too (#50) |

### 11.7 Deliberate, and to be left alone

`view.py:523` drops `measured` and `expected` from a grouped finding's header
where the instances differ, which the audit reports as "the user is told the
target and not the defect" (#44). The code says why: six shapes off six
different grid lines have six expectations, and printing the first at the top
would be a wrong number stated confidently. The audit's complaint is fair and
the fix is to say *both* — a range, or a count — not to print one instance's
measurement as the group's. Read §9 before touching it.
