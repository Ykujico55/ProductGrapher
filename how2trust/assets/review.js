(() => {
  "use strict";
  const report = JSON.parse(document.querySelector("#report-data").textContent);
  const $ = (id) => document.getElementById(id);
  const modeInfo = {
    requirements: ["要求完成情况", "验收要求"],
    defenses: ["防御逻辑", "防御逻辑复核"],
    scope: ["改动范围", "变更归属"],
    changes: ["文件差异", "变更文件"],
  };
  const rows = Object.fromEntries(
    Object.keys(modeInfo).map((key) => [key, report[key] || []]),
  );
  const byFile = new Map(rows.changes.map((change) => [change.file, change]));
  const reqs = new Map(rows.requirements.map((req) => [req.id, req]));
  const statusText = {
    passed: "检查通过",
    failed: "检查失败",
    error: "执行异常",
    unverified: "未验证",
    not_run: "未运行",
    skipped: "未运行",
    partial: "部分验证",
    review: "待复核",
    mapped: "已匹配约定范围",
    forbidden: "违反明示规则",
    unmapped: "待说明归属",
    added: "新增",
    modified: "修改",
    deleted: "删除",
    typechanged: "类型变化",
  };
  const decisionLabels = {
    requirements: {
      accept: "确认满足要求",
      reject: "要求尚未满足",
      more: "需要补充证据",
    },
    defenses: {
      accept: "防御有依据",
      reject: "判定为过度防御",
      more: "需要说明失败场景",
    },
    scope: {
      accept: "属于任务范围",
      reject: "判定为超出范围",
      more: "需要说明归属",
    },
  };
  const pageSize = 80;
  let mode = "requirements",
    active = null,
    limit = pageSize,
    query = "",
    filter = "all";
  let judgments = {},
    toastTimer;
  const decisionKey = "how2trust.review.1." + report.fingerprint;
  try {
    judgments = JSON.parse(localStorage.getItem(decisionKey) || "{}") || {};
  } catch {
    /* storage unavailable */
  }
  function el(tag, cls, text) {
    const elem = document.createElement(tag);
    if (cls) elem.className = cls;
    if (text !== undefined && text !== null) elem.textContent = String(text);
    return elem;
  }
  function button(cls, text, handler) {
    const elem = el("button", cls, text);
    elem.type = "button";
    elem.addEventListener("click", handler);
    return elem;
  }
  function field(parent, term, value) {
    if (value === undefined || value === null || value === "") return;
    parent.append(el("dt", "", term), el("dd", "", value));
  }
  function section(parent, title) {
    const box = el("section", "section");
    box.append(el("h3", "", title));
    parent.append(box);
    return box;
  }
  function notice(message) {
    clearTimeout(toastTimer);
    $("notice").textContent = message;
    toastTimer = setTimeout(() => ($("notice").textContent = ""), 2800);
  }
  const showValue = (value) =>
    typeof value === "object" ? JSON.stringify(value) : String(value);
  const keyOf = (row) => (mode === "changes" ? row.file : row.id);
  const decisionId = (row) => mode + "|" + keyOf(row);
  const textOf = (row) =>
    mode === "requirements"
      ? row.text
      : mode === "changes"
        ? row.file
        : row.title || row.symbol || row.file;
  function tag(status) {
    const tone = ["failed", "error", "forbidden"].includes(status)
      ? "bad"
      : ["passed", "mapped"].includes(status)
        ? "good"
        : ["review", "unmapped", "partial", "unverified"].includes(status)
          ? "warn"
          : "";
    return el("span", "tag " + tone, statusText[status] || status || "待复核");
  }
  function summary(key) {
    const list = rows[key];
    const count = (status) =>
      list.filter((row) => row.status === status).length;
    if (key === "requirements")
      return count("passed") + " / " + list.length + " 项检查通过";
    if (key === "defenses")
      return list.length + " 项发现 · " + count("forbidden") + " 项违反规则";
    if (key === "scope")
      return (
        count("unmapped") + " 项待说明 · " + count("forbidden") + " 项越界"
      );
    return list.length + " 个变更文件";
  }
  function renderCategories() {
    const nav = $("categories");
    nav.replaceChildren();
    for (const [key, [label]] of Object.entries(modeInfo)) {
      const tab = button("category", "", () => switchMode(key));
      tab.append(el("strong", "", label), el("small", "", summary(key)));
      tab.setAttribute("aria-pressed", String(mode === key));
      nav.append(tab);
    }
  }
  function switchMode(key, target) {
    mode = key;
    query = "";
    filter = "all";
    limit = pageSize;
    active = target || null;
    $("search").value = "";
    const options = [["all", "全部"]];
    if (mode !== "changes")
      options.push(
        ["attention", "需要关注"],
        ["open", "未复核"],
        ["reviewed", "已复核"],
      );
    $("filter").replaceChildren(
      ...options.map(([value, label]) => new Option(label, value)),
    );
    renderCategories();
    renderList();
    renderDetail();
  }
  function matches(row) {
    const decision = judgments[decisionId(row)];
    if (filter === "open" && decision?.choice) return false;
    if (filter === "reviewed" && !decision?.choice) return false;
    if (
      filter === "attention" &&
      ["passed", "mapped"].includes(row.status) &&
      !["more", "reject"].includes(decision?.choice)
    )
      return false;
    if (!query) return true;
    return [
      keyOf(row),
      textOf(row),
      row.file,
      row.symbol,
      row.target,
      row.detail,
      ...(row.requirementIds || []),
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query));
  }
  function renderList() {
    const visible = rows[mode].filter(matches);
    if (!active || !visible.includes(active)) active = visible[0] || null;
    $("list-title").textContent = modeInfo[mode][1];
    $("list-count").textContent = visible.length + " 项";
    const list = $("items");
    list.replaceChildren();
    for (const row of visible.slice(0, limit)) {
      const node = button("item", "", () => {
        active = row;
        renderList();
        renderDetail();
      });
      node.setAttribute("aria-current", String(active === row));
      node.append(tag(row.status));
      const decision = judgments[decisionId(row)];
      if (decision?.choice)
        node.append(
          el(
            "span",
            "tag",
            decisionLabels[mode]?.[decision.choice] || "已复核",
          ),
        );
      node.append(el("strong", "", textOf(row)));
      if (mode === "scope" || mode === "defenses")
        node.append(el("small", "", location(row)));
      list.append(node);
    }
    if (visible.length > limit)
      list.append(
        button("more", "显示更多", () => {
          limit += pageSize;
          renderList();
        }),
      );
    if (!visible.length) list.append(el("p", "empty", "没有符合条件的项目。"));
  }
  function location(point) {
    const side = point.side === "before" ? "基线" : "交付";
    if (!point.line) return point.file;
    return (
      point.file +
      " · " +
      side +
      " L" +
      point.line +
      (point.end > point.line ? "–" + point.end : "")
    );
  }
  function checkRecord(check) {
    const box = el("details", "check");
    const verdict = check.businessStatus || check.status;
    box.open = ["partial", "failed", "error", "unverified"].includes(verdict);
    const summary = el("summary", "", check.name || "验证检查");
    if (check.baseline) {
      summary.append(
        el("span", "check-label", "基线"),
        tag(check.baseline.businessStatus || check.baseline.status),
        el("span", "check-label", "交付"),
      );
    }
    summary.append(tag(verdict));
    box.append(summary);
    const info = el("dl");
    field(info, "执行参数", JSON.stringify(check.argv || []));
    const expect = check.expected || {};
    field(info, "预期退出码", expect.exit);
    if (typeof expect.stdout === "string")
      field(info, "预期标准输出", JSON.stringify(expect.stdout));
    if (expect.contains !== undefined)
      field(info, "输出必须包含", showValue(expect.contains));
    field(info, "实际退出码", check.actualExit);
    if (check.businessStatus) field(info, "命令执行结果", statusText[check.status]);
    field(info, "判定依据", check.detail);
    box.append(info);
    if (check.observations?.length) {
      const table = el("table", "observations");
      const header = table.createTHead().insertRow();
      for (const label of ["输出字段", "预期", "基线实测", "交付实测", "交付判定"]) {
        header.append(el("th", "", label));
      }
      const body = table.createTBody();
      const baseline = new Map((check.baseline?.observations || []).map(obs => [obs.path, obs]));
      const observed = (obs) => obs?.missing ? "字段缺失"
        : obs && "actual" in obs ? JSON.stringify(obs.actual) : "未取得";
      for (const obs of check.observations) {
        const row = body.insertRow();
        for (const value of [obs.path || "整个输出", JSON.stringify(obs.expected),
                             observed(baseline.get(obs.path)), observed(obs)]) {
          row.append(el("td", "", value));
        }
        row.insertCell().append(tag(obs.status));
      }
      box.append(table);
    }
    appendTrace(box, check.traceEvidence, false);
    if (check.sourceReference) {
      const ref = check.sourceReference;
      const area = el("details", "check");
      area.append(el("summary", "", "清单指定的源码位置 · " + ref.selector));
      if (ref.resolved) {
        area.append(el("p", "muted", location(ref)), el("pre", "output", ref.snippet));
        if (report.graphUrl === "/") {
          const link = el("a", "link-row", "在架构图中查看");
          link.href = "/#" + new URLSearchParams({file: ref.file, line: String(ref.line)});
          area.append(link);
        }
      } else {
        area.append(el("p", "empty", "交付快照中未找到该符号。"));
      }
      box.append(area);
    }
    if (verdict === "passed" && (check.baseline?.businessStatus || check.baseline?.status) === "passed") {
      box.append(
        el(
          "p",
          "muted",
          "基线也满足这一检查。若要验证新增行为，需要能区分两个版本的检查。",
        ),
      );
    }
    appendOutput(box, check);
    if (check.baseline) {
      const base = el("details", "check");
      const baseHead = el("summary", "", "同一检查在基线上的结果");
      baseHead.append(tag(check.baseline.businessStatus || check.baseline.status));
      base.append(baseHead);
      const baseInfo = el("dl");
      field(baseInfo, "退出码", check.baseline.actualExit);
      field(baseInfo, "判定依据", check.baseline.detail);
      base.append(baseInfo);
      appendTrace(base, check.baseline.traceEvidence, true);
      appendOutput(base, check.baseline);
      box.append(base);
    }
    return box;
  }
  function appendTrace(box, trace, baseline) {
    if (!trace) return;
    const labels = {observed: "已命中", not_observed: "未观察到调用", unavailable: "无法观测", not_run: "未运行"};
    const area = el("details", "check");
    area.append(el("summary", "", "运行时源码 · " + (labels[trace.status] || trace.status)));
    area.append(el("p", "muted", trace.detail));
    if (trace.file) {
      area.append(el("p", "copy", trace.file + " · " + trace.symbol + " · 采集解释器 " +
        trace.interpreters + " · 命中 " + trace.hits));
    }
    if (trace.sourceLines?.length) {
      const snippet = el("pre", "output");
      for (const row of trace.sourceLines) {
        if (report.graphUrl === "/" && !baseline) {
          const link = el("a", "", "L" + row.line);
          link.href = "/#" + new URLSearchParams({file: trace.file, line: String(row.line)});
          snippet.append(link);
        } else {
          snippet.append("L" + row.line);
        }
        snippet.append("  " + row.text + "\n");
      }
      area.append(snippet);
    }
    box.append(area);
  }
  function appendOutput(box, check) {
    for (const [field, label] of [
      ["output", "标准输出"],
      ["stderr", "标准错误"],
    ]) {
      if (check[field])
        box.append(el("h4", "", label), el("pre", "output", check[field]));
    }
    if (check.outputTruncated)
      box.append(el("p", "muted", "日志仅显示已保存的前 128 KB。"));
  }
  function hunkRange(hunk) {
    return (
      "−" +
      hunk.oldStart +
      "," +
      hunk.oldCount +
      "  +" +
      hunk.newStart +
      "," +
      hunk.newCount
    );
  }
  function decodeLines(hunk) {
    let old = hunk.oldStart,
      next = hunk.newStart;
    return hunk.lines.map((text) => {
      const prefix = text[0];
      return {
        old: prefix === "+" ? null : old++,
        next: prefix === "-" ? null : next++,
        text,
        prefix,
      };
    });
  }
  function drawDiff(host, change, point) {
    if (!change) {
      host.append(el("p", "empty", "该位置没有对应的文件差异。"));
      return;
    }
    if (!change.hunks?.length) {
      host.append(
        el(
          "p",
          "empty",
          change.binary
            ? "该文件含二进制内容，无文本行差异。"
            : "此项未包含文本差异块。",
        ),
      );
      return;
    }
    const side = point?.side === "before" ? "old" : "next";
    const line = point?.line || 0,
      end = point?.end || line;
    const contains = (h) => {
      const start = side === "old" ? h.oldStart : h.newStart;
      const count = side === "old" ? h.oldCount : h.newCount;
      return count > 0 && start <= end && start + count > line;
    };
    const match = line ? change.hunks.findIndex(contains) : -1;
    let index = Math.max(0, match),
      page = 0;
    if (match >= 0) {
      const hit = decodeLines(change.hunks[index]).findIndex(
        (row) => row[side] >= line && row[side] <= end && row[side] !== null,
      );
      page = Math.max(0, Math.floor(hit / pageSize));
    }
    function render() {
      host.replaceChildren();
      if (line && match < 0)
        host.append(
          el(
            "p",
            "muted",
            "证据位置未落入可显示的差异块，下面列出该文件的改动。",
          ),
        );
      const wrapper = el("div", "diff-box");
      const title = el("div", "diff-title");
      title.append(el("span", "", change.file));
      const select = el("select");
      select.setAttribute("aria-label", "选择差异块");
      change.hunks.forEach((h, i) => select.add(new Option(hunkRange(h), i)));
      select.selectedIndex = index;
      select.onchange = () => {
        index = select.selectedIndex;
        page = 0;
        render();
      };
      title.append(select);
      wrapper.append(title);
      const scroll = el("div", "diff-scroll"),
        table = el("table", "diff-table");
      table.setAttribute("aria-label", "基线与交付差异");
      const head = table.createTHead().insertRow();
      ["旧行", "新行", "代码"].forEach((label) =>
        head.append(el("th", "", label)),
      );
      const body = table.createTBody(),
        lines = decodeLines(change.hunks[index]);
      for (const row of lines.slice(page * pageSize, (page + 1) * pageSize)) {
        const tr = body.insertRow();
        tr.className =
          row.prefix === "+" ? "plus" : row.prefix === "-" ? "minus" : "";
        if (line && row[side] !== null && row[side] >= line && row[side] <= end)
          tr.classList.add("focus");
        tr.append(
          el("td", "num", row.old),
          el("td", "num", row.next),
          el("td", "code", row.text),
        );
      }
      scroll.append(table);
      wrapper.append(scroll);
      if (lines.length > pageSize) {
        const controls = el("div", "diff-controls"),
          pages = Math.ceil(lines.length / pageSize);
        const prev = button("action", "上一段", () => {
          page--;
          render();
        });
        const next = button("action", "下一段", () => {
          page++;
          render();
        });
        prev.disabled = page === 0;
        next.disabled = page + 1 === pages;
        controls.append(
          prev,
          el("span", "muted", page + 1 + " / " + pages),
          next,
        );
        wrapper.append(controls);
      }
      host.append(wrapper);
      const first = body.querySelector(".focus");
      if (first)
        scroll.scrollTop = Math.max(0, first.offsetTop - table.offsetTop - 70);
    }
    render();
  }
  function proofPanel(parent, points) {
    const panel = section(parent, "源码差异依据");
    if (!points.length) {
      panel.append(el("p", "empty", "本次差异中没有与此项关联的位置。"));
      return;
    }
    let selected = 0,
      shown = 12;
    const list = el("div"),
      graphLink = el("div"),
      preview = el("div");
    panel.append(list, graphLink, preview);
    function render() {
      const point = points[selected];
      list.replaceChildren();
      graphLink.replaceChildren();
      for (const [i, p] of points.slice(0, shown).entries()) {
        const label = (p.symbol ? p.symbol + "  ·  " : "") + location(p);
        const b = button("source-item", label, () => {
          selected = i;
          render();
        });
        b.setAttribute("aria-pressed", String(selected === i));
        list.append(b);
      }
      if (points.length > shown)
        list.append(
          button("more", "显示更多依据", () => {
            shown += 20;
            render();
          }),
        );
      if (
        report.graphUrl === "/" &&
        point.side !== "before" &&
        point.file?.endsWith(".py") &&
        point.line > 0
      ) {
        const a = el("a", "link-row", "在架构图中查看");
        const hash = new URLSearchParams({
          file: point.file,
          line: String(point.line),
        });
        if (point.symbol) hash.set("symbol", point.symbol);
        a.href = "/#" + hash;
        graphLink.append(a);
      }
      preview.replaceChildren();
      drawDiff(preview, byFile.get(point.file), point);
    }
    render();
  }
  function reviewForm(parent, row) {
    const area = section(parent, "人工复核");
    const form = el("form", "review-form"),
      choices = el("select");
    choices.setAttribute("aria-label", "人工判定");
    choices.add(new Option("尚未判定", ""));
    for (const [value, label] of Object.entries(decisionLabels[mode]))
      choices.add(new Option(label, value));
    const key = decisionId(row),
      prev = judgments[key];
    choices.value = prev?.choice || "";
    const memo = el("textarea");
    memo.setAttribute("aria-label", "复核依据");
    memo.placeholder = "记录判定依据或需要 agent 补充的证据";
    memo.value = prev?.note || "";
    const save = el("button", "action", "保存复核");
    save.type = "submit";
    form.append(choices, memo, save);
    area.append(form);
    form.onsubmit = (event) => {
      event.preventDefault();
      const note = memo.value.trim();
      if (choices.value || note)
        judgments[key] = {
          category: mode,
          id: keyOf(row),
          choice: choices.value,
          decision: decisionLabels[mode][choices.value] || "尚未判定",
          note,
        };
      else delete judgments[key];
      try {
        localStorage.setItem(decisionKey, JSON.stringify(judgments));
        notice("复核记录已保存");
      } catch {
        notice("本页已记录，可导出复核记录保留");
      }
      renderList();
      renderDetail();
    };
  }
  function renderDetail() {
    const root = $("detail");
    root.replaceChildren();
    const row = active;
    if (!row) {
      const empty = rows[mode].length
        ? "没有符合筛选条件的项目。"
        : {
            requirements: "没有列入报告的验收要求。",
            defenses: "扫描规则没有标出新增防御模式。",
            scope: "本次比较没有范围变更项。",
            changes: "版本间没有文件差异。",
          }[mode];
      root.append(el("p", "empty", empty));
      return;
    }
    const head = el("div", "pane-head");
    head.append(tag(row.status));
    if (mode === "requirements") head.append(el("code", "muted", row.id));
    root.append(head, el("h2", "detail-title", textOf(row)));
    if (row.detail) root.append(el("p", "copy", row.detail));
    if (mode === "changes") {
      if (row.beforeHash || row.afterHash) {
        const meta = el("details", "check"),
          dl = el("dl");
        meta.append(el("summary", "", "内容指纹与文件模式"));
        field(dl, "基线 SHA-256", row.beforeHash);
        field(dl, "交付 SHA-256", row.afterHash);
        field(dl, "基线文件模式", row.beforeMode);
        field(dl, "交付文件模式", row.afterMode);
        meta.append(dl);
        root.append(meta);
      }
      const box = section(root, "文件差异");
      drawDiff(box, row);
      return;
    }
    if (mode === "requirements") {
      if (row.comparison) root.append(el("p", "copy", row.comparison));
      if (row.scope?.length)
        root.append(el("p", "muted", "约定范围　" + row.scope.join("、")));
      if (row.writes?.length)
        root.append(el("p", "muted", "约定写入路径　" + row.writes.join("、")));
      if (row.scenarios?.length) {
        const scenarios = section(root, "约定的业务场景");
        for (const scenario of row.scenarios) {
          const name = typeof scenario === "string" ? scenario : scenario.name;
          scenarios.append(el("h4", "", name));
          if (typeof scenario === "object") {
            const detail = el("dl", "scenario");
            field(detail, "初始条件", scenario.given);
            field(detail, "操作", scenario.when);
            field(detail, "预期结果", scenario.then);
            scenarios.append(detail);
          }
          if (row.missingScenarios?.includes(name)) scenarios.append(el("p", "muted", "未验证 · 未绑定同名检查"));
        }
      }
      const checks = section(root, "行为检查");
      if (!row.checks?.length)
        checks.append(
          el("p", "empty", "此项没有可执行的验收检查，需要补充验证。"),
        );
      else {
        checks.append(el("p", "muted", "状态依据清单中的检查和期望输出计算。"));
        row.checks.forEach((check) => checks.append(checkRecord(check)));
      }
      proofPanel(root, row.evidence || []);
    } else {
      if (mode === "defenses") {
        const rationale = section(root, "影响与待核问题");
        if (row.cost) rationale.append(el("p", "copy", row.cost));
        if (row.question) rationale.append(el("p", "copy", row.question));
        if (row.rule)
          rationale.append(el("p", "muted", "匹配模式　" + row.rule));
      }
      const reqSection = section(root, "关联要求");
      if (row.kind === "file-write")
        reqSection.append(el("p", "copy", "位置命中以下要求。写入目标　" +
          (row.target === null ? "运行时决定，见源码表达式" : row.target)));
      if (row.requirementIds?.length) {
        for (const id of row.requirementIds) {
          const req = reqs.get(id);
          if (req)
            reqSection.append(
              button("link-row", id + " · " + req.text, () =>
                switchMode("requirements", req),
              ),
            );
        }
      } else reqSection.append(el("p", "empty", "未关联到清单中的要求。"));
      if (row.matchedSelectors?.length) {
        const basis = row.mapping === "symbol" ? "符号规则" : "文件规则";
        reqSection.append(
          el("p", "muted", basis + "　" + row.matchedSelectors.join("、")),
        );
      }
      if (row.forbiddenSelectors?.length)
        reqSection.append(
          el(
            "p",
            "muted",
            "命中禁止范围　" + row.forbiddenSelectors.join("、"),
          ),
        );
      const allRanges = [];
      for (const [field, side] of [
        ["afterRanges", "after"],
        ["beforeRanges", "before"],
      ]) {
        for (const range of row[field] || [])
          allRanges.push({ ...row, ...range, side });
      }
      proofPanel(root, allRanges.length ? allRanges : [row]);
    }
    reviewForm(root, row);
  }
  $("task").textContent = report.task;
  $("base").textContent = report.base;
  $("target").textContent = report.target;
  $("search").oninput = (event) => {
    query = event.target.value.trim().toLowerCase();
    limit = pageSize;
    renderList();
    renderDetail();
  };
  $("filter").onchange = (event) => {
    filter = event.target.value;
    limit = pageSize;
    renderList();
    renderDetail();
  };
  for (const [rules, label] of [
    [report.forbidden, "禁止改动"],
    [report.forbiddenDefenses, "禁止防御模式"],
  ]) {
    for (const rule of rules || []) {
      $("policies").hidden = false;
      $("policy-list").append(el("li", "", label + "　" + rule));
    }
  }
  if (report.diagnostics?.length) {
    $("diagnostics").hidden = false;
    $("diagnostics").querySelector("summary").textContent =
      "分析记录（" + report.diagnostics.length + "）";
    for (const entry of report.diagnostics)
      $("diagnostic-list").append(el("li", "", showValue(entry)));
  }
  $("export-decisions").onclick = () => {
    const record = {
      task: report.task,
      base: report.base,
      target: report.target,
      fingerprint: report.fingerprint,
      decisions: Object.values(judgments),
    };
    const blob = new Blob([JSON.stringify(record, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob),
      anchor = el("a");
    anchor.href = url;
    anchor.download =
      "how2trust-review-" + String(report.fingerprint).slice(0, 12) + ".json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  switchMode("requirements");
})();
