# TieOut — what the tie-out reads now, and what it still cannot

Written 2026-09-23, replacing "tying and ticking, and the confidentiality
boundary", whose queue is spent. §2 accounts for every section of that document
and what it became; it is deleted because it was built, not because it was
abandoned. This is the only build document. Start here.

**Amended later the same day.** §5.1 is done — the tie-out has now been driven
against a deck written the way a real one is written, and §10 is what it said.
It anticipated three things and found ten. A parallel read-only audit of the UI,
fifty findings driven through the browser, is folded in at §11.

**Amended again, 2026-09-27.** §11 is mostly done: the colour bug, the fix
affordances, findings that named no object, the report's confidentiality and
polish, five wording fixes, and the great majority of the interaction/state
group. §11's own text carries what is still open.

**Amended again, 2026-10-01.** §0 is new and comes before everything in §7: a
problem the queue did not name, which decides whether the product is usable.

---

## 0. The missing dimension — before anything in §7

**Every false positive in the latest demo was one defect, and it is not a
threshold.** Three of them, from a real run:

- A deck said "64% of revenue is recurring" on one slide and "9% revenue
  growth" on another. CO-001 reported them as a mismatch. Both are correct, and
  neither restates the other. Reproduced: both index as
  `revenue / FY2025A / %` and become one fact stated two ways.
- A decorative shape bled off the slide edge on purpose. LO-001 reported it.
- A text box sat off the learned grid exactly where the designer put it. LO-003
  reported it.

The index carries metric, scope, period and unit. It carries nothing for **what
kind of claim a figure makes**. "64% of revenue" is a share of a base; "9%
revenue growth" is a change in that base; "24.8% EBITDA margin" is a ratio to
it. All three fold to metric `revenue`, quantity `%`, and become candidates to
compare. The layout rules have the same hole: a shape's position carries no
record of whether anything in the deck suggests the position was chosen.

With no vocabulary for telling these apart, the only lever left is a confidence
threshold — and a threshold can only trade false positives against silence.
That is why it felt unattackable. It is a missing dimension, not a tuning
problem.

**What is built against it, in this order** (the measurement moved first: a
baseline taken after the change it measures is not a baseline):

1. **Precision as a gate.** A corpus of real-shaped decks with *known* defects
   and *known* intentional oddities, scoring every tie-out and layout rule on
   recall and on false positives. Until that number existed every decision in
   this section was taste. `tests/corpus/`, §0.1.
2. **A `kind` on every figure** — level, share, change, rate, ratio, count —
   read from the words around it and from the label, never guessed. Two figures
   of different kinds are never the same fact, and `comparable()` refuses them
   before any value is looked at. A figure whose kind cannot be read is not
   compared, and says so.
3. **Evidence of intent as a first-class field on a shape** — repeated across
   slides, aligned to a consistent line the profile did not learn, encoding a
   value, decorative with no text — unifying the fragments that existed
   (`data_mark_uids`, furniture detection, the structural bleed) rather than
   re-deriving them.
4. **"This is intentional", written to the profile.** A false positive
   dismissed once, by a signature that does not depend on slide position, never
   fires again for that house style. One that returns every run is why tools
   get switched off.

**The trade, stated before it is measured.** Item 2 will make some
currently-caught defects go quiet: a percentage with no word saying what kind it
is stops being compared at all. That is the cost of the dimension, and §0.1
records what it cost rather than arguing it.

### 0.1 The measurement

`tests/corpus.py` builds 16 cases from code: Marlin (§10's deck) clean and once
per tie-out rule with that rule's defect seeded; the generator's clean and dirty
reference decks, the dirty one checked against a profile *learned* from the
clean one; **Heron**, twelve slides of prose against one P&L carrying the demo's
kind collisions and four same-kind contradictions; **Osprey**, a house style and
a later deck in it carrying the demo's bleed and off-grid cases beside real
layout defects; and Kestrel, the real-world constructions deck. Scored over
CO-001…CO-009 and LO-001…LO-003. `tests/test_precision.py` pins the exact list
of misses and of false positives, so any change in either direction fails until
this section and that file are updated together. `python -m tests.corpus`
prints the table.

Every unlabelled finding on the dirty reference deck was traced by hand to
another rule's seed seen from a second angle (TY-006's stray decimal makes the
chart and table genuinely disagree; HY-008's low-resolution logo stops being
recognised as the logo) and is labelled `echo`: true, not this rule's catch, not
scored.

| Measured | Defects | Caught | Recall | False positives |
| --- | --- | --- | --- | --- |
| Baseline, before §0 items 2–4 | 28 | 27 | 96% | 9 |
| CO-001 examines every period (below) | 28 | 28 | 100% | 9 |
| `kind` on every figure (§0.2) | 28 | 27 | 96% | 6 |
| Intent evidence on every shape (§0.3), with its twin added | 29 | 27 | 93% | 0 |

The baseline's nine false positives are exactly the demo's three, each
reproduced three ways: CO-001 on a share against a change, a change against a
share of a base, and a change in revenue against revenue; LO-001 at `info` on a
decorative oval on the cover and on two dividers; LO-003 on one takeaway box set
3.5pt inside its column on three slides.

**The corpus found a silence nobody was looking for.** The one miss is not a
threshold either: CO-001 stops examining a metric the moment its earliest
statement produces one disagreement — across *every period*, not its own. So
"64% … in FY25A" against "9% … in FY25A" disagreeing hid "Revenue grew 17% in
FY24A" against "15% revenue growth in FY24A" entirely. The same shape is
reachable on any deck where a figure with no period disagrees with an early
table cell: every later contradiction under that metric goes quiet. Fixed: every
figure not already weighed against an earlier baseline is a baseline of its own,
held by `test_a_contradiction_in_one_period_does_not_hide_one_in_another`, which
fails on the previous code.

### 0.2 `kind` — what it cost, measured

Every figure now carries one of level, share, change, rate, ratio or count, and
where it was read from (`Figure.kind`, `Figure.kind_from`). `comparable()`
refuses two kinds, and a missing kind, before any value is looked at;
`grouped()` and `lookup()` see only figures whose kind was read, and `lookup()`
only levels — so "Revenue up $146m" no longer leaves FY25A revenue stated two
ways and every derivation needing it refusing.

How a kind is read, nearest evidence first, and nothing past the last line:

1. a rate word in reach of the figure — "grew at a 19.6% **CAGR**", "per site";
2. "of" straight after a percentage — "64% **of** revenue" is a share;
3. a change word, backwards until a word saying the figure is a *state*
   ("grew **to** $412m", "up **from** $1,352m" are levels) and forwards until the
   clause turns to a period or a comparison;
4. the label — "EBITDA **margin**", "Revenue **growth**", "EV**/**EBITDA"; in a
   table either axis, and two axes naming different kinds refuse the cell;
5. the deck's own tables, where they state the metric at that quantity one way
   only — the grounding prose metrics already have;
6. the unit — an amount is a level, a multiple a ratio. A percentage or a basis
   point is four kinds, and the unit says nothing about which.

**The result on the corpus:** CO-001's three demo false positives went to
zero — and a fourth, added while writing this section, stayed at zero: two
shares of one base ("64% of revenue is recurring", "30% of revenue is from
Europe") are declined, because the part is not read. It lost exactly the
defect predicted — "Churn of 4.8%" against a table showing 3.8%, where nothing
names what kind of figure churn is. It is declined
on both statements, in words, through `FigureIndex.unread()` and CO-001's
unchecked record; the gate pins that it is *declined*, not silent. On the
marlin, reference-clean and reference-dirty decks no figure lost its kind, so
nothing there went quiet: every table percentage is a margin, a CAGR or a
movement column, and every prose figure there is an amount, a multiple, a
labelled margin or a CAGR. That says the generated decks are kind-tidy, not
that real ones are: a KPI table of bare percentages — churn, retention,
utilisation, conversion — is the shape that goes quiet, and the next real-deck
pass is where to measure how common it is.

### 0.3 Intent evidence — what it cost, measured

`placement_intent(deck, profile)` in `tieout/rules/layout.py` gives every shape
a `Placement`: the evidence (`tieout/model/intent.py`) that its position was
chosen. The fragments were not re-derived — furniture is `furniture_for`, the
bleed is `is_decorative_bleed`, and the slide's own alignment lines, evenly
spaced runs and data marks are LO-003's own helpers, called exactly as LO-003
called them. LO-001, LO-002 and LO-003 now read that one answer. Every silence
it causes is written to `AuditResult.excused` with the evidence named, the
counterpart of `unchecked`: "I looked, and the deck says this was meant".

One piece of evidence is new: **the same element placed identically on
several slides** — the whole box on two, or both edges of one axis on three,
within 0.25pt. Copy-paste and layouts land on the same EMU, and a hand nudge
does not repeat itself to a quarter of a point. At the grid's 2pt a box nudged
to 493pt on one slide joined a takeaway at 493.5pt on three others, and the one
real mistake on the deck was excused by its neighbours.

What each rule does with it:

- **LO-001 and LO-002** — a decorative bleed's structure alone still reports at
  `info`: a misplaced background panel has the same structure. A second piece
  of evidence excuses it — the reference deck bleeds (as before), the *whole*
  box recurs at the same place, or it is on a title or divider slide. A panel
  the width of its column shares that column's edges with every body box and
  is not excused by them; the corpus caught that one on the first run.
- **LO-003** — an edge in the near-miss window is excused where aligned,
  spaced, plotted or repeated evidence accounts for its axis. It used to skip
  those axes before measuring; it now measures first, so every excused edge is
  recorded.

**The result on the corpus:** LO-001's three and LO-003's three false
positives went to zero. The stretched panel stayed reported. And the cost was
measured rather than argued, by building the takeaway's twin — a callout
dragged 3.5pt off its column and copied to two more slides. In the file it *is*
the takeaway: one placement, repeated. It is read as meant, and says so on
every slide. Repetition cannot tell a copied mistake from a choice, and nothing
in the file can; that is what §0's item 4 is for, in the other direction.

### 0.4 "This is intentional" — dismissed once, for the house style

Every finding carries a `signature`: what it is about, never which slide. The
rules §0 is about state their own — LO-001 and LO-002 the shape's kind, whether
it carries text and its box to the point; LO-003 each edge and the line it
missed ("left 493pt against 490pt"); CO-001 and CO-002 the fact and both values
as written. Everything else gets the measurement, the expectation and the box,
with slide references stripped. A clustered finding carries one line per
instance and is covered only when all of them are.

`Profile.intended` holds the declarations. `run_rules` moves a covered finding
to `excused`, naming the declaration and its note, so a wrong declaration is
findable in every report. They survive `learn` (carried across the overwrite),
`learn --add` (the union) and the UI's learn. Three ways in: **This is
intentional** beside Set aside in the note, with Undo; the House style tab's
"Declared intentional" list, where any one is withdrawn; and
`tieout check --intended RULE@slideN[:Shape] --intended-note TEXT`.

Measured by construction rather than by the corpus, because the corpus scores
one deck at a time and this is about the second one: a box declared intended on
slide 3 of one deck is not reported on slide 6 of another deck in the house
style, while a box 6pt away from it still is (`tests/test_intended.py`).

**What it does not do.** It cannot help a miss — the copied callout in §0.3 is
excused by evidence, and no button un-excuses it; that would need a "this is
*not* intentional", which nothing asks for yet. And a declaration is only as
narrow as its signature: LO-003's is the edge and the line, so declaring one
inset box intended excuses every shape with that edge at that position in the
house style. That is the point for a takeaway column and the wrong answer for a
one-off, and the House style list is where to see which it was.

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
2. **Detection had outrun action.** A real deck produced twenty findings in the
   app and ten had no way to resolve them. §5.5 closed two of those ten and PR
   #11 closed most of the rest; §11 carries what is still open.

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
rules defined                   53
rules on by default             51
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
| Cross-references | "see page 12" pointing at page 14 |
| A figure only ever stated once, in prose, in a deck with no tables | Nothing to tie it to, by construction |
| Two periods where the deck writes one both ways | "LTM" and "LTM September 2026" do not match, deliberately: conflating them would invent findings between LTM Sep-25 and LTM Sep-26 (§5.5 made "LTM Sep-25" keep its anchor too) |
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

**Done, 2026-10-01** — all three paths below now record a refusal, and `kind`
closed a fourth nobody had listed. `tests/test_unchecked_paths.py`; five of its
six tests fail on the previous code, and the sixth is the guard (a correct
CAGR in prose stays silent).

- A labelled ratio CO-004 or CO-005 has no derivation for ("Adj. EBITDA
  margin", "Net debt / EBITDA") is declined once per label, not per cell.
  Labelled only: a multiple in prose binds to its denominator ("8.7x LTM
  EBITDA" binds to EBITDA), so its metric says nothing about what was divided.
- A bare "CAGR" naming no metric is declined.
- "Nearly a bridge" is defined as one end naming itself as an end ("Opening",
  "Closing") and the other naming nothing. Declined, never reported.
- **The fourth:** a CAGR stated in prose — "Revenue grew at a 19.6% CAGR
  between FY23A and FY25A" — was never recomputed, because CO-006 looked for a
  growth word in the *label* and prose has none. `kind` now says it is a rate of
  revenue over that span, and CO-006 recomputes it; a rate in prose stated for
  one period rather than a span is declined.

**Found while doing it, not fixed:** the Marlin valuation table ("Valuation
($m) | Low | Mid | High") is indexed with the *case* as the metric — Low, Mid,
High — and the row as the scope, so its "Implied EV/EBITDA" row is invisible
to CO-005 and nothing records that. Turning it the right way up is an index
orientation change (a case column is a scope, not a metric), and doing it
alone only converts the silence into a refusal: the row's EBITDA is unscoped
and the case-scoped lookup will not find it. Both halves are the next change
to `figures.py`, and §10's corpus is where to check it.

The account as it stood before:

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

**The write half is done, 2026-10-01, and it was worse than this section
said.** Every derived **Fix it** addressed its figure by the *run* holding it
and replaced that run. A sentence is usually one run, so correcting "EBITDA
margin of 25.8% in FY25A." wrote **"24.8%" over the whole sentence**; no
test caught it because every derived-fix test corrected a table cell, whose
run usually is the figure. And a cell reading "26.8%*" with its marker in the
same run lost the marker. A figure now carries its `span` — its characters in
its paragraph — and `_write_span` replaces exactly those, across as many runs
as they cross: the replacement in the first run's formatting, the rest of the
figure removed from the runs after it, every run keeping its own formatting and
everything else it held. "$4" + "12m in FY25A" corrected to 2,100 reads
"$2,100" + "m in FY25A". Before writing, the characters are compared with what
the check read, and a deck edited in between is refused rather than written by
position. `tests/test_span_writes.py`; all four fail on the previous code.

A span crossing a line break or a field is refused, with the reason. The canvas
editor's own text edit still replaces a whole run, which is right there: the
person typed the run.

What follows is the section as it stood; the second bullet is what is now done.

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

**Done, 2026-10-01, as CH-006.** `tieout/model/workbook.py` reads one range of
the embedded workbook — the range each series' `c:numRef/c:f` names — with the
standard library and lxml, and `ChartSeries.workbook_values` carries it beside
the cache. CH-006 (chart, major) reports every point where the cache and the
workbook disagree, a gap on one side and a number on the other included,
naming both and the cell. What to say was the hard part, and the answer is
not which is right: it is what happens if nothing is done — PowerPoint redraws
the chart from the workbook the next time anyone opens Edit Data. Driven on
every real-shaped deck in the corpus first: silent on all of them, with no
refusals (python-pptx writes the two in agreement). Declined, out loud: data
linked to a file outside the deck, a range of several areas, a sheet the
workbook lacks. `tests/test_chart_workbook.py`. The tie-out rules still read
the cache, which is what the reader sees.

`ChartSeries.values` reads the cache, which is what PowerPoint draws. A cache
that disagrees with the embedded workbook behind it means the chart shows one
thing and its own data says another — a worse defect than anything CO-001 finds,
and invisible to every rule here. The workbook is a `.xlsx` inside the package;
reading it is not hard. Deciding what to say when they disagree is.

### 5.5 Periods that are ranges, and periods that are neither

**Done for what was listed, 2026-10-01, and the listing was wrong about the
direction.** Driven through `parse_period`, three of these did not read as no
period — they read as a *different* one, which invents findings rather than
costing them: "9M 2025" and "6M FY25" as the full year FY2025, "2025 Budget"
as the plain FY2025 it would be compared against, and "LTM Sep-25" as bare LTM,
so LTM Sep-24 and LTM Sep-25 were one period. Each is now its own: `9M-2025`,
`FY2025B` (budget, plan), `FY2025E` (forecast, estimate), `FY2025A` (actual),
`LTM-SEP-2025`, and `CY2024` for a calendar year, never equal to FY2024. "$9m
2025" is nine million in 2025, not a stub. A column headed only "Budget" still
reads as no period, because it is. `tests/test_periods.py`; every behavioural
test fails on the previous code, and one is a gain — "9M25" read as no period,
so two statements of one stub were never compared.

**Still not handled, deliberately:** a deck stating its fiscal year ends in
March reads a bare "2024" as FY2024 like any other deck. Re-keying every bare
year on a year-end the deck states once, in a footnote, is the inference this
module refuses everywhere else; the real-deck pass is where to find out
whether decks state it in a way worth reading.

The section as it stood:

`parse_period` handles fiscal years, quarters, halves, relative windows and
ranges. It does not handle: calendar versus fiscal year-end (a deck whose FY24
ends in March against one whose FY24 ends in December), stub periods, or a
column headed only "Budget". Each currently reads as no period, which is the
safe direction and costs findings.

---

## 6. Edge cases that will decide whether this is airtight

**Reading figures**
- A figure split across runs — read as one number, addressed to the run with the
  digits, and written by its span across every run it crosses (§5.3). A span
  crossing a line break or a field is refused with the reason.
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

0. **The missing dimension** (§0) — before everything below. Precision corpus
   first, then `kind`, then intent evidence, then "this is intentional".
1. ~~**Drive the tie-out against a real deck** (5.1).~~ **Done** — §10. It found
   a crash, four false positives and five silences, and everything below is
   ordered by what it turned up.
2. ~~**The UI queue** (§11).~~ **Done, mostly** — §11.1–11.5 and the great
   majority of §11.6 landed in PR #11. §11's own "Still open" list is the
   remainder: #18 (reload recovery), #26 (small-shape resize handles), #33
   (chart raster), #36 (overflow's visual distinction from chrome), and
   #45–#47 from the grouped-findings run.
3. ~~**Record every unchecked path** (5.2).~~ **Done**, with one new silence
   found and recorded in §5.2 (a Low/Mid/High table's implied multiples).
4. ~~**Writing a figure that spans runs** (5.3).~~ **Done** — and the same
   change fixed a worse defect: every derived Fix it in prose overwrote the
   whole sentence.
5. ~~**The chart cache against its workbook** (5.4).~~ **Done** — CH-006.
6. ~~**Periods** (5.5).~~ **Done** — stubs, budgets, anchored LTM windows and
   calendar years, each of which had been read as a different period.
7. **§11's remainder**, listed in §11 itself — smaller now, and worth clearing
   before the next real-deck pass finds new evidence to reorder against.

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

* **A percentage nothing names the kind of is not compared** (§0.2). "Churn"
  stated as 3.8% in a table and 4.8% in a sentence is a real contradiction the
  tool declines, and says so; inventing the kind is what reported a share of
  revenue against a growth in it. The same holds one level down: "64% of
  revenue is recurring" is bound to its *base*, and the part ("recurring") is
  not read, so it is declined too — otherwise it and "30% of revenue is from
  Europe" are one fact. A share that names its part first ("EBITDA at 24.8% of
  revenue", or a table row under a "% of total" header) is compared.
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
* A structurally-identified decorative bleed is silent where something
  corroborates it — the reference deck bleeds, the whole box recurs at the same
  place, or it is on a title or divider slide (§0.3) — so a misplaced
  text-free background graphic in any of those places is not reported. It is
  recorded in `excused`, with the evidence.
* **Repetition reads a copied mistake as a choice** (§0.3). A box dragged off
  the grid and then copied to two more slides is excused by its own copies.
  The corpus carries the case and pins the miss.
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

## 11. The UI queue, from the read-only audit — **11.1–11.5 and most of 11.6 done**

A second audit ran in parallel: read-only, driven through the browser as a user
would, against `falcon_seeded.pptx` (20 slides, 20 findings) and then
`reference_clean.pptx` checked against a foreign profile (26 slides, 300
findings collapsed into 24 jobs, which is the only way to see the grouping at
scale). Fifty findings, on the branch `claude/comprehensive-audit-be2242` as
`UI_AUDIT.md`. That document is the detail; this is the queue.

It audited commit `4172c99`, which is behind this branch, and §5.5 had already
landed by the time this section was written — `Correction` on the finding,
`recell_fix` writing a table cell, closing the audit's #8 and #41. §11.1–11.5
and the great majority of §11.6 landed in PR #11, on 2026-09-27, without
re-driving the app: each fix is a source-level correction to a defect the
audit named exactly, verified by reading the code the bug was in rather than
by clicking through a browser. No test in this suite drives one yet, so the UI
changes carry source-level regression tests (a string that must or must not
appear in the served page) rather than behavioural ones; §11.6's own note on
that gap is below.

**Still open**, in order of what is left to do:

* **#18** — reloading the page strands the deck and every correction on the
  server, with no way back. The server already holds enough to recover from
  (`GET /api/decks/{id}` exists, and `deck.rejected` persists there) — what is
  missing is purely client-side: remembering `{deck_id, client}` across a
  reload and re-attaching to a deck the server still has, rather than showing
  "No deck open".
* **#26** — a small shape's resize handles cover its own text, so a page
  number cannot be edited. Every handle is drawn whenever a shape is in the
  editor, regardless of whether *this* shape's own rules permit that axis;
  narrowing that needs per-axis capability threaded into `S.edit`, which
  nothing carries today.
* **#33** — charts render as grey hatching while a rendered raster sits 200px
  away in the rail. Solvable (crop the slide thumbnail to the chart's own
  bbox) but not attempted.
* **#36** — overflowing text is drawn correctly outside its shape's own box
  (deliberate, and right — see the note on `.shape .text` in
  `tieout_ui/static/index.html`) but with no visual distinction from the
  canvas it spills onto, so it can read as chrome rather than deck content. A
  design question (how to mark it without implying it is clipped) more than a
  bug.
* **#44–#49**, from the 26-slide grouped-findings run: #44 is answered by
  §11.7 below (deliberate); #45 (Move it on a grouped finding opens the first
  place only), #46 (three counts of three different things shown with
  nothing relating them) and part of #47 (a remedy phrased as an instruction
  to TieOut for a rule this queue's §11.2 wording fix does not cover) are
  still open. #48 (`FontRole.describe()`'s "one of" phrasing) and #49
  (LO-002's evidence) are done, in §11.5.

Everything else the audit numbered — the colour bug, the fix affordances, the
document-level findings, the report's confidentiality and polish, the wording
fixes, and the interaction/state group (drag, resize, the native dialog, the
counts that lied, the profile picker, the rail's blanking, By slide's missing
buttons, the reachability of off-canvas content) — is done. `git log` on this
branch names each one by its audit number.

### 11.7 Deliberate, and to be left alone

`view.py` drops `measured` and `expected` from a grouped finding's header where
the instances differ, which the audit reports as "the user is told the target and
not the defect" (#44). The code says why: six shapes off six different grid lines
have six expectations, and printing the first at the top would be a wrong number
stated confidently. The complaint is fair and the answer is to say *both* — a
range, or a count — not to print one instance's measurement as the group's.
Read §9 before touching it.
