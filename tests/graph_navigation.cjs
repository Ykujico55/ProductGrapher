// Run with Node, Playwright, Chrome and the project's Python interpreter.
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

async function main() {
  const root = path.resolve(__dirname, "..");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-navigation-"));
  const output = path.join(tmp, "graph.html");
  let browser;
  try {
    execFileSync(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust",
      "export", root, "-o", output], { cwd: root, windowsHide: true });
    browser = await chromium.launch({ channel: "chrome", executablePath: process.env.CHROME_BIN });
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(pathToFileURL(output).href);
    const search = page.locator("#search");
    const tree = page.locator("#tree");
    const cards = page.locator(".graph-node");
    await search.fill("review");
    await tree.getByRole("button", { name: "review", exact: true }).click();
    const local = await cards.count();
    const zoom = await page.locator("#zoom-label").innerText();
    await search.fill("build_report");
    assert(await tree.getByRole("button", { name: /build_report/ }).count() > 0,
      "method search returned no result; review initially rendered " + local + " nodes at " + zoom);
    const toggle = page.locator("#call-range");
    await toggle.click();
    const full = await cards.count();
    assert(local < full && parseInt(zoom) >= 80);
    const allEdges = await page.locator(".graph-edge").count();
    await toggle.click();
    assert.equal(await cards.count(), local);
    await page.locator('.graph-node[data-key="function:how2trust/review.py:build_report"]').click();
    await page.getByRole("button", { name: "查看此节点的直接调用" }).click();
    assert.match(await page.locator("#canvas-name").innerText(), /build_report/);
    await search.fill("how2trust.review.build_report");
    await tree.getByRole("button", { name: /build_report/ }).click();
    const selected = cards.and(page.locator(".selected"));
    assert.equal(await selected.getAttribute("data-key"), "function:how2trust/review.py:build_report");
    const selectedCard = selected.locator(".card");
    const box = await selectedCard.boundingBox();
    const viewport = await page.locator("#drawing").boundingBox();
    assert(box.x >= viewport.x && box.x + box.width <= viewport.x + viewport.width);
    assert(box.y >= viewport.y && box.y + box.height <= viewport.y + viewport.height);
    assert.match(await page.locator(".proof[open]").first().innerText(), /def build_report/);
    for (const [term, expected, snippet] of [
      ["how2trust/discussion.py::Snapshot", "class:how2trust/discussion.py:Snapshot", "class Snapshot"],
      ["Snapshot.matches", "method:how2trust/discussion.py:Snapshot.matches", "def matches"],
    ]) {
      await search.fill(term);
      await tree.getByRole("button", { name: new RegExp(term.split(/::|\./).at(-1)) }).first().click();
      assert.equal(await selected.getAttribute("data-key"), expected);
      assert((await page.locator(".proof[open]").first().innerText()).includes(snippet));
    }
    // Enter the viewer via a review report's independent file/line deep link.
    const deepLink = await page.evaluate(() => {
      const node = window.H2T_GRAPH.nodes.find(n => n.id === "function:how2trust/review.py:build_report");
      return new URLSearchParams({ file: node.file, line: node.line }).toString();
    });
    await page.goto("about:blank");
    await page.goto(pathToFileURL(output).href + "#" + deepLink);
    assert.equal(await selected.getAttribute("data-key"), "function:how2trust/review.py:build_report");
    const linkedBox = await selected.locator(".card").boundingBox();
    assert(linkedBox.x >= viewport.x && linkedBox.x + linkedBox.width <= viewport.x + viewport.width);
    assert(linkedBox.y >= viewport.y && linkedBox.y + linkedBox.height <= viewport.y + viewport.height);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ initialNodes: local, fullNodes: full, fullEdges: allEdges, initialZoom: zoom,
      classMethodAndFunctionEvidence: true, reviewLink: true, pageErrors: errors }));
  } finally {
    if (browser) await browser.close();
    fs.rmSync(output, { force: true });
    fs.rmdirSync(tmp);
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
