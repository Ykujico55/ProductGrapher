// Exercise both generation modes through the workbench using a loopback model.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const {spawn} = require("node:child_process");
const {once} = require("node:events");
const {createInterface} = require("node:readline");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

const labels = {place_order: "提交订单", reserve_stock: "预留库存"};
const privateMarkers = ["UNRELATED_SAME_FILE_BODY", "UNRELATED_STOCK_BODY", "UNRELATED_REPORT_BODY"];
const sources = {
  "orders.py": "from stock import reserve_stock\n\ndef place_order(order):\n    if order.paid:\n        return reserve_stock(order)\n\ndef internal_note():\n    return 'UNRELATED_SAME_FILE_BODY'\n",
  "stock.py": "def reserve_stock(order):\n    return order.reserve('RESERVE_STOCK_BODY')\n\ndef load_prices():\n    return 'UNRELATED_STOCK_BODY'\n",
  "reports.py": "def print_report():\n    return 'UNRELATED_REPORT_BODY'\n",
};

function modelReply(payload) {
  if (payload.task === "local_business") {
    const groups = payload.nodes.map(node => {
      const block = payload.sources.find(s => s.file === node.file && s.line <= node.end && s.end >= node.line);
      const line = Math.max(block.line, node.line);
      return {label: labels[node.qualname] || node.qualname, nodeIds: [node.id], text: node.qualname,
        refs: [{file: node.file, line, end: line}]};
    });
    return {groups};
  }
  if (payload.task === "verify") return {claimReplacements: [], itemReplacements: []};
  if (payload.task === "explain") {
    const items = payload.nodes.filter(n => n.kind === "function").map(n => ({
      nodeId: n.id, title: n.qualname, text: n.qualname, aliases: [], capability: "整仓履约",
      refs: [{file: n.file, line: n.line, end: n.line}],
    }));
    return {items, overview: {claims: [{text: "仓库提供订单操作。", refs: items[0].refs}]}};
  }
  throw new Error("Unexpected model task " + payload.task);
}

async function readProof(page, file, expected) {
  const proof = page.locator("#inspect-content details.proof").filter({
    has: page.locator("summary", {hasText: file + " · L"}),
  }).first();
  await proof.waitFor();
  if (!(await proof.evaluate(el => el.open))) await proof.locator("summary").click();
  const lines = proof.locator(".source-line.focus");
  await lines.first().waitFor();
  const observed = await lines.evaluateAll(rows => rows.map(el => [
    Number(el.querySelector("b").textContent), el.querySelector("code").textContent,
  ]));
  for (const [number, text] of observed) assert.equal(text, sources[file].split("\n")[number - 1]);
  assert.ok(observed.some(([, text]) => text.includes(expected)), file + " should cite " + expected);
}

async function exercise() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-local-business-"));
  const repo = path.join(folder, "orderdesk");
  fs.mkdirSync(repo);
  for (const [file, source] of Object.entries(sources)) fs.writeFileSync(path.join(repo, file), source);
  const requests = [], errors = [];
  const provider = http.createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(JSON.parse(raw).messages.at(-1).content);
    requests.push(payload);
    try {
      const content = JSON.stringify(modelReply(payload));
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({choices: [{message: {content}}]}));
    } catch (error) {
      errors.push(error.message);
      res.writeHead(500);
      res.end();
    }
  });
  let app, browser;
  async function start() {
    app = spawn(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust", "serve", repo,
      "--port", "0", "--base-url", "http://127.0.0.1:" + provider.address().port, "--model", "local-fixture"],
      {cwd: path.resolve(__dirname, ".."), env: {...process.env, LOCALAPPDATA: folder, HOW2TRUST_LLM_CONFIG: ""},
        windowsHide: true, stdio: ["ignore", "pipe", "inherit"]});
    for await (const line of createInterface({input: app.stdout})) if (line.startsWith("http://127.")) return line;
    throw new Error("Workbench did not start");
  }
  async function stop() {
    const closed = once(app, "close");
    app.kill();
    await closed;
    app = null;
  }
  try {
    provider.listen(0, "127.0.0.1");
    await once(provider, "listening");
    let base = await start();
    browser = await chromium.launch({channel: "chrome", executablePath: process.env.CHROME_BIN});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(base);
    async function generateLocal(neighbors) {
      await page.locator("#search").fill("place_order");
      await page.locator("#tree .symbol-result").first().click();
      await page.locator("#local-neighbors").setChecked(neighbors);
      const result = page.waitForResponse(r => r.url() === base + "api/semantics/local");
      await page.locator("#generate-local").click();
      const response = await result;
      assert.ok(response.ok(), await response.text());
      const artifact = await response.json();
      await page.locator("#drawing").getByRole("button", {name: "提交订单", exact: true}).waitFor();
      return artifact;
    }
    const strict = await generateLocal(false);
    assert.equal(requests.length, 1);
    function checkScope(payload, neighbors) {
      assert.equal(payload.task, "local_business");
      const text = payload.sources.map(s => s.text).join("\n");
      assert.ok(text.includes("if order.paid:"));
      assert.equal(text.includes("RESERVE_STOCK_BODY"), neighbors);
      for (const marker of privateMarkers) assert.ok(!JSON.stringify(payload).includes(marker), marker);
      assert.deepEqual(new Set(payload.sources.map(s => s.file)),
        new Set(neighbors ? ["orders.py", "stock.py"] : ["orders.py"]));
    }
    checkScope(requests[0], false);
    await page.locator("#drawing").getByRole("button", {name: "提交订单", exact: true}).click();
    await readProof(page, "orders.py", "def place_order");
    await page.locator("#up").click();
    const expanded = await generateLocal(true);
    assert.notEqual(expanded.id, strict.id);
    assert.equal(requests.length, 2);
    checkScope(requests[1], true);
    const inspectEdge = async p => {
      const edge = p.locator("#drawing .graph-edge");
      assert.equal(await edge.count(), 1);
      await edge.focus();
      await edge.press("Enter");
      await readProof(p, "orders.py", "reserve_stock(order)");
      await p.locator("#inspect-content .condition").filter({hasText: "order.paid"}).first().waitFor();
    };
    await inspectEdge(page);
    const saved = path.join(folder, "local.html");
    const download = page.waitForEvent("download");
    await page.locator("#export").click();
    await (await download).saveAs(saved);
    const html = fs.readFileSync(saved, "utf8");
    for (const marker of privateMarkers) assert.ok(!html.includes(marker), "Export includes " + marker);
    assert.ok(html.includes("RESERVE_STOCK_BODY"));
    const offline = await browser.newContext({offline: true});
    const exported = await offline.newPage();
    exported.on("pageerror", error => errors.push(error.message));
    await exported.goto(pathToFileURL(saved).href);
    await exported.locator("#drawing").getByRole("button", {name: "预留库存", exact: true}).click();
    await readProof(exported, "stock.py", "def reserve_stock");
    await inspectEdge(exported);
    await offline.close();

    await page.locator("#up").click();
    await page.locator('[data-view="product"]').click();
    await page.locator(".generate-in-canvas").click();
    await page.locator(".reading-claim").first().waitFor();
    const all = await (await page.request.get(base+"api/graph")).json();
    assert.ok(all.semantics);
    assert.deepEqual(new Set(all.localBusinessViews.map(v => v.id)), new Set([strict.id, expanded.id]));
    assert.ok(requests.some(r => r.task === "explain" && JSON.stringify(r).includes("UNRELATED_REPORT_BODY")));
    const fullExport = await (await page.request.get(base+"export.html")).text();
    for (const marker of privateMarkers) assert.ok(fullExport.includes(marker));
    const calls = requests.length;
    await stop();
    base = await start();
    await page.goto(base);
    await page.locator("#local-history").selectOption(expanded.id);
    await page.locator("#drawing").getByRole("button", {name: "预留库存", exact: true}).waitFor();
    await inspectEdge(page);
    assert.equal(requests.length, calls);
    assert.deepEqual(errors, []);
    console.log("Local selection, direct context, cited edges, scoped offline export, full generation and replay passed.");
  } finally {
    if (browser) await browser.close();
    if (app) await stop();
    provider.close();
    fs.rmSync(folder, {recursive: true, force: true});
  }
}
exercise().catch(error => { console.error(error); process.exitCode = 1; });
