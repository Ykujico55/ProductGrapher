// Run with node tests/review_attention.cjs and a local Playwright installation.
const assert = require("node:assert/strict");
const {execFileSync} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

async function main() {
  const root = path.resolve(__dirname, "..");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-attention-"));
  const file = path.join(temp, "review.html");
  let browser;
  try {
    execFileSync(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust", "review",
      "examples/delivery/submission", "--base-dir", "examples/delivery/baseline", "--spec",
      "examples/delivery/contract.json", "--run-checks", "-o", file], {cwd: root, windowsHide: true});
    browser = await chromium.launch({channel: "chrome", executablePath: process.env.CHROME_BIN});
    const page = await browser.newPage();
    await page.goto(pathToFileURL(file).href);
    for (const [category, label] of [
      ["要求完成情况", "待发货订单可以取消"], ["改动范围", "cancel_order"],
    ]) {
      const go = () => page.getByRole("button", {name: new RegExp(category)}).click();
      await go();
      const row = page.locator("#items button").filter({hasText: label});
      const choice = page.getByLabel("人工判定");
      const save = page.getByRole("button", {name: "保存复核"});
      await row.click();
      await choice.selectOption("more");
      await save.click();
      for (const reload of [false, true]) {
        if (reload) {
          await page.reload();
          await go();
        }
        await page.locator("#filter").selectOption("attention");
        assert.equal(await row.count(), 1, label + " waiting for evidence must be visible");
      }
      await row.click();
      await choice.selectOption("accept");
      await save.click();
      assert.equal(await row.count(), 0, label + " no longer needs attention");
    }
    console.log("ATTENTION_FILTER_OK");
  } finally {
    if (browser) await browser.close();
    if (fs.existsSync(file)) fs.unlinkSync(file);
    fs.rmdirSync(temp);
  }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
