# TieOut — read-only end-to-end UI/UX QA audit

**Date:** 2026-09-20
**Build audited:** commit `4172c99` (merge of PR #7), served by the already-running
`tieout-ui` instance on `http://127.0.0.1:8765/` (process launched from
`/Users/mridulmalani/tiedeck/tiedeck`).
**Deck:** `falcon_seeded.pptx` (20 slides) — the seeded deck from `~/Downloads`.
**Profile:** existing client profile `falcon` (v1), derived from
`Project_Falcon_Halyard_Clean_v2.pptx`.
**Driven through:** Chrome (Claude-in-Chrome connector), as a user would — upload,
choose house style, check, then click every finding and attempt every offered action.

Nothing in the application, its profiles, its code or the seeded deck was modified.
The corrections described below were applied *inside the running session* (TieOut
writes each correction to a new copy in its own temp directory and never touches the
upload); the only file written outside that is `~/Downloads/falcon_seeded (corrected).pptx`,
produced by pressing **Export deck** as part of testing the workflow.

---

## 1. What was tested

The full six-step workflow, twice (a second session was needed after a native
`window.confirm()` dialog locked the first page — see Finding #24):

1. **Deck** — drag/open `falcon_seeded.pptx`; 20 slides ingested, thumbnails rendered
   by LibreOffice.
2. **House style** — *Use existing profile* → `falcon`; panel inspected (not saved —
   saving writes `profiles/falcon.yaml`, a project file, so it was deliberately left
   alone).
3. **Check** — 20 findings: 4 blocker, 12 major, 4 minor, from 41 rules across 20 slides.
4. **Review** — every one of the 20 findings clicked; navigation, highlight, badge,
   available controls and the "Where" disclosure inspected on each.
5. **Fix** — every *Fix it* (5) and every *Move it* (3) exercised; in-place text editing
   used as a manual fix on three findings; *Set aside*, *Put it back*, ribbon *Undo* and
   `Cmd+Z` exercised.
6. **Share** — *Copy note*, *HTML report*, *Export deck*; exported file re-opened with
   python-pptx to confirm the correction actually landed in the file.

Also exercised: *Content review* (redaction preview only — **nothing was sent**, no key
entered), *By fix* vs *By slide* views, direct shape selection on the canvas, drag,
resize, arrow-key nudging, snap-to-grid, page reload, and a second browser tab.

**A second run was made deliberately**, to exercise behaviour the seeded deck cannot show:
`reference_clean.pptx` (26 slides, from `tieout scaffold-reference`) checked against the
same `falcon` profile. Because that deck belongs to a different house style it produces
**300 findings collapsed into 24 jobs** — which is the only way to see the grouped-finding
UI, the volume behaviour and the counts at scale. Findings #42–#49 come from that run; the
deck was then closed and the seeded deck reloaded, so the application is left as it was
found (falcon_seeded.pptx, profile falcon, 20 findings, no corrections).

**Findings that resolve cleanly, end to end (worth recording as the baseline):**
HY-004 (docProps), HY-002 (speaker notes), BR-004 (recolour), TY-005 (canon term),
TY-001 (curly quotes) all apply, re-audit, decrement the count and mark the slide ✓.
LO-001 and LO-004 resolve through the position editor; the arrow keys are exact
(6 × Shift+Right moved a shape from X 817.2 to X 877.2 — exactly +60.0pt). Export
contains the applied edit. Undo and `Cmd+Z` both work from any tab.

**Ground truth used:** the deck's own XML/shape model, read with python-pptx and with
TieOut's own `tieout_ui.canvas.canvas_view()` offline, so that "what the canvas draws"
could be compared against "what the file says" without guessing.

---

## 2. The three patterns behind most of the findings

1. **The canvas is not a faithful picture of the deck, but it is the only surface the
   workflow gives you.** Text colour is never applied (#1), dragging does not move
   anything (#2), charts are grey hatching (#33), off-canvas content is clipped away
   (#34) and overflow is painted onto the application background (#36). Several
   findings are therefore *invisible at the exact moment you are asked to judge them*.
2. **The fix affordances do not line up with the remedies the rules compute.** A rule
   that prints exact target coordinates gets no *Move it* (#6); a rule whose remedy is a
   mechanical recase gets no *Fix it* (while a sibling rule does); the two tie-out rules
   that are the product's reason to exist get no path at all (#8). 10 of the 20 findings
   have no in-app resolution path.
3. **Locating information exists but is hidden in the view that can act, and shown in
   the view that cannot.** *By slide* names the shape, the column and both sides of an
   overlap; *By fix* — the default — hides the shape behind an unmarked toggle and drops
   the rest, and *By slide* has no buttons (#9, #10).

---

## 3. Findings

### Finding #1 — The slide canvas draws every text run in a fallback colour; white text on dark slides is illegible
**Category:** Bug / Rendering
**Issue:** No text on the live canvas is ever drawn in its real colour. Slides whose
text is white on navy render as near-black on navy and cannot be read.
**Steps to reproduce:**
1. Open `falcon_seeded.pptx`, choose *Use existing profile* → `falcon`.
2. Look at slide 1 on the canvas; compare with the slide-1 thumbnail in the left rail.
3. (Cleanest A/B) Upload the deck and look at the canvas **before** picking a profile —
   that state shows the LibreOffice raster, with correct white/gold text. Pick the
   profile and the same slide switches to the shape-model canvas, drawn dark on dark.
**Expected:** `PROJECT FALCON` is `#FFFFFF` Cambria 54pt in the file, and renders white
on the thumbnail and in PowerPoint; the canvas should match.
**Actual:** Title, subtitle, strapline, date and the confidentiality line all render in
the default dark ink. Same on slide 3 (agenda "01…08" numerals invisible inside their
navy squares), slide 11 and 14 (white table header row text invisible on navy), slide 12
(`Financial Performance & Valuation`, and `SECTION 03` which is gold `#C9A227`).
**Location:** Slide canvas, every slide; most damaging on 1, 3, 11, 12, 14.
**Impact:** The surface on which the user is asked to identify, judge and fix defects
misrepresents the deck. Anything colour-related is unjudgeable, and dark slides are
unreadable.
**Evidence:** `canvas_view()` emits `color_hex: "#FFFFFF"` (with the `#`), and
`tieout_ui/static/index.html:2244` builds the style as ``color:#${f.color_hex}`` →
`color:##FFFFFF`, which is invalid CSS and is dropped. Shape *fills* go through
`hexToRgba(fill.hex, …)` and are correct — which is why the gold logo box is right while
the text beside it is not.

### Finding #2 — Dragging a shape moves only the selection outline; the shape itself does not move until Apply
**Category:** Bug / Fix behaviour
**Issue:** During a drag there is no live preview. The outline and handles follow the
pointer; the shape and its text stay exactly where they were.
**Steps to reproduce:**
1. Review → click *A shape extends outside the slide canvas* (LO-001, slide 12) →
   **Move it**.
2. Drag the shape body ~380px to the left.
3. Observe the canvas.
**Expected:** The shape follows the pointer, so you can see where you are putting it
relative to everything around it.
**Actual:** The readout updates (`X 385.1 … (−444.9, +0.0pt)`) and the outline moves, but
`SECTION 03` is still painted at its original position, 830pt. Reproduced again on slide 10
(LO-003): dragged the title 166.8pt down — the outline sat in the middle of the quadrant
chart while "Competitive Positioning" was still drawn at the top of the slide.
**Location:** Position editor, any slide.
**Impact:** The judgement the product explicitly reserves for the human — *where should
this go?* — has to be made without being able to see the result. Only **Apply move**
reveals it, and that writes a correction.

### Finding #3 — Resize handles are drawn and advertised but do nothing on a shape whose text overflows its box
**Category:** Bug / Missing functionality
**Issue:** On slide 9 `Text 8` (the shape LO-007 is about) all eight handles are drawn and
the hint says "a handle to resize", but dragging any of them changes nothing; the drag is
swallowed as a text selection.
**Steps to reproduce:**
1. Go to slide 9, click the large `TAM —` caption. The editor opens: `Text 8 · slide 9`,
   `X 39.6 Y 442.8 (unmoved)`, eight handles visible.
2. Drag the right-middle handle left. Then the bottom-right corner. Then the top-right
   corner (tried at the exact handle centres, 619,593 / 619,617 / 621,641).
3. Watch the readout and the *Apply* button.
**Expected:** The readout switches to `W … H …` and *Apply resize* lights up, as it does
elsewhere.
**Actual:** Readout stays `(unmoved)`, *Apply move* stays disabled, and a pink text-
selection band appears across the shape. No message, no refusal, no feedback.
**Control:** The same gesture on `Text 1` (the slide-9 title, which does not overflow)
resizes correctly: `W 623.0pt H 47.5pt (−128.2, +0.0pt)`, *Apply resize* enabled.
**Location:** Slide 9, `Text 8`; likely any shape whose text overflows its frame.
**Impact:** Resize is silently unavailable on precisely the shapes whose text is too big
for their box — the case where resizing is the obvious thing to reach for.
**Evidence:** The canvas payload for that shape is
`{"movable": true, "resizable": true, "resize_refused": "", …}` — the shape is not
refusing; the interaction is failing.

### Finding #4 — A font-size finding cannot be acted on: there is no size control anywhere, and its explanation is about moving shapes
**Category:** Missing functionality / UI text
**Issue:** LO-007 ("A font size falls outside the learned band for its role", slide 9,
52pt against a 9–25pt band) offers only *Set aside*. There is no font-size control in the
finding, in the position editor, or in the double-click text editor.
**Steps to reproduce:** Click the LO-007 finding; read the offered controls; then select
the shape on the canvas and inspect every control the editor exposes.
**Expected:** The remedy is literally "Set the size to 9pt to 25pt" — a single number the
rule has already computed. Either a fix, or a size field, or an explicit statement of why
not.
**Actual:** No control. The greyed explanation reads *"Yours to fix: moving shapes to
satisfy a rule breaks the layout around them."* — an explanation about **moving shapes**
attached to a **font size** finding.
**Location:** Review note, LO-007; slide 9 `Text 8`.
**Impact:** A major finding with a mechanically known answer is a dead end, and the
reason given for the dead end does not describe the finding.

### Finding #5 — A blocker (typeface outside the approved set) has no resolution path in the product
**Category:** Missing functionality
**Issue:** BR-005 (slide 4, `Comic Sans MS` → one of Calibri, Cambria) is a **blocker**
and offers only *Set aside*.
**Steps to reproduce:** Click finding 1; note the controls; double-click the offending
paragraph — the editor opens for the text only; there is no typeface control.
**Expected:** Some way to set the typeface, or a statement that this must be done in
PowerPoint. The product's own documentation lists "a typeface to the approved one" as one
of the substitutions it is willing to make.
**Actual:** "Yours to fix: TieOut can see this is wrong and cannot know what is right."
The approved set is two typefaces; the deck is 86.4% Calibri.
**Impact:** The deck cannot be cleared of blockers inside the tool.

### Finding #6 — BR-006 computes the exact target position but offers no *Move it*, and says it does not know what is right
**Category:** Bug / Missing functionality / UI text
**Issue:** "Page number missing, malformed or out of position" (slide 15) prints
*"Move the page number to left 877.18pt, top 509.76pt, 43.2x17.28pt (+/-2pt)"* and then
offers only *Set aside*, captioned "TieOut can see this is wrong and **cannot know what
is right**" — in the same card that states exactly what is right.
**Steps to reproduce:**
1. Click the BR-006 finding. Note: no *Move it* (LO-001 and LO-003, whose remedies are
   the same kind, both have one).
2. Click the highlighted `15` on the canvas — the position editor opens anyway.
3. Press Shift+Right six times: `X 877.2 Y 509.8 (+60.0, +0.0pt)`. **Apply move.**
**Expected:** *Move it*, since the editor exists, opens on this shape and resolves the
finding.
**Actual:** The affordance is missing; the capability is not. Applying the manual move
resolved it: *"Move Text 28 on slide 15 to 877.2, 509.8pt. 1 fixed, 15 remain."*
**Impact:** A fixable major looks unfixable. The workaround is only discoverable by
guessing that slide shapes are clickable.

### Finding #7 — A blocker points at a slide but at no object: nothing is highlighted and no shape is named
**Category:** Bug / Navigation
**Issue:** HY-001 ("Placeholder or draft marker text left in the deck", slide 2, `TBD`)
navigates to slide 2 and highlights nothing. It is also the only finding with no "Where"
disclosure, so nothing names the shape.
**Steps to reproduce:** Click finding 2. Compare with finding 1, which draws a badge and
an outline immediately.
**Expected:** The shape carrying `TBD` outlined, as every other shape-bearing finding is.
**Actual:** Slide 2 opens; the canvas is untouched. The marker is the first word of a
five-line legal paragraph among three dense paragraphs.
**Impact:** On a real disclaimer slide the user hunts for the marker by eye. This is a
blocker — the highest-priority class.
**Evidence:** `tieout/rules/hygiene.py` iterates `slide.leaf_shapes()` to find the marker
and then emits `where=slide.index` only — the shape it matched is discarded before the UI
ever sees it.

### Finding #8 — The two tie-out rules cannot be acted on at all; clicking the offending figure offers to move the table
**Category:** Missing functionality / UX
**Issue:** CO-001 (same labelled figure differs between tables, slide 20) and CO-003 (a
total that does not sum, slide 11) highlight the **whole table**, never the cell, row or
column, and there is no way to edit a table cell. Clicking the offending number selects
the table and opens the *position* editor.
**Steps to reproduce:**
1. Click CO-001 → slide 20, the entire balance-sheet table is outlined.
2. Click the cell containing `23.8`.
**Expected:** Either the cell identified, or at least not an invitation to move the table.
**Actual:** `Table 0 · slide 20  X 39.6 Y 142.6 (unmoved)` with drag handles around the
table. A stray drag here would move the table; nothing offers to correct the figure.
**Impact:** The product's headline capability — tying out figures — ends at detection.
The user is told two numbers disagree and is handed a tool for moving the table.
**Related:** In *By slide* the same findings do carry the detail ("'ebitda' / 'fy26e' is
23.8 here but 37.4 in Table 0 on slide 14") — see #9.

### Finding #9 — The default *By fix* view hides the shape name behind an unmarked toggle and drops locating detail the *By slide* view has
**Category:** UX / Information architecture
**Issue:** The same finding is described differently in the two views, and the default
view is the less useful one.
**Examples (same session, same audit):**
| Rule | *By fix* (default) | *By slide* |
|---|---|---|
| TY-006 | "0 and 1 decimal places in one column" | "**'FY23A'** mixes number formats: decimal places" |
| LO-004 | "83.4% of the smaller shape" | "**Text 1 overlaps Text 0** across 83% of the smaller shape (2786 square points)" |
| CO-001 | "23.8 → 37.4 (slide 14)" | "**'ebitda' / 'fy26e'** is 23.8 here but 37.4 in **Table 0 on slide 14**" |
| BR-005 | shape name hidden | "**Text 2** — 1 run in 1 shape set in Comic Sans MS" |
**Steps to reproduce:** Read a finding in *By fix*; click the small "· Where" line under
it; then switch to *By slide* and read the same finding.
**Actual:** "Where" is a disclosure that does expand to *"Slide 4 · Text 2 — 1 run in 1
shape set in Comic Sans MS, which is not an approved typeface."* — but it is rendered as a
bullet and grey word with no chevron, no underline and no hover affordance, and in the
accessibility tree it is a plain `generic`, not a button or link (so it is not reachable
by keyboard either).
**Impact:** In the view the user is given by default, the single most useful fact — which
object — is hidden behind something that does not look like a control. For TY-006 the
offending column is never named in *By fix* at all.

### Finding #10 — The *By slide* view has no action buttons and does not show set-aside state
**Category:** Missing functionality / Inconsistency
**Issue:** *By slide* — the view the product itself describes as "the order a deck actually
gets corrected" — carries no *Fix it*, no *Move it*, no *Set aside*.
**Steps to reproduce:** Set aside HY-002 in *By fix* (it greys out and offers *Put it
back*). Switch to *By slide* and find HY-002 on slide 3.
**Actual:** No controls anywhere in the list; HY-002 appears as an ordinary live finding
with no indication it was set aside.
**Impact:** You must switch views to do anything, and a finding you already dismissed
looks outstanding in the other view.

### Finding #11 — Finding badges are drawn on top of the slide and cover the very content being discussed
**Category:** UI / Rendering
**Issue:** The rule-id badge (e.g. `CO-001`) is painted at the top-left of the highlighted
shape, over the slide, and routinely obscures adjacent text.
**Observed instances:**
- Slide 20, CO-001 — covers the heading "SUMMARY BALANCE SHEET ($M)" (reads "…IVE BALANCE SHEET").
- Slide 17, TY-005 — covers "Recommended positioning range: $650M – $800M enterprise value".
- Slide 8, CH-003 — covers the chart's own caption "FY26E REVENUE BY GEOGRAPHY" — the
  caption CH-001 asks you to check for.
- Slide 7, LO-004 and slide 16, TY-004 — cover the eyebrow line above the title.
- Slides 11 and 14 — cover the first table header cell.
**Expected:** The marker should not hide slide content, least of all the content the
finding is about.
**Impact:** The user cannot read the slide while a finding is selected.

### Finding #12 — BR-004 reports a colour that is invisible in the deck; the fix changes nothing on screen, and the badge points at an unrelated shape
**Category:** Bug / Fix behaviour / False positive
**Issue:** The off-palette colour `#E81123` on slide 6 is a **solid fill on a shape whose
geometry is `line` with height 0**. PowerPoint paints a line from `a:ln` (here a pale
`#DCE1E8`), never from the fill, so the red exists in the XML and appears nowhere on the
rendered slide.
**Steps to reproduce:**
1. Click BR-004 (slide 6). Zoom the milestone timeline.
2. Press **Fix it**. Zoom the same region again.
**Expected:** Either a visible change, or an explanation that the change is not visible.
**Actual:** Pixel-identical before and after. The banner says *"Recolour #E81123 to
#C9A227. 1 fixed, 16 remain."* and slide 6 gets a green ✓. The LibreOffice thumbnail also
shows no red — there is no red in the deck as rendered.
**Secondary:** Because the shape has zero height, its highlight renders as a dark bar
along the timeline and its badge lands directly on the gold "2016" milestone marker, so
the natural reading is that the gold square is the defect. It is not.
**Location:** Slide 6, `Shape 10`.
**Impact:** A major finding the user cannot see, verify or disbelieve; and a highlight
that points at the wrong object.

### Finding #13 — *Set aside* is excluded from one count and included in the other two
**Category:** Bug / Inconsistency
**Steps to reproduce:** With 20 findings reported, press *Set aside* on HY-002.
**Expected:** A finding you have decided to leave should stop counting as work.
**Actual:** The card greys out and offers *Put it back*, and "TieOut can fix" drops from
**5** to **4** — but "**20** things to do" stays 20, and `4 BLOCKER / 12 MAJOR / 4 MINOR`
is unchanged, as is the *Not ready to send* verdict and the `By fix (20)` tab count.
**Impact:** The counts the user works from do not reflect the decisions they have made.

### Finding #14 — Undoing a correction labels the restored finding as **NEW**
**Category:** Bug / UI text
**Steps to reproduce:** *Fix it* on TY-001 (slide 5). Press **Undo** on the ribbon.
**Expected:** The finding returns as it was.
**Actual:** Banner reads *"Undone: Convert quotes and apostrophes to straight. 20 findings
reported."*, the text reverts correctly — and the card is now tagged
`18. TY-001  MINOR  **NEW**`.
**Impact:** "NEW" is the product's own signal for *a finding the correction exposed that
was not reported before* — the one thing worth acting on. Using it for a finding that
simply came back destroys that signal.

### Finding #15 — Re-running *Check deck* wipes the correction log from the note while the ribbon still claims the corrections
**Category:** Bug / Inconsistency
**Steps to reproduce:** Apply one correction (ribbon: "1 CORRECTION, 1 FIXED"; note shows
the correction and "1 fixed / 19 remain"). Press **Check deck** again.
**Actual:** The note's correction banner and the fixed/remain block disappear; the ribbon
still reads "1 CORRECTION, 1 FIXED". The count stays 19.
**Impact:** The record of what was changed — the thing the product says travels with the
deck — is silently dropped from the note by an action that looks like a refresh.

### Finding #16 — The whole application scrolls: the ribbon can be scrolled out of the viewport
**Category:** Bug / Layout
**Steps to reproduce:** Scroll the review pane to its end and keep scrolling (or scroll
anywhere with the pane at an extreme). The document scrolls behind the app.
**Actual:** The title bar, the Deck/House style/Review tabs and the entire ribbon
(*Check deck*, *Export deck*, *Undo*, *Copy note*, *HTML report*) scroll off the top,
leaving ~250px of empty grey below the app. Recovering requires scrolling the page back.
**Impact:** The primary controls, including Undo, can vanish with no visible cause.

### Finding #17 — The sticky correction banner overlaps the first finding's header line
**Category:** UI / Rendering
**Steps to reproduce:** Apply a correction, then scroll the review note down one notch.
**Actual:** The banner ("Edit text on Text 3 on slide 2. 1 fixed, 19 remain.") is pinned
at the top of the pane and the first card's `1. BR-005  BLOCKER` line slides underneath it
and is clipped mid-glyph.

### Finding #18 — Reloading the page strands the deck and every correction on the server, with no way back
**Category:** Bug / State
**Steps to reproduce:** With a deck open and corrections applied, reload `127.0.0.1:8765`.
**Expected:** Either the session is restored, or the user is told the work is gone.
**Actual:** The page returns to "No deck open" / "Open a deck to begin" with House style
and Review greyed. The server still holds the deck, its version chain and the corrections
(its temp directory and the report URL both still resolve), but nothing in the UI can
reach them — there is no endpoint that lists open decks. A second browser tab likewise
gets a blank session.
**Impact:** One accidental refresh discards an entire review, silently.

### Finding #19 — The HTML report is opened with the session token in the URL query string
**Category:** Bug / Security-adjacent
**Steps to reproduce:** Press **HTML report**.
**Actual:** A new tab opens at
`http://127.0.0.1:8765/api/report/Q44nPPoDE_4?t=tAcyW9dLVxbiQRa1HBz9H8u49gE8643F5oO1VbSIO1M`
and the token stays in the address bar and in browser history.
**Expected (product's own stated design):** "The token is substituted into the document
rather than passed in a link, and the page clears it out of the address bar on load so it
does not sit in browser history." The main page does exactly that; the report link does not.
**Impact:** The credential that authorises reading live deck material is written into
browser history by a normal button press.

### Finding #20 — The HTML report has no slide images, and leaks the internal working filename and temp path
**Category:** Bug / Polish / Confidentiality
**Steps to reproduce:** Press **HTML report**; scroll it.
**Actual:**
- Every one of the 20 slide rows shows an empty grey box captioned "slide image", although
  the application has fully rendered thumbnails for all 20 slides at that moment.
- The report's title, tab title and heading are `v1-falcon_seeded.pptx` — TieOut's internal
  versioned working copy, not the user's filename.
- The footer prints the absolute local path:
  `Generated by tieout from /var/folders/3d/…/T/tieout-ui-oiz7qwmj/Q44nPPoDE_4/v1-falcon_seeded.pptx`.
**Impact:** The artefact meant to be sent to someone else is the least presentable view of
the audit, and carries machine-local paths.

### Finding #21 — "Not checked" never appears in the UI, only in the HTML report
**Category:** Missing functionality
**Issue:** The HTML report's header carries a fourth number — `39 NOT CHECKED` — plus
"Rules not run (5)". The review note in the application shows only
"20 slides checked against 41 rules."
**Expected:** The product's stated principle is that "not checked" is what lets you tell
*nothing is wrong* from *nothing was looked at*. The primary surface omits it.
**Impact:** In the UI a user cannot tell which rules did not run or which shapes were not
examined.

### Finding #22 — *Export deck* gives no confirmation of any kind
**Category:** UX
**Steps to reproduce:** Press **Export deck**.
**Actual:** Nothing happens in the UI — no toast, no status line, no change to the
ribbon. The file does arrive (`~/Downloads/falcon_seeded (corrected).pptx`, verified to
contain the applied edit and to be otherwise unchanged), but only Chrome's own download
shelf says so.
**Impact:** On a machine with the download bar hidden, the user cannot tell the export
happened, and the natural response is to press it again.

### Finding #23 — *Copy note* is enabled before any check has run, and reports success
**Category:** Bug
**Steps to reproduce:** Open a deck, choose a profile, go to Review **without pressing
Check deck**, press **Copy note**.
**Actual:** The status bar reports "Note copied…". There is no note: the pane still says
"Run **Check deck** to audit this deck against **falcon**". (*HTML report* is correctly
disabled in the same state.)
**Impact:** The user believes they have a review note on the clipboard when they have not.

### Finding #24 — Clicking a possible data-mark raises a native browser dialog, unlike every other confirmation in the app
**Category:** UX / Bug
**Steps to reproduce:** Go to slide 10 (the hand-drawn quadrant chart) and click one of
the plotted markers, e.g. the gold "Project Falcon" square.
**Actual:** A native `window.confirm()` opens
(`tieout_ui/static/index.html:2448`). Every other confirmation, warning and refusal in the
product is inline in the task pane. While the dialog is up the page is fully blocked —
during this audit it could not be dismissed through the automation layer and the session
had to be abandoned and rebuilt in a new tab (a human clicking OK/Cancel is not blocked,
but the page is frozen until they do, including the ribbon and Undo).
**Impact:** Inconsistent with the rest of the product, unstyleable, and it blocks the
whole page rather than the one shape.

### Finding #25 — You are asked to delete speaker notes you cannot read, and the fix is deck-wide although the finding is per-slide
**Category:** Missing functionality / Fix behaviour
**Steps to reproduce:** Click HY-002 (slide 3, "65 characters of notes"). Look for the
notes. Press **Fix it**.
**Expected:** Sight of the text being deleted before deleting it — the notes on this deck
read *"Don't forget to check these numbers with the CFO before Thursday."*
**Actual:** Nothing in the UI shows notes (the ribbon's "Speaker notes" checkbox is an
input to content review, not a viewer). The confirmation after the fact reads *"Delete the
speaker notes **on every slide**. 1 fixed, 14 remain."* — the scope is deck-wide and is
only disclosed after it has been applied.
**Impact:** An irreversible-looking deletion of unseen content, at a wider scope than the
finding stated. (Only slide 3 carried notes in this deck, so nothing else was lost here.)

### Finding #26 — Small shapes cannot be text-edited: the handles cover the shape, so BR-007 has no path
**Category:** Bug / Missing functionality
**Steps to reproduce:** Click BR-007 (slide 19, "Page numbers not strictly ascending",
`7 → greater than 18`). Double-click the highlighted page number to correct it.
**Expected:** The run opens for editing, as it does on larger shapes.
**Actual:** The first click selects the shape and opens the position editor; the eight
resize handles are drawn around a 43×17pt shape and completely cover the digit. Repeated
double-clicks only re-open the move editor. The number cannot be edited.
**Impact:** The remedy ("Renumber the slides so the sequence ascends") has no in-app path,
and the only thing the UI lets you do to a page number is move it.

### Finding #27 — Text editing needs two double-clicks to select a word, and gives no hint that it is editable
**Category:** UX
**Steps to reproduce:** Double-click the word `TBD` on slide 2.
**Actual:** The first double-click opens the run for editing and places the caret at the
**end of the whole paragraph** (five lines away); a second double-click at the same point
selects the word. Nothing on screen says you are in an editor, what commits (Enter) or
what cancels (Esc).
**Impact:** Correcting one word in a long paragraph appears to require arrow-keying across
it. (Once discovered, the edit itself works well: `TBD` → `Final` produced *"Edit text on
Text 3 on slide 2. 1 fixed, 19 remain."*, a green ✓ on slide 2, and the blocker cleared.)

### Finding #28 — A remedy is expressed to the user as a raw regular expression
**Category:** UI text
**Location:** TY-007, slide 18.
**Actual:** *"Write it to match the house pattern `^(\$)\s?[\d(]`"*, and the measurement
line repeats it: *"USD: 'USD 1.35' → matching `^(\$)\s?[\d(]`"*.
**Expected:** The *By slide* view manages a human sentence for the same finding — "'USD
1.35' does not follow the deck's currency notation" — but the remedy is the regex in both
views. The evidence line already says the house form is `'$'`.
**Impact:** The instruction the user is meant to act on is unreadable to its audience.

### Finding #29 — A slide with no headline is labelled with the letter from the logo
**Category:** Bug
**Steps to reproduce:** Review → *By slide* → scroll to slide 13.
**Actual:** The group header reads `SLIDE 13   H`. Every other slide shows its title;
slide 13 has no headline (which is what CH-001 reports about it), and the label falls back
to the text of the logo badge, `H`.
**Impact:** Confusing on exactly the slide whose missing title is the finding.

### Finding #30 — The profile picker defaults to the alphabetically first profile
**Category:** UX
**Steps to reproduce:** Deck tab → *Use existing profile*.
**Actual:** The dropdown is pre-selected to `Test` (options: `Test`, `default`, `falcon`).
Pressing *Use this profile* straight away audits against a scratch profile.
**Expected:** No pre-selection, or the most recently used one. `Test` is a leftover
scratch profile sitting first because of case-sensitive ordering.
**Impact:** One click audits the deck against the wrong client's house style; nothing
downstream flags it as unlikely.

### Finding #31 — The content-review preview replaces the review note and will not go away when you untick the checkbox
**Category:** Bug / Navigation
**Steps to reproduce:** Review → tick **Content review** → **Show what would be sent** →
untick **Content review**.
**Actual:** The right pane still shows "WHAT WOULD BE SENT" with the redaction table; the
review note does not return. Switching to another tab and back restores it.
**Also:** the correction banner ("Edit text on Text 7 on slide 18. 1 fixed, 19 remain.") is
rendered inside the redaction panel, and again inside the **House style** panel — a
Review-tab banner appearing in two panels it does not belong to.
**Impact:** The user appears to have lost the findings list by unticking a checkbox.

### Finding #32 — Redaction over-matches names, and the preview is otherwise good
**Category:** Bug (minor, fails safe)
**Observed in the redaction table (24 terms):**
- `[PERSON_1] = "Prospective Counterparties Under"` — a fragment of the footer "Prepared
  for Prospective Counterparties Under NDA", not a person.
- `[PERSON_2] = "Julian Ashcroft Managing"` — the name has absorbed the first word of the
  job title on the next line ("Managing Director").
**Impact:** Harmless direction (more is hidden, not less), but it makes the residual list
harder to trust and mangles the text the model is asked to read.
**Note:** The rest of this screen is the strongest part of the product: 24 terms mapped,
the source of each (`detected:company`, `docProps:app.Company`), "Nothing outstanding —
every identifier was redacted", and the payload verbatim before anything is sent.

### Finding #33 — Chart findings cannot be seen on the canvas
**Category:** Missing functionality (disclosed) / UX
**Issue:** Charts render as grey hatched boxes reading "TieOut does not model chart layout,
so it cannot redraw this chart faithfully."
**Impact:** CH-003 ("value axis starts at 40 rather than zero", slide 8) and CH-001 ("no
title, caption or headline", slide 13) are both judgements about a picture the review
surface will not draw — while the left rail is showing a perfectly good rendering of the
same chart 200px away. The honesty of the placeholder is right; not offering the raster in
its place is the gap.

### Finding #34 — For "a shape extends outside the slide canvas", the part that is outside is not drawn
**Category:** Rendering / UX
**Steps to reproduce:** Click LO-001 (slide 12). The shape's right edge is at 1406pt on a
960pt canvas.
**Actual:** The canvas clips at the slide edge, so the 446pt that is off-slide — the entire
subject of the finding — is invisible; only a sliver of the shape and its highlight can be
seen, and most of its handles are unreachable, including every handle on its right side.
**Impact:** You cannot see the problem, and you can only grab the shape by the fragment
that happens to be on-slide.

### Finding #35 — The unsettled-question text on House style is rendered in full capitals
**Category:** UI
**Location:** House style tab, "1 question the evidence did not settle".
**Actual:** A five-line question is set entirely in uppercase:
"THE REFERENCE DECK'S DOCUMENT PROPERTIES NAME COMPANY='HALYARD PARTNERS', CREATOR=…
SHOULD TIEOUT STILL CHECK FOR OTHER NAMES IN FUTURE DECKS?"
**Impact:** The one thing on that screen that requires a decision is the hardest thing on
it to read.

### Finding #36 — Overflowing text is painted outside the slide, onto the application background
**Category:** Rendering
**Steps to reproduce:** Go to slide 9 and look below the white slide rectangle.
**Actual:** `Text 8` (three paragraphs; the first is the seeded 52pt run) overflows its
61.2pt-tall box; the second and third lines ("SAM — Addressable industrial IoT sensing
segment", "SOM — Company's current serviceable reach") are drawn *below the bottom edge of
the slide*, on the grey canvas surround, where they read as part of the application rather
than the deck. The 52pt line also collides with the footer.
**Impact:** Drawing overflow is the right call; letting it escape the slide's own bounds
makes deck content indistinguishable from chrome.

### Finding #37 — The thumbnail rail blanks completely after every correction
**Category:** UX / Rendering
**Steps to reproduce:** Apply any correction and watch the left rail.
**Actual:** All 20 thumbnails go to empty white boxes (badges remain) while the deck
re-renders, then return. On a 20-slide deck this is brief; the rail is the only way to
navigate, and it becomes unusable at exactly the moment the user wants to check what
changed.

### Finding #38 — The cross-slide reference in CO-001 is not a link
**Category:** UX
**Actual:** "slide 20" (the finding's own slide) is a link; "(slide 14)" — the slide
holding the number it disagrees with — is plain text. Reconciling the two figures is the
whole task, and the second table has to be found by hand.

### Finding #39 — A document-level finding is attributed to slide 1 and counted against it
**Category:** UX
**Actual:** HY-004 ("Author or company metadata left in docProps") is listed as "slide 1",
puts a red "1" badge on slide 1 in the rail, navigates to slide 1, highlights nothing, and
marks slide 1 with a green ✓ when fixed. The defect is a property of the file.
**Impact:** Slide 1 is reported as defective when nothing on it is wrong, and the one
finding that cannot be shown on a slide is presented exactly like the ones that can.

### Finding #40 — LO-004 identifies only one of the two overlapping shapes on the canvas
**Category:** UX
**Actual:** "Two text-bearing shapes overlap" (slide 7) outlines the title only; the other
shape (`Text 0`, the "COMPANY OVERVIEW" eyebrow) is not outlined, and in *By fix* neither
shape is named — the badge sits on top of the eyebrow, hiding it.
**Note:** The fix path itself works: opening *Move it* and nudging the title to 39.6, 50.2
resolved it (*"Move Text 1 on slide 7 to 39.6, 50.2pt. 1 fixed, 13 remain."*) with no new
findings exposed.

### Finding #41 — 10 of the 20 findings have no resolution path inside the product
**Category:** Missing functionality (summary)
| Finding | Slide | Remedy stated | Offered |
|---|---|---|---|
| BR-005 typeface (**blocker**) | 4 | set typeface to Calibri/Cambria | Set aside only |
| BR-006 page number position | 15 | exact coordinates | Set aside only (editor reachable by clicking the shape — #6) |
| BR-007 page numbers not ascending | 19 | renumber | Set aside only; the number cannot be edited (#26) |
| CH-001 chart not named | 13 | add a title or caption | Set aside only; nothing in the UI can add a shape or text box |
| CH-003 axis not at zero | 8 | set axis minimum to 0 | Set aside only; chart internals are not modelled |
| CO-001 figures disagree | 20 | reconcile | Set aside only; table cells are not editable (#8) |
| CO-003 total does not sum | 11 | correct the total | Set aside only; as above |
| LO-007 font size out of band | 9 | set size to 9–25pt | Set aside only; no size control (#4) |
| TY-004 title capitalisation | 16 | recase to title case | Set aside only — although TY-005, a comparable mechanical substitution, does get *Fix it* |
| TY-006 mixed number formats | 14 | one format per column | Set aside only |
Two of these (BR-006 and TY-004) *can* in fact be resolved by hand through the canvas —
the position editor and in-place text editing — but nothing in the finding says so.
Verified by doing it: BR-006 was cleared by selecting the page number and nudging it 60pt
("Move Text 28 on slide 15 to 877.2, 509.8pt. 1 fixed, 15 remain."), and TY-007 — not in
this table, since it is listed with a fix-less remedy too — by double-clicking `USD` and
typing `$` ("Edit text on Text 7 on slide 18. 1 fixed, 19 remain."). BR-007 cannot be
done by hand: its shape is too small to reach its text (#26).

### Finding #42 — Opening a second deck keeps the previous deck's profile chip in the header
**Category:** Bug / State
**Steps to reproduce:** With `falcon_seeded.pptx` open and audited against `falcon`, press
**Open deck** and choose a different deck (`reference_clean.pptx`, 26 slides).
**Actual:** The header updates to `reference_clean.pptx`, the Review tab greys out and the
ribbon reverts to *Use existing profile / Create profile* — correctly, because no house
style has been chosen for the new deck. But the top-right chip still reads
**"Profile: falcon"**.
**Impact:** The one persistent indicator of what the deck is being judged against is stale
and says a profile is in force when none is.

### Finding #43 — After opening a new deck the rail keeps its old scroll position while the canvas resets to slide 1
**Category:** UX
**Actual:** The canvas and status bar show "Slide 1 of 26"; the thumbnail rail is still
scrolled to where it was on the previous deck (slide 3 onwards), so the selected slide is
off-screen and nothing in the rail is highlighted.

### Finding #44 — Grouping drops the measurement: a grouped finding says what is wanted but not what is wrong
**Category:** Bug / UI text
**Issue:** When a finding covers more than one place, the `measured → expected` line loses
the measured half.
**Steps to reproduce:** Check `reference_clean.pptx` against `falcon` (300 findings, 24
groups) and compare a grouped card with a single-place card.
**Actual:**
- Grouped BR-005: *"expected one of Calibri, Cambria"* — the offending typeface is never
  named (the single-place version reads "Comic Sans MS → one of Calibri, Cambria").
- Grouped BR-006: *"expected left 877.18pt, top 509.76pt…"* — the current position is gone.
- Grouped TY-004: *"expected title case"* — the offending title is gone.
**Impact:** In exactly the case the grouping is designed for — one job across many slides —
the user is told the target and not the defect.

### Finding #45 — *Move it* on a grouped finding opens the editor on the first place only, with no way to reach the other 17
**Category:** Missing functionality
**Steps to reproduce:** In the 26-slide run, press **Move it** on LO-003, "A shape edge
sits just off a learned grid line · 18 places".
**Actual:** The editor opens on `Logo · slide 2`, X 852.0 Y 24.0. The card still says "18
places"; there is no "next place", no progress, and no indication that this is 1 of 18.
Clearing the group means finding and visiting each of the other 17 by hand.
**Impact:** The grouping promises "one job"; the control delivers one shape.

### Finding #46 — Three counts of three different things are shown side by side with nothing to relate them
**Category:** UX
**Actual (26-slide run):** the verdict strip shows `50 BLOCKER / 190 MAJOR / 60 MINOR`,
the line under it says "**24** things to do · **4** TieOut can fix", the tabs say
`By fix (24)` and `By slide (300)`, and the status bar says "50 blocker · 190 major · 60
minor". Nothing says that 300 findings collapse into 24 jobs.
**Impact:** Invisible on the seeded deck (20 findings = 20 jobs), glaring on a real one.

### Finding #47 — Remedies that no control can carry out, phrased as instructions to TieOut
**Category:** Missing functionality / UI text
**Examples from the 26-slide run:** *"Add the page number at left 877.18pt, top 509.76pt,
43.2x17.28pt"* (7 places), *"Place the logo at left 808.78pt, top 27.36pt"* (20 places),
*"Add the footer text: 'HALYARD PARTNERS'"* (25 places), *"Add the confidentiality
marking: 'Project Falcon | Strictly Private and Confidential'"* (24 places).
**Actual:** Nothing in the UI can add a shape or a text box, so every one of these is
*Set aside* only. One of them reads *"Required boilerplate text absent — Add the footer
text: **'H'**"* — the single letter of the logo badge, presented to the user as required
boilerplate.
**Impact:** The largest jobs in the note are the ones the product cannot touch, and at
least one reads as nonsense.

### Finding #48 — "Set the size to one of 10.5pt"
**Category:** UI text
**Actual:** LO-007 grouped over 6 places renders its remedy as *"Set the size to one of
10.5pt"* with the evidence *"1 distinct size observed across 120 runs on 4 slides: 10.5pt;
emitted as an exact set because fewer than 4 distinct sizes do not describe a band"*.
**Impact:** Minor, but it is the instruction line — the sentence the user is meant to act
on — and "one of" a single value reads as a template that was not finished.

### Finding #49 — A finding carries provenance text asserting that the rule cannot fail
**Category:** UI text
**Actual:** LO-002's evidence line reads *"…the bottom, top margins were clamped to the
tightest edge observed **so the rule cannot fail this deck**"* — printed underneath 13
findings where the rule has just failed. The sentence is true of the deck the profile was
learned from and false of the deck being checked, but nothing in the wording says so.
**Impact:** The evidence line is the product's answer to "why should I believe this"; here
it argues against the finding it is attached to.

### Finding #50 — Ticking *Content review* without a key blocks the whole check, not just the review
**Category:** UX
**Steps to reproduce:** Tick **Content review**, leave the API key empty, press **Check deck**.
**Actual:** An inline warning — *"Content review needs an API key, or turn it off."* —
clear and well placed, and the existing findings are preserved. But the mechanical audit
does not run either: an optional, off-by-default extra with a missing field prevents the
product's primary, offline action.
**Impact:** Minor, but the failure of the optional layer takes the core function with it.

---

## 4. Recurring inconsistencies worth calling out as a group

- **Fix availability does not follow the remedy's determinacy.** TY-005 (`Cagr`→`CAGR`)
  gets *Fix it*; TY-004 (recase a title) does not. LO-001/LO-003/LO-004 get *Move it*;
  BR-006, whose remedy is a pair of coordinates, does not.
- **The same "Yours to fix" sentence is used for three different situations:** genuinely
  unknowable answers (CO-001), answers the tool knows but will not apply (BR-006), and
  answers the tool knows but has no control for (LO-007) — where the sentence is about
  moving shapes.
- **Locating precision exists everywhere except where you act.** The rules compute shape
  names, column names, both sides of an overlap and the counterpart slide; the HTML report
  and *By slide* print them; the acting view hides or drops them.
- **The canvas is authoritative for interaction and unreliable for appearance.** Selection,
  dragging, resizing and text editing all work off the shape model — correctly — while
  colour, charts, off-canvas content and overflow are drawn wrongly, not at all, or outside
  the slide.
- **Feedback is inconsistent:** corrections announce themselves in a banner with
  fixed/remaining counts (very good); exports announce nothing; *Copy note* announces
  success with nothing to copy; a failed resize announces nothing at all.

## 5. Not tested, and why

- **Create profile / Save changes on House style.** Both write `profiles/<name>.yaml`
  inside the project, which this audit was instructed not to modify. The House style panel
  was read (palette swatches, size bands, the unsettled question, "Save changes" correctly
  disabled while nothing is dirty) but nothing was saved.
- **Sending a content review.** The redaction preview was exercised in full; no API key was
  entered and nothing was sent.
- **Clipboard contents of *Copy note*.** The button's status message was observed; reading
  the clipboard was out of reach from the browser connector, so the *contents* of the note
  (and whether it carries the correction log, as the product states) are unverified.
- **Grouping behaviour of "By fix".** This deck has one finding per slide and 20 distinct
  rules, so nothing collapses; the collapsing claim could not be exercised.
- **Snap-to-grid magnet.** Drags with snap on landed at 385.1 and 32.9pt; whether a grid
  line was engaged could not be established from the readout (an em dash appears beside the
  coordinates, unexplained), so no claim is made either way.
