# TieOut — what the tie-out reads now, and what it still cannot

Written 2026-09-23, replacing "tying and ticking, and the confidentiality
boundary", whose queue is spent. §2 accounts for every section of that document
and what it became; it is deleted because it was built, not because it was
abandoned. This is the only build document. Start here.

**Amended later the same day.** §5.1 is done — the tie-out has now been driven
against a deck written the way a real one is written, and §10 is what it said.
It anticipated three things and found ten. A parallel read-only audit of the UI,
fifty findings driven through the browser, is folded in at §11.

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

Two things qualify that, both established on 2026-09-23 by measurement rather
than argument:

1. **It reads figures wherever the deck states them, on decks written the way
   this one's author writes them.** Pointed at a deck written the way a banker
   writes one, it crashed and then disagreed with itself nine times — on a deck
   with nothing wrong in it. All ten are fixed; §10 is the account, and the
   reason it is worth reading is that five of the ten were *silent*.
2. **Detection has outrun action.** A real deck produced twenty findings in the
   app and ten had no way to resolve them. §5.5 closed two of those ten; §11 is
   the queue for the rest, and its first item is one character.

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
figures indexed, §10 corpus     72      58 table, 11 text, 3 chart
tests                        1,714      6 skipped
```

The second figure line is the one that matters. The reference decks index no
prose at all — their only candidate was a year — so every prose path in the
index was, until §10, exercised by nothing but unit tests written alongside it.

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
| A total labelled after its metric | "Total revenue" under three segments is not read as a total, because "Total addressable market" is a metric and widening it reports every TAM/SAM/SOM slide in banking. §9 |
| A table scoped only by its headline | The corner cell is read and the headline is not. "Revenue by Segment" names a metric the deck uses and means nothing of the sort. §9 |

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

### 5.1 Drive the tie-out against real material — **done, and §10 is the account**

`tests/test_real_world_figures.py`. This section is kept as written because it
predicted well and it is worth knowing how well: of the three things it said to
expect, **two happened exactly as described and the third happened for a
different reason.**

- **A table that is neither period-keyed nor entity-keyed** — yes, and not an
  exotic one. A trading-comparables table dates *one* of its columns
  ("LTM EBITDA") and not the others, and the index read that as "the columns are
  the periods" and filed the EBITDA column **upside down**: under the company as
  its metric, with no scope, while the columns either side of it were filed under
  the company as their scope. No row could find its own EBITDA. §10 #7.
- **Scope collisions** — yes, and CO-001's stated remedy ("label the tables'
  scopes") turned out to be a remedy the tool then ignored: a real deck labels
  the segment table's corner cell "Analytics ($m)" and nothing read it. §10 #2.
- **An unrecognised result label recorded as unchecked** — the section asked for
  this because silence looks like a pass. It was the right instinct aimed one
  step short: **five of the ten defects were silent**, and none of them was an
  unrecognised label. CO-005 refused every row of a comparables table because
  `EV ($m)` folded to the label `ev $m`, which no alias matches; CO-006 refused
  every CAGR; and every comparison sentence in the deck fell out of every rule
  without any refusal being recorded at all.

And one the section did not anticipate, which was worse than all of them: the
two oldest rules in the file **crashed**. See §10.

The instruction stands and is now load-bearing rather than aspirational: **this
has to come before any new rule**, and the next rule needs its own pass of it.
A tie-out that cries wolf gets switched off, and the fifty formatting rules get
switched off with it.

### 5.2 Record what a derived check could not verify

**Partly done, and the list was too short.** §10 closed the two that cost most:
CO-005 now refuses a comparables *statistics* row with a reason, and the
refusals that came from a mis-folded label and a mis-oriented table stopped
being refusals at all because the underlying reads were wrong. What §10 shows is
that the dangerous silences are not only the recorded ones — a figure indexed
against a period that matches nothing is silent with no record anywhere.

`_DerivedRatio` records an unchecked reason where an input is missing or
ambiguous. Three paths still do not:

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

1. ~~**Drive the tie-out against a real deck** (5.1).~~ **Done** — §10. It found
   a crash, four false positives and five silences, and everything below is
   ordered by what it turned up.
2. **The UI queue** (§11). This is now first among the outstanding work, and it
   moved there on evidence rather than on taste: a real deck produced twenty
   findings in the app and **ten had no resolution path**. §11.1 is one character
   and stops the canvas — the surface the workflow asks you to judge on — from
   misrepresenting the deck.
3. **Record every unchecked path** (5.2). Still open, and §10 sharpened what it
   means: the recorded refusals were the visible half.
4. **Writing a figure that spans runs** (5.3). A real deck did show it matters —
   the executive summary in §10's corpus splits `$1,196m` across two runs — so
   this is no longer conditional. It is refused safely today.
5. **The chart cache against its workbook** (5.4).
6. **Periods** (5.5). §10 supplied one answer already: a sentence naming two
   periods gives each figure the period next to it, not the range of both.

---

## 8. How to work on this

Unchanged, because it is what has worked.

**Reproduce against a purpose-built deck before fixing, and check the new test
fails on the previous code.** Three times now a test has passed for the wrong
reason. The most recent was the soft-hyphen leak in §4, which went green while
the name went out — and the one before it was a rule agreeing with a bug in the
index, which is why every rule in §5.4 of the previous plan got a seeded defect
it must catch *and* a clean deck it must stay silent on.

It paid again in §10: of the 22 tests added there, **17 fail on the previous
code**, and the five that pass are regression guards for behaviour that had to
survive the change.

**A clean deck a rule is silent on proves nothing on its own.** Every assertion
in §10 — reports nothing, crashed nowhere, left nothing unchecked without saying
why — passes on a tie-out that does nothing at all, which is very nearly what
was there. So each of the nine rules is also given the same deck with one figure
changed and has to catch it. Two of those seeds did not fire at first, and only
one of the two was the seed's fault.

**A refusal is not a pass.** Five of the ten defects in §10 were silent. CO-005
recomputed nothing at all on a comparables table and CO-006 refused every CAGR
in the deck, and both survived the whole suite. Test what a rule *declined* to
check, not only what it reported.

**Drive the app, with intent.** The clean reference deck found four false
positives in the figure index on its way through — a year read as a revenue, a
cumulative row read as a single year, a percentage inheriting a currency scale,
and two slides read as one shape because `uid` is unique per slide and not per
deck. None of them were visible to any test that existed before the rule did.

**Quote a number with the conditions it was measured under.**

The gate is `ruff` **and** `mypy` **and** `pytest` (85% floor, 1,714 tests as at
2026-09-23; 1,686 before §10 added 22 and the merge with the corrections branch
brought the rest).

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

* **A table's scope comes from its corner cell and from nowhere else.** A
  segment P&L headed "Analytics ($m)" is scoped to Analytics and no longer
  contradicts the group P&L; one headed only "$m", whose slide headline is the
  only thing naming the segment, still will. Reading the headline was tried and
  rejected: "Revenue by Segment" names a metric the deck uses and means nothing
  of the sort, and a scope read off a headline is a scope read off a sentence.
  The corner cell is honoured only where it names something the deck's own
  tables already use, so a table headed "Fiscal year" does not take that as a
  scope and stop matching the headline that restates it.
* **A total labelled after its metric is not read as a total.** "Total revenue"
  under three segments is a genuine total and CO-003 skips it, because the label
  has to be a total word exactly or followed by a period. The reason is in the
  rule: "Total addressable market" is a metric and "Total shareholder return" is
  a percentage. CO-003 also skips a column with fewer than three addends, so a
  two-segment table is not checked at all.
* **Prose gives up a figure rather than guess what it measures.** A number in a
  sentence is indexed only where a metric the deck's tables use sits immediately
  before or after it, or where an earlier figure in the same sentence is so
  bound and this one is the same quantity at the same scale. "An enterprise
  value of $4,180m" is therefore not indexed at all, because "enterprise value"
  is a scope in that deck and not a metric. That is the intended direction:
  §10 #3 is what the other one costs.
* **CO-005 refuses on a statistics row** — median, mean, average, high, low and
  their kin — because the arithmetic relating the columns does not hold across
  one. "Total" and "sum" are deliberately not in that set: a total row is
  additive and its margin really is its own EBITDA over its own revenue.
* **A multiple that names no period takes its inputs' periods**, and only inside
  a named scope. That is what lets a comparables row divide an undated EV by an
  LTM EBITDA; it is also a relaxation of the exact-period rule `lookup` exists
  to enforce, and the scope is the only thing keeping it from reaching across to
  another company.
* **`lookup` narrows to the modal stated currency**, and figures stating none
  join it. A deck reporting half in one currency and half in another has no
  majority, and the tie breaks alphabetically — deterministic, so findings
  reproduce, and arbitrary. Such a deck should state a presentation currency,
  and this is not a substitute for it.
* **A bridge is recognised by its ends, and now by two of its periods.** Ends
  naming one metric at two periods with no period on any step between them is a
  bridge; that last clause is what keeps a year-by-year history table from being
  read as one and reported for not summing.
* The tie-out has still never been pointed at a **real client deck**, for the
  same reason the render oracle has not. `tests/test_real_world_figures.py` is a
  deck written to look like one, by the same person who wrote the fixes — better
  than the generator, and not the same thing as the real article.


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

---

## 10. What the tie-out said about a deck that ties out

§5.1, done. `tests/test_real_world_figures.py` builds an eleven-slide sell-side
deck in the shape of a real one — a group P&L, a segment P&L under the group's
own row labels, a trading-comparables table ending in a median row, a
Low/Mid/High valuation, a revenue bridge, a chart, a CAGR, an executive summary
whose prose restates the tables, and a figure restated in a second currency.
Every figure in it agrees with every other statement of it: the margins equal
their inputs, the multiples equal EV over EBITDA, the segments sum to the group,
the bridge carries, the CAGR is the CAGR.

Pointed at it, the tie-out **crashed, and then disagreed with itself nine
times.** The crash first, because nothing in §5.1 anticipated it:

> `FigureIndex.grouped()` keys on `(metric, scope, quantity)`, where `quantity`
> is `str | None`, and the caller sorted the keys. A deck that says "EBITDA of
> $480m" and "8.7x LTM EBITDA" on one page compares `None` with `'x'`, and
> **CO-001 and CO-002 both raise `TypeError` and check nothing.** That is every
> banking deck. It survived the whole suite because the generated decks never
> state one metric in two quantities.

All ten are fixed, each held by a test that fails without the fix.

| # | Kind | What the tool did | Why the generated decks could not show it |
| --- | --- | --- | --- |
| 1 | crash | CO-001 and CO-002 raised and checked nothing | the generator never states one metric in two quantities |
| 2 | false positive | the Analytics segment P&L contradicted the group P&L on every row — CO-001's stated false-positive mode, whose stated remedy is "label the tables' scopes", which the deck did and nothing read | no generated deck has a segment P&L |
| 3 | false positive | "an enterprise value of $4,180m implies 8.7x LTM EBITDA" indexed the EV as EBITDA, so EBITDA was $4,180m on one slide and $480m on the next | the generator's prose names one metric per sentence |
| 4 | false positive | "8.0x - 9.5x", one statement of a range, was two multiples disagreeing with each other and with the 8.7x the deck proposes | the generator writes no ranges |
| 5 | false positive | "GBP 1,510m (US$1,935m)" read as revenue of **minus** 1,935, and CO-008 reported the deck's own stated conversion as unit drift | the generator writes one currency |
| 6 | **silent** | CO-005 refused every row of the comparables table: `EV ($m)` folded to the label `ev $m`, which no alias matches | the generator heads its columns without units |
| 7 | **silent** | behind that, the LTM EBITDA column was indexed **upside down** — filed under the company as its metric with no scope, while the columns either side were filed under the company as their scope. No row could find its own EBITDA | the generator dates every column or none |
| 8 | **silent** | CO-006 refused every CAGR in the deck, because two revenues answered to one period once a segment P&L was in it | as #2 |
| 9 | **silent** | every comparison sentence fell out of every rule: "revenue of $1,935m in FY25A, up from $1,352m in FY23A" gave **both** figures the range `FY2025A..FY2023A`, which agrees with no single period — and recorded no refusal anywhere | the generator's prose names one period per sentence |
| 10 | **silent** | "Group EBITDA of $480m(1)" put a figure of **1** into the index, labelled `ebitda`. It cost no finding only because no real EBITDA is 1; what it cost was `lookup`, which then saw EBITDA stated two ways and refused | table cells have had footnote handling from the beginning; prose never did |

Two more were found while fixing those, and are fixed with them:

* A comparables **median row** is a statistic of the rows above rather than one
  of them. The median multiple is the median of the multiples, not the median EV
  over the median EBITDA — so the moment #6 and #7 were fixed, CO-005 reported
  9.0x against a recomputed 8.0x on the one row of the table that cannot be
  wrong in the way the rule describes. It refuses now, and says why. **This is
  the shape to watch for: fixing a silence exposes the false positive the
  silence was hiding.**
* **CO-007 recognised no bridge at all** on a bridge labelled the ordinary way.
  It looked for "Opening" and "Closing"; a revenue bridge is labelled "FY24A
  revenue | Volume | Price | FX | FY25A revenue".

And one outside the tie-out, in `tieout/text.py`: `%d-%B-%y` was an accepted
date format and `%d-%b-%y` was not, so **"as at 30-Jun-25" — the commonest as-of
form in banking — parsed as no date at all** and CO-009 saw no as-of date on the
slide.

**The lesson, for the next rule.** One crash, four false positives, five
silences. The silences were the expensive half: a false positive is argued with,
a refusal is believed, and a figure indexed against a period that matches
nothing is not even a refusal. §8 carries the rule this produced.

---

## 11. The UI queue, from the read-only audit

A second audit ran in parallel: read-only, driven through the browser as a user
would, against `falcon_seeded.pptx` (20 slides, 20 findings) and then
`reference_clean.pptx` checked against a foreign profile (26 slides, 300
findings collapsed into 24 jobs, which is the only way to see the grouping at
scale). Fifty findings, on the branch `claude/comprehensive-audit-be2242` as
`UI_AUDIT.md`. That document is the detail; this is the queue.

It audited commit `4172c99`, which is behind this branch, and §5.5 has landed
since — `Correction` on the finding, `recell_fix` writing a table cell. **So its
headline finding is partly answered already**: the audit's #8 and #41 say the
two tie-out rules have no path at all, and a table cell now has one. The rest
below stands. Each item says whether it was re-verified against the current head
on 2026-09-23; **findings that need the app driven were not re-driven, and are
carried on the auditor's evidence rather than re-measured.**

### 11.1 The canvas misrepresents the deck — verified live, one character

`tieout_ui/static/index.html` builds a run's colour as
`` `color:#${f.color_hex}` ``, and `canvas_view()` emits `color_hex` **with the
`#` already on it**. Measured rather than read: `canvas_view()` over a test deck
returns `['#000000', '#0F2A4A', '#6B7280', '#C9A227']`. Every run is therefore
styled `color:##0F2A4A`, which is invalid CSS and is dropped, so **no text on
the live canvas is ever drawn in its real colour.** White-on-navy slides render
dark on dark and cannot be read. Shape *fills* go through `hexToRgba()`, which
strips the `#` defensively — which is why the gold logo box is right while the
text beside it is not.

First, because the canvas is the surface the workflow asks the user to identify,
judge and fix defects on, and anything colour-related is unjudgeable while it
holds. It is one character.

### 11.2 The fix affordances still do not follow the remedies the rules compute

Verified in `tieout_ui/view.py`: `MOVABLE_RULES` is `{BR-002, BR-008,
LO-001…LO-005, LO-008}`.

* **BR-006 prints the exact target coordinates** — "Move the page number to left
  877.18pt, top 509.76pt" — and is not in the set, so it offers only *Set aside*
  under the caption "TieOut can see this is wrong and cannot know what is
  right", in the same card that states exactly what is right. The auditor
  cleared it by hand through the canvas: the capability is there and only the
  affordance is missing.
* The same "Yours to fix" sentence covers three different situations: an answer
  nobody can know, an answer the tool knows and will not apply (BR-006), and an
  answer the tool knows and has no control for (LO-007 — where the sentence
  shown is about *moving shapes*, attached to a font size).
* **Ten of the twenty findings on the seeded deck had no resolution path.** Two
  of those ten are now answerable by §5.5; the other eight are this queue.

### 11.3 Findings that name no object

* **HY-001 discards the shape it matched.** `tieout/rules/hygiene.py` emits
  `where=slide.index` only, so a **blocker** — placeholder text left in the deck
  — navigates to the slide and highlights nothing. Verified live. The rule has
  the shape in hand when it matches and throws it away before the UI sees it.
* HY-004 is a property of the file and is attributed to slide 1, badging and
  then ✓-ing a slide with nothing wrong on it. *Not re-driven.*
* LO-004 outlines one of the two overlapping shapes. *Not re-driven.*

### 11.4 Confidentiality and polish in the report

* **The HTML report is opened with the session token in the query string**, so
  the credential authorising reads of live deck material is written into browser
  history by a normal button press — while the main page does substitute-and-clear
  exactly as designed. Verified live.
* **The report is titled with TieOut's internal working copy**, `v1-<name>.pptx`
  (`tieout/report/html.py`), and prints the absolute temp path in its footer.
  The artefact meant to be sent to someone else carries machine-local paths.
  Verified live.
* Every slide row shows an empty "slide image" box although the app has rendered
  thumbnails for all of them at that moment. *Not re-driven.*

### 11.5 Wording that argues against the finding it is attached to

* **LO-002's evidence ends "so the rule cannot fail this deck"**
  (`tieout/learn/derive_layout.py`) and was printed under 13 findings where the
  rule had just failed. True of the deck the profile was learned from, false of
  the deck being checked, and nothing says which. Verified live.
* **TY-007 gives its remedy as a raw regular expression** — "Write it to match
  the house pattern `^(\$)\s?[\d(]`" (`tieout/rules/typography.py`). Verified
  live.
* "Set the size to one of 10.5pt", and a required-boilerplate remedy reading
  "Add the footer text: 'H'" — the single letter of a logo badge. *Not
  re-driven.*

### 11.6 Interaction and state — carried on the auditor's evidence

Not re-driven in this session, grouped by what they cost:

| Cost | Findings |
| --- | --- |
| You cannot see what you are judging | drag moves only the outline (#2); off-slide content is clipped away on the one rule about off-slide content (#34); overflow is painted onto the app background (#36); charts are grey hatching while a good raster sits 200px away (#33); badges cover the content the finding is about (#11) |
| You cannot act | resize is silently dead on shapes whose text overflows (#3); a small shape's handles cover its own text so a page number cannot be edited (#26); *By slide* has no buttons at all (#10) |
| The counts and state lie | *Set aside* leaves "20 things to do" at 20 (#13); undo labels a restored finding **NEW**, which is the product's signal for *newly exposed* (#14); re-running *Check deck* wipes the correction log while the ribbon still claims it (#15); a reload strands the deck and every correction on the server with no way back (#18); the profile chip goes stale on opening a second deck (#42) |
| Feedback is inconsistent | *Export deck* says nothing; *Copy note* reports success with nothing to copy (#23); a failed resize says nothing. Corrections announce themselves well, which is the standard the rest should meet |
| Other | a native `window.confirm()` blocks the whole page for the data-mark guardrail, alone among every confirmation in the product (#24, verified live); the profile picker pre-selects the alphabetically first profile, so one click audits against the wrong client (#30, verified live); ticking *Content review* with no key blocks the offline audit too (#50) |

### 11.7 Deliberate, and to be left alone

`view.py` drops `measured` and `expected` from a grouped finding's header where
the instances differ, which the audit reports as "the user is told the target and
not the defect" (#44). The code says why: six shapes off six different grid lines
have six expectations, and printing the first at the top would be a wrong number
stated confidently. The complaint is fair and the answer is to say *both* — a
range, or a count — not to print one instance's measurement as the group's.
Read §9 before touching it.
