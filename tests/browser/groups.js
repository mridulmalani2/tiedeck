// The 26-slide reference deck checked against a foreign house style, where
// hundreds of findings collapse into a few jobs. Prints one JSON object.
//
//   node groups.js URL DECK CLIENT
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
  await page.click("#pick-existing");
  await page.waitForSelector("#grp-existing:not([hidden])");
  await page.selectOption("#client", client);
  await page.click("#useclient");
  await page.click('button[data-tab="review"]');
  await page.click("#check");
  await page.waitForSelector(".verdict", { timeout: 120000 });

  // #46: the jobs and the findings they collapse from, in one sentence.
  out.totals = await page.$eval(".totals", (e) => e.innerText);
  out.findings = await page.evaluate(() => S.audit.summary.total);
  out.jobs = await page.evaluate(() => S.audit.actions.length);

  // #47: the reason given beside each job no button can carry out.
  out.reasons = await page.evaluate(() => S.audit.actions
    .filter((a) => !a.fixable && !a.movable && !a.editable)
    .map((a) => ({ remedy: a.remedy, why: whyManual(a) })));

  // #45: Move it on a group walks the group.
  const index = await page.evaluate(() =>
    S.audit.actions.findIndex((a) => a.movable && a.instances.filter((i) => movableOf(i)).length > 1));
  out.groupFound = index >= 0;
  if (index >= 0) {
    await page.click(`[data-move="${index}"]`);
    await page.waitForSelector("#posbar");
    out.first = await page.$eval("#posbar", (e) => e.innerText);
    await page.click("#nextplace");
    await page.waitForSelector("#posbar");
    out.second = await page.$eval("#posbar", (e) => e.innerText);
    out.secondShape = await page.evaluate(() => S.edit && S.edit.name);
    await page.focus(".mark.live");
    await page.keyboard.press("ArrowRight");
    await page.click("#applymove");
    await page.waitForFunction(() => /still to do in this job/.test(document.getElementById("flash").innerText),
      null, { timeout: 60000 }).catch(() => {});
    out.afterApply = await page.$eval("#flash", (e) => e.innerText);
    out.editorAfterApply = await page.evaluate(() => !!S.edit);
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e) })); process.exit(1); });
