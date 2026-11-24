// Run with Node, Playwright, Chrome and the project's Python interpreter.
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

async function checkRecursiveCalls(page, root, tmp) {
  const source = path.join(tmp, "recursive");
  const output = path.join(tmp, "recursive.html");
  fs.mkdirSync(source);
  const chain = ["start", ...Array.from({length:11}, (_, i) => "stage_" + (i + 1)), "walk"];
  const code = ["class Walker:"];
  for (let i = 0; i < chain.length - 1; i++) {
    code.push("    def " + chain[i] + "(self, n):", "        return self." + chain[i + 1] + "(n)");
  }
  code.push("    def walk(self, n):", "        if n > 2:", "            return self.walk(n - 2)",
    "        if n == 2:", "            return self.back(n - 1)", "        return n",
    "    def back(self, n):", "        return self.walk(n)", "");
  fs.writeFileSync(path.join(source, "steps.py"), code.join("\n"));
  execFileSync(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust", "export", source, "-o", output],
    {cwd:root, windowsHide:true});
  await page.goto(pathToFileURL(output).href);
  await page.locator("#search").fill("Walker.start");
  await page.locator("#tree .symbol-result").first().click();
  const drawing = page.locator("#drawing");
  const key = name => "method:steps.py:Walker." + name;
  assert.equal(await drawing.locator('.graph-node[data-key="' + key("walk") + '"]').count(), 0);
  await page.getByRole("button", {name:"展开完整调用图"}).click();
  const visible = await drawing.locator(".graph-edge").evaluateAll(elements => {
    const byId = new Map(window.H2T_GRAPH.edges.map(edge => [edge.id, edge]));
    return elements.map(el => byId.get(el.dataset.key));
  });
  const pairs = [...chain.slice(1).map((name, i) => [chain[i], name]), ["walk","walk"], ["walk","back"], ["back","walk"]];
  for (const [from, to] of pairs) {
    assert.ok(visible.some(edge => edge.source === key(from) && edge.target === key(to)), from + " → " + to);
  }
  assert.equal(await drawing.locator('.graph-node[data-key="' + key("walk") + '"]').count(), 1);
  const loop = drawing.getByRole("button", {name:"递归调用", exact:true});
  await loop.focus();
  await loop.press("Enter");
  assert.match(await page.locator("#inspect-content .proof[open]").first().innerText(), /self\.walk\(n - 2\)/);
}

async function checkCallEvidence(page, root, tmp) {
  const source = path.join(tmp, "evidence");
  const output = path.join(tmp, "evidence.html");
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, "service.py"), [
    "from math import sqrt", "from vendorlib import solve", "",
    "def store(x): return x", "",
    "class Service:", "    def run(self, items, callback):",
    "        store(len(items))", "        store(sqrt(4))", "        return solve(callback(items))", "",
  ].join("\n"));
  execFileSync(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust", "export", source, "-o", output],
    {cwd:root, windowsHide:true});
  const panel = page.locator("#inspect-content .method-calls");
  const dialog = page.locator("#diag-dialog");
  async function openMethod(file) {
    await page.goto(pathToFileURL(file).href);
    await page.locator("#search").fill("Service.run");
    await page.locator("#tree .symbol-result").first().click();
  }
  await openMethod(output);
  assert.equal(await panel.locator(".section-head span").innerText(), "6");
  const toggle = page.locator("#call-dependencies");
  const drawing = page.locator("#drawing");
  const solver = drawing.locator('[data-key="dependency:vendorlib.solve"]');
  assert.equal(await solver.count(), 0);
  await toggle.click();
  assert.equal(await toggle.getAttribute("aria-pressed"), "true");
  for (const item of [solver, drawing.getByRole("button", {name:/vendorlib\.solve.*仓外调用/})]) {
    await item.focus();
    await item.press("Enter");
    await dialog.locator(".proof summary").click();
    assert((await dialog.locator(".source-line.focus").innerText()).includes("return solve(callback(items))"));
    await dialog.getByRole("button", {name:"关闭"}).click();
  }
  await toggle.click();
  assert.equal(await solver.count(), 0);
  await toggle.click();
  await page.locator("#search").fill("store");
  await page.locator("#tree .symbol-result").first().click();
  assert.equal(await solver.count(), 0);
  await openMethod(output);
  for (const [label, n, line, snippet] of [["仓内",2,8,"store(len(items))"],["内置",1,8,"len(items)"],
      ["标准库",1,9,"sqrt(4)"],["外部依赖",1,10,"solve(callback(items))"],["未解析",1,10,"callback(items)"]]) {
    await panel.getByRole("button", {name:label+" "+n, exact:true}).click();
    assert.equal(await dialog.locator(".diag-item").count(), n);
    const ref = dialog.locator(".proof").filter({has:page.locator("summary", {hasText:"service.py · L"+line})}).first();
    await ref.locator("summary").click();
    assert((await ref.locator(".source-line.focus").innerText()).includes(snippet));
    await dialog.getByRole("button", {name:"关闭"}).click();
  }
  // An older graph can carry a message without either classification or caller.
  const legacy = path.join(tmp, "old-evidence.html");
  const current = fs.readFileSync(output, "utf8");
  const start = "<script>window.H2T_GRAPH=";
  const offset = current.indexOf(start) + start.length, end = current.indexOf(";</script>", offset);
  const graph = JSON.parse(current.slice(offset, end));
  delete graph.callDiagnosticVersion;
  graph.diagnostics = [
    {file:"service.py", line:9, end:9, caller:"method:service.py:Service.run", message:"调用目标未解析 · sqrt"},
    {file:"service.py", line:10, end:10, message:"调用目标未解析 · solve"},
  ];
  fs.writeFileSync(legacy, current.slice(0, offset) + JSON.stringify(graph).replaceAll("<", "\\u003c") + current.slice(end));
  await openMethod(legacy);
  assert.equal(await toggle.isVisible(), false);
  assert.equal(await panel.locator(".section-head span").innerText(), "3");
  assert.equal(await panel.getByRole("button", {name:"未分类 1"}).count(), 1);
  assert.equal(await panel.getByText(/标准库|外部依赖/).count(), 0);
  await page.locator("#diagnostics").click();
  assert.equal(await dialog.locator("h2").innerText(), "分析记录");
  assert.equal(await dialog.getByText("调用目标未解析 · solve", {exact:true}).count(), 1);
  await dialog.getByRole("button", {name:"关闭"}).click();
}

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
    await checkRecursiveCalls(page, root, tmp);
    await checkCallEvidence(page, root, tmp);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ initialNodes: local, fullNodes: full, fullEdges: allEdges, initialZoom: zoom,
      classMethodAndFunctionEvidence: true, reviewLink: true, recursiveCallChain: true, directCallEvidence: true, pageErrors: errors }));
  } finally {
    if (browser) await browser.close();
    fs.rmSync(tmp, {recursive:true, force:true});
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
