// Upload a historical workbench export to a real service over loopback.
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {once} = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {createInterface} = require("node:readline");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

async function main() {
  const root = path.resolve(__dirname, "..");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-compare-"));
  const old = path.join(root, "audit", "fulfillment-deepseek-improved", "architecture.html");
  let server, browser;
  try {
    const repo = path.join(temp, "orders");
    fs.cpSync(path.join(root, "audit", "terra-fulfillment", "project"), repo, {recursive:true});
    const store = path.join(repo, "dispatchdesk", "store.py");
    const before = fs.readFileSync(store, "utf8");
    const after = before.replace('if quality == "sellable":', 'if quality != "sellable":');
    assert.notEqual(after, before);
    fs.writeFileSync(store, after);
    const env = {...process.env, LOCALAPPDATA:temp, XDG_DATA_HOME:temp, HOW2TRUST_LLM_CONFIG:""};
    server = spawn(env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust", "serve", repo, "--port", "0"],
      {cwd:root, env, windowsHide:true, stdio:["ignore", "pipe", "inherit"]});
    let url;
    for await (const line of createInterface({input:server.stdout})) {
      if (line.startsWith("http://127.")) { url = line; break; }
    }
    assert.ok(url, "workbench must listen on loopback");
    browser = await chromium.launch({channel:"chrome", executablePath:process.env.CHROME_BIN});
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(url);
    await page.getByRole("button", {name:"核对旧图"}).click();
    const dialog = page.getByRole("dialog", {name:"核对旧图中的业务结论"});
    const input = dialog.locator('input[type="file"]');
    await input.setInputFiles({name:"invalid.json", mimeType:"application/json", buffer:Buffer.from("{}")});
    await dialog.getByRole("status").filter({hasText:"请选择导出的旧架构"}).waitFor();
    // Full-source exports can exceed the request size for normal chat messages.
    const fullExport = Buffer.concat([fs.readFileSync(old), Buffer.alloc(1_000_001, 0x20)]);
    await input.setInputFiles({name:"orders.html", mimeType:"text/html", buffer:fullExport});
    const status = dialog.getByRole("status");
    await status.filter({hasText:"实现有变化 2"}).waitFor();
    assert.match(await status.textContent(), /引用仍匹配 7/);
    const claims = dialog.locator("#compare-result > details");
    assert.equal(await claims.count(), 9);
    const claim = claims.filter({has:page.locator("summary", {hasText:/实现有变化.*退货/})}).first();
    await claim.locator(":scope > summary").click();
    const oldLines = claim.locator("details.reading-citations").filter({has:page.locator("pre", {hasText:'if quality == "sellable":'})}).first();
    await oldLines.locator("summary").click();
    assert.ok(await oldLines.locator("pre").isVisible());
    const current = oldLines.locator("xpath=following-sibling::details[1]");
    await current.locator("summary").click();
    assert.match(await current.innerText(), /quality != "sellable"/);
    await current.locator("xpath=following-sibling::button[1]").click();
    assert.ok(await dialog.isHidden());
    assert.match(await page.locator("#inspect-content > .proof[open]").first().innerText(), /quality != "sellable"/);
    await page.setViewportSize({width:390, height:844});
    await page.getByRole("button", {name:"核对旧图"}).click();
    assert.ok(await dialog.isVisible());
    const widths = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth,
      document.querySelector(".topbar").scrollWidth, document.getElementById("compare-dialog").scrollWidth]);
    assert.ok(widths[1] <= widths[0], "viewport, page, header and dialog widths: " + widths);
    assert.ok((await dialog.boundingBox()).width <= 390);
    assert.deepEqual(errors, []);
    console.log("Compared a historical order graph, located both affected claims and opened the changed source.");
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      const closed = once(server, "close");
      server.kill();
      await closed;
    }
    fs.rmSync(temp, {recursive:true, force:true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
