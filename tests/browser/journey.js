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
