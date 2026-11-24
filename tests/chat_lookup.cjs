// Browser -> workbench -> local DeepSeek-shaped responses, using a synthetic repository.
const assert = require("node:assert/strict");
const {execFileSync, spawn} = require("node:child_process");
const {once} = require("node:events");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const {createInterface} = require("node:readline");
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || "playwright");

const file = "demo/services/migrate.py";
const statusFile = "demo/services/status.py";
// Captured shape, including the missing opening parameter for the action.
const dsml = [
  '<｜｜DSML｜｜ calls>',
  '<｜｜DSML｜｜ invoke name="lookup" string="true">find</｜｜DSML｜｜ parameter>',
  '<｜｜DSML｜｜ parameter name="query" string="true">migrate</｜｜DSML｜｜ parameter>',
  '<｜｜DSML｜｜ parameter name="offset" string="false">0</｜｜DSML｜｜ parameter>',
  '</｜｜DSML｜｜ invoke>',
  '</｜｜DSML｜｜ calls>',
].join("\n");
const question = "这个示例如何读取 **迁移状态**？";
const fieldName = "last_successfully_applied_migration_before_synchronization";
const codeLine = `return ${fieldName}("<em>ready</em>")`;
const answer = [
  "## 迁移结果", "",
  `有记录时返回 **已迁移**，状态值是 \`migrated once\`。[${statusFile}:L1]`, "",
  "- 读取记录", "  - 检查状态", "- 返回结果", "",
  "```python", codeLine, "```", "",
  "| 输入 | 返回值 |", "| --- | --- |", "| 空记录 | None |", `| 字段 | ${fieldName} |`, "",
  "[SQLite 文档](https://docs.python.org/3/library/sqlite3.html)", "",
  '<button id="reply-html-test">示例标签</button>',
].join("\n");
const followup = "没有记录时呢？";
const nextAnswer = `没有记录时返回 None。[${file}:L5]`;

async function checkMarkdown(card) {
  await card.getByRole("heading", {name:"迁移结果", exact:true}).waitFor();
  assert.equal(await card.locator("p strong").textContent(), "已迁移");
  assert.equal(await card.locator("p code").textContent(), "migrated once");
  assert.equal(await card.locator("ul > li > ul > li").textContent(), "检查状态");
  assert.equal((await card.locator("pre > code").textContent()).trimEnd(), codeLine);
  assert.equal(await card.locator("pre em, #reply-html-test").count(), 0);
  assert.deepEqual(await card.getByRole("columnheader").allTextContents(), ["输入", "返回值"]);
  assert.deepEqual(await card.getByRole("cell").allTextContents(), ["空记录", "None", "字段", fieldName]);
  assert.equal(await card.getByRole("link", {name:"SQLite 文档"}).getAttribute("href"),
    "https://docs.python.org/3/library/sqlite3.html");
}

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "how2trust-chat-lookup-"));
  const repo = path.join(tmp, "repo");
  const db = path.join(tmp, "chat.sqlite");
  const python = process.env.PYTHON || "python";
  fs.mkdirSync(path.dirname(path.join(repo, file)), {recursive:true});
  fs.writeFileSync(path.join(repo, file),
    "from .status import MIGRATED\n\ndef migrate(record):\n    if record: return MIGRATED\n    return None\n");
  fs.writeFileSync(path.join(repo, statusFile), 'MIGRATED = "migrated once"\n');

  const requests = [];
  let broken = false;
  const provider = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    const {messages} = JSON.parse(body);
    requests.push(messages);
    const last = messages.at(-1).content;
    let content = dsml;
    if (broken) content = '<｜DSML｜function_calls><｜DSML｜invoke name="lookup">';
    else if (last.includes("migrated once")) content = answer;
    else if (last.includes(followup)) content = nextAnswer;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({choices: [{message: {content}}]}));
  });
  let app, browser;
  try {
    provider.listen(0, "127.0.0.1");
    await once(provider, "listening");
    app = spawn(python, ["-B", "-X", "utf8", "-m", "how2trust", "serve", repo, "--db", db, "--port", "0",
      "--model", "local-fixture", "--base-url", "http://127.0.0.1:" + provider.address().port,
      "--key-env", ""], {cwd: path.resolve(__dirname, ".."),
      env: {...process.env, LOCALAPPDATA: tmp}, windowsHide: true,
      stdio: ["ignore", "pipe", "inherit"]});
    let base;
    for await (const line of createInterface({input: app.stdout})) {
      if (line.startsWith("http://127.")) { base = line; break; }
    }
    assert.ok(base, "Workbench did not start");
    browser = await chromium.launch({channel: "chrome", executablePath: process.env.CHROME_BIN});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const state = async () => (await (await page.request.get(base + "api/state")).json()).threads;
    await page.goto(base + "#view=structure");
    await page.locator("#search").fill("services");
    await page.locator("#tree").getByRole("button", {name:"services", exact:true}).click();
    await page.locator("#drawing").getByRole("button", {name:file, exact:true}).click();
    await page.locator('[data-inspect="chat"]').click();
    await page.locator(".scope-chip").filter({hasText:file}).waitFor();
    const input = page.getByLabel("向模型提问");
    const ask = async text => {
      await input.fill(text);
      const posted = page.waitForResponse(r => r.url() === base + "api/chat" && r.request().method() === "POST");
      await page.getByRole("button", {name:"发送问题"}).click();
      return await posted;
    };
    assert.ok((await ask(question)).ok());
    const card = page.locator(".message.assistant").first();
    await checkMarkdown(card);
    await page.locator(".message.user").getByText(question, {exact:true}).waitFor();
    const resizer = page.locator("#inspector-resizer");
    for (let i = 0; i < 4; i++) await resizer.press("ArrowRight");
    for (const container of [card.locator("pre"), card.locator("table").locator("..")]) {
      assert.ok(await container.evaluate(el => {
        el.scrollLeft = 50;
        return el.scrollLeft > 0;
      }), "Wide Markdown content should scroll within its own block");
    }
    assert.ok(await page.locator("#inspect-content").evaluate(el => el.scrollWidth <= el.clientWidth + 1),
      "Narrow inspector should not overflow horizontally");
    assert.equal(requests.length, 2, "Expected one local lookup followed by a final answer");
    assert.ok(!requests[0].at(-1).content.includes("migrated once"));
    assert.match(requests[1].at(-1).content, /migrated once/);
    const [thread] = await state();
    assert.deepEqual(thread.targets, ["module:" + file]);
    assert.deepEqual(thread.messages.map(m => m.content), [question, answer]);
    assert.deepEqual(thread.messages[1].sources, [{file:statusFile, line:1, end:1}]);
    await card.getByText("查看引用（1）").click();
    await card.locator(".proof > summary").click();
    assert.match(await card.locator(".source-lines").innerText(), /MIGRATED = "migrated once"/);
    assert.ok((await ask(followup)).ok());
    await page.locator(".message.assistant").last().getByText(nextAnswer).waitFor();

    async function reopenThread() {
      await page.reload();
      await page.locator("#drawing .graph-node").first().waitFor();
      await page.locator('[data-inspect="chat"]').click();
      await page.getByLabel("选择历史对话").selectOption(thread.id);
    }
    await reopenThread();
    await checkMarkdown(card);

    // Reopen a history in the pre-fix format, complete with a later successful follow-up.
    execFileSync(python, ["-c", "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); " +
      "db.execute(\"UPDATE messages SET content=?,sources='[]' WHERE thread=? AND seq=1\", sys.argv[2:4]); db.commit()",
      db, dsml, thread.id], {windowsHide:true});
    await reopenThread();
    const retry = page.getByRole("button", {name:"重新回答此问题"});
    await retry.waitFor();
    assert.ok(!(await page.locator("#inspect-content").innerText()).includes("DSML"));
    broken = true;
    const failed = page.waitForResponse(r => r.url() === base + "api/chat");
    await retry.click();
    assert.ok(!(await failed).ok());
    await retry.waitFor();
    assert.match(await card.getByRole("alert").innerText(), /重新回答失败.*查询.*重试/);
    assert.ok((await state())[0].messages[1].needsRetry);
    assert.ok(!(await page.locator("#inspect-content").innerText()).includes("DSML"));

    broken = false;
    const draft = "再解释一下调用方";
    await input.fill(draft);
    const before = requests.length;
    await retry.click();
    await checkMarkdown(card);
    assert.equal(await input.inputValue(), draft);
    assert.equal(await retry.count(), 0);
    assert.deepEqual((await state())[0].messages.map(m => m.content), [question, answer, followup, nextAnswer]);
    assert.ok(requests[before].at(-1).content.includes(file));
    assert.ok(!JSON.stringify(requests[before]).includes(followup), "Retry included future turns");
    assert.deepEqual(errors, []);
    console.log("Markdown replies, verified citations, stored conversations, DSML lookup and legacy retry passed.");
  } finally {
    if (browser) await browser.close();
    if (app && app.exitCode === null && app.signalCode === null) {
      const exited = once(app, "exit");
      app.kill();
      await exited;
    }
    provider.close();
    fs.rmSync(tmp, {recursive:true, force:true});
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
