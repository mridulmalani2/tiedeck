// One person's journey through the page, driven in a real browser. Prints one
// JSON object; tests/test_ui_browser.py asserts on it.
//
//   node journey.js URL DECK CLIENT
const { chromium } = require("playwright");

const [url, deck, client] = process.argv.slice(2);

(async () => {
  const out = { errors: [] };
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on("pageerror", (e) => out.errors.push(e.message));
  await page.goto(url);
  await page.setInputFiles("#file", deck);
  await page.waitForSelector("#grp-choose:not([hidden])", { timeout: 60000 });

  // The existing-profile path: unreachable once nothing was pre-selected.
  await page.click("#pick-existing");
  out.existingShown = await page
    .waitForSelector("#grp-existing:not([hidden])", { timeout: 15000 })
    .then(() => true, () => false);
  out.flashOnPick = await page.$eval("#flash", (el) => el.innerText);
  if (!out.existingShown) { console.log(JSON.stringify(out)); await browser.close(); return; }
  await page.selectOption("#client", client);
  await page.click("#useclient");
  await page.click('button[data-tab="review"]');
  await page.click("#check");
  await page.waitForSelector(".verdict", { timeout: 120000 });
  out.tallyChecked = await page.$eval("#tally", (el) => el.innerText);

  // This is intentional, then Undo.
  out.intendButtons = (await page.$$("[data-intend]")).length;
  await page.click("[data-intend]");
  await page.waitForSelector("#unintend", { timeout: 60000 });
  out.flashOnIntend = await page.$eval("#flash", (el) => el.innerText);
  out.tallyIntended = await page.$eval("#tally", (el) => el.innerText);
  await page.click("#unintend");
  await page.waitForFunction(
    (n) => document.querySelectorAll("[data-intend]").length === n, out.intendButtons,
    { timeout: 60000 });
  out.tallyUndone = await page.$eval("#tally", (el) => el.innerText);

  // The audit's #26: a page number can be edited, straight away or from the
  // move editor. Its one digit is a few pixels inside a padded box. Slide 1
  // is the cover, which carries none.
  let number = null;
  for (let index = 2; index <= 8 && !number; index++) {
    await page.evaluate(async (n) => { select(n, null, ""); await loadCanvas(n); renderSlide(); }, index);
    await page.waitForTimeout(800);
    number = await page.evaluate(() => {
      const el = [...document.querySelectorAll(".shape")].find((e) =>
        e.querySelector("[data-run]")
        && (/^\d+$/.test(e.innerText.trim()) || /page number|slide number/i.test(e.title)));
      return el && el.getBoundingClientRect().toJSON();
    });
  }
  out.pageNumberFound = !!number;
  if (number) {
    const x = number.x + number.width / 2, y = number.y + number.height / 2;
    await page.mouse.dblclick(x, y);
    await page.waitForTimeout(600);
    out.pageNumberEditable = await page.evaluate(() => !!S.textEdit);
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);
    await page.mouse.click(x, y);
    await page.waitForTimeout(800);
    out.editorOpened = await page.evaluate(() => !!S.edit);
    await page.mouse.dblclick(x, y);
    await page.waitForTimeout(600);
    out.pageNumberEditableFromEditor = await page.evaluate(() => !!S.textEdit);
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);
  }

  // The audit's #33: where the slide has been rendered, a chart is drawn from
  // that render rather than as hatching. Only asserted where this machine
  // renders slides at all (LibreOffice), which the status bar reports.
  for (let i = 0; i < 120; i++) {
    const ready = await page.evaluate(() => S.deck.thumbnails.available || !!S.deck.thumbnails.reason);
    if (ready) break;
    await page.waitForTimeout(1000);
  }
  out.rendered = await page.evaluate(() => !!S.deck.thumbnails.available);
  out.chartFound = false;
  for (let index = 1; index <= 30 && !out.chartFound; index++) {
    const has = await page.evaluate(async (n) => {
      if (n > S.deck.slide_count) return null;
      select(n, null, ""); await loadCanvas(n); renderSlide();
      const box = document.querySelector(".shape .placeholder-box");
      return box ? { raster: box.classList.contains("raster"), title: box.title || "" } : false;
    }, index);
    if (has === null) break;
    if (has) { out.chartFound = true; out.chartRaster = has.raster; }
  }

  // A correction, then a reload: the deck and the correction come back.
  const fix = await page.$("[data-fix]");
  out.fixed = !!fix;
  if (fix) {
    await fix.click();
    await page.waitForFunction(
      () => /1 correction/i.test(document.getElementById("editstate").innerText),
      null, { timeout: 60000 });
  }
  out.ribbonBefore = await page.$eval("#editstate", (el) => el.innerText);
  await page.reload();
  await page.waitForSelector(".verdict", { timeout: 120000 });
  out.flashAfterReload = await page.$eval("#flash", (el) => el.innerText);
  out.ribbonAfter = await page.$eval("#editstate", (el) => el.innerText);
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e) })); process.exit(1); });
