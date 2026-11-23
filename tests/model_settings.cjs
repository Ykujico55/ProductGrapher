// Real browser -> local workbench -> local model fixture. No external API calls.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const {spawn} = require("node:child_process");
const {once} = require("node:events");
const {createInterface} = require("node:readline");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

function modelReply(body) {
  if (body.model === "chat-fixture") return "本机问答已收到选区。";
  const payload = JSON.parse(body.messages.at(-1).content);
  if (payload.task === "verify") return JSON.stringify({claimReplacements: [], itemReplacements: []});
  if (payload.task === "domains") return JSON.stringify({domains: payload.groups.map(g => ({name: g.name, groupIds: [g.id]}))});
  const items = payload.nodes.filter(n => n.kind === "function").map(n => ({
    nodeId: n.id, title: n.label, text: n.label, aliases: [], capability: n.label,
    refs: [{file: n.file, line: n.line, end: n.end}],
  }));
  return JSON.stringify({items, overview: {claims: [{text: "提交", refs: items[0].refs}]}});
}

async function test() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-model-settings-"));
  const repo = path.join(folder, "repo");
  fs.mkdirSync(repo);
  fs.writeFileSync(path.join(repo, "job.py"), "def submit(): pass\n");
  const calls = [], secret = "temporary-test-credential";
  let releaseFirstRequest;
  const firstRequest = new Promise(resolve => {releaseFirstRequest = resolve;});
  let rejectRegeneration = true;
  const provider = http.createServer(async (req, res) => {
    let text = "";
    for await (const chunk of req) text += chunk;
    const body = JSON.parse(text);
    calls.push({model: body.model, key: req.headers.authorization});
    res.setHeader("content-type", "application/json");
    if (body.model === "guide-fixture") await firstRequest;
    if (body.model === "second-guide" && rejectRegeneration) {
      rejectRegeneration = false;
      res.writeHead(401);
      res.end(JSON.stringify({error: secret}));
      return;
    }
    res.end(JSON.stringify({choices: [{message: {content: modelReply(body)}}]}));
  });
  let app, browser;
  async function start() {
    const env = {...process.env, LOCALAPPDATA: folder, HOW2TRUST_LLM_CONFIG: "", HOW2TRUST_MODEL: "",
      HOW2TRUST_BASE_URL: "", MODEL_SETTINGS_FIXTURE: "from-env"};
    app = spawn(env.PYTHON || "python", ["-B", "-X", "utf8", "-m", "how2trust", "serve", repo, "--port", "0"],
      {cwd: path.join(__dirname, ".."), env, windowsHide: true, stdio: ["ignore", "pipe", "inherit"]});
    for await (const line of createInterface({input: app.stdout}))
      if (line.startsWith("http://127.")) return line;
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
    const endpoint = "http://127.0.0.1:" + provider.address().port;
    const base = await start();
    browser = await chromium.launch({channel:"chrome", executablePath:process.env.CHROME_BIN});
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(base);
    const button = page.getByRole("button", {name:"模型设置", exact:true});
    async function save() {
      await page.getByRole("button", {name:"保存并应用"}).click();
      await page.locator("#model-dialog").waitFor({state:"hidden"});
    }
    await button.click();
    await page.getByLabel("供应商名称").fill("本地测试");
    await page.getByLabel("API 基础地址").fill(endpoint);
    await page.getByLabel("模型名", {exact:true}).fill("guide-fixture");
    await page.getByLabel("鉴权方式").selectOption("env");
    await page.getByLabel("密钥环境变量名").fill("MODEL_SETTINGS_FIXTURE");
    await save();
    await page.locator('[data-view="product"]').click();
    await page.locator(".generate-in-canvas").click();
    const progress = page.locator("#generation-feedback");
    const batches = page.locator("#generation-batches");
    await batches.getByText(/job\.py/).first().waitFor();
    assert.equal(await progress.getAttribute("data-status"), "running");
    assert.equal(await page.locator("#generation-progress").getAttribute("max"), "1");
    assert.equal(await page.locator("#generation-progress").getAttribute("value"), "0");
    // A newly opened workbench observes the same run without resubmitting it.
    const observer = await browser.newPage();
    await observer.goto(base + "#view=business");
    await observer.locator("#generation-batches").getByText(/job\.py/).first().waitFor();
    assert.equal(calls.length, 1);
    releaseFirstRequest();
    await observer.locator(".reading-claim").first().waitFor();
    await observer.close();
    await page.locator("#generate-semantics").waitFor({state:"hidden"});
    assert.equal((await (await page.request.get(base+"api/graph")).json()).semantics.model, "guide-fixture");
    await button.click();
    await page.getByLabel("应用范围").selectOption("chat");
    await page.getByLabel("模型名", {exact:true}).fill("chat-fixture");
    await page.getByLabel("鉴权方式").selectOption("session");
    await page.getByLabel("API Key", {exact:true}).fill(secret);
    await save();
    await page.locator('[data-inspect="chat"]').click();
    await page.getByLabel("向模型提问").fill("这段代码做了什么？");
    await page.getByRole("button", {name:"发送问题"}).click();
    await page.getByText("本机问答已收到选区。").waitFor();
    assert.ok(calls.some(c => c.model === "chat-fixture" && c.key === "Bearer "+secret));
    assert.ok(calls.some(c => c.model === "guide-fixture" && c.key === "Bearer from-env"));
    await button.click();
    await page.getByLabel("应用范围").selectOption("semantics");
    await page.getByLabel("模型名", {exact:true}).fill("second-guide");
    await save();
    await page.getByRole("button", {name:"用当前模型重新生成"}).click();
    await page.locator('#generation-feedback[data-status="failed"]').waitFor();
    assert.match(await page.locator("#generation-error").innerText(), /401/);
    assert.match(await progress.innerText(), /job\.py/);
    assert.ok(!(await progress.innerText()).includes(secret));
    assert.equal((await (await page.request.get(base+"api/graph")).json()).semantics.model, "guide-fixture");
    await page.reload();
    await page.locator('#generation-feedback[data-status="failed"]').waitFor();
    await page.locator("#generation-retry").click();
    await page.locator("#generate-semantics").waitFor({state:"hidden"});
    assert.equal((await (await page.request.get(base+"api/graph")).json()).semantics.model, "second-guide");
    for (const url of ["api/models", "api/state", "api/semantics/progress", "export.html"])
      assert.ok(!(await (await page.request.get(base+url)).text()).includes(secret));
    assert.ok(!fs.readFileSync(path.join(folder, "how2trust", "workbench-models.json"), "utf8").includes(secret));
    await stop();
    const restarted = await start();
    const prefs = await (await page.request.get(restarted+"api/models")).json();
    assert.equal(prefs.chat.model, "chat-fixture");
    assert.equal(prefs.chat.sessionKey, false);
    assert.equal(prefs.chat.useSessionKey, true);
    assert.equal((await (await page.request.get(restarted+"api/state")).json()).llm.configured, false);
    assert.equal(prefs.semantics.model, "second-guide");
    assert.equal(prefs.semantics.api_key_env, "MODEL_SETTINGS_FIXTURE");
    assert.deepEqual(errors, []);
    console.log("Model settings, real batch progress, reconnect, error details, retry, chat and restart passed.");
  } finally {
    releaseFirstRequest();
    if (browser) await browser.close();
    if (app) await stop();
    provider.close();
    fs.rmSync(folder, {recursive:true, force:true});
  }
}
test().catch(e => {console.error(e); process.exitCode = 1;});
