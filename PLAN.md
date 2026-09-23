# TieOut — what the tie-out reads now, and what it still cannot

Written 2026-09-23, replacing "tying and ticking, and the confidentiality
boundary", whose queue is spent. §2 accounts for every section of that document
and what it became; it is deleted because it was built, not because it was
abandoned. This is the only build document. Start here.

---

## 1. The one-sentence version

**The tie-out now reads figures wherever the deck states them, and it still
cannot read a sentence.**

Nine consistency rules, all reading one figure index. Three of them compare a
figure with another statement of it — including a headline contradicting the
table under it, and a chart contradicting the table beside it. Four recompute a
figure the deck's own numbers determine. Two report how a figure is told rather
than what it says.

What is left is the half no index reaches: a claim with no figure to check it
against, a count that does not match its bullets, a measure defined one way and
used another. Those need reading rather than counting, and the optional review
layer is where they live.

---

## 2. What the previous plan asked for, and where it landed

Every item verified against code and tests before its section was deleted.

| Previous section | Asked for | Where it landed |
| --- | --- | --- |
| §5.1 | Bind the approval to the residual list; an outbound log; prove the single call site | `Prepared.approve()` takes a digest over the residual list **and** the payload text; `tieout_review/attest.py` says why both halves are needed. `tieout_review/outbound.py` appends one line per transmission — never the text — before the transport is called, and refuses the send if it cannot. `tests/test_review_airgap.py` asserts `client.complete()` has exactly one call site, that it is inside `send`, and that the hold, the re-verification and the record all precede it |
| §5.1 (corpus) | A fake client over adversarial decks | Six constructions in `tests/test_review_outbound.py`. It found a real leak on its first run — see §4 |
| §5.2 | One figure index; CO-001/002/003 refactored onto it | `tieout/figures.py`. 57 figures on the clean reference deck (46 table, 11 chart), 70 on the dirty one. The three rules read it and no longer walk tables |
| §5.3 | Chart values in the model | `ChartSeries.values`, from `c:numCache` or `c:numLit`, indexed by `c:pt@idx` so a sparse cache stays aligned with its categories |
| §5.4 | Six derived checks | CO-004 to CO-009 in `tieout/rules/derived.py`, each recomputing from figures the deck supplies and each stating its inputs |
| §5.5 | Fix where derived, Edit where stated | `Correction` on the finding, carried to the button. `tieout_fix.recell_fix` writes a table cell — the path §6 said must refuse until it existed. `figures.restate` keeps the original's format |
| §5.6 | A reference deck that restates its own figures | Six seeded defects, one per rule, each on its own slide against a period the projections table does not carry, and a test that no seed is claimed by two rules |

Two items the previous plan carried from the one before it are now closed: a
deck whose figures restate each other, and the derived checks being tested
against something other than fixtures written by their own author. The third —
pointing the render oracle at a real client deck — is still blocked for the same
reason, and always will be: client decks are never committed.

---

## 3. Where the tie-out actually stands

Measured 2026-09-23 against the generated reference decks.

```
rules defined                   52
rules on by default             50
consistency rules                9      CO-001 … CO-009
of those, reading something
other than a table               6      CO-001 (prose, charts), CO-004…CO-008
figures indexed, clean deck     57      46 table, 11 chart, 0 prose
tests                        1,686      6 skipped
```

`tieout/figures.py` states its own scope in its first paragraph. Two limits in
it are worth knowing before reading anything else.

**Prose is grounded in the deck's own vocabulary.** A figure in a sentence gets
a metric only where the sentence names a metric the deck's tables or charts
already use. That is why "Revenue reached 2,100 in 2025A" ties to the Revenue
row it contradicts, and why the clean reference deck indexes **zero** prose
figures — its only candidate was a year. The trade is stated rather than
discovered: a deck with no tables has no prose figures indexed at all.

**A year in a sentence is not a figure.** Every condition has to hold at once —
four digits, no decimal, no separator, no currency, no suffix, in range — and a
revenue of exactly 2,022 written without its separator is lost with it. That is
the price of not inventing the other kind of finding.

| What is not tied today | The error it lets through |
| --- | --- |
| A claim with no figure anywhere to check it | "materially ahead of plan" over a table showing 2% |
| A chart cache against its embedded workbook | The cache is what PowerPoint draws, so it is what is read; a workbook that disagrees is a different and more alarming defect |
| Cross-references | "see page 12" pointing at page 14 |
| A figure only ever stated once, in prose, in a deck with no tables | Nothing to tie it to, by construction |
| Two periods where the deck writes one both ways | "LTM" and "LTM September 2026" do not match, deliberately: conflating them would invent findings between LTM Sep-25 and LTM Sep-26 |

---

## 4. The confidentiality boundary

**What holds.** `tieout` cannot reach a network, proved by an AST walk over
every module. `tests/test_review_airgap.py` proves the review layer cannot leak
back into it, that `client.complete()` has one call site, and that the hold, the
re-verification and the outbound record all run before it. `prepare()` is
offline and needs no key. The approval names a digest over the residual list and
the payload text together, so a deck, blocklist or forbidden-terms list that
moved between `/api/redact` and `/api/check` stops the send. Every transmission
is recorded before it leaves, and one that cannot be recorded is not made.

**The leak the corpus found, kept here because the shape of it will recur.**
`Thorn­bury Holdings` — a soft hyphen, which Word inserts silently and
nothing renders — defeated the term list, produced no residual, reported
`is_clear`, and passed `Redacted.verify`. Four controls, all silent, because
every one of them was matching the visible string and the payload was not made
of it. Worse: the first version of the test **passed**, because it searched the
raw text for "thornbury", which is genuinely absent from "thorn­bury".

Invisible characters are now stripped at the one door every payload comes
through, and `verify` strips independently rather than trusting the code it is
checking. The general lesson is the one to carry: *a check that normalises its
input with the same function as the thing it is checking is not a check.*

**What is still open.** The digest is not a signature: it defends against drift,
not against someone who can already post to the loopback API. A clear payload
can still be sent without previewing it — the binding closes the stale-approval
hole, not the never-looked one. The invisible-character list is a list, and a
codepoint not on it still hides a name.

---

## 5. What has to be built

### 5.1 Drive the tie-out against real material

Everything in §2 was verified against generated decks. The generator builds what
TieOut expects, which makes it the one corpus that cannot test TieOut's
assumptions — the same argument `tests/test_real_world_constructions.py` already
makes about the formatting rules, where a real twenty-slide deck produced 168
findings and all but a handful were the tool disagreeing with itself.

The tie-out has never been pointed at a real deck. What to expect, and what each
would mean:

- **A metric vocabulary that is not in `_MARGINS` or `_MULTIPLES`.** "Contribution
  margin", "EV/EBITDAR", "adjusted EBITDA pre-IFRS 16". The rule falls silent
  rather than reporting wrongly, which is the safe direction, but silence that
  looks like a pass is this tool's worst failure mode. **The check must record an
  unrecognised result label as unchecked**, the way a missing input already is.
- **A table that is neither period-keyed nor entity-keyed.** A three-level header,
  a transposed block, a table whose first column is a footnote marker. The index
  reads the first column as the row label and the first row as the header; nothing
  else.
- **Scope collisions.** Two identically-labelled tables covering different
  entities is CO-001's stated false-positive mode and the one most likely to
  appear at scale.

This has to come before any new rule. A tie-out that cries wolf gets switched
off, and the fifty formatting rules get switched off with it.

### 5.2 Record what a derived check could not verify

`_DerivedRatio` records an unchecked reason where an input is missing or
ambiguous. Three paths do not:

- a result label the rule does not recognise (§5.1);
- `GrowthDoesNotMatch` returning `None` for a bare "CAGR" naming no base metric;
- `BridgeDoesNotCarry` declining a shape whose ends it does not recognise.

Each is a place where "I could not check this" and "this is fine" currently look
the same in the report. The third is the hardest: a table that is not a bridge
must stay silent, so the note can only be attached where the ends *nearly*
matched — which needs a definition of "nearly" that does not itself become a
false positive.

### 5.3 The edit path a person actually takes

`Edit it` opens the run with the counterpart beside it. Two things it does not do:

- **It does not offer the counterpart as the answer.** Clicking the chip to write
  the other figure into this cell is one keystroke of work and is the action
  someone takes nine times out of ten. It is left out because "the other one" is
  a decision, and a one-click decision is one people stop reading. Worth
  revisiting with a real user, not by argument.
- **It does not follow a figure split across runs, and now says so.** "$4" in one
  run and "12m" in the next reads as one number — a reader sees one number, so
  the index records one — and it addresses to the run holding the first digit,
  which is where to point someone. It is not where to *write*: a replacement put
  there would leave "12m" sitting after it, so correcting 412 to 2,100 would
  produce "2,10012m" in a deck about to be sent. `figures.unwritable` refuses it
  with that sentence. What is still missing is the ability to **write** such a
  figure at all, which needs clearing the trailing runs as well as rewriting the
  first, and is a bigger change than it looks: the runs a fix would clear carry
  formatting somebody chose.

### 5.4 Chart values against their workbook

`ChartSeries.values` reads the cache, which is what PowerPoint draws. A cache
that disagrees with the embedded workbook behind it means the chart shows one
thing and its own data says another — a worse defect than anything CO-001 finds,
and invisible to every rule here. The workbook is a `.xlsx` inside the package;
reading it is not hard. Deciding what to say when they disagree is.

### 5.5 Periods that are ranges, and periods that are neither

`parse_period` handles fiscal years, quarters, halves, relative windows and
ranges. It does not handle: calendar versus fiscal year-end (a deck whose FY24
ends in March against one whose FY24 ends in December), stub periods, or a
column headed only "Budget". Each currently reads as no period, which is the
safe direction and costs findings.

---

## 6. Edge cases that will decide whether this is airtight

**Reading figures**
- A figure split across runs — read as one number, addressed to the run with the
  digits, and refused on write with the reason. Both spellings: prose and a table
  cell. See §5.3 for what is still missing.
- A table whose scale is stated in two places that disagree — a caption above
  saying millions and a footnote below saying thousands. The nearest wins, and
  nothing says the two disagreed.
- A cell whose figure carries a footnote marker in a run of its own: read and
  addressed correctly today, and the write keeps the marker. Only because
  `_cell_run_holding` looks for the digits rather than assuming run zero.
- A percentage of a percentage; basis points against percentage points.

**Deciding two figures are the same figure**
- The same metric for the group and for a segment, both correct.
- Restated against unrestated, pro forma against actual, LTM against FY — the
  period carries the basis suffix (`FY2025A` against `FY2025E`) and nothing else.
- A figure on a divider or summary page, which is the case this is most useful
  for and the one most likely to carry a shortened label.

**Acting**
- A fix whose run is inside a group — written correctly, because the write path
  reaches into groups for text; only *geometry* refuses a group.
- A fix that corrects a figure another finding also names; the second finding
  re-measures on the re-audit after every correction.
- Two fixes in flight — the per-deck lock serialises corrections.
- Undo of a figure fix, and of a fix whose correction exposed a new finding.

**Confidentiality**
- A deck re-uploaded between approving and sending — refused.
- A blocklist edited between approving and sending — refused.
- An empty residual list — the digest still binds, because it covers the payload
  text as well as the list.
- A model returning a finding that quotes a placeholder that was never issued.

---

## 7. Order of work

1. **Drive the tie-out against a real deck** (5.1). Everything else is guessing
   until this has been done once.
2. **Record every unchecked path** (5.2). Silence that looks like a pass is this
   tool's worst failure mode and there are three places it still happens.
3. **The chart cache against its workbook** (5.4).
4. **Writing a figure that spans runs** (5.3), if a real deck shows it matters.
   It is refused safely today; writing it needs clearing runs somebody formatted.
5. **Periods** (5.5), as the real deck turns up which ones matter.

---

## 8. How to work on this

Unchanged, because it is what has worked.

**Reproduce against a purpose-built deck before fixing, and check the new test
fails on the previous code.** Three times now a test has passed for the wrong
reason. The most recent was the soft-hyphen leak in §4, which went green while
the name went out — and the one before it was a rule agreeing with a bug in the
index, which is why every rule in §5.4 of the previous plan got a seeded defect
it must catch *and* a clean deck it must stay silent on.

**Drive the app, with intent.** The clean reference deck found four false
positives in the figure index on its way through — a year read as a revenue, a
cumulative row read as a single year, a percentage inheriting a currency scale,
and two slides read as one shape because `uid` is unique per slide and not per
deck. None of them were visible to any test that existed before the rule did.

**Quote a number with the conditions it was measured under.**

The gate is `ruff` **and** `mypy` **and** `pytest` (85% floor, 1,686 tests).

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
  position and size go unchecked.
* Where the reference deck bleeds, a structurally-identified decorative bleed is
  silent, so a misplaced text-free background graphic is not reported.
* `LOCKUP_PLATE_AREA_RATIO` 4.0, `LOCKUP_GAP_HEIGHTS` 1.0, `TITLE_BAND_SHARE`
  0.5, and every other tuned constant, are fitted to two house styles.
* Rotated shapes are drawn, selected and resized correctly by construction and by
  unit test, but no reference deck contains one.
* The render oracle has still never been pointed at a real client deck.
* The model layer composes only offset and scale when flattening a group's
  children into slide space; a rotated group's children are positioned as though
  the group were not rotated. Geometry writes refuse such a group.
* The rail's photographic thumbnail re-renders after a move, a resize, a text
  edit and an undo, but not after `/api/fix`, on the argument that a LibreOffice
  conversion should not sit between a click and its result. Defensible now the
  canvas is drawn from the model, and still an inconsistency nobody chose after
  the surface landed.
* **`tieout.figures` deviates from the previous plan's confidence ladder in two
  places, both recorded in its module docstring.** The period is required where a
  metric is matched by name across two constructions (chart or prose) and not
  where two table cells share a (row, column) key — applying the ladder literally
  would silence the comparison CO-001 makes correctly on an entity-keyed table.
  And the ladder's grade *gates* rather than relabels, because passing it into
  `Rule.finding` is what stops that method consulting the profile's provenance.
* **A slide carrying two tables in different scales and one footnote hands both
  the same unit.** The slide is the boundary for inherited scale, and a deck that
  does this should say so.
* CO-008 and CO-009 pair figures whose periods are both `None`, so a
  comparables table with no period anywhere is still checked for unit and
  as-of-date drift. That is deliberate and is the one place a `None` period
  matches another `None` rather than merely failing to conflict.
