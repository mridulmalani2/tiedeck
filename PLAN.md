# TieOut — what it is, what it has to become, and the work between

Written 2026-09-20, replacing `HANDOFF.md`, whose queue is spent. This is the
only build document. Start here.

It exists because the tool passes its own tests, reports its own decks cleanly,
and still is not usable for the job it is named after. Everything below was
found by driving the running app the way a banker would, not by reading code.
Where a claim has a reproduction, the reproduction is given.

---

## 1. The one-sentence version

**TieOut is architected as *audit and report*. It is being asked to be *audit,
correct, and verify*.** Those need different foundations, and the difference is
concentrated in one place: the slide canvas is a static picture.

Everything in §3 follows from that.

---

## 2. What is genuinely good, and must survive

Do not rebuild these. They are the reason the tool is worth finishing.

* **The rule engine.** 41 rules across layout, brand, typography, hygiene,
  chart and consistency. On the client's own deck `check(D, learn(D))` reports
  nothing; on a deck with twenty seeded defects it reports each one, on its own
  slide, and nothing else.
* **Learning a house style from one reference deck**, with evidence attached to
  every derived value and an honest `not_learned` list for what the deck did
  not settle. This is the product's real idea.
* **The separation of severity from confidence**, and the `not checked` block.
  "I could not verify this" and "this is fine" do not look the same.
* **The air gap.** No network capability in the runtime at all, enforced by an
  AST walk over every module. Keep it. It is why this tool is allowed near a
  live deal, and it rules out webfonts, CDNs and telemetry forever.
* **The correction ledger.** Every fix is a new version of the file with a log
  entry, and undo removes one. The bones of an audit trail are already here.

---

## 3. What is broken, with reproductions

All reproduced on 2026-09-20 against `falcon_seeded.pptx` checked against a
profile learned from `falcon_clean.pptx`.

### 3.1 You cannot see your own correction — the blocking defect

Select BR-004 ("Colour off the learned palette", slide 6), press **Fix it**.

```
major count      12  ->  11          the correction landed in the file
canvas <img> src  /api/thumbnails/<id>/6?t=<token>
                  ... identical before and after
markers on screen 1                  still boxing the shape that is now correct
```

The slide is **never re-rendered after a correction**, so the reader is still
looking at the old colour, with a red box around it, with no way to clear the
box. This is deliberate — `tieout_ui/server.py:_reload()` says so:

> Thumbnails are deliberately left as they were. Re-rendering after every fix
> would put a LibreOffice conversion between a click and its result, for images
> that a recoloured fill or a deleted note barely changes.

That is a sound trade for a tool that reports. It is fatal for a tool that
corrects: **the verify step of the loop does not exist.** The rail thumbnails
are stale for the same reason. The canvas URL already carries `&v=${...
thumbnails.version || 0}` — a version that nothing ever increments.

### 3.2 The marker has no dismiss

Click a finding: one `.mark` overlay is drawn. Then:

| action | markers after |
| --- | --- |
| press Escape | 1 |
| click the canvas gutter | 1 |
| click a different slide | 0 |

There is no way to say "I have seen this, show me the slide". Combined with
3.1, a fixed finding stays boxed until you navigate away and come back — and
when you come back the picture is still wrong.

### 3.3 The canvas is a picture, so nothing on it can be touched

| attempted | result |
| --- | --- |
| click a shape on the slide | nothing; no selection |
| double-click a text box | nothing; `contenteditable` count 0 |
| drag a corner to resize | no handles exist |
| drag a shape | only via **Move it**, which draws one overlay rectangle |

**Move it** does work — the outline tracks the pointer (x 289.5 -> 229.7 on a
60px drag) and arrow keys nudge — but it is a rectangle floating over a stale
raster. The shape does not appear to move because the shape is not there; it is
baked into a PNG. That is the whole of "it doesn't show it move in the UI".

### 3.4 A regression this branch introduced, and fixed

Unifying the two note modes onto one CSS class put the by-fix cards under a
click handler written for the by-slide cards, which read a `data-slide` they do
not carry. Every click ran `select(NaN)`: the canvas blanked to a broken-image
icon reading "Slide NaN", the status bar read "Slide NaN of 20", and the browser
asked the server for thumbnail `NaN` (two 422s per click).

Recorded because of how it happened, not because it was hard to fix: a class
was renamed for a visual reason and the behaviour bound to that name was never
re-tested. **No screenshot would have caught it. One click would have.**

---

## 4. The loop the product is actually for

Tying out a deck is one loop, repeated until the count is zero. TieOut does
three of its six steps.

| # | step | today |
| --- | --- | --- |
| 1 | **Find** — what is wrong, worst first | works |
| 2 | **Locate** — see it on the slide | partial: marks a stale picture |
| 3 | **Understand** — what was measured, expected, and why | works, and is the best part of the tool |
| 4 | **Decide** — fix, move, accept, or defer | works |
| 5 | **Act** — change the deck | partial: 5 rules auto-fix, position is manual, text is impossible |
| 6 | **Verify** — see that it is now right | **missing** |

Step 6 is not a feature. It is the step that makes the other five trustworthy.
A banker who cannot see the correction will open the file in PowerPoint to
check — and once they have PowerPoint open, they will do the rest there too.

---

## 5. What has to be built

### 5.1 A live slide surface — the one architectural change

Replace the rendered PNG with a **rendering of the shape model** the tool already
has: `SlideModel` carries every shape's resolved geometry, fill, line, and text
runs with fully resolved fonts. Draw it, do not photograph it.

- **DOM/SVG, not canvas.** Text must be selectable, editable and measurable, and
  accessibility and hit-testing come free. One absolutely-positioned element per
  leaf shape, positioned in slide points scaled to the viewport.
- **The model is the single source of truth.** A correction mutates the model;
  the surface re-renders from it; the audit re-runs against it. No conversion
  step between a click and its result — which is exactly the objection
  `_reload()` raises against re-rendering, answered by removing the renderer
  from the loop rather than by accepting a stale image.
- **Keep LibreOffice for fidelity, not for interaction.** It stays as the
  rendering oracle (`tests/test_render_oracle.py`) and as an optional
  "show me what PowerPoint will show" preview. It must not be in the edit path.
- **Degrade honestly.** Where the surface cannot draw a shape faithfully —
  SmartArt, charts, embedded media, effects — draw a labelled placeholder at the
  correct geometry and say so, rather than drawing something subtly wrong. The
  tool's whole posture is that an unverifiable thing must look unverifiable.

This is the large piece. Nothing else in this document is worth doing first,
because everything else is either blocked by it or cosmetic beside it.

### 5.2 Direct manipulation on that surface

Once shapes are real elements: click to select; handles to resize; drag to move;
double-click to edit text; arrow keys to nudge; Escape to deselect. Snapping to
the learned grid already exists and should be reused, along with the existing
refusal to auto-place anything whose correct position is a judgement.

**Guardrail, non-negotiable:** a shape whose position encodes a value — a bar
in a football field, a dot in a quadrant — must not be silently draggable. That
finding already exists (`_data_series_axes`); the editor must consume it, or the
first thing this tool ships will be a way to quietly restate $815M as $821M.

### 5.3 Close the loop

- Bump the thumbnail version on every correction, and re-render the affected
  slide and its rail thumbnail. With 5.1 this is free.
- Clear the marker on Escape, on canvas click, and on the finding being resolved.
- After a correction, say what changed *on the slide*: flash the shape, not just
  the count.
- The delta block (what a correction fixed, and what it exposed) already exists
  and is good. Surface it on the slide as well as in the note.

### 5.4 Editing text at all

There is no text editing today. It is needed for the largest class of findings
the tool reports and cannot act on: draft markers, non-canonical terms,
capitalisation, currency notation, number formatting. Each of those is a
substitution with one correct outcome and no judgement — the same test
`tieout_fix` already applies to decide what it may correct automatically.

Editing a run must preserve its resolved font, its language, and its place in
the paragraph, and must write back through the same versioned-file path as every
other correction so undo and the audit trail keep working.

---

## 6. Edge cases that will decide whether this is airtight

Not a wish list. Each of these is either already a known failure or one line of
reasoning from becoming one.

**Geometry**
- Shapes inside groups: child coordinates are group-relative; the loader
  flattens them, and the editor must write back into group space.
- Rotated shapes: the overlay is an axis-aligned rectangle over a rotated shape.
  Selection, handles and snapping all need the rotated box.
- Shapes off the canvas, or wholly behind others; zero-area and 1px shapes.
- A slide whose size is not 960x540 (the client deck is 959.976 wide).

**Content the surface cannot own**
- Tables and charts: cell-level and series-level edits are out of scope, so
  select must stop at the frame and say why.
- SmartArt: geometry lives in a diagram part the model does not read. Placeholder
  and a stated refusal — never a guess.
- Pictures and embedded media: movable and resizable, never editable.

**The correction path**
- Two corrections in flight (double-clicked **Fix it**): serialise them.
- Undo after a re-check; undo of a correction that exposed a new finding.
- A correction that fails mid-write: the version file must not be left behind
  (already handled in `/api/fix` — keep it).
- Export while a correction is in flight.
- A profile edited between check and fix.

**The environment**
- No LibreOffice, or a core-only install with no Impress filter: reports
  "source file could not be loaded", which reads like a bad deck. 5.1 removes
  this from the critical path; until then it must be said plainly in the UI.
- No Calibri/Cambria metrics: ink is bounded rather than measured, and LO-001
  over-reports. Already visible as `medium` confidence — keep it that way.
- Decks of 100+ slides: rendering, memory, and rail virtualisation.
- Two browser tabs on one session; a deck re-uploaded mid-session.

**Data integrity**
- `cNvPr@id` is not unique in practice — fixed, and the reason `ShapeRef.uid`
  exists. Anything new that keys on identity uses `uid`, never `shape_id`.

---

## 7. Order of work

1. **Live slide surface** (5.1). Everything else waits on it.
2. **Selection and direct manipulation** (5.2), including the data-mark
   guardrail.
3. **Close the loop** (5.3) — re-render on correction, dismissable markers.
4. **Text editing** (5.4).
5. **Edge cases** (§6), as each surface lands rather than as a phase.

Two things from the old queue are still open and still worth doing, but after
the above: point the render oracle at a real deck, and get a deck whose tables
restate figures so CO-001/002/003 are proven on real material.

---

## 8. How to work on this

The method that has worked, and the two ways it has failed.

**Reproduce against a purpose-built deck before fixing, and check the new test
fails on the previous code.** Twice a test has passed for the wrong reason.

**Drive the app, with intent.** Both defects in §3.4 and §3.1 were invisible to
1,409 passing tests and to every screenshot taken of the panel, because a
screenshot proves what a thing looks like and not what it does. Click the
button. Then click the button again after the thing it changed.

**Quote a number with the conditions it was measured under.** "All twenty seeded
defects are caught" is true without Calibri-metric fonts and false with them —
and the honest number is the one with the fonts.

The gate is `ruff` **and** `mypy` **and** `pytest` (85% floor, currently ~93%,
1,409 tests). `pyproject.toml` already passes `-q`; adding another hides the
summary and makes a green run look like a silent one.

```
.venv/bin/ruff check . && .venv/bin/mypy . && .venv/bin/python -m pytest
./run.sh                 # the UI on http://127.0.0.1:8765/
```

For thumbnails and the render oracle:

```
apt-get update
apt-get install -y --no-install-recommends libreoffice-impress
apt-get install -y --no-install-recommends poppler-utils
apt-get install -y --no-install-recommends fonts-crosextra-carlito fonts-crosextra-caladea
```

Install them one at a time: a single `apt-get` aborts the whole transaction on
one 404 and leaves a core-only LibreOffice behind, which fails in a way that
reads as a bad deck.

Client decks are never committed; CI asserts no `.pptx` is tracked.

---

## 9. Standing limitations to carry forward, not rediscover

* Ink is a bound, not a measurement, wherever Carlito/Caladea are absent. The
  bounds are sound for overflow (they under-report) and unsound for LO-001,
  which over-reports a frame whose text is on the canvas.
* A logo drawn as text alone, with no badge and no image, is not learned, so its
  position and size go unchecked. Deliberate: nothing separates it from any
  other repeated line.
* Where the reference deck bleeds, a structurally-identified decorative bleed is
  silent, so a misplaced text-free background graphic is not reported.
* `LOCKUP_PLATE_AREA_RATIO` 4.0, `LOCKUP_GAP_HEIGHTS` 1.0, `TITLE_BAND_SHARE`
  0.5, and every other tuned constant, are fitted to two house styles.
* **`LO-006` is unreachable as documented.** The learner writes it into every
  profile's `rules.disabled`, and an explicit disable beats the `--rules LO-006`
  opt-in the README offers. Text-overflow detection is effectively off and the
  documented way to turn it on does nothing. Small, and worth fixing early.
