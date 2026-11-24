// Browser regression for the semantic overlay. The source fixture uses real Terra code.
const assert = require("node:assert/strict");
const {execFileSync} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");
const root = path.resolve(__dirname, "..");

function makeFixture(output) {
  const source = `
import json, sys
from pathlib import Path
from how2trust.capabilities import capability_groups, shared_calls
from how2trust.project import build_graph, digest
from how2trust.semantics import validate_semantics
from how2trust.web import standalone

graph = build_graph(Path("audit/terra-fulfillment/project"))
ship = next(n for n in graph["nodes"] if n["kind"] == "entry" and n["label"] == "ship")
impl = next(n for n in graph["nodes"] if n["qualname"] == "Desk.ship")
desk = next(n for n in graph["nodes"] if n["qualname"] == "Desk" and n["kind"] == "class")
retry = next(n for n in graph["nodes"] if n["qualname"] == "Desk.retry")
lookup = next(n for n in graph["nodes"] if n["qualname"] == "Desk.get_order")
line = next(i for i,s in enumerate(graph["files"][impl["file"]].splitlines(), 1) if '"partial" if pending else "shipped"' in s)
refs = [{"file":impl["file"], "line":line, "end":line}]
retry_refs = [{"file":retry["file"], "line":start, "end":end} for start,end in [(101,101),(157,157),(185,186)]]
claims = [{"text":"多仓库共同履约，订单可分批配送。","refs":refs},
          {"text":"补货、取消及可再售退货都可调用重试。","refs":retry_refs}]
eligible = sum(bool(graph["files"].get(n["file"])) for n in graph["nodes"])
semantics = {
    "schema":"how2trust.semantics.v1", "snapshot":graph["snapshot"], "provider":"Browser fixture", "model":"local-fixture",
    "overview":{"text":" ".join(c["text"] for c in claims), "refs":refs + retry_refs, "claims":claims},
    "items":[{"nodeId":ship["id"], "title":"按仓发货", "text":"仍有待发分配时，订单显示部分发货。",
              "capability":"订单出库", "aliases":["分批配送"], "refs":refs},
             {"nodeId":impl["id"], "title":"更新出库状态", "text":"根据待发分配更新订单状态。",
              "capability":"订单出库", "aliases":[], "refs":refs},
             {"nodeId":desk["id"], "title":"履约工作台", "text":"Desk 类管理履约数据。",
              "capability":"履约存储", "aliases":[], "refs":[{"file":desk["file"], "line":desk["line"], "end":desk["line"]}]},
             {"nodeId":retry["id"], "title":"重试等待订单", "text":"按顺序尝试分配等待订单。",
              "capability":"库存重试", "aliases":[], "refs":[{"file":retry["file"], "line":87, "end":91}]},
             {"nodeId":lookup["id"], "title":"读取订单", "text":"按订单号查找订单。", "aliases":[],
              "refs":[{"file":lookup["file"],"line":43,"end":47}]}],
    "coverage":{"eligible":eligible, "explained":5}
}
semantics["groups"] = capability_groups(graph, semantics["items"])
semantics["sharedCalls"] = shared_calls(graph, semantics["items"])
assert semantics["sharedCalls"][0]["target"] == lookup["id"]  # More entry paths, but no joint overview claim.
semantics["fingerprint"] = digest(semantics)
validate_semantics(graph, semantics)
Path(sys.argv[1]).write_bytes(standalone({**graph, "semantics":semantics}))
print(json.dumps({"graph":graph,"semantics":semantics,"methodId":impl["id"]}, ensure_ascii=True))
`;
  return JSON.parse(execFileSync(process.env.PYTHON || "python",
    ["-B", "-X", "utf8", "-c", source, output], {cwd:root, encoding:"utf8", windowsHide:true}));
}

async function inspect(page, graph, semantics, methodId) {
  const reading = page.getByRole("region", {name:"模型业务解读"});
  await reading.waitFor();
  assert.match(await reading.innerText(), /模型解读.*local-fixture/);
  assert.match(await reading.innerText(), /多仓库共同履约/);
  const drawing = page.locator("#drawing");
  const card = label => drawing.getByRole("button", {name:label, exact:true});
  const group = semantics.groups.find(g => g.label === "订单出库");
  const desk = graph.nodes.find(n => n.qualname === "Desk" && n.kind === "class");
  const layer = page.getByRole("combobox", {name:"首页图层"});
  assert.equal(await layer.inputValue(), "business");
  assert.equal(await drawing.locator(".graph-node").count(), 2);
  assert.equal(await card(group.label).count(), 1);
  assert.equal(await card("履约存储").count(), 0);
  const internalInTree = page.locator("#tree").getByRole("button",{name:"履约存储",exact:true});
  assert.equal(await internalInTree.count(), 0);
  await layer.selectOption("all");
  assert.equal(await drawing.locator(".graph-node").count(), semantics.groups.length + 1);
  assert.ok(await internalInTree.isVisible());
  await layer.selectOption("internal");
  assert.equal(await card(group.label).count(), 0);
  assert.equal(await card("履约存储").count(), 1);
  assert.ok(await internalInTree.isVisible());
  await layer.selectOption("source");
  assert.equal(await card("源码模块").count(), 1);
  assert.equal(await card(group.label).count(), 0);
  assert.ok(await page.locator("#tree").getByRole("button", {name:"源码模块",exact:true}).isVisible());
  assert.equal(await page.getByRole("button", {name:"测试与夹具",exact:true}).count(), 0);
  await layer.selectOption("business");
  const claim = reading.locator(".reading-claim").filter({hasText:"订单可分批配送"});
  await claim.getByRole("button", {name:/store\.py · L139/}).click();
  assert.match(await page.locator("#inspect-content > .proof[open]").first().innerText(), /"partial" if pending else "shipped"/);
  await page.locator("#home").click();
  const shared = page.getByRole("region", {name:"多个入口共用的实现"});
  const retry = shared.locator(".shared-target").filter({has:page.locator("summary", {hasText:"Desk.retry"})});
  const summary = await retry.locator("summary").innerText();
  for (const command of ["stock","cancel","return"]) assert.ok(summary.includes(command));
  assert.equal(await shared.locator("summary").first().innerText(), summary);
  await layer.selectOption("internal");
  await card("库存重试").click();
  assert.equal(await shared.locator("summary").count(), 1);
  await retry.locator("summary").click();
  await retry.getByRole("button", {name:"→ Desk.retry · L186"}).click();
  const sourceAtCall = page.locator("#inspect-content > .proof[open]").first();
  assert.match(await sourceAtCall.innerText(), /if quality == "sellable"/);
  assert.match(await sourceAtCall.locator(".source-line.focus").innerText(), /self\.retry\(/);
  await page.locator("#home").click();
  await card("履约存储").click();
  await card("履约工作台 · Desk").click();
  assert.equal(await page.locator('[data-view="class"]').getAttribute("aria-pressed"), "true");
  assert.equal(await drawing.locator(".graph-node.selected").getAttribute("data-key"), desk.id);
  await page.locator("#home").click();
  await layer.selectOption("business");
  await card(group.label).click();
  assert.equal(await page.locator("#scope-name").innerText(), group.label);
  assert.match(await page.locator("#inspect-content").innerText(), /模型根据这些入口和代码符号的实际职责归组/);
  const members = await drawing.locator(".graph-node").evaluateAll(nodes => nodes.map(n => n.dataset.key));
  assert.deepEqual(members.sort(), [group.id, ...group.children.map(leaf => leaf.id)].sort());
  await card("按仓发货 · ship").click();
  assert.match(await page.locator("#crumbs").innerText(), /订单出库/);
  assert.equal(await page.locator('[data-view="call"]').getAttribute("aria-pressed"), "true");
  const ship = semantics.items[0].nodeId;
  assert.equal(await card("按仓发货 · ship").getAttribute("aria-pressed"), "true");
  const call = graph.edges.find(edge => edge.kind === "calls" && edge.source === ship && edge.target === methodId);
  assert.ok(call, "The Terra CLI ship entry directly dispatches to Desk.ship");
  await drawing.locator(`.graph-edge[data-key="${call.id}"]`).focus();
  await page.keyboard.press("Enter");
  const proof = page.locator("#inspect-content .source-line.focus").first();
  assert.match(await proof.innerText(), /desk\.ship\(/i);
  assert.equal(await proof.locator("b").innerText(), String(call.line));
  await page.locator('[data-view="class"]').click();
  assert.equal(await card("Desk").getAttribute("data-key"), desk.id);
  await card("Desk").click();
  assert.match(await page.locator("#inspect-content").innerText(), /类中定义的方法/);
  await page.locator("#inspect-content").getByRole("button", {name:/^ship · L/}).click();
  assert.match(await proof.innerText(), /def ship\(/);
  await page.locator("#home").click();
  await page.locator("#search").fill("处理分批配送");
  const related = page.locator(".related-terms .symbol-result");
  await related.first().waitFor();
  assert.match(await related.first().innerText(), /按仓发货.*ship[\s\S]*匹配业务词「分批配送」/);
  for (const text of ["分批配送", "部分发货", "ship"]) {
    await page.locator("#search").fill(text);
    await page.locator("#tree").getByRole("button", {name:"按仓发货 · ship", exact:true}).waitFor();
  }
  await page.locator("#tree").getByRole("button", {name:"按仓发货 · ship", exact:true}).click();
  assert.match(await reading.innerText(), /仍有待发分配/);
  assert.match(await page.locator("#scope-name").innerText(), /按仓发货 · ship/);
  const article = reading.locator(".reading-citations");
  await article.locator("summary").first().click();
  await article.locator(".proof > summary").first().click();
  assert.match(await article.locator(".source-line.focus").first().innerText(), /"partial" if pending else "shipped"/);
  await article.getByRole("button", {name:"在图中定位 Desk.ship"}).click();
  assert.equal(await page.locator(".graph-node.selected").getAttribute("data-key"), methodId);
  const selectedProof = page.locator("#inspect-content > .proof[open]").first();
  assert.match(await selectedProof.locator(".source-line.focus").first().innerText(), /"partial" if pending else "shipped"/);
}

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-semantics-"));
  const file = path.join(temp, "graph.html");
  let browser;
  try {
    const {graph, semantics, methodId} = makeFixture(file);
    browser = await chromium.launch({channel:"chrome", executablePath:process.env.CHROME_BIN});
    const page = await browser.newPage({viewport:{width:1366, height:768}});
    const errors = [];
    page.on("pageerror", err => errors.push(err.message));
    await page.goto(pathToFileURL(file).href);
    assert.equal(await page.locator("#generate-semantics").isVisible(), false);
    await inspect(page, graph, semantics, methodId);

    const ref = semantics.items[0].refs[0];
    await page.goto(pathToFileURL(file).href + "#" + new URLSearchParams({file:ref.file, line:ref.line}));
    await page.waitForFunction(line =>
      document.querySelector("#inspect-content > .proof[open] .source-line.focus b")?.textContent === String(line), ref.line);
    await page.reload();
    assert.equal(await page.locator("#inspect-content > .proof[open] .source-line.focus b").first().innerText(), String(ref.line));

    await page.locator("#search").fill("分批配送");
    const leaf = page.locator("#tree .tree-item").filter({hasText:"按仓发货"});
    await leaf.click();
    await page.locator('[data-inspect="notes"]').click();
    await page.getByLabel("批注内容").fill("按仓发货说明");
    await page.getByRole("button", {name:"保存批注"}).click();
    await page.locator(".note-card").waitFor();
    // Earlier exports stored notes against the tree leaf instead of the underlying entry.
    await page.evaluate(() => {
      const graph = window.H2T_GRAPH;
      const key = "How2Trust.notes." + graph.snapshot;
      const [note] = JSON.parse(localStorage.getItem(key));
      const stack = [graph.tree];
      while (stack.length) {
        const node = stack.pop();
        if (node.kind === "entry" && node.label === "ship") {
          localStorage.setItem(key, JSON.stringify([note, {...note, id:"old", targets:[node.id], text:"旧版批注"}]));
          break;
        }
        stack.push(...node.children);
      }
    });
    await page.reload();
    await page.locator("#search").fill("处理分批配送");
    await page.locator(".related-terms .symbol-result").first().click();
    await page.locator('[data-inspect="notes"]').click();
    assert.equal(await page.locator(".note-card").count(), 2);
    await page.locator("#search").fill("");
    await page.locator("#home").click();
    await page.getByRole("combobox", {name:"首页图层"}).selectOption("source");
    await page.locator("#drawing").getByRole("button", {name:"dispatchdesk", exact:true}).click();
    await page.locator("#drawing").getByRole("button", {name:"按仓发货 · ship", exact:true}).click();
    assert.match(await page.locator("#crumbs").innerText(), /dispatchdesk/);
    await page.locator('[data-inspect="notes"]').click();
    assert.equal(await page.locator(".note-card").count(), 2);
    assert.match(await page.locator("#inspect-content").innerText(), /按仓发货说明/);
    assert.match(await page.locator("#inspect-content").innerText(), /旧版批注/);

    // Exercise the browser's POST contract independently of vendor availability.
    let requests = 0;
    await page.route("http://how2trust.test/**", async route => {
      const p = new URL(route.request().url()).pathname;
      if (p === "/api/graph") return route.fulfill({json:graph});
      if (p === "/api/state") return route.fulfill({json:{notes:[], threads:[], llm:{configured:false},
        semanticLlm:{configured:true, provider:semantics.provider, model:semantics.model}}});
      if (p === "/api/semantics") {
        assert.equal(route.request().method(), "POST");
        requests++;
        return route.fulfill({json:semantics});
      }
      const asset = {"/":"index.html","/assets/app.js":"app.js","/assets/style.css":"style.css"}[p];
      if (!asset) return route.abort();
      return route.fulfill({path:path.join(root, "how2trust/assets", asset)});
    });
    await page.goto("http://how2trust.test/");
    const generate = page.locator("#generate-semantics");
    await generate.click();
    await inspect(page, graph, semantics, methodId);
    assert.equal(await generate.isVisible(), false);
    assert.equal(requests, 1);
    const oldGuide = JSON.parse(JSON.stringify(semantics));
    delete oldGuide.overview.claims;
    delete oldGuide.sharedCalls;
    // The same fallback serves libraries without entries and guides which only explain internals.
    oldGuide.groups = oldGuide.groups.filter(group => group.children.every(leaf => leaf.kind !== "entry"));
    await page.route("http://how2trust.test/api/graph", route=>route.fulfill({json:{...graph, semantics:oldGuide}}));
    await page.reload();
    assert.match(await page.getByRole("region", {name:"模型业务解读"}).innerText(), /订单可分批配送/);
    assert.equal(await page.locator(".reading-citations").count(), 1);
    assert.equal(await page.locator("#shared-calls").isVisible(), false);
    assert.equal(await page.locator("#root-layer").inputValue(), "business");
    assert.equal(await page.locator('#root-layer option[value="internal"]').count(), 0);
    assert.equal(await page.locator("#drawing .graph-node").count(), oldGuide.groups.length + 1);
    await page.locator("#root-layer").selectOption("source");
    assert.ok(await page.locator("#drawing").getByRole("button", {name:"源码模块",exact:true}).isVisible());
    assert.deepEqual(errors, []);
    console.log("Semantic navigation passed offline and online: capability, entry, class, actual call edge and cited source. Shared notes verified across business and static entry paths.");
  } finally {
    if (browser) await browser.close();
    if (fs.existsSync(file)) fs.unlinkSync(file);
    fs.rmdirSync(temp);
  }
}

main().catch(error => {console.error(error); process.exitCode = 1;});
