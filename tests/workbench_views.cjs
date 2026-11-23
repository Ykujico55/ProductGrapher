// Exercise the exported workbench and its generation response using the same source fixture.
const assert = require("node:assert/strict");
const {execFileSync} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");
const root = path.resolve(__dirname, "..");

function build(temp) {
  const repo = path.join(temp, "source");
  fs.mkdirSync(path.join(repo, "tests"), {recursive: true});
  fs.writeFileSync(path.join(repo, "dispatch.py"), "from carrier import send\nfrom audit import record\nfrom packing import pack\n\ndef stamp():\n    return 1\n\ndef submit(ready):\n    record()\n    if ready:\n        pack()\n        return send()\n");
  fs.writeFileSync(path.join(repo, "packing.py"), "def pack():\n    return 'box'\n");
  fs.writeFileSync(path.join(repo, "carrier.py"), "from dispatch import stamp\n\ndef send():\n    return stamp()\n");
  fs.writeFileSync(path.join(repo, "audit.py"), "def record():\n    return True\n");
  fs.writeFileSync(path.join(repo, "tests", "test_ship.py"), "if __name__ == '__main__':\n    print('test')\n");
  const code = `
import json, sys
from pathlib import Path
from how2trust.project import build_graph
from how2trust.capabilities import capability_groups
from how2trust.collaboration import collaboration_graph
from how2trust.web import standalone
out = Path(sys.argv[1])
g = build_graph(out/"source")
(out/"static.json").write_text(json.dumps(g), "utf-8")
items = []
for n in g["nodes"]:
    if n["kind"] == "function" and n["label"] in {"send", "submit", "pack"}:
        title = "交给承运方" if n["label"] == "send" else "提交发货"
        items.append({"nodeId": n["id"], "capability": title, "title": title, "text": n["qualname"],
                      "aliases": [], "refs": [{k: n[k] for k in ("file", "line", "end")}]})
a = {"promptVersion": "8", "provider": "fixture", "model": "fixture", "items": items,
     "groups": capability_groups(g, items), "overview": {"claims": [{"text": "提交发货。", "refs":items[0]["refs"]}]},
     "coverage": {"explained": len(items), "eligible": len(g["nodes"])}}
g.update(semantics=a, collaboration=collaboration_graph(g, a))
(out/"page.html").write_bytes(standalone(g))
(out/"graph.json").write_text(json.dumps(g), "utf-8")
`;
  execFileSync(process.env.PYTHON || "python", ["-B", "-X", "utf8", "-c", code, temp], {cwd:root});
  return JSON.parse(fs.readFileSync(path.join(temp, "graph.json"), "utf8"));
}

async function verify(page, graph) {
  const edge = page.locator("#drawing .graph-edge").filter({has: page.locator("title", {hasText:"提交发货 → 交给承运方"})});
  await edge.locator(".site-count").click();
  const site = page.locator(".module-site");
  assert.match(await site.innerText(), /ready/);
  assert.match(await site.locator(":scope > .proof[open]").innerText(), /return send\(\)/);
  const conditionRef = site.locator(".reading-anchor .text-btn").first();
  await conditionRef.click();
  assert.match(await page.locator("#inspect-content > .proof[open]").first().innerText(), /if ready:/);
  await page.locator('[data-view="collaboration"]').click();
  await page.locator("#module-direction").selectOption("in");
  const dest = graph.collaboration.modules.find(m => m.label === "交给承运方");
  await page.locator("#module-focus").selectOption(dest.id);
  assert.equal(await edge.count(), 1);
  await page.locator("#module-direction").selectOption("all");
  assert.ok(await page.locator("#module-focus").isDisabled());
  await edge.focus();
  await edge.press("Enter");
  await page.locator('[data-inspect="notes"]').click();
  await page.getByLabel("批注内容").fill("核对发货条件");
  await page.getByRole("button", {name:"保存批注"}).click();
  await page.locator(".note-card").waitFor();
  const source = graph.collaboration.modules.find(m => m.label === "提交发货");
  const marked = graph.collaboration.relations.find(r => r.source === source.id && r.target === dest.id);
  assert.deepEqual(await page.evaluate(key =>
    JSON.parse(localStorage.getItem("How2Trust.notes." + key))[0].targets, graph.snapshot),
    [marked.sites[0].edgeId]);
}

async function verifyModuleDrilldown(page, graph) {
  const drawing = page.locator("#drawing");
  const card = key => drawing.locator(`.graph-node[data-key="${key}"]`);
  const byLabel = label => graph.collaboration.modules.find(m => m.label === label);
  const source = byLabel("提交发货");
  const dest = byLabel("交给承运方");
  const internal = graph.edges.find(e => e.kind === "calls" &&
    e.source.endsWith(":submit") && e.target.endsWith(":pack"));
  assert.ok(internal, "fixture must contain a real internal call");

  // Drill into the neighboring caller, not the current focus, to check return navigation.
  await page.locator("#module-direction").selectOption("in");
  await page.locator("#module-focus").selectOption(dest.id);
  const overviewKeys = await drawing.locator(".graph-node").evaluateAll(cards => cards.map(c => c.dataset.key).sort());
  await card(source.id).click({modifiers:["Shift"]});
  assert.notEqual(await page.locator("#canvas-name").innerText(), "模块内部关系图");
  await page.locator("#select").click();
  await card(source.id).click();
  assert.equal(await card(dest.id).count(), 1);
  await page.locator("#pan").click();

  await card(source.id).click();
  assert.equal(await page.locator("#canvas-name").innerText(), "模块内部关系图");
  assert.equal(await page.locator("#scope-name").innerText(), source.label);
  assert.equal(await page.locator('[data-view="collaboration"]').getAttribute("aria-pressed"), "true");
  const members = await drawing.locator(".graph-node").evaluateAll(cards => cards.map(c => c.dataset.key).sort());
  assert.deepEqual(members, [...source.nodeIds].sort());
  assert.deepEqual(await drawing.locator(".graph-edge").evaluateAll(edges => edges.map(e => e.dataset.key)), [internal.id]);
  const internalEdge = drawing.locator(`.graph-edge[data-key="${internal.id}"]`);
  await internalEdge.focus();
  await internalEdge.press("Enter");
  const highlight = page.locator("#inspect-content .proof[open] .source-line.focus");
  assert.match(await highlight.first().innerText(), /pack\(\)/);
  assert.equal(await highlight.first().locator("b").textContent(), String(internal.line));
  await card(internal.target).click();
  assert.match(await highlight.first().innerText(), /def pack\(/);

  await page.locator('[data-view="call"]').click();
  assert.equal(await page.locator("#up").innerText(), "返回模块详情");
  await page.locator("#up").click();
  assert.equal(await internalEdge.count(), 1);
  assert.equal(await page.locator("#up").innerText(), "返回模块协作");
  await page.locator("#up").click();
  assert.equal(await page.locator("#module-focus").inputValue(), dest.id);
  assert.equal(await page.locator("#module-direction").inputValue(), "in");
  assert.deepEqual(await drawing.locator(".graph-node").evaluateAll(cards => cards.map(c => c.dataset.key).sort()), overviewKeys);

  await card(dest.id).focus();
  await card(dest.id).press("Enter");
  assert.equal(await page.locator("#scope-name").innerText(), dest.label);
  assert.equal(await drawing.locator(".graph-edge").count(), 0);
  assert.equal(await drawing.locator(".graph-node").count(), 1);
  await card(dest.nodeIds[0]).click();
  assert.match(await highlight.first().innerText(), /def send\(/);
  await page.locator("#up").click();
  assert.equal(await card(dest.id).count(), 1);
}

async function verifyFullscreen(page, graph) {
  const drawing = page.locator("#drawing");
  const enter = page.getByRole("button", {name:"全屏查看", exact:true});
  const exit = page.getByRole("button", {name:"退出全屏", exact:true});
  await page.locator('[data-view="collaboration"]').click();
  const normal = await drawing.boundingBox();
  await enter.click();
  await exit.waitFor();
  assert.ok(await page.evaluate(() => document.fullscreenElement === document.documentElement));
  const full = await drawing.boundingBox();
  assert.ok(full.width > normal.width + 250 && full.height > normal.height);
  assert.ok(await page.evaluate(() => document.getElementById("drawing").clientWidth >= innerWidth - 2));
  assert.ok(await page.locator(".navpanel").isHidden());
  assert.ok(await page.locator("#graph-inspector").isHidden());

  const arrow = drawing.locator(".graph-edge").first();
  await arrow.focus();
  await arrow.press("Enter");
  await page.locator("#graph-inspector .module-site").first().waitFor();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.evaluate(() => document.activeElement.dataset.key), await arrow.getAttribute("data-key"));
  await page.locator("#fullscreen-inspector").click();
  const mod = graph.collaboration.modules.find(m => m.label === "提交发货");
  await drawing.locator(`[data-key="${mod.id}"]`).click();
  assert.equal(await page.locator("#canvas-name").innerText(), "模块内部关系图");
  const edge = drawing.locator(".graph-edge").first();
  await edge.focus();
  await edge.press("Enter");
  await page.locator("#graph-inspector .proof[open]").first().waitFor();
  assert.match(await page.locator("#inspect-content").innerText(), /pack\(/);
  await page.locator("#fullscreen-inspector").click();
  await page.locator("#graph-inspector").waitFor({state:"hidden"});
  assert.equal(await exit.getAttribute("aria-pressed"), "true");
  const selected = await drawing.locator(".picked").getAttribute("data-key");
  const camera = () => drawing.evaluate(svg => {
    const {a, e, f} = svg.querySelector(":scope > g").getCTM();
    return {zoom:a, x:(svg.clientWidth / 2 - e) / a, y:(svg.clientHeight / 2 - f) / a};
  });
  const fit = await camera();
  await page.getByRole("button", {name:"放大",exact:true}).click();
  assert.ok((await camera()).zoom > fit.zoom);
  const origin = await drawing.boundingBox();
  await page.mouse.move(origin.x + 5, origin.y + 5);
  await page.mouse.down();
  await page.mouse.move(origin.x + 55, origin.y + 28);
  await page.mouse.up();
  const panned = await camera();
  assert.ok(Math.abs(panned.x - fit.x) > 10);
  await page.keyboard.press("Escape");
  await enter.waitFor();
  assert.ok(await page.locator(".navpanel").isVisible());
  assert.equal(await drawing.locator(".picked").getAttribute("data-key"), selected);
  assert.equal(await page.locator("#canvas-name").innerText(), "模块内部关系图");
  // The same graph point stays under the centre after switching back to the narrower pane.
  await page.waitForFunction(({zoom, x, y}) => {
    const svg = document.getElementById("drawing");
    const {a, e, f} = svg.querySelector(":scope > g").getCTM();
    return a === zoom && Math.abs((svg.clientWidth / 2 - e) / a - x) < 1 &&
      Math.abs((svg.clientHeight / 2 - f) / a - y) < 1;
  }, panned);
  await page.locator("#up").click();
  assert.equal(await page.locator("#canvas-name").innerText(), "业务模块协作图");

  await enter.click();
  await exit.waitFor();
  await page.evaluate(() => document.exitFullscreen());
  await enter.waitFor(); // browser-initiated exits must also restore the controls
}

async function verifyInspectorResize(page) {
  const handle = page.getByRole("separator", {name:"调整检查器宽度"});
  const inspector = page.locator("#graph-inspector"), drawing = page.locator("#drawing");
  const width = async () => (await inspector.boundingBox()).width;
  async function dragBy(dx) {
    const box = await handle.boundingBox();
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y, {steps:5});
    await page.mouse.up();
  }
  const original = await width(), canvas = await drawing.boundingBox();
  await page.locator('[data-inspect="notes"]').click();
  const draft = page.getByLabel("批注内容");
  await draft.fill("保留正在阅读的选区");
  const selected = await drawing.locator(".selected,.picked").evaluateAll(items => items.map(i => i.dataset.key));
  await dragBy(-240);
  assert.equal(await width(), original + 240);
  assert.equal((await drawing.boundingBox()).width, canvas.width - 240);
  assert.equal(await draft.inputValue(), "保留正在阅读的选区");
  assert.deepEqual(await drawing.locator(".selected,.picked").evaluateAll(items => items.map(i => i.dataset.key)), selected);
  await handle.press("ArrowRight");
  const saved = await width();
  assert.equal(saved, original + 240 - 24);
  await page.reload();
  await handle.waitFor();
  assert.equal(await width(), saved);

  await page.getByRole("button", {name:"全屏查看"}).click();
  await page.locator("#fullscreen-inspector").click();
  assert.equal(await width(), saved);
  await dragBy(-80);
  const wide = await width();
  assert.equal(wide, saved + 80);
  await page.getByRole("button", {name:"退出全屏"}).click();
  await page.getByRole("button", {name:"全屏查看"}).waitFor();
  assert.equal(await width(), wide);

  const viewport = page.viewportSize();
  await page.setViewportSize({width:960,height:viewport.height});
  assert.ok(await width() < wide && (await page.locator(".mainpanel").boundingBox()).width >= 280);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.setViewportSize(viewport);
  assert.equal(await width(), wide);
  await dragBy(wide - 5);
  assert.equal(await width(), Number(await handle.getAttribute("aria-valuemin")));
  await handle.dblclick();
  assert.equal(await width(), original);
  await page.reload();
  await handle.waitFor();
  assert.equal(await width(), original);
}

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-views-"));
  let browser;
  try {
    const graph = build(temp);
    browser = await chromium.launch({channel:"chrome", executablePath:process.env.CHROME_BIN});
    const page = await browser.newPage({viewport:{width:1366,height:768}});
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    const url = pathToFileURL(path.join(temp, "page.html")).href;
    await page.goto(url + "#view=structure");
    await page.locator('#drawing [aria-label="测试与夹具"]').waitFor();
    await page.goto(url + "#view=business");
    assert.equal(await page.locator("#drawing .graph-node").count(), 3);
    await page.goto(url + "#view=collaboration");
    await verify(page, graph);
    await verifyModuleDrilldown(page, graph);
    await verifyFullscreen(page, graph);
    await verifyInspectorResize(page);
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.locator("#inspector-resizer").isHidden());
    await page.locator('[data-view="collaboration"]').click();
    assert.ok(await page.locator("#module-direction").isVisible());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));

    let requests = 0;
    const staticGraph = JSON.parse(fs.readFileSync(path.join(temp, "static.json"), "utf8"));
    const guardLine = staticGraph.files["dispatch.py"].split("\n").findIndex(line => line.includes("if ready:")) + 1;
    const facts = [[{text:"ready 成立时进入分支",file:"dispatch.py",line:guardLine,end:guardLine}]];
    let release;
    const gate = new Promise(resolve => {release = resolve;});
    const state = {notes:[], threads:[], llm:{configured:false}, semanticLlm:{configured:true, model:"fixture"}};
    await page.route("http://workbench.test/**", async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === "/api/graph") return route.fulfill({json:staticGraph});
      if (pathname === "/api/state") return route.fulfill({json:state});
      if (pathname === "/api/semantics") {
        assert.deepEqual(route.request().postDataJSON(), {includeCollaboration:true});
        requests++;
        await gate;
        return route.fulfill({json:{semantics:graph.semantics, collaboration:graph.collaboration, overviewFacts:facts}});
      }
      const file = pathname === "/" ? "index.html" : path.basename(pathname);
      await route.fulfill({path:path.join(root, "how2trust", "assets", file)});
    });
    await page.goto("http://workbench.test/#view=business");
    await page.locator(".generate-in-canvas").click();
    await page.locator('[data-view="collaboration"]').click();
    assert.equal(await page.locator("#module-focus").inputValue(), "collab:file:dispatch.py");
    await page.locator('.graph-node[data-key="collab:file:dispatch.py"]').click();
    assert.equal(await page.locator("#scope-name").innerText(), "dispatch.py");
    const members = staticGraph.collaboration.modules.find(m => m.label === "dispatch.py").nodeIds;
    assert.deepEqual(await page.locator("#drawing .graph-node").evaluateAll(cards => cards.map(c => c.dataset.key).sort()),
      [...members].sort());
    await page.locator("#up").click();
    release();
    await page.getByRole("button", {name:/提交发货 → 交给承运方/}).first().waitFor();
    assert.equal(await page.locator('[data-view="collaboration"]').getAttribute("aria-pressed"),"true");
    assert.notEqual(await page.locator("#module-focus").inputValue(), "collab:file:dispatch.py");
    await page.locator('[data-view="product"]').click();
    assert.equal(requests, 1);
    assert.equal(await page.locator("#drawing .graph-node").count(), 3);
    const branches = page.locator(".reading-claim .reading-citations");
    await branches.locator("summary").first().click();
    assert.match(await branches.innerText(), /ready 成立时进入分支/);
    await branches.locator(".text-btn").click();
    assert.match(await page.locator("#inspect-content > .proof[open]").innerText(), /if ready:/);

    // A browser panel can deny native fullscreen. The live viewer should still fill a narrow tab.
    await page.evaluate(() => {
      document.documentElement.requestFullscreen = () => Promise.reject(new Error("Fullscreen unavailable"));
    });
    await page.getByRole("button", {name:"全屏查看"}).click();
    const exit = page.getByRole("button", {name:"退出全屏"});
    await exit.waitFor();
    assert.ok(await page.evaluate(() => !document.fullscreenElement &&
      document.getElementById("drawing").clientWidth === innerWidth && document.documentElement.scrollWidth === innerWidth));
    for (const next of ["structure", "product", "class", "call", "collaboration"]) {
      await page.locator(`[data-view="${next}"]`).click();
      assert.ok(await exit.isVisible());
    }
    await page.locator("#fullscreen-inspector").click();
    const mobileCanvas = await page.locator("#drawing").boundingBox();
    const mobileInspector = await page.locator("#graph-inspector").boundingBox();
    assert.ok(await page.locator("#inspector-resizer").isHidden());
    assert.ok(mobileCanvas.height > 150 && mobileInspector.y >= mobileCanvas.y + mobileCanvas.height);
    await exit.click();
    await page.getByRole("button", {name:"全屏查看"}).waitFor();
    assert.deepEqual(errors, []);
    console.log("Workbench views, drilldown, generation, source anchors, notes, fullscreen and inspector resizing passed.");
  } finally {
    if (browser) await browser.close();
    fs.rmSync(temp, {recursive:true, force:true});
  }
}
main().catch(err => { console.error(err); process.exitCode = 1; });
