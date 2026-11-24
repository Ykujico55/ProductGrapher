(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const svgNS = "http://www.w3.org/2000/svg";
  const iconPaths = {
    branch:
      "M6 3v12m0-5h8a3 3 0 0 0 3-3V6 M3 3h6v4H3z M3 16h6v5H3z M14 2h6v5h-6z",
    repo: "M4 4a2 2 0 0 1 2-2h13v17H6a2 2 0 0 0 0 4h13 M4 4v17 M8 6h6",
    folder: "M3 6h6l2 2h10v11H3z",
    file: "M6 2h8l5 5v15H6z M14 2v6h5",
    class: "M3 4h18v16H3z M3 10h18 M7 7h4 M7 14h8",
    route: "M5 16V8a3 3 0 0 1 3-3h7m0 0-3-3m3 3-3 3 M6 18h10a3 3 0 0 0 3-3v-3",
    entry: "M3 12h12m-5-5 5 5-5 5 M16 4h5v16h-5",
    search: "M17 17l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    collapse: "M5 5h14M5 12h9M5 19h14",
    scan: "M3 9V4h5m8 0h5v5M3 15v5h5m8 0h5v-5 M7 12h10",
    up: "M5 10l6-6 6 6M11 4v12a3 3 0 0 0 3 3h6",
    panel: "M3 4h18v16H3z M14 4v16",
    hand: "M7 13V8a1.5 1.5 0 0 1 3 0v5-8a1.5 1.5 0 0 1 3 0v7-6a1.5 1.5 0 0 1 3 0v6-4a1.5 1.5 0 0 1 3 0v8c0 4-2 6-6 6h-1c-3 0-5-3-7-6l-2-3c-1-2 1-3 3-1z",
    select: "M3 9V3h6m6 0h6v6m0 6v6h-6m-6 0H3v-6",
    minus: "M5 12h14",
    plus: "M5 12h14M12 5v14",
    fit: "M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6",
    expand: "M14 3h7v7m0-7-8 8M10 21H3v-7m0 7 8-8",
    shrink: "M21 3l-8 8m0-7v7h7M3 21l8-8m0 7v-7H4",
    pointer: "M5 3l14 9-7 1-3 8z",
    down: "M5 9l7 7 7-7",
    right: "M9 5l7 7-7 7",
    close: "M5 5l14 14M19 5 5 19",
    download: "M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4",
  };
  const kinds = {
    project: "仓库",
    group: "功能分组",
    capability: "模型业务分组",
    collaborationModule: "协作模块",
    localGroup: "选区业务分组",
    entry: "功能入口",
    module: "源码模块",
    class: "类",
    function: "函数",
    method: "方法",
    package: "包",
  };
  const relations = {
    contains: "包含",
    inherits: "继承",
    calls: "调用",
    handles: "处理函数",
    associates: "关联",
    part: "包含",
  };
  const viewNames = {
    structure: "整仓结构图",
    product: "生产业务图",
    collaboration: "业务模块协作图",
    module: "模块内部关系图",
    class: "类关系图",
    call: "方法调用链",
    local: "选区业务图",
  };
  function html(tag, cls, value) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = value;
    return el;
  }
  function svg(tag, attrs = {}, value) {
    const el = document.createElementNS(svgNS, tag);
    Object.entries(attrs).forEach(([key, val]) => el.setAttribute(key, val));
    if (value !== undefined) el.textContent = value;
    return el;
  }
  function drawIcon(el, name) {
    const picture = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    picture.append(svg("path", { d: iconPaths[name] || iconPaths.file }));
    el.replaceChildren(picture);
  }
  function icon(name) {
    const el = html("i");
    el.dataset.icon = name;
    drawIcon(el, name);
    return el;
  }
  function action(text, cls, fn) {
    const el = html("button", cls, text);
    el.type = "button";
    el.onclick = fn;
    return el;
  }
  function toast(text) {
    $("toast").textContent = text;
    $("toast").classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("toast").classList.remove("show"), 6500);
  }
  async function api(path, method = "GET", body) {
    const response = await fetch("/api/" + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error || "请求失败 (" + response.status + ")");
      error.generationProgress = data.generationProgress;
      throw error;
    }
    return data;
  }
  let graph,
    state,
    scope,
    view = "product",
    inspector = "evidence",
    tool = "pan",
    currentThread = "",
    pendingChat = null,
    retryError = null;
  const offline = !!window.H2T_GRAPH,
    nodes = new Map(),
    tree = new Map(),
    parent = new Map(),
    edges = new Map(),
    out = new Map(),
    diagnosticsByCaller = new Map(),
    owner = new Map(),
    moduleLeaves = new Map(),
    open = new Set();
  const fullCallViewLimit = 12;
  let callFocus = null,
    wholeCallGraph = false,
    scopedCode = { items: [], links: [] };
  let dependencyCaller = null;
  let chosen = new Set(),
    displayNodes = [],
    displayEdges = [],
    coordinates = new Map(),
    layer,
    scale = 1,
    offset = { x: 0, y: 0 },
    world = { w: 0, h: 0 },
    drag = null;
  let canvasSize, responsiveLayoutWidth = null, framing = "initial";
  const graphIsFullscreen = () => document.documentElement.classList.contains("graph-fullscreen");
  let noteDraft = "",
    questionDraft = "",
    query = "";
  const graphKeys = () => Array.from(chosen);
  const modules = new Map(), moduleLinks = new Map(), callSites = new Map();
  const linksPerPage = 5;
  let modulePage = 0;
  let moduleDrill = null;
  let generation = null, generationMonitor = null, serviceProgress = null;
  let activeLocal = null, localOrigin = null, localPending = false, localError = "";
  const localItems = new Map(), localLinks = new Map();
  const lookup = (id) => (view === "local" && (localItems.get(id) || localLinks.get(id))) ||
    tree.get(id) || nodes.get(id) || edges.get(id) || modules.get(id) || moduleLinks.get(id);
  function targetKey(id) {
    const leaf = tree.get(id);
    const key = leaf?.nodeIds?.[0];
    return leaf?.nodeIds?.length === 1 && !leaf.children.length && nodes.get(key)?.kind === leaf.kind ? key : id;
  }
  const selectionTargets = () => [...new Set(graphKeys().flatMap(id => {
    if (view === "local" && localItems.has(id)) return localItems.get(id).nodeIds;
    if (view === "local" && localLinks.has(id)) return localLinks.get(id).edgeIds;
    if (moduleLinks.has(id)) return moduleLinks.get(id).sites.map(site => site.edgeId);
    if (modules.has(id)) return modules.get(id).nodeIds;
    return [targetKey(id)];
  }))];
  let requestedSource = null;
  let interpretations = new Map();
  let productRoot;
  let rootLayers;
  let sharedCalls = [];
  const capabilityLeaves = new Set(), groupedSymbols = new Set();
  function orderSharedCalls(guide) {
    const rows = guide?.sharedCalls || [];
    const claims = guide?.overview?.claims || [];
    if (rows.length < 2 || !claims.length) return rows;
    const ranked = rows.map(row => {
      const calls = row.paths.map(path => edges.get(path.edges.at(-1)));
      let score = 0;
      for (const claim of claims) {
        const sites = new Set();
        for (const call of calls) {
          if (claim.refs.some(r => call.file === r.file && r.line <= call.line && call.line <= r.end))
            sites.add(JSON.stringify([call.file, call.line, call.column]));
        }
        if (sites.size > 1) score = Math.max(score, sites.size);
      }
      return {row, score};
    });
    return ranked.sort((a, b) => b.score - a.score).map(item => item.row);
  }
  function indexCollaboration() {
    modules.clear();
    moduleLinks.clear();
    callSites.clear();
    for (const site of graph.collaboration?.internalSites || []) callSites.set(site.edgeId, site);
    for (const m of graph.collaboration?.modules || [])
      modules.set(m.id, {...m, kind: "collaborationModule", evidence: m.refs});
    const counts = new Map();
    for (const r of graph.collaboration?.relations || []) {
      const label = modules.get(r.source).label + " → " + modules.get(r.target).label;
      moduleLinks.set(r.id, {...r, kind: "collaboration", label});
      for (const site of r.sites) callSites.set(site.edgeId, site);
      counts.set(r.source, (counts.get(r.source) || 0) + 1);
    }
    const focus = $("module-focus"), last = focus.value;
    focus.replaceChildren(...[...modules.values()].map(m => new Option(m.label, m.id)));
    const busiest = [...modules.values()].sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0))[0];
    const incoming = $("module-direction").value === "in";
    const stillConnected = [...moduleLinks.values()].some(r => (incoming ? r.target : r.source) === last);
    if (busiest) {
      focus.value = stillConnected ? last : busiest.id;
      if (!stillConnected && incoming) $("module-direction").value = "out";
    }
    modulePage = 0;
  }
  function focusedRelations(direction, focus) {
    const neighbor = r => modules.get(direction === "out" ? r.target : r.source);
    return [...moduleLinks.values()].filter(r => (direction === "out" ? r.source : r.target) === focus)
      .sort((a, b) => Number(!!neighbor(b).groupId) - Number(!!neighbor(a).groupId) ||
        b.sites.length - a.sites.length || neighbor(a).label.localeCompare(neighbor(b).label, "zh"));
  }
  function collaborationView() {
    const direction = $("module-direction").value, focus = $("module-focus").value;
    const all = direction === "all" ? [...moduleLinks.values()] : focusedRelations(direction, focus);
    const paged = direction !== "all" && all.length > linksPerPage;
    const pages = Math.ceil(all.length / linksPerPage);
    modulePage = paged ? Math.min(Math.max(0, modulePage), pages - 1) : 0;
    $("module-pages").hidden = !paged;
    $("module-page").textContent = (modulePage + 1) + " / " + pages;
    $("module-prev").disabled = modulePage === 0;
    $("module-next").disabled = modulePage + 1 >= pages;
    const links = paged ? all.slice(modulePage * linksPerPage, (modulePage + 1) * linksPerPage) : all;
    const visible = new Set([focus, ...links.flatMap(r => [r.source, r.target])]);
    return {items: [...modules.values()].filter(m => direction === "all" || visible.has(m.id)), links};
  }
  function indexSemantics() {
    interpretations = new Map((graph.semantics?.items || []).map(item => [item.nodeId, item]));
    sharedCalls = orderSharedCalls(graph.semantics);
    const layers = $("root-layer");
    const previousLayer = (rootLayers?.all.length || layers.value === "source") ? layers.value : "";
    const groups = graph.semantics?.groups || [];
    const entryGroups = new Set(groups.filter(group => group.children.some(leaf =>
      leaf.nodeIds.some(id => nodes.get(id)?.kind === "entry"))));
    const internal = entryGroups.size ? groups.filter(group => !entryGroups.has(group)) : [];
    // The role-projected source tree keeps production roots separate from tests and examples.
    function hasProductionMember(branch) {
      return branch.nodeIds.some(id => graph.fileRoles[nodes.get(id).file] === "production") ||
        branch.children.some(hasProductionMember);
    }
    const source = graph.fileRoles ? graph.tree.children.filter(hasProductionMember) : graph.tree.children;
    rootLayers = {all: groups, business: internal.length ? [...entryGroups] : groups, internal, source};
    layers.replaceChildren();
    if (internal.length) layers.add(new Option("全部业务分组", "all"));
    layers.add(new Option(internal.length ? "入口业务能力" : "业务能力", "business"));
    if (internal.length) layers.add(new Option("内部职责", "internal"));
    if (source.length) layers.add(new Option("源码与配置", "source"));
    // Preserve each guide's initial overview. Available filters depend only on its data.
    const defaultLayer = internal.length && Number(graph.semantics?.promptVersion) > 6 ? "all" : "business";
    layers.value = [...layers.options].some(option => option.value === previousLayer) ? previousLayer : defaultLayer;
    productRoot = groups.length ? {...graph.tree, children: [...rootLayers.business, ...internal, ...graph.tree.children]} : graph.tree;
    tree.set(productRoot.id, productRoot);
    for (const group of groups) {
      tree.set(group.id, group);
      parent.set(group.id, productRoot.id);
      for (const leaf of group.children) {
        tree.set(leaf.id, leaf);
        parent.set(leaf.id, group.id);
        capabilityLeaves.add(leaf.id);
        groupedSymbols.add(leaf.nodeIds[0]);
      }
    }
    if (scope?.id === productRoot.id) scope = productRoot;
    const enabled = !!graph.semantics;
    const hint = enabled ? "搜索业务词、入口或代码符号" : "查找模块、入口或代码符号";
    $("search").placeholder = hint;
    $("search").setAttribute("aria-label", hint);
    document.querySelector('[data-inspect="evidence"]').textContent = enabled ? "解读与源码" : "源码";
    updateGenerationButton();
  }
  function updateGenerationButton() {
    const button = $("generate-semantics");
    button.hidden = offline || view === "local" || !state.semanticLlm?.configured || (!!graph.semantics && !state.semanticLlm.canRegenerate);
    button.disabled = generation?.status === "running";
    button.textContent = button.disabled ? "正在生成业务图…" : graph.semantics ? "用当前模型重新生成" : "一键生成业务图";
    if (!button.hidden) button.title = [state.semanticLlm.provider, state.semanticLlm.model].filter(Boolean).join(" · ");
  }
  const generationStages = {
    prepare: "准备生产源码", batches: "解读源码批次", overview: "整理业务总览",
    grouping: "归并业务分组", review: "对照源码复核总览", saving: "保存业务图",
  };
  function showGeneration(progress) {
    generation = progress;
    if (progress?.runId != null) serviceProgress = progress;
    updateGenerationButton();
    const panel = $("generation-feedback");
    panel.hidden = !progress;
    if (view === "product" && !displayNodes.length) $("empty-graph").hidden = !!progress;
    if (!progress) return;
    const failed = progress.status === "failed";
    const stage = generationStages[progress.stage] || "生成生产业务图";
    const batches = progress.stage === "batches" && progress.total > 0;
    panel.dataset.status = progress.status;
    const label = (failed ? "生成失败 · " : "生成生产业务图 · ") + stage;
    if ($("generation-stage").textContent !== label) $("generation-stage").textContent = label;
    $("generation-count").textContent = progress.cachedGuide ? "复用已保存的业务讲解" : progress.total > 0
      ? "已完成 " + progress.completed + " / " + progress.total + " 批" +
        (progress.cached ? "（含复用 " + progress.cached + " 批）" : "") : "";
    const bar = $("generation-progress");
    bar.hidden = failed;
    if (batches) {
      bar.max = progress.total;
      bar.value = progress.completed;
      bar.setAttribute("aria-label", "已完成的源码批次");
    } else {
      bar.removeAttribute("value");
      bar.setAttribute("aria-label", stage);
    }
    const list = $("generation-batches");
    list.replaceChildren();
    const rows = [...(progress.active || [])];
    if (progress.failedBatch) rows.unshift(progress.failedBatch);
    for (const row of rows) {
      const files = (row.files || []).join("、");
      const phase = row.phase === "reviewing" ? "复核" : "解读";
      const failure = row === progress.failedBatch ? "失败" : "";
      list.append(html("li", "", "第 " + row.batch + " 批 · " + phase + failure + (files ? " · " + files : "")));
    }
    $("generation-connection").hidden = true;
    $("generation-error").hidden = !failed;
    $("generation-error").textContent = failed ? progress.error : "";
    $("generation-actions").hidden = !failed;
    $("generation-retry").disabled = !state.semanticLlm?.configured;
  }
  function stopGenerationMonitor() {
    if (generationMonitor) clearTimeout(generationMonitor.timer);
    generationMonitor = null;
  }
  function applyGeneration(result, job) {
    stopGenerationMonitor();
    showGeneration(null);
    if (state.semanticLlm) state.semanticLlm.canRegenerate = false;
    graph.semantics = result.semantics || result;
    if (result.collaboration) graph.collaboration = result.collaboration;
    if (result.overviewFacts) graph.overviewFacts = result.overviewFacts;
    indexCollaboration();
    if (moduleDrill) {
      // Regrouping can change both module IDs and their membership.
      moduleDrill = null;
      setScope(productRoot.id);
      view = "collaboration";
    } else if ((job.ownsRequest || view === "product") && view === job.view && scope.id === job.scope) {
      setScope(productRoot.id);
      view = "product";
      selectScopeInView();
      inspector = "evidence";
    }
    if (view === "collaboration") {
      const focus = $("module-focus").value;
      chosen = new Set(focus ? [focus] : []);
      inspector = "evidence";
    }
    indexSemantics();
    render();
    toast("生产业务图已就绪");
  }
  function failGeneration(progress) {
    stopGenerationMonitor();
    showGeneration(progress);
  }
  function monitorGeneration(ownsRequest, previousId) {
    stopGenerationMonitor();
    const job = {ownsRequest, view, scope: scope.id, timer: null};
    generationMonitor = job;
    async function poll() {
      try {
        const progress = await api("semantics/progress");
        if (generationMonitor !== job) return;
        if (progress.status === "running" && progress.runId !== previousId) {
          showGeneration(progress);
        } else if (!job.ownsRequest) {
          if (progress.status === "succeeded") {
            const fresh = await api("graph");
            if (generationMonitor === job) applyGeneration(fresh, job);
          } else {
            failGeneration(progress.status === "failed" ? progress : {...generation, status: "failed",
              error: "服务已结束这次生成。请重试以生成业务图。"});
          }
          return;
        }
      } catch (_) {
        if (generationMonitor === job) $("generation-connection").hidden = false;
      }
      if (generationMonitor === job) job.timer = setTimeout(poll, 900);
    }
    if (serviceProgress) job.timer = setTimeout(poll, 200);
    return job;
  }
  async function generateBusinessGraph() {
    if (generation?.status === "running") return;
    const previousId = serviceProgress?.runId;
    const job = monitorGeneration(true, previousId);
    showGeneration({status: "running", stage: "prepare"});
    try {
      const result = await api("semantics", "POST", {includeCollaboration: true});
      applyGeneration(result, job);
    } catch (error) {
      let progress = error.generationProgress;
      if (!progress && serviceProgress) {
        try { progress = await api("semantics/progress"); } catch (_) { /* Show the POST error below. */ }
      }
      if (progress?.runId !== previousId && progress?.status === "running") {
        showGeneration(progress);
        monitorGeneration(false);
        return;
      }
      if (progress?.runId && progress.runId === generation?.runId && progress.status === "succeeded") {
        monitorGeneration(false);
        return;
      }
      const message = /failed to fetch|networkerror|load failed/i.test(error.message)
        ? "无法连接本地工作台。请检查服务是否仍在运行，再重试生成。" : error.message;
      failGeneration(progress?.runId !== previousId && progress?.status === "failed" ? progress :
        {...generation, status: "failed", error: message});
    }
  }
  function localTargets() {
    return graphKeys().filter(id => id !== productRoot.id && lookup(id)).sort();
  }
  function refreshLocalHistory() {
    const history = $("local-history");
    history.replaceChildren(new Option("已生成的选区业务图", ""));
    for (const item of graph.localBusinessViews || []) {
      history.add(new Option(item.title + (item.includeNeighbors ? " · 含直接调用" : " · 仅选区"), item.id));
    }
    history.hidden = graph.localOnly || history.options.length < 2;
    history.value = view === "local" ? activeLocal.id : "";
  }
  function updateLocalTools() {
    const local = view === "local", button = $("generate-local");
    $("local-actions").hidden = offline || local;
    $("local-tools").hidden = $("local-actions").hidden && $("local-history").hidden && !localPending && !localError;
    const targets = local ? [] : localTargets();
    button.disabled = !targets.length || localPending;
    button.title = targets.length ? "根据所选 " + targets.length + " 项和相关源码生成局部业务图" : "先在图中或搜索结果中选择模块、符号或关系";
    $("local-neighbors").disabled = localPending;
    $("local-progress").hidden = !localPending;
    $("local-status").classList.toggle("error", !!localError);
    $("local-status").textContent = localPending ? "模型正在解读所选源码…" : localError ||
      (!offline && !local && !targets.length ? "先选择模块、符号或关系" : "");
    $("local-history").value = local ? activeLocal.id : "";
    $("export").href = local ? "/export.html?local=" + encodeURIComponent(activeLocal.id) : "/export.html";
    $("export").download = local ? "how2trust-selection.html" : "how2trust.html";
    $("export").lastChild.textContent = local ? "导出选区 HTML" : "导出 HTML";
  }
  function openLocal(item) {
    if (!item) return;
    if (view !== "local") {
      localOrigin = {view, scope, chosen, moduleDrill, inspector, callFocus, wholeCallGraph, currentThread,
        requestedSource, search: $("search").value, scale, offset: {...offset}, canvasSize, framing};
    }
    activeLocal = item;
    view = "local";
    moduleDrill = null;
    currentThread = "";
    requestedSource = null;
    wholeCallGraph = false;
    chosen = new Set();
    inspector = "evidence";
    $("search").value = query = "";
    localItems.clear();
    localLinks.clear();
    for (const group of item.groups)
      localItems.set(group.id, {...group, kind: "localGroup", evidence: group.refs});
    for (const [index, rel] of item.relations.entries()) {
      const id = item.id + ":relation:" + index;
      localLinks.set(id, {...rel, id, kind: "localRelation", evidence: rel.refs,
        label: localItems.get(rel.source).label + " → " + localItems.get(rel.target).label});
    }
    render();
  }
  function leaveLocal(redraw = true) {
    if (view !== "local" || graph.localOnly) return;
    const origin = localOrigin;
    view = origin.view;
    scope = tree.get(origin.scope.id) || productRoot;
    moduleDrill = origin.moduleDrill;
    chosen = origin.chosen;
    inspector = origin.inspector;
    callFocus = origin.callFocus;
    wholeCallGraph = origin.wholeCallGraph;
    currentThread = origin.currentThread;
    requestedSource = origin.requestedSource;
    $("search").value = origin.search;
    query = origin.search.trim().toLowerCase();
    activeLocal = localOrigin = null;
    if (redraw) {
      render();
      if (origin.canvasSize?.width === canvasSize.width && origin.canvasSize.height === canvasSize.height) {
        scale = origin.scale;
        offset = origin.offset;
        framing = origin.framing;
        updateTransform();
      }
    }
  }
  async function generateLocal() {
    const targets = localTargets(), originView = view;
    if (!targets.length || localPending || offline || view === "local") return;
    if (!state.semanticLlm?.configured) return openModelSettings("semantics");
    localPending = true;
    localError = "";
    updateLocalTools();
    try {
      const item = await api("semantics/local", "POST", {targets, includeNeighbors: $("local-neighbors").checked});
      graph.localBusinessViews = [...(graph.localBusinessViews || []).filter(old => old.id !== item.id), item];
      refreshLocalHistory();
      if (view === originView && JSON.stringify(targets) === JSON.stringify(localTargets())) openLocal(item);
      else toast("选区业务图已保存，可从「已生成的选区业务图」打开。");
    } catch (error) {
      localError = "选区业务图生成失败 · " + error.message;
    } finally {
      localPending = false;
      updateLocalTools();
    }
  }
  function interpretation(item) {
    const id = item.nodeIds?.length === 1 ? item.nodeIds[0] : item.id;
    return interpretations.get(id);
  }
  function businessTitle(item) {
    const title = interpretation(item)?.title;
    return title?.startsWith(item.label + " ") ? title.slice(item.label.length + 1).trim() : title;
  }
  function businessLabel(item) {
    const title = businessTitle(item);
    return title && title !== item.label ? title + " · " + item.label : item.label;
  }
  function matchesBusiness(item) {
    const entry = interpretation(item);
    return entry && [entry.title, ...entry.aliases, entry.text].some(value => value.toLowerCase().includes(query));
  }
  const readable = (id) =>
    lookup(id)?.label || (edges.has(id) ? edgeName(edges.get(id)) : id);
  function edgeName(edge) {
    return (
      (nodes.get(edge.source)?.label || edge.source) +
      " · " +
      (relations[edge.kind] || edge.kind) +
      " · " +
      (nodes.get(edge.target)?.label || edge.target)
    );
  }
  function typeIcon(kind) {
    return (
      {
        project: "repo",
        group: "folder",
        capability: "folder",
        entry: "entry",
        module: "file",
        class: "class",
      }[kind] || "route"
    );
  }
  function changed() {
    requestedSource = null;
    currentThread = "";
    if (dependencyCaller && (chosen.size !== 1 || !chosen.has(dependencyCaller))) {
      dependencyCaller = null;
      renderGraph(false);
    } else {
      updateDependencyButton();
    }
    renderInspector();
    markSelection();
  }
  function pick(ids, add = false) {
    const previous = add ? new Set(chosen) : new Set();
    ids.forEach((id) => {
      if (add && previous.has(id)) previous.delete(id);
      else previous.add(id);
    });
    chosen = previous;
    changed();
  }
  function setScope(id) {
    requestedSource = null;
    scope = tree.get(id);
    currentThread = "";
    callFocus = null;
    wholeCallGraph = false;
    let p = id;
    while (p) {
      open.add(p);
      p = parent.get(p);
    }
  }
  function navigate(id) {
    if (!tree.has(id)) return;
    if (graph.localOnly) return;
    leaveLocal(false);
    moduleDrill = null;
    const oldSourceLayer = view === "product" && $("root-layer").value === "source";
    setScope(id);
    if (scope === productRoot) view = view === "product" || (graph.semantics && view !== "structure") ? "product" : "structure";
    else if (scope.children.length) view = scope.kind === "capability" || oldSourceLayer ? "product" : "structure";
    else view = scope.kind === "class" ? "class" : "call";
    selectScopeInView();
    render();
  }
  function selectScopeInView() {
    const symbol = nodes.get(targetKey(scope.id));
    const focusSymbol = view === "class" ? symbol?.kind === "class" :
      view === "call" && ["entry", "function", "method", "class"].includes(symbol?.kind);
    chosen = new Set([focusSymbol ? symbol.id : scope.id]);
    callFocus = view === "call" && focusSymbol ? symbol.id : null;
  }
  function locateSymbol(node, sourceRef = null) {
    const nextView = node.kind === "class" ? "class" : "call";
    // Within an entry, keep its call graph as the context for further exploration.
    if (view !== nextView || !scopedCode.items.some(n => n.id === node.id)) {
      const leaf = node.kind === "entry"
        ? [...tree.values()].find(item => item.nodeIds.includes(node.id))
        : moduleLeaves.get(node.file);
      if (!leaf) return;
      setScope(leaf.id);
    }
    view = nextView;
    callFocus = node.id;
    wholeCallGraph = false;
    chosen = new Set([node.id]);
    requestedSource = sourceRef;
    currentThread = "";
    inspector = "evidence";
    render();
    centerIfNeeded(node.id);
  }
  function selectInspector(tab) {
    inspector = tab;
    if (graphIsFullscreen()) showFullscreenInspector(true);
    renderInspector();
  }
  function indexGraph() {
    graph.nodes.forEach((n) => nodes.set(n.id, n));
    diagnosticsByCaller.clear();
    for (const diagnostic of graph.diagnostics) {
      if (!diagnostic.caller) continue;
      if (!diagnosticsByCaller.has(diagnostic.caller)) diagnosticsByCaller.set(diagnostic.caller, []);
      diagnosticsByCaller.get(diagnostic.caller).push(diagnostic);
    }
    graph.edges.forEach((e) => {
      edges.set(e.id, e);
      if (!out.has(e.source)) out.set(e.source, []);
      out.get(e.source).push(e);
      if (e.kind === "contains") owner.set(e.target, e.source);
    });
    const stack = [graph.tree];
    while (stack.length) {
      const item = stack.pop();
      tree.set(item.id, item);
      if (item.kind === "module")
        moduleLeaves.set(nodes.get(item.nodeIds[0]).file, item);
      for (const child of item.children || []) {
        parent.set(child.id, item.id);
        stack.push(child);
      }
    }
  }
  function aggregate(item) {
    const members = new Set(),
      stack = [item];
    while (stack.length) {
      const branch = stack.pop();
      for (const id of branch.nodeIds || []) members.add(id);
      for (const child of branch.children || []) stack.push(child);
    }
    return members;
  }
  function enterModule(id) {
    moduleDrill = {id, members: new Set(modules.get(id).nodeIds), scale, offset: {...offset}, canvasSize, framing, query: $("search").value};
    $("search").value = query = "";
    showModuleView("module");
    layer.querySelector(".graph-node")?.focus({preventScroll: true});
  }
  function showModuleView(next) {
    const selected = graphKeys()[0];
    setScope(productRoot.id);
    view = next;
    const keep = moduleDrill.members.has(selected) && moduleCodeView().items.some(n => n.id === selected);
    chosen = new Set([keep ? selected : moduleDrill.id]);
    if (keep && next === "call") callFocus = selected;
    inspector = "evidence";
    render();
  }
  function returnToCollaboration() {
    const origin = moduleDrill;
    moduleDrill = null;
    setScope(productRoot.id);
    view = "collaboration";
    $("search").value = origin.query;
    query = origin.query.trim().toLowerCase();
    chosen = new Set([origin.id]);
    inspector = "evidence";
    render();
    if (canvasSize.width === origin.canvasSize.width && canvasSize.height === origin.canvasSize.height) {
      scale = origin.scale;
      offset = origin.offset;
      framing = origin.framing;
      updateTransform();
    }
    layer.querySelector(".graph-node.selected")?.focus({preventScroll: true});
  }
  function moduleCodeView() {
    const members = moduleDrill.members;
    const links = graph.edges.filter(e => members.has(e.source) && members.has(e.target) &&
      (view === "module" || (view === "class" ? ["inherits", "associates"] : ["calls", "handles"]).includes(e.kind)));
    const connected = new Set(links.flatMap(e => [e.source, e.target]));
    const items = [...members].map(id => nodes.get(id)).filter(n => view === "module" || connected.has(n.id) ||
      (view === "class" ? n.kind === "class" : ["function", "method", "entry"].includes(n.kind)));
    return {items, links};
  }
  function codeView() {
    const members = aggregate(scope);
    // Declarations define the breadth of a selected source module. An entry
    // reaches its dependencies only through handling and actual call sites.
    const declarations = [...members].filter((id) =>
      ["module", "class"].includes(nodes.get(id)?.kind),
    );
    for (let i = 0; i < declarations.length; i++)
      for (const edge of out.get(declarations[i]) || []) {
        if (edge.kind !== "contains" || members.has(edge.target)) continue;
        members.add(edge.target);
        declarations.push(edge.target);
      }
    const pending = [...members];
    for (let i = 0; i < pending.length; i++)
      for (const edge of out.get(pending[i]) || []) {
        if (
          !["calls", "handles"].includes(edge.kind) ||
          members.has(edge.target)
        )
          continue;
        members.add(edge.target);
        pending.push(edge.target);
      }
    const ids = new Set();
    if (view === "class") {
      for (const id of members) {
        const n = nodes.get(id),
          p = owner.get(id);
        if (n?.kind === "class") ids.add(id);
        if (nodes.get(p)?.kind === "class") ids.add(p);
      }
      const core = new Set(ids);
      for (const e of graph.edges) {
        if (
          ["associates", "inherits"].includes(e.kind) &&
          (core.has(e.source) || core.has(e.target))
        ) {
          ids.add(e.source);
          ids.add(e.target);
        }
      }
    } else {
      for (const id of members)
        if (
          ["entry", "method", "function", "class", "module"].includes(
            nodes.get(id)?.kind,
          )
        )
          ids.add(id);
    }
    const allowed =
      view === "class" ? ["associates", "inherits"] : ["calls", "handles"];
    const relevant = graph.edges.filter(
      (e) => allowed.includes(e.kind) && ids.has(e.source) && ids.has(e.target),
    );
    if (view === "call") {
      const connected = new Set(relevant.flatMap((e) => [e.source, e.target]));
      for (const id of ids)
        if (
          ["class", "module"].includes(nodes.get(id).kind) &&
          !connected.has(id) && id !== callFocus
        )
          ids.delete(id);
    }
    return {
      items: [...ids]
        .sort()
        .map((id) => nodes.get(id))
        .filter(Boolean),
      links: relevant,
    };
  }
  function renderTree() {
    const nav = $("tree");
    nav.replaceChildren();
    if (view === "local") {
      $("nav-title").textContent = "选区业务分组";
      nav.setAttribute("aria-label", "选区业务分组");
      $("collapse").hidden = true;
      const groups = [...localItems.values()].filter(group => !query ||
        [group.label, group.text, ...group.nodeIds.map(readable)].some(value => value.toLowerCase().includes(query)));
      for (const group of groups) {
        const button = action(group.label, "tree-item", () => {
          pick([group.id]);
          selectInspector("evidence");
          centerIfNeeded(group.id);
        });
        const row = html("div", "nav-row");
        row.append(button);
        nav.append(row);
      }
      if (!groups.length) nav.append(html("p", "small-empty", "没有匹配的业务分组。"));
      return;
    }
    if (moduleDrill && scope === productRoot) {
      $("nav-title").textContent = "模块成员";
      nav.setAttribute("aria-label", "模块成员");
      $("collapse").hidden = true;
      const members = [...moduleDrill.members].map(id => nodes.get(id)).filter(n =>
        !query || (n.qualname + " " + n.file).toLowerCase().includes(query) || matchesBusiness(n));
      series(nav, members, n => {
        const button = action(n.qualname, "tree-item", () => {
          if (!displayNodes.some(item => item.id === n.id)) showModuleView("module");
          pick([n.id]);
          selectInspector("evidence");
          centerIfNeeded(n.id);
        });
        button.title = n.qualname + " · " + n.file + ":" + n.line;
        const row = html("div", "nav-row");
        row.append(button);
        nav.append(row);
      });
      if (!members.length) nav.append(html("p", "small-empty", "没有匹配的模块成员。"));
      return;
    }
    const productView = view === "product", sourceLayer = productView && $("root-layer").value === "source";
    $("nav-title").textContent = view === "collaboration" ? "协作模块" : sourceLayer ? "源码与配置" : productView ? "生产业务" :
      view === "structure" ? "整仓结构" : "产品结构";
    nav.setAttribute("aria-label", $("nav-title").textContent);
    $("collapse").hidden = view === "collaboration";
    if (view === "collaboration") {
      const visible = [...modules.values()].filter(m => !query || m.label.toLowerCase().includes(query) ||
        m.nodeIds.some(id => {
          const n = nodes.get(id);
          return n.file.toLowerCase().includes(query) || n.qualname.toLowerCase().includes(query);
        }));
      for (const basis of new Set(visible.map(m => m.basis))) {
        nav.append(html("h3", "tree-section", basis));
        for (const m of visible.filter(item => item.basis === basis)) {
          const row = html("div", "nav-row" + ($("module-focus").value === m.id ? " active" : ""));
          const button = action(m.label, "tree-item", () => {
            $("module-focus").value = m.id;
            $("module-direction").value = [...moduleLinks.values()].some(r => r.source === m.id) ? "out" : "in";
            $("module-focus").dispatchEvent(new Event("change"));
          });
          button.title = m.label;
          row.append(button);
          nav.append(row);
        }
      }
      if (!visible.length) nav.append(html("p", "small-empty", "没有匹配的生产模块。"));
      return;
    }
    if (productView && !sourceLayer && !graph.semantics?.groups?.length) {
      nav.append(html("p", "small-empty", "生成业务图后可浏览业务分组。"));
      return;
    }
    const matches = new Set();
    if (query)
      for (const item of tree.values()) {
        if (view !== "structure" && !sourceLayer && item.nodeIds?.length === 1 && groupedSymbols.has(item.nodeIds[0]) && !capabilityLeaves.has(item.id)) continue;
        if (item.label.toLowerCase().includes(query) || (view !== "structure" && matchesBusiness(item))) {
          let id = item.id;
          while (id && !matches.has(id)) {
            matches.add(id);
            id = parent.get(id);
          }
        }
      }
    function walk(item, depth) {
      if (query && !matches.has(item.id)) return;
      const row = html(
        "div",
        "nav-row" + (item.id === scope.id ? " active" : ""),
      );
      row.style.setProperty("--depth", depth);
      if (item.children.length) {
        const expanded = open.has(item.id) || !!query;
        const toggle = action("", "tree-toggle", () => {
          if (expanded) open.delete(item.id);
          else open.add(item.id);
          renderTree();
        });
        toggle.setAttribute(
          "aria-label",
          (expanded ? "折叠 " : "展开 ") + item.label,
        );
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.append(icon(expanded ? "down" : "right"));
        row.append(toggle);
      } else row.append(html("span", "tree-spacer"));
      const button = action("", "tree-item", () => navigate(item.id));
      button.title = view === "structure" ? item.label : businessLabel(item);
      button.append(icon(typeIcon(item.kind)), html("span", "", button.title));
      row.append(button);
      nav.append(row);
      if (open.has(item.id) || query) {
        let section = "";
        const visibleChildren = item !== productRoot ? item.children : view === "structure" ? graph.tree.children
          : productView ? (query && !sourceLayer ? rootLayers.all : rootLayers[$("root-layer").value]) : item.children;
        for (const child of visibleChildren) {
          if (query && !matches.has(child.id)) continue;
          if (item === productRoot && !productView && view !== "structure" && graph.semantics?.groups?.length) {
            const label = child.kind !== "capability" ? "源码与配置" :
              rootLayers.internal.includes(child) ? "内部职责" : "业务能力";
            if (section !== label) nav.append(html("h3", "tree-section", label));
            section = label;
          }
          walk(child, depth + 1);
        }
      }
    }
    walk(productRoot, 0);
    if (query) {
      const symbols = graph.nodes.filter(n => {
        if (!["function", "method", "class"].includes(n.kind)) return false;
        if (productView && graph.fileRoles?.[n.file] && graph.fileRoles[n.file] !== "production") return false;
        const leaf = moduleLeaves.get(n.file);
        const moduleName = leaf ? nodes.get(leaf.nodeIds[0]).qualname : "";
        return matchesBusiness(n) || [moduleName + "." + n.qualname, n.file + "::" + n.qualname]
          .some(name => name.toLowerCase().includes(query));
      });
      if (symbols.length) {
        symbols.sort((a, b) => Number(b.label.toLowerCase() === query) -
          Number(a.label.toLowerCase() === query));
        nav.append(heading("代码符号", symbols.length));
        const results = html("div");
        nav.append(results);
        series(results, symbols, n => {
          const title = businessTitle(n);
          const button = action(title ? title + " · " + n.qualname : n.qualname, "symbol-result", () => locateSymbol(n));
          button.append(html("small", "", kinds[n.kind] + " · " + n.file + ":" + n.line));
          button.setAttribute("aria-current", chosen.has(n.id) ? "true" : "false");
          results.append(button);
        });
      }
      if (!matches.size && !symbols.length) {
        const related = [];
        for (const item of interpretations.values()) {
          // A longer user phrase may contain a complete business alias supplied by the model.
          const term = item.aliases.filter(alias => {
            const word = alias.toLowerCase();
            if ([...word].length < 2) return false;
            if (/\p{Script=Han}/u.test(word)) return query.includes(word);
            return query.split(/[^\p{L}\p{N}_]+/u).includes(word);
          }).sort((a, b) => b.length - a.length)[0];
          if (term) related.push({item, term});
        }
        if (related.length) {
          related.sort((a, b) => b.term.length - a.term.length);
          nav.append(heading("相关业务词", related.length));
          const list = html("div", "related-terms");
          nav.append(list);
          series(list, related, ({item, term}) => {
            const node = nodes.get(item.nodeId);
            const button = action(businessLabel(node), "symbol-result", () => locateSymbol(node));
            button.append(html("small", "", "匹配业务词「" + term + "」"));
            list.append(button);
          });
        }
      }
    }
    if (!nav.childElementCount)
      nav.append(html("p", "small-empty", "没有匹配的模块、入口或代码符号。"));
  }
  function render() {
    renderTree();
    $("repo-name").textContent = graph.name;
    $("scope-name").textContent = view === "structure" ? scope.label : businessLabel(scope);
    const trail = [];
    let id = scope.id;
    while (id) {
      trail.unshift(tree.get(id));
      id = parent.get(id);
    }
    const crumbs = $("crumbs");
    crumbs.replaceChildren();
    if (moduleDrill) {
      const name = modules.get(moduleDrill.id).label;
      crumbs.append(action("模块协作", "", returnToCollaboration), html("span", "", "/"),
        action(name, "", () => showModuleView("module")));
      if (scope === productRoot) {
        $("scope-name").textContent = name;
        trail.length = 0;
      } else crumbs.append(html("span", "", "/"));
    }
    for (const [i, item] of trail.entries()) {
      if (i) crumbs.append(html("span", "", "/"));
      crumbs.append(action(item.label, "", () => navigate(item.id)));
    }
    $("up").hidden = !moduleDrill && !parent.has(scope.id);
    $("up-label").textContent = moduleDrill ? (view === "module" ? "返回模块协作" : "返回模块详情") : "上一级";
    $("metrics").textContent =
      scope.id === graph.tree.id
        ? Object.keys(graph.files).length +
          " 个快照文件 · " +
          graph.nodes.length +
          " 个代码节点 · " +
          graph.edges.length +
          " 条代码关系"
        : (kinds[scope.kind] || scope.kind) +
          " · " +
          scope.children.length +
          " 个下级节点";
    if (scope === productRoot && view === "product" && $("root-layer").value === "source") {
      $("metrics").textContent = "生产源码中的模块、显式入口和配置分组";
    } else if (scope === productRoot && view === "product" && graph.semantics) {
      const guide = graph.semantics;
      const layer = $("root-layer").value;
      const groupCount = rootLayers[layer].length + (layer === "internal" ? " 个组内无显式入口的分组" : " 个业务分组");
      $("metrics").textContent = groupCount + " · " + guide.items.length + " 项源码讲解 · " +
        (guide.overview.claims?.length || 0) + " 条总览结论";
    }
    if (view === "collaboration") {
      const sites = [...moduleLinks.values()].reduce((sum, relation) => sum + relation.sites.length, 0);
      $("metrics").textContent = modules.size + " 个生产模块 · " + moduleLinks.size + " 对有向关系 · " + sites + " 处调用或绑定";
    }
    if (moduleDrill && scope === productRoot)
      $("metrics").textContent = modules.get(moduleDrill.id).basis + " · " + moduleDrill.members.size + " 个模块成员";
    if (view === "local") {
      $("scope-name").textContent = activeLocal.title;
      $("metrics").textContent = "选区内 " + activeLocal.selectedNodeIds.length + " 个代码节点" +
        (activeLocal.includeNeighbors ? " · 直接关联 " + activeLocal.neighborNodeIds.length + " 个节点" : " · 仅选区") +
        " · " + activeLocal.model;
      crumbs.replaceChildren(html("span", "", graph.name), html("span", "", "/"), html("span", "", "选区业务图"));
      $("up").hidden = !!graph.localOnly;
      $("up-label").textContent = "返回原图";
    }
    const searchHint = view === "local" ? "查找选区中的业务分组" :
      graph.semantics ? "搜索业务词、入口或代码符号" : "查找模块、入口或代码符号";
    $("search").placeholder = searchHint;
    $("search").setAttribute("aria-label", searchHint);
    updateGenerationButton();
    document.querySelectorAll("[data-view]").forEach((b) => {
      const active = b.dataset.view === (view === "module" ? "collaboration" : view);
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    $("canvas-name").textContent = viewNames[view];
    $("root-layer").hidden = view !== "product" || $("root-layer").options.length < 2;
    showSharedCalls();
    renderGraph();
    renderInspector();
  }
  const cardWidth = 218;
  const methodList = (id) =>
    (out.get(id) || [])
      .filter(
        (e) => e.kind === "contains" && nodes.get(e.target)?.kind === "method" &&
          (!moduleDrill || scope !== productRoot || moduleDrill.members.has(e.target)),
      )
      .map((e) => nodes.get(e.target));
  function cardHeight(n) {
    return view === "class"
      ? 82 + Math.min(4, methodList(n.id).length) * 18
      : 78;
  }
  function businessOverview() {
    return view === "product" && scope === productRoot && $("root-layer").value !== "source" && displayNodes.length >= 6;
  }
  function layout(items, links) {
    coordinates = new Map();
    responsiveLayoutWidth = null;
    if (businessOverview()) {
      const groups = displayNodes.slice(1);
      const rows = Math.ceil(groups.length / 2), h = 47, pitch = 57;
      responsiveLayoutWidth = $("drawing").clientWidth;
      const card = Math.min(cardWidth, Math.max(145, (responsiveLayoutWidth - 132) / 3));
      const col = card + 48;
      world = {w: 3 * card + 128, h: (rows - 1) * pitch + h + 30};
      coordinates.set(scope.id, {x: 16 + col, y: (world.h - h) / 2, w: card, h});
      for (const [i, group] of groups.entries()) {
        const right = i >= rows;
        const count = right ? groups.length - rows : rows;
        const y = 15 + (rows - count) * pitch / 2 + (right ? i - rows : i) * pitch;
        coordinates.set(group.id, {x: 16 + (right ? 2 * col : 0), y, w: card, h});
      }
      return;
    }
    if (view === "collaboration" && $("module-direction").value !== "all") {
      const incoming = $("module-direction").value === "in", focus = $("module-focus").value;
      responsiveLayoutWidth = $("drawing").clientWidth;
      const card = Math.min(cardWidth, Math.max(128, (responsiveLayoutWidth - 112) / 2));
      const h = 53, gap = 18, far = card + 98;
      world = {w: far + card + 22, h: Math.max(1, displayEdges.length) * (h + gap) - gap + 32};
      coordinates.set(focus, {x: incoming ? far : 18, y: (world.h - h) / 2, w: card, h});
      displayEdges.forEach((r, i) => coordinates.set(incoming ? r.source : r.target, {
        x: incoming ? 18 : far, y: 16 + i * (h + gap), w: card, h,
      }));
      return;
    }
    const depth = new Map(),
      incoming = new Set(
        links.filter((e) => e.source !== e.target).map((e) => e.target),
      );
    const adj = new Map();
    for (const edge of links) {
      if (!adj.has(edge.source)) adj.set(edge.source, []);
      adj.get(edge.source).push(edge.target);
    }
    function assign(starts) {
      const queue = [];
      for (const node of starts)
        if (!depth.has(node.id)) {
          depth.set(node.id, 0);
          queue.push(node.id);
        }
      for (let i = 0; i < queue.length; i++)
        for (const target of adj.get(queue[i]) || []) {
          if (!depth.has(target)) {
            depth.set(target, depth.get(queue[i]) + 1);
            queue.push(target);
          }
        }
    }
    assign(items.filter((n) => !incoming.has(n.id)));
    for (const n of items) if (!depth.has(n.id)) assign([n]);
    const columns = new Map();
    for (const n of items) {
      const d = depth.get(n.id);
      if (!columns.has(d)) columns.set(d, []);
      columns.get(d).push(n);
    }
    world = { w: Math.max(1, columns.size) * (cardWidth + 84) - 84 + 72, h: 0 };
    const heights = new Map();
    for (const [d, col] of columns) {
      const h = col.reduce((sum, n) => sum + cardHeight(n) + 25, -25);
      heights.set(d, h);
      world.h = Math.max(world.h, h + 84);
    }
    for (const [d, col] of columns) {
      // Keep early nodes reachable without scrolling past empty space in large columns.
      let y = world.h < 900 ? (world.h - heights.get(d)) / 2 : 42;
      for (const n of col) {
        const h = cardHeight(n);
        coordinates.set(n.id, {
          x: 30 + d * (cardWidth + 84),
          y,
          w: cardWidth,
          h,
        });
        y += h + 25;
      }
    }
  }
  function updateTransform() {
    if (layer)
      layer.setAttribute(
        "transform",
        "translate(" + offset.x + " " + offset.y + ") scale(" + scale + ")",
      );
    $("zoom-label").textContent = Math.round(scale * 100) + "%";
  }
  function fit(initial = false) {
    framing = initial ? "initial" : "fit";
    const box = $("drawing").getBoundingClientRect();
    const ratio = Math.min(
      (box.width - 26) / Math.max(1, world.w),
      (box.height - 60) / Math.max(1, world.h),
    );
    const focused = view === "call" && callFocus && !wholeCallGraph ||
      view === "collaboration" && $("module-direction").value !== "all";
    const initialFloor = focused ? 0.86 : 0.58;
    const maxFit = graphIsFullscreen() ? 1.5 : 1.07;
    scale = Math.max(0.001, Math.min(maxFit, initial ? Math.max(initialFloor, ratio) : ratio));
    offset = {
      x: Math.max(6, (box.width - world.w * scale) / 2),
      y: Math.max(6, (box.height - 32 - world.h * scale) / 2),
    };
    updateTransform();
    if (initial && view === "call" && callFocus && !wholeCallGraph) centerIfNeeded(callFocus);
  }
  function fitCanvas(initial = false) {
    const drawing = $("drawing");
    if (responsiveLayoutWidth === null || responsiveLayoutWidth === drawing.clientWidth) {
      fit(initial);
      return;
    }
    const focused = drawing.contains(document.activeElement) ? document.activeElement.closest("[data-key]") : null;
    renderGraph(initial);
    if (focused) {
      const kind = focused.classList.contains("graph-edge") ? ".graph-edge" : ".graph-node";
      layer.querySelector(kind + '[data-key="' + CSS.escape(focused.dataset.key) + '"]')?.focus({preventScroll: true});
    }
  }
  function centerIfNeeded(id) {
    const p = coordinates.get(id);
    if (!p) return;
    const box = $("drawing").getBoundingClientRect(),
      left = offset.x + p.x * scale, top = offset.y + p.y * scale;
    if (left >= 0 && top >= 0 && left + p.w * scale <= box.width &&
        top + p.h * scale < box.height - 40 && scale >= 0.86) return;
    scale = Math.max(0.86, scale);
    offset = {
      x: (box.width - p.w * scale) / 2 - p.x * scale,
      y: (box.height - 32 - p.h * scale) / 2 - p.y * scale,
    };
    updateTransform();
  }
  function zoom(factor, x, y) {
    framing = "manual";
    const box = $("drawing").getBoundingClientRect();
    x ??= box.width / 2;
    y ??= box.height / 2;
    const next = Math.max(0.035, Math.min(3, scale * factor));
    offset = {
      x: x - ((x - offset.x) * next) / scale,
      y: y - ((y - offset.y) * next) / scale,
    };
    scale = next;
    updateTransform();
  }
  function clip(text, size) {
    let units = 0,
      res = "";
    for (const char of String(text)) {
      units += char.charCodeAt(0) > 255 ? 1.8 : 1;
      if (units > size) return res + "…";
      res += char;
    }
    return res;
  }
  function labelSvg(parent, x, y, cls, value) {
    parent.append(svg("text", { x, y, class: cls }, value));
  }
  function drawCard(n) {
    const p = coordinates.get(n.id);
    const title = view === "product" || (n.kind === "entry" && view !== "structure") ? businessTitle(n) : null;
    const label = title ? businessLabel(n) : n.label;
    const group = svg("g", {
      class: "graph-node",
      tabindex: "0",
      role: "button",
      "aria-label": label,
      transform: "translate(" + p.x + " " + p.y + ")",
    });
    group.dataset.key = n.id;
    const kind = n.dependencySites ? dependencyLabels[n.dependencySites[0].category] : kinds[n.kind] || n.kind;
    const compact = businessOverview() || view === "collaboration" && $("module-direction").value !== "all";
    group.append(
      svg("title", {}, kind + " · " + label + (view === "collaboration" ? " · 点击展开模块内部关系" : "")),
      svg("rect", { class: "card", width: p.w, height: p.h, rx: 8 }),
    );
    if (!compact) {
      group.append(svg("rect", {
        class: "accent",
        x: 12,
        y: 9,
        width: kind.length * 12 + 14,
        height: 19,
        rx: 4,
      }));
      labelSvg(group, 18, 23, "kind", kind);
    }
    const name = n.dependencySites ? n.label.split(".").pop()
      : compact && n.basis === "源码模块" ? n.label.split("/").pop() : title || n.label;
    labelSvg(group, 12, compact ? 21 : 49, "name", clip(name, compact ? Math.floor((p.w - 22) / 8) : 23));
    let detail = n.file ? n.file + ":" + n.line : "";
    if (n.dependencySites) detail = n.label;
    if (view === "local") detail = n.nodeIds.length + " 个代码节点 · " + n.text;
    if (view === "collaboration") detail = n.basis + " · " + n.nodeIds.length + " 个节点";
    if (view === "product" || view === "structure")
      detail = n.children.length
        ? (n === scope ? displayNodes.length - 1 : n.children.length) + " 个下级节点"
        : n.evidence?.[0]?.file || "查看代码关系";
    if (view === "product" && n.kind === "capability") {
      const entries = n.children.filter(child => child.kind === "entry");
      if (entries.length) detail = entries.map(child => child.label).join(" · ");
      else if (compact) detail = n.children.length + " 个代码节点";
    }
    labelSvg(group, 12, compact ? 39 : 67, "detail", clip(title ? n.label : detail, 30));
    if (view === "class") {
      const methods = methodList(n.id);
      if (methods.length)
        group.append(
          svg("line", { x1: 0, x2: p.w, y1: 75, y2: 75, stroke: "#d2dfd4" }),
        );
      methods.slice(0, 4).forEach((m, i) => {
        labelSvg(
          group,
          12,
          90 + 18 * i,
          "detail",
          i === 3 && methods.length > 4
            ? "另有 " + (methods.length - 3) + " 个方法"
            : clip(m.label + "()", 32),
        );
      });
    }
    group.onclick = (event) => {
      if (n.dependencySites) {
        showDiagnostics(n.dependencySites);
        return;
      }
      if (event.shiftKey || event.ctrlKey || event.metaKey) {
        pick([n.id], true);
        return;
      }
      if (tool === "select") {
        pick([n.id], true);
        return;
      }
      if (view === "collaboration") {
        enterModule(n.id);
        return;
      }
      if ((view === "product" || view === "structure") && scope.id !== n.id) {
        navigate(n.id);
        return;
      }
      pick([n.id]);
      selectInspector("evidence");
    };
    group.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        group.onclick(e);
      }
    };
    layer.append(group);
  }
  function drawLink(e, index, count) {
    const a = coordinates.get(e.source),
      b = coordinates.get(e.target);
    if (!a || !b) return;
    const ax = a.x + a.w,
      ay = a.y + a.h / 2,
      bx = b.x,
      by = b.y + b.h / 2;
    const spread = (index - (count - 1) / 2) * 9;
    let path, labelX, labelY;
    if (e.source === e.target) {
      const cx = a.x + a.w / 2, top = a.y - 32 - Math.abs(spread);
      path = "M" + (cx - 28 + spread) + "," + a.y + " C" + (cx - 28 + spread) + "," + top +
        " " + (cx + 28 + spread) + "," + top + " " + (cx + 28 + spread) + "," + (a.y - 3);
      labelX = cx + spread;
      labelY = top + 3;
    } else if (bx > ax) {
      const mid = (ax + bx) / 2 + spread;
      path =
        "M" +
        ax +
        "," +
        (ay + spread) +
        " C" +
        mid +
        "," +
        (ay + spread) +
        " " +
        mid +
        "," +
        (by + spread) +
        " " +
        (bx - 3) +
        "," +
        (by + spread);
      labelX = (ax + bx) / 2;
      labelY = (ay + by) / 2 - 5 + spread;
    } else {
      const right = Math.max(ax, b.x + b.w) + 23 + Math.abs(spread);
      const from = ay + spread, to = by + spread;
      path =
        "M" +
        ax +
        "," +
        from +
        " C" +
        right +
        "," +
        from +
        " " +
        right +
        "," +
        to +
        " " +
        (b.x + b.w + 3) +
        "," +
        to;
      labelX = right + 2;
      labelY = (from + to) / 2;
    }
    const collab = view === "collaboration";
    if (businessOverview() && b.x < a.x) {
      const start = a.x, end = b.x + b.w + 3, mid = (start + end) / 2;
      path = "M" + start + "," + ay + " C" + mid + "," + ay + " " + mid + "," + by + " " + end + "," + by;
    }
    if (collab && $("module-direction").value !== "all") {
      const incoming = $("module-direction").value === "in";
      const elbowStart = ax + (incoming ? 39 : 12), elbowEnd = bx - (incoming ? 12 : 39);
      path = "M" + ax + "," + ay + " H" + elbowStart + " C" + (elbowStart+8) + "," + ay + " " +
        (elbowEnd-8) + "," + by + " " + elbowEnd + "," + by + " H" + (bx-3);
      labelX = incoming ? ax + 20 : bx - 20;
      labelY = (incoming ? ay : by) - 6;
    }
    const relationLabel = e.dependencySites ? "仓外调用 · " + e.dependencySites.length + " 处" :
      view === "local" ? e.edgeIds.length + " 处关系" :
      e.source === e.target && e.kind === "calls" ? "递归调用" : relations[e.kind] || e.kind;
    const title = e.dependencySites ? e.dependencySites[0].targetName + " · " + relationLabel :
      view === "local" ? e.label + " · " + relationLabel :
      collab ? e.label + " · " + e.sites.length + " 处" : relationLabel;
    const el = svg("g", {
      class: "graph-edge",
      tabindex: "0",
      role: "button",
      "aria-label": title,
    });
    if (e.id) el.dataset.key = e.id;
    el.append(
      svg(
        "title",
        {},
        title +
          (e.file ? " · " + e.file + ":" + e.line : ""),
      ),
      svg("path", {
        d: path,
        class: "edge-path",
        "marker-end":
          e.kind === "inherits" ? "url(#generalization)" : "url(#arrow)",
      }),
      svg("path", { d: path, class: "edge-hit" }),
    );
    if (collab && $("module-direction").value !== "all") {
      labelY -= 2;
      const width = 28 + 7 * String(e.sites.length).length;
      el.append(svg("rect", {
        x: labelX - width / 2, y: labelY - 13, width, height: 18, rx: 5, class: "site-count",
      }));
    }
    if (view !== "product" && view !== "structure")
      el.append(
        svg(
          "text",
          { x: labelX, y: labelY, "text-anchor": "middle" },
          collab ? e.sites.length + " 处" : relationLabel,
        ),
      );
    el.onclick = (ev) => {
      if (e.dependencySites) {
        showDiagnostics(e.dependencySites);
        return;
      }
      pick([e.id || e.target], ev.ctrlKey || ev.shiftKey || tool === "select");
      selectInspector("evidence");
    };
    el.onkeydown = (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        el.onclick(ev);
      }
    };
    layer.append(el);
  }
  const dependencyLabels = {builtin: "Python 内置", stdlib: "Python 标准库", external: "外部依赖"};

  function updateDependencyButton() {
    const selected = chosen.size === 1 ? chosen.values().next().value : null;
    const sites = view === "call" && selected && displayNodes.some(n => n.id === selected)
      ? (diagnosticsByCaller.get(selected) || []).filter(d => Object.hasOwn(dependencyLabels, d.category) && d.targetName)
      : [];
    const button = $("call-dependencies");
    if (dependencyCaller !== selected || !sites.length) dependencyCaller = null;
    button.hidden = !sites.length;
    button.setAttribute("aria-pressed", String(!!dependencyCaller));
    button.textContent = (dependencyCaller ? "收起仓外调用" : "显示仓外调用") + "（" + sites.length + " 处）";
    return sites;
  }

  function renderGraph(initial = !wholeCallGraph) {
    const drawing = $("drawing");
    drawing.replaceChildren();
    const defs = svg("defs"),
      marker = svg("marker", {
        id: "arrow",
        viewBox: "0 0 10 10",
        refX: 8,
        refY: 5,
        markerWidth: 6,
        markerHeight: 6,
        orient: "auto",
      });
    marker.append(svg("path", { d: "M0 0L9 5L0 10z", fill: "#779582" }));
    const generalization = svg("marker", {
      id: "generalization",
      viewBox: "0 0 14 14",
      refX: 12,
      refY: 7,
      markerWidth: 10,
      markerHeight: 10,
      orient: "auto",
    });
    generalization.append(
      svg("path", {
        d: "M1 1L12 7L1 13z",
        fill: "white",
        stroke: "#63816c",
        "stroke-width": 1.5,
      }),
    );
    defs.append(marker, generalization);
    drawing.append(defs);
    layer = svg("g");
    drawing.append(layer);
    const range = $("call-range");
    range.hidden = true;
    $("module-controls").hidden = view !== "collaboration";
    $("module-focus").disabled = $("module-direction").value === "all";
    if (view === "local") {
      scopedCode = {items: [], links: []};
      displayNodes = [...localItems.values()];
      displayEdges = [...localLinks.values()];
    } else if (view === "collaboration") {
      scopedCode = {items: [], links: []};
      const selected = collaborationView();
      displayNodes = selected.items;
      displayEdges = selected.links;
    } else if (view === "product" || view === "structure") {
      scopedCode = { items: [], links: [] };
      const children = scope !== productRoot ? scope.children : view === "structure"
        ? graph.tree.children : rootLayers[$("root-layer").value] || [];
      displayNodes = view === "product" && scope === productRoot && !children.length ? [] : [scope, ...children];
      displayEdges = children.map((child) => ({
        source: scope.id,
        target: child.id,
        kind: "part",
      }));
    } else {
      scopedCode = moduleDrill && scope === productRoot ? moduleCodeView() : codeView();
      displayNodes = scopedCode.items;
      displayEdges = scopedCode.links;
      if (view === "call" && displayNodes.length > fullCallViewLimit) {
        if (!callFocus) {
          const members = moduleDrill && scope === productRoot ? moduleDrill.members : aggregate(scope);
          const seed = displayNodes.find(n => members.has(n.id) && n.kind === "entry") ||
            displayNodes.find(n => n.file === scope.evidence[0]?.file) || displayNodes[0];
          callFocus = seed.id;
        }
        if (!wholeCallGraph) {
          displayEdges = scopedCode.links.filter(e => e.source === callFocus || e.target === callFocus);
          const near = new Set([callFocus, ...displayEdges.flatMap(e => [e.source, e.target])]);
          displayNodes = scopedCode.items.filter(n => near.has(n.id));
        }
        range.hidden = false;
        range.textContent = wholeCallGraph ? "仅看直接调用" : "展开完整调用图";
      }
    }
    const sites = updateDependencyButton();
    const dependencyNodes = new Map();
    if (dependencyCaller) {
      for (const site of sites) {
        const id = "dependency:" + site.targetName;
        if (!dependencyNodes.has(id)) dependencyNodes.set(id, {id, label: site.targetName, dependencySites: []});
        dependencyNodes.get(id).dependencySites.push(site);
      }
    }
    const drawnNodes = [...displayNodes, ...dependencyNodes.values()];
    const drawnEdges = [...displayEdges];
    for (const node of dependencyNodes.values()) {
      drawnEdges.push({source: dependencyCaller, target: node.id, dependencySites: node.dependencySites});
    }
    layout(drawnNodes, drawnEdges);
    const bundles = new Map();
    for (const e of drawnEdges) {
      const key = JSON.stringify([e.source, e.target]);
      if (!bundles.has(key)) bundles.set(key, []);
      bundles.get(key).push(e);
    }
    for (const group of bundles.values())
      group.forEach((e, i) => drawLink(e, i, group.length));
    drawnNodes.forEach(drawCard);
    fit(initial);
    $("empty-graph").hidden = !!displayNodes.length || (view === "product" && !!generation);
    $("empty-graph").textContent =
      view === "product" ? (graph.semantics ? "当前讲解没有业务分组，可查看整仓结构或模块协作。"
        : offline ? "此快照尚无业务解读。打开已配置模型的本地工作台可一键生成。"
        : state.semanticLlm?.configured ? "从生产源码提取业务分组与带引用的讲解。" : "请为工作台的业务解读阶段配置模型后生成。") :
      view === "collaboration" ? "此快照没有可展示的生产模块。" : view === "class"
        ? "此范围内没有可定位的类。"
        : "此范围内没有可定位的函数或方法。";
    if (view === "product" && !displayNodes.length && !graph.semantics && !offline && !state.semanticLlm?.configured)
      $("empty-graph").append(action("打开模型设置", "form-btn primary generate-in-canvas", () => $("model-settings").click()));
    if (view === "product" && !displayNodes.length && !graph.semantics && !offline && state.semanticLlm?.configured) {
      const primary = $("generate-semantics");
      const generate = action(primary.textContent, "form-btn primary generate-in-canvas", () => primary.click());
      generate.disabled = primary.disabled;
      $("empty-graph").append(generate);
    }
    $("canvas-count").textContent =
      (!range.hidden && !wholeCallGraph ? displayNodes.length + " / " + scopedCode.items.length : displayNodes.length) +
      " 节点 · " + displayEdges.length + " 关系";
    if (dependencyNodes.size) $("canvas-count").textContent += " · " + dependencyNodes.size + " 个仓外目标";
    if (view === "collaboration")
      $("canvas-count").textContent = displayEdges.length + " / " + moduleLinks.size + " 条模块关系";
    $("canvas-name").textContent = !range.hidden && !wholeCallGraph
      ? "直接调用 · " + nodes.get(callFocus).label : viewNames[view];
    syncTool();
    markSelection();
    canvasSize = {width: drawing.clientWidth, height: drawing.clientHeight};
  }
  function markSelection() {
    document.querySelectorAll(".graph-node,.graph-edge").forEach((el) => {
      const marked = chosen.has(el.dataset.key);
      el.classList.toggle(
        el.classList.contains("graph-node") ? "selected" : "picked",
        marked,
      );
      el.setAttribute("aria-pressed", String(marked));
    });
    $("selection-count").textContent = "已选 " + chosen.size + " 项";
    updateLocalTools();
  }
  function syncTool() {
    $("drawing").setAttribute("class", tool);
    for (const k of ["pan", "select"]) {
      $(k).classList.toggle("active", tool === k);
      $(k).setAttribute("aria-pressed", String(tool === k));
    }
    $("canvas-hint").textContent =
      tool === "select"
        ? "拖动圈选 · 点击增减节点"
        : view === "collaboration" ? "点击模块展开内部关系 · 点击箭头查看条件和源码" : view === "product" || view === "structure"
          ? "点击模块展开 · Shift 点击多选"
          : "点击节点或关系查看源码";
  }
  function pointerPos(event) {
    const box = $("drawing").getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }
  function showFullscreenInspector(show) {
    document.documentElement.classList.toggle("graph-inspecting", show);
    $("fullscreen-inspector").setAttribute("aria-expanded", String(show));
  }
  function bindInspectorResize() {
    const inspector = $("graph-inspector"), handle = $("inspector-resizer");
    const workspace = inspector.closest(".workspace");
    const preference = "How2Trust.inspectorWidth", property = "--inspector-width";
    let minWidth, maxWidth, startX, startWidth;

    try {
      const saved = Number(localStorage.getItem(preference));
      if (Number.isFinite(saved) && saved > 0) workspace.style.setProperty(property, saved + "px");
    } catch { /* Some offline viewers disable local storage. */ }
    function rememberWidth() {
      try {
        const width = workspace.style.getPropertyValue(property);
        if (width) localStorage.setItem(preference, String(parseFloat(width)));
        else localStorage.removeItem(preference);
      } catch { /* The width still applies to this page. */ }
    }
    function describeWidth() {
      if (!handle.offsetWidth) return;
      const css = getComputedStyle(workspace);
      minWidth = parseFloat(css.getPropertyValue("--inspector-min"));
      maxWidth = workspace.clientWidth - parseFloat(css.getPropertyValue("--nav-width"))
        - parseFloat(css.getPropertyValue("--graph-min-width"));
      const width = inspector.offsetWidth;
      handle.setAttribute("aria-valuemin", minWidth);
      handle.setAttribute("aria-valuemax", maxWidth);
      handle.setAttribute("aria-valuenow", width);
      handle.setAttribute("aria-valuetext", width + " 像素");
    }
    function setWidth(width) {
      workspace.style.setProperty(property, Math.round(Math.max(minWidth, Math.min(maxWidth, width))) + "px");
    }
    function resetWidth() {
      workspace.style.removeProperty(property);
      rememberWidth();
    }
    const observer = new ResizeObserver(describeWidth);
    observer.observe(workspace);
    observer.observe(inspector);
    handle.onpointerdown = event => {
      if (event.button !== 0 || !event.isPrimary) return;
      event.preventDefault();
      handle.focus({preventScroll: true});
      describeWidth();
      startX = event.clientX;
      startWidth = inspector.offsetWidth;
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
    };
    handle.onpointermove = event => {
      if (handle.hasPointerCapture(event.pointerId)) setWidth(startWidth + startX - event.clientX);
    };
    handle.onlostpointercapture = () => {
      handle.classList.remove("dragging");
      rememberWidth();
    };
    handle.ondblclick = resetWidth;
    handle.onkeydown = event => {
      if (event.key === "Enter") resetWidth();
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        setWidth(inspector.offsetWidth + (event.key === "ArrowLeft" ? 24 : -24));
        rememberWidth();
      } else return;
      event.preventDefault();
    };
  }
  function bindFullscreen() {
    const root = document.documentElement, button = $("fullscreen");
    function display(expanded) {
      root.classList.toggle("graph-fullscreen", expanded);
      const label = expanded ? "退出全屏" : "全屏查看";
      button.querySelector("span").textContent = label;
      drawIcon(button.querySelector("i"), expanded ? "shrink" : "expand");
      button.title = expanded ? "退出全屏（Esc）" : label;
      button.setAttribute("aria-pressed", String(expanded));
      $("fullscreen-inspector").hidden = !expanded;
      showFullscreenInspector(false);
      button.focus({preventScroll: true});
    }
    async function leave() {
      if (document.fullscreenElement === root) {
        await document.exitFullscreen();
      } else display(false);
    }
    button.onclick = async () => {
      if (graphIsFullscreen()) return leave();
      if (document.fullscreenEnabled && root.requestFullscreen) {
        try {
          await root.requestFullscreen();
          return;
        } catch {
          // Embedded viewers can prohibit native fullscreen while still allowing a full-tab canvas.
        }
      }
      display(true);
    };
    document.addEventListener("fullscreenchange", () => display(document.fullscreenElement === root));
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && graphIsFullscreen() && !document.querySelector("dialog[open]")) {
        event.preventDefault();
        leave();
      }
    });
    $("fullscreen-inspector").onclick = () => showFullscreenInspector(!root.classList.contains("graph-inspecting"));
  }
  function bindCanvas() {
    const drawing = $("drawing");
    new ResizeObserver(() => {
      const width = drawing.clientWidth, height = drawing.clientHeight;
      if (!canvasSize || (width === canvasSize.width && height === canvasSize.height)) return;
      if (framing === "manual") {
        // Keep the world point under the viewport centre when the reader has panned or zoomed.
        offset.x += (width - canvasSize.width) / 2;
        offset.y += (height - canvasSize.height) / 2;
        updateTransform();
      } else fitCanvas(framing === "initial");
      canvasSize = {width, height};
    }).observe(drawing);
    drawing.onpointerdown = (e) => {
      if (e.button !== 0 || e.target.closest(".graph-node,.graph-edge")) return;
      const start = pointerPos(e),
        rect = tool === "select" ? svg("rect", { class: "rubber" }) : null;
      drag = {
        start,
        origin: { ...offset },
        rect,
        add: e.shiftKey || e.ctrlKey || e.metaKey,
      };
      if (rect) drawing.append(rect);
      drawing.setPointerCapture(e.pointerId);
    };
    drawing.onpointermove = (e) => {
      if (!drag) return;
      const p = pointerPos(e),
        s = drag.start;
      if (drag.rect) {
        const props = {
          x: Math.min(s.x, p.x),
          y: Math.min(s.y, p.y),
          width: Math.abs(s.x - p.x),
          height: Math.abs(s.y - p.y),
        };
        Object.entries(props).forEach(([k, v]) => drag.rect.setAttribute(k, v));
      } else {
        framing = "manual";
        offset = { x: drag.origin.x + p.x - s.x, y: drag.origin.y + p.y - s.y };
        updateTransform();
      }
    };
    drawing.onpointerup = (e) => {
      if (!drag) return;
      if (drag.rect) {
        const p = pointerPos(e),
          s = drag.start;
        const box = {
          x: (Math.min(p.x, s.x) - offset.x) / scale,
          y: (Math.min(p.y, s.y) - offset.y) / scale,
          w: Math.abs(p.x - s.x) / scale,
          h: Math.abs(p.y - s.y) / scale,
        };
        const hits = [];
        for (const [id, r] of coordinates)
          if (
            !id.startsWith("dependency:") && r.x < box.x + box.w &&
            r.x + r.w > box.x &&
            r.y < box.y + box.h &&
            r.y + r.h > box.y
          )
            hits.push(id);
        pick(drag.add ? [...new Set([...chosen, ...hits])] : hits);
        drag.rect.remove();
      }
      drag = null;
    };
    drawing.onpointercancel = () => {
      drag?.rect?.remove();
      drag = null;
    };
    drawing.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const p = pointerPos(event);
        zoom(event.deltaY < 0 ? 1.1 : 0.91, p.x, p.y);
      },
      { passive: false },
    );
  }
  function heading(label, count) {
    const el = html("div", "section-head");
    el.append(html("h3", "", label));
    if (count !== undefined) el.append(html("span", "", count));
    return el;
  }
  const chip = (id) => html("span", "scope-chip", readable(id));
  function proof(ref, expanded = false) {
    const detail = html("details", "proof");
    detail.open = expanded;
    const line = Number(ref.line) || 1,
      end = Math.max(line, Number(ref.end) || line);
    detail.append(
      html(
        "summary",
        "",
        ref.file + " · L" + line + (end === line ? "" : "–" + end),
      ),
    );
    const source = graph.files[ref.file];
    if (typeof source !== "string") {
      detail.append(html("p", "small-empty", "此快照没有包含这份文件。"));
      return detail;
    }
    const input = source.split(/\r?\n/);
    let full = false,
      loaded = false;
    const code = html("div", "source-lines");
    const toggle = action("展开完整范围", "expand-source", () => {
      full = !full;
      fill();
    });
    function fill() {
      loaded = true;
      code.replaceChildren();
      const last = Math.min(
        input.length,
        full ? end + (view === "local" ? 0 : 1) : Math.min(end + (view === "local" ? 0 : 1), line + 9),
      );
      for (let i = Math.max(1, view === "local" ? line : line - 1); i <= last; i++) {
        const row = html(
          "div",
          "source-line" + (i >= line && i <= end ? " focus" : ""),
        );
        row.append(html("b", "", i), html("code", "", input[i - 1]));
        code.append(row);
      }
      toggle.textContent = full
        ? "收起源码"
        : "展开完整范围（" + (end - line + 1) + " 行）";
    }
    detail.append(code);
    if (end > line + 9) detail.append(toggle);
    detail.ontoggle = () => {
      if (detail.open && !loaded) fill();
    };
    if (expanded) fill();
    return detail;
  }
  function refsOf(id) {
    const item = lookup(id);
    if (!item) return [];
    if (item.file) return [item, ...(item.evidence || [])];
    if (item.evidence?.length) return item.evidence;
    return (item.nodeIds || [])
      .map((key) => nodes.get(key))
      .filter((n) => n?.file);
  }
  function scopedDiagnostics() {
    const active = new Set(chosen);
    for (const id of chosen)
      if (tree.has(id)) for (const n of aggregate(tree.get(id))) active.add(n);
    if (view !== "product" && active.has(scope.id))
      for (const n of displayNodes) active.add(n.id);
    if (active.size === 1) return diagnosticsByCaller.get(active.values().next().value) || [];
    return graph.diagnostics.filter((d) => active.has(d.caller));
  }
  function showMethodCalls(body, method) {
    const labels = {local: "仓内", builtin: "内置", stdlib: "标准库", external: "外部依赖", unresolved: "未解析", legacy: "未分类"};
    const groups = new Map([["local", (out.get(method.id) || []).filter(edge => edge.kind === "calls")]]);
    if (graph.callDiagnosticVersion) {
      for (const key of ["builtin", "stdlib", "external", "unresolved"]) groups.set(key, []);
    }
    for (const diagnostic of diagnosticsByCaller.get(method.id) || []) {
      let key = diagnostic.category;
      if (!Object.hasOwn(labels, key)) {
        // Older exports label call records in the message and have no target classification.
        if (!/^(调用目标未解析(?: · |$)|嵌套表达式的调用目标未解析$)/.test(diagnostic.message)) continue;
        key = "legacy";
      }
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(diagnostic);
    }
    const count = [...groups.values()].reduce((total, records) => total + records.length, 0);
    const block = html("section", "method-calls");
    block.append(heading("已记录的直接调用点", count));
    const filters = html("div", "call-counts");
    for (const [key, records] of groups) {
      const label = labels[key] + " " + records.length;
      const bucket = records.length ? action(label, "", () => {
        const byPosition = [...records].sort((a, b) => a.line - b.line || (a.column || 0) - (b.column || 0));
        showDiagnostics(byPosition, method.qualname + " · " + labels[key] + "调用点");
      }) : html("span", "", label);
      filters.append(bucket);
    }
    block.append(filters);
    if (!graph.callDiagnosticVersion) block.append(html("p", "subtle", "旧快照仅计入能归属到此方法的记录，调用分类可能缺失。"));
    body.append(block);
  }
  function series(host, items, show) {
    const pageSize = 12;
    let index = 0,
      more = null;
    function append() {
      more?.remove();
      const stop = Math.min(index + pageSize, items.length);
      while (index < stop) show(items[index++]);
      if (index < items.length) {
        more = action(
          "再显示 " + Math.min(pageSize, items.length - index) + " 项",
          "text-btn",
          append,
        );
        host.append(more);
      }
    }
    append();
  }
  function sourceNode(file, line) {
    const candidates = graph.nodes.filter(node => node.file === file && node.line <= line && line <= node.end);
    candidates.sort((a, b) => (a.end - a.line) - (b.end - b.line));
    return candidates[0];
  }
  function showSharedCalls() {
    const panel = $("shared-calls"), list = $("shared-list");
    const active = view === "product" && (scope.kind === "capability" ||
      scope === productRoot && ["all", "business"].includes($("root-layer").value));
    const members = new Set(scope.children.flatMap(child => child.nodeIds));
    const visible = active ? sharedCalls.filter(row => scope.id === productRoot.id ||
      members.has(row.target) || row.paths.some(path => members.has(path.entry))) : [];
    panel.hidden = !visible.length;
    list.replaceChildren();
    series(list, visible, row => {
      const target = nodes.get(row.target), block = html("details", "shared-target");
      const label = businessTitle(target);
      const entries = row.paths.map(path => nodes.get(path.entry).label).join("、");
      block.append(html("summary", "", entries + " → " + (label ? label + " · " : "") + target.qualname));
      const paths = html("ol");
      for (const path of row.paths) {
        const item = html("li");
        item.append(html("span", "", businessLabel(nodes.get(path.entry))));
        for (const id of path.edges) {
          const edge = edges.get(id);
          const source = nodes.get(edge.source), dest = nodes.get(edge.target);
          const button = action((edge.kind === "handles" ? "绑定 " : "→ ") + dest.qualname + " · L" + edge.line,
            "text-btn", () => locateSymbol(source, edge));
          button.title = edge.file + " · L" + edge.line;
          button.dataset.edge = id;
          item.append(button);
        }
        paths.append(item);
      }
      block.append(html("p", "subtle", "每个入口列出一条静态调用路径。点击行号查看调用位置和执行条件。"), paths);
      list.append(block);
    });
  }
  function readingAnchor(ref) {
    const anchor = html("div", "reading-anchor");
    anchor.append(proof(ref));
    const target = sourceNode(ref.file, ref.line);
    if (target && view !== "local")
      anchor.append(action("在图中定位 " + target.qualname, "text-btn", () => locateSymbol(target, ref)));
    return anchor;
  }
  function showConditions(parent, conditions) {
    for (const condition of conditions || []) {
      parent.append(html("p", "condition", condition.text), readingAnchor(condition.predicate || condition));
      if (condition.kind === "assertion" && condition.predicate &&
          (condition.line !== condition.predicate.line || condition.end !== condition.predicate.end)) {
        parent.append(html("p", "subtle", "完整断言语句"), readingAnchor(condition));
      }
      if (condition.exit) {
        parent.append(html("p", "condition", "分支末尾 · " + condition.exit.text), readingAnchor(condition.exit));
      }
    }
  }
  function showValueChoices(parent, choices, title) {
    if (!choices?.length) return;
    parent.append(html("h4", "", title));
    for (const choice of choices) {
      parent.append(html("p", "condition", choice.text), readingAnchor(choice));
    }
  }
  function showUncitedEvidence(parent, facts) {
    const missing = new Map();
    for (const fact of facts) {
      for (const ref of fact.uncoveredEvidence || []) {
        missing.set(JSON.stringify([ref.file, ref.line, ref.end, ref.role, ref.text]), ref);
      }
    }
    if (!missing.size) return;
    const details = html("details", "reading-citations");
    details.append(html("summary", "", "结论引用未覆盖的源码依据（" + missing.size + "）"),
      html("p", "subtle", "以下判断、退出或取值影响已引用的动作，点开可补读对应源码。"));
    const roles = {condition: "执行条件", exit: "提前退出", value: "条件取值"};
    for (const ref of missing.values())
      details.append(html("p", "condition", roles[ref.role] + " · " + ref.text), readingAnchor(ref));
    parent.append(details);
  }
  function showGroupEvidence(parent, label) {
    const evidence = graph.semantics?.groupEvidence?.[label];
    if (!evidence) return;
    const details = html("details", "reading-citations");
    details.append(html("summary", "", "查看分组依据"));
    if (evidence.reason) {
      details.append(html("p", "reading-text", evidence.reason.text));
      for (const ref of evidence.reason.refs) details.append(readingAnchor(ref));
    }
    for (const origin of evidence.origins) {
      const members = origin.nodeIds.map(id => nodes.get(id)).filter(Boolean);
      const part = html("details", "reading-citations");
      part.append(html("summary", "", origin.label + " · " + members.length + " 个源码成员"));
      series(part, members, node => part.append(action(node.qualname + " · " + node.file + " L" + node.line,
        "relation-row", () => locateSymbol(node, node))));
      details.append(part);
    }
    for (const shared of evidence.sharedTargets || []) {
      const target = nodes.get(shared.target);
      if (!target) continue;
      const part = html("details", "reading-citations");
      part.append(html("summary", "", "共同调用 " + target.qualname));
      for (const id of shared.edgeIds) {
        const edge = edges.get(id);
        if (edge) part.append(readingAnchor(edge));
      }
      details.append(part);
    }
    if (evidence.boundaryCalls?.length) {
      const calls = html("details", "reading-citations");
      calls.append(html("summary", "", "跨职责调用示例"));
      for (const id of evidence.boundaryCalls) {
        const edge = edges.get(id);
        if (edge) calls.append(html("p", "subtle", edgeName(edge)), readingAnchor(edge));
      }
      details.append(calls);
    }
    parent.append(details);
  }
  function showInterpretation(body, selected) {
    const guide = graph.semantics;
    if (!guide || !selected) return;
    if (selected.kind === "capability") {
      const section = html("section", "business-reading");
      section.setAttribute("aria-label", "业务分组讲解");
      section.append(html("div", "reading-source", "模型业务分组 · " + guide.provider + " / " + guide.model),
        html("h3", "", selected.label));
      body.append(section);
      showGroupEvidence(section, selected.label);
      series(section, selected.children, leaf => {
        section.append(action(businessLabel(leaf), "relation-row", () => navigate(leaf.id)),
          html("p", "reading-text", interpretation(leaf).text));
      });
      return;
    }
    const overview = selected.id === graph.tree.id;
    const entry = overview ? guide.overview : interpretation(selected);
    if (!entry) return;
    const section = html("section", "business-reading");
    section.setAttribute("aria-label", "模型业务解读");
    section.append(html("div", "reading-source", "模型解读 · " + guide.provider + " / " + guide.model),
      html("h3", "", overview ? "业务导读" : entry.title));
    if (overview && entry.claims?.length) {
      for (const [index, claim] of entry.claims.entries()) {
        const article = html("article", "reading-claim");
        article.append(html("p", "reading-text", claim.text));
        const refs = html("div", "claim-refs");
        for (const ref of claim.refs) {
          const node = sourceNode(ref.file, ref.line);
          if (node) {
            const location = ref.file + " · L" + ref.line + (ref.end > ref.line ? "–" + ref.end : "");
            const button = action(location, "text-btn", () => locateSymbol(node, ref));
            button.title = "打开源码 " + location;
            refs.append(button);
          } else refs.append(proof(ref));
        }
        article.append(refs);
        const facts = graph.overviewFacts?.[index] || [];
        if (facts.length) {
          showUncitedEvidence(article, facts);
          const conditions = html("details", "reading-citations");
          conditions.append(html("summary", "", "源码中的分支与筛选（" + facts.length + "）"));
          for (const fact of facts) {
            const row = html("div", "source-fact");
            row.append(html("p", "reading-text", fact.action?.text || fact.text), readingAnchor(fact.action || fact));
            showConditions(row, fact.conditions);
            showValueChoices(row, fact.valueChoices, "表达式如何取值");
            conditions.append(row);
          }
          article.append(conditions);
        }
        section.append(article);
      }
    } else {
      section.append(html("p", "reading-text", entry.text));
      const citations = html("details", "reading-citations");
      citations.append(html("summary", "", "核对引用（" + entry.refs.length + "）"));
      for (const ref of entry.refs) citations.append(readingAnchor(ref));
      section.append(citations);
    }
    if (overview) {
      section.append(html("small", "reading-coverage", "已讲解 " + guide.coverage.explained + " / " + guide.coverage.eligible + " 个节点"));
    }
    body.append(section);
    if (overview) {
      const entries = guide.items.filter(item => nodes.get(item.nodeId)?.kind === "entry");
      if (entries.length) {
        body.append(heading("查看业务入口", entries.length));
        const list = html("div", "reading-entries");
        body.append(list);
        series(list, entries, item => {
          const node = nodes.get(item.nodeId);
          list.append(action(businessLabel(node), "relation-row", () => locateSymbol(node)));
        });
      }
    }
  }
  function showLocalEvidence(body) {
    function memberList(label, ids) {
      if (!ids.length) return;
      const details = html("details", "module-site");
      details.append(html("summary", "", label + "（" + ids.length + "）"));
      series(details, ids, id => {
        const n = nodes.get(id);
        details.append(html("p", "subtle", n ? n.qualname + " · " + n.file + ":" + n.line : readable(id)));
      });
      body.append(details);
    }
    const selected = graphKeys().map(id => localItems.get(id) || localLinks.get(id)).filter(Boolean);
    if (!selected.length) {
      body.append(heading("本图的源码范围"));
      memberList("选中的代码节点", activeLocal.selectedNodeIds);
      memberList("直接关联的代码节点", activeLocal.neighborNodeIds);
      if (activeLocal.sourceRanges?.length) {
        const details = html("details", "module-site");
        details.append(html("summary", "", "模型可读的源码片段（" + activeLocal.sourceRanges.length + "）"));
        series(details, activeLocal.sourceRanges, r => details.append(proof(r)));
        body.append(details);
      }
      body.append(html("p", "subtle", "点击业务分组阅读讲解，点击箭头核对分组之间的源码关系。"));
      return;
    }
    for (const item of selected) {
      body.append(heading(item.label));
      if (item.nodeIds) {
        body.append(html("p", "reading-text", item.text));
        memberList("分组成员", item.nodeIds);
      } else if (item.sites?.length) {
        series(body, item.sites, site => {
          const article = html("article", "module-site");
          article.append(html("strong", "", edgeName(site)), proof(site, selected.length === 1));
          showInferredTarget(article, edges.get(site.id));
          showConditions(article, site.conditions);
          showValueChoices(article, site.valueChoices, "调用参数如何取值");
          body.append(article);
        });
        continue;
      } else {
        series(body, item.edgeIds, id => {
          const edge = edges.get(id);
          if (edge) body.append(html("p", "subtle", edgeName(edge)));
        });
      }
      series(body, item.refs, r => body.append(proof(r, selected.length === 1 && r === item.refs[0])));
    }
  }
  function showEvidence(body) {
    if (view === "local") return showLocalEvidence(body);
    if (requestedSource) body.append(heading("定位到的源码"), proof(requestedSource, true));
    const selected = chosen.size === 1 && graphKeys()[0];
    if (view === "collaboration" && selected && showCollaborationEvidence(body, selected)) return;
    if (selected && callSites.has(selected)) body.append(callSiteEvidence(callSites.get(selected)));
    if (moduleDrill && scope === productRoot && selected === moduleDrill.id) {
      body.append(html("h3", "module-title", modules.get(selected).label));
      if (modules.get(selected).groupId) showGroupEvidence(body, modules.get(selected).label);
      const counts = new Map();
      for (const edge of scopedCode.links) counts.set(edge.kind, (counts.get(edge.kind) || 0) + 1);
      if (counts.size) {
        body.append(heading("模块内的源码关系", scopedCode.links.length));
        for (const [kind, count] of counts) body.append(html("p", "subtle", (relations[kind] || kind) + " · " + count + " 处"));
      } else body.append(html("p", "small-empty", "当前视图未解析到成员之间的内部关系。"));
      if (scopedCode.items.length) body.append(html("p", "subtle", "点击成员查看定义，点击连线查看产生这条关系的源码。"));
      return;
    }
    const method = selected && nodes.get(targetKey(selected));
    if (method?.kind === "function" || method?.kind === "method") showMethodCalls(body, method);
    if (chosen.size === 1 && view !== "structure" && view !== "collaboration") showInterpretation(body, lookup(selected));
    body.append(heading("选中范围", chosen.size));
    const refs = new Map();
    for (const id of chosen) {
      body.append(chip(id));
      for (const r of refsOf(id))
        if (r.file) refs.set(JSON.stringify([r.file, r.line, r.end]), r);
    }
    if (chosen.size === 1) {
      const item = lookup(graphKeys()[0]);
      const definition =
        item.nodeIds?.length === 1 ? nodes.get(item.nodeIds[0]) : item;
      if (definition?.engine === "pycg") {
        if (!callSites.has(selected)) showInferredTarget(body, definition);
      } else if (definition?.basis) {
        body.append(heading("判定依据"), html("p", "subtle", definition.basis));
      }
      if (definition?.expression) {
        body.append(
          heading("源码表达式"),
          html("code", "expression", definition.expression),
        );
      }
    }
    body.append(heading("源码依据", refs.size));
    if (!refs.size)
      body.append(
        html(
          "p",
          "small-empty",
          chosen.size
            ? "展开下级模块，检查入口声明和代码关系。"
            : "点击图中的节点或关系，查看对应文件和行号。",
        ),
      );
    const refList = html("div");
    body.append(refList);
    let first = true;
    series(refList, [...refs.values()], (r) => {
      refList.append(proof(r, first));
      first = false;
    });
    if (chosen.size === 1) {
      const id = graphKeys()[0],
        n = nodes.get(id);
      if (view === "call" && n && scopedCode.items.length > fullCallViewLimit &&
          (wholeCallGraph || n.id !== callFocus)) {
        body.append(action("查看此节点的直接调用", "relation-row", () => {
          callFocus = n.id;
          wholeCallGraph = false;
          render();
        }));
      }
      if (n?.kind === "class") {
        const methods = methodList(id);
        if (methods.length) {
          body.append(heading("类中定义的方法", methods.length));
          const list = html("div");
          body.append(list);
          series(list, methods, (m) =>
            list.append(
              action(m.label + " · L" + m.line, "relation-row", () =>
                pick([m.id]),
              ),
            ),
          );
        }
      }
      const related = displayEdges.filter(
        (e) => e.id && (e.source === id || e.target === id),
      );
      if (related.length) {
        body.append(heading("相邻关系", related.length));
        const list = html("div");
        body.append(list);
        series(list, related, (e) =>
          list.append(
            action(edgeName(e) + " · L" + e.line, "relation-row", () =>
              pick([e.id]),
            ),
          ),
        );
      }
    }
    const diagnostics = scopedDiagnostics();
    if (diagnostics.length)
      body.append(
        action(
          "查看此范围的 " + diagnostics.length + " 条分析记录",
          "relation-row",
          () => showDiagnostics(diagnostics),
        ),
      );
  }
  function showInferredTarget(block, edge) {
    if (edge?.engine !== "pycg") return;
    const target = nodes.get(edge.target);
    block.append(html("p", "subtle", edge.basis),
      html("h4", "", "目标定义 · " + target.qualname),
      proof({file: target.file, line: target.line, end: target.line}));
  }
  function callSiteEvidence(site) {
    const block = html("article", "module-site");
    block.dataset.edge = site.edgeId;
    const caller = nodes.get(site.caller), callee = nodes.get(site.callee);
    const edge = edges.get(site.edgeId);
    block.append(html("strong", "", caller.qualname + " → " + callee.qualname));
    showInferredTarget(block, edge);
    if (site.kind === "handles") block.append(html("p", "subtle", "入口绑定处理函数"));
    if (site.conditions.length) {
      block.append(html("h4", "", "源码中的触发条件"));
      showConditions(block, site.conditions);
    } else if (site.kind !== "handles" && edge?.engine !== "pycg")
      block.append(html("p", "subtle", "执行到此处时调用。此前的返回或异常可能提前结束流程。"));
    showValueChoices(block, site.valueChoices, "调用参数如何取值");
    block.append(html("h4", "", site.kind === "handles" ? "绑定位置" : "调用位置"),
      proof(site, true), action("在图中定位 " + caller.qualname, "text-btn", () => locateSymbol(caller, site)));
    if (site.entryPaths?.length) {
      const paths = html("details", "reading-citations");
      paths.append(html("summary", "", "可定位的入口路径"));
      for (const path of site.entryPaths) {
        paths.append(html("p", "reading-text", businessLabel(nodes.get(path.entry))));
        for (const id of path.edges) {
          const edge = edges.get(id);
          if (!edge) continue;
          paths.append(html("p", "subtle", edgeName(edge)), readingAnchor(edge));
          if (id !== site.edgeId) showConditions(paths, callSites.get(id)?.conditions);
        }
      }
      block.append(paths);
    }
    return block;
  }
  function showCollaborationEvidence(body, id) {
    const relation = moduleLinks.get(id);
    if (relation) {
      body.append(html("h3", "module-title", relation.label), heading("源码调用与绑定", relation.sites.length));
      const list = html("div");
      body.append(list);
      series(list, relation.sites, site => {
        list.append(callSiteEvidence(site));
      });
      return true;
    }
    const module = modules.get(id);
    if (!module) return false;
    body.append(html("h3", "module-title", module.label));
    if (module.basis) body.append(html("p", "subtle", module.basis));
    if (module.groupId) showGroupEvidence(body, module.label);
    body.append(action("展开模块内部关系图", "relation-row", () => enterModule(id)));
    const related = [...moduleLinks.values()].filter(r => r.source === id || r.target === id);
    body.append(heading("模块协作", related.length));
    const links = html("div"); body.append(links);
    series(links, related, r => links.append(action(r.label + " · " + r.sites.length + " 处", "relation-row", () => {
      $("module-focus").value = id;
      $("module-direction").value = r.source === id ? "out" : "in";
      modulePage = Math.floor(focusedRelations($("module-direction").value, id).indexOf(r) / linksPerPage);
      chosen = new Set([r.id]);
      render();
    })));
    if (!related.length) body.append(html("p", "small-empty", "没有识别到此模块与其他生产模块之间的静态调用。"));
    body.append(heading("模块中的代码", module.nodeIds.length));
    const members = html("div"); body.append(members);
    series(members, module.nodeIds, key => {
      const node = nodes.get(key);
      members.append(action(node.qualname + " · " + node.file + ":" + node.line, "relation-row", () => locateSymbol(node)));
    });
    return true;
  }
  function showDiagnostics(list, title = "分析记录") {
    const panel = $("diag-list");
    $("diag-dialog").querySelector("h2").textContent = title;
    panel.replaceChildren();
    if (!list.length)
      panel.append(html("p", "small-empty", "当前没有分析记录。"));
    series(panel, list, (d) => {
      const item = html("div", "diag-item");
      const target = d.kind === "calls" && nodes.get(d.target);
      const message = target ? (d.expression ? d.expression + " → " : "") + target.qualname : d.message;
      item.append(html("p", "", message));
      if (d.targetName && !message.includes(d.targetName)) item.append(html("code", "expression", d.targetName));
      if (d.targetName && d.expression && d.expression !== d.targetName)
        item.append(html("code", "expression", "调用写法 · " + d.expression));
      if (d.file) item.append(proof(d));
      panel.append(item);
    });
    $("diag-dialog").showModal();
  }
  function bindClaimComparison() {
    if (offline) return;
    const button = $("compare-graph"), dialog = $("compare-dialog"), input = $("previous-graph");
    const status = $("compare-status"), result = $("compare-result");
    const labels = {unchanged: "引用仍匹配", moved: "位置移动", changed: "实现有变化",
      missing: "原证据缺失", unresolved: "需人工定位"};
    button.hidden = false;
    button.onclick = () => dialog.showModal();
    $("compare-close").onclick = () => dialog.close();
    function showRef(parent, ref) {
      parent.append(html("p", "reading-text", labels[ref.status] + " · " + ref.reason));
      const old = html("details", "reading-citations");
      old.append(html("summary", "", "旧图 · " + ref.old.file + " L" + ref.old.line + "–" + ref.old.end),
        html("pre", "", ref.oldText));
      for (const condition of ref.oldConditions || [])
        old.append(html("p", "condition", condition.file + " L" + condition.line + " · " + condition.text));
      parent.append(old);
      if (ref.new) {
        parent.append(proof(ref.new));
        const target = sourceNode(ref.new.file, ref.new.line);
        if (target) parent.append(action("在当前图中定位", "text-btn", () => {
          dialog.close();
          locateSymbol(target, ref.new);
        }));
      }
      for (const condition of ref.newConditions || [])
        parent.append(html("p", "condition", condition.text), proof(condition));
    }
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      input.disabled = true;
      result.replaceChildren();
      status.textContent = "正在用当前源码核对 " + file.name + "…";
      try {
        const report = await api("semantics/compare", "POST", {previous: await file.text()});
        status.textContent = "旧图 #" + report.previousSnapshot.slice(0, 9) + " → 当前 #" +
          report.currentSnapshot.slice(0, 9) + " · " + Object.entries(labels)
            .map(([key, label]) => label + " " + report.counts[key]).join("，");
        const needsReview = c => ["changed", "missing", "unresolved"].includes(c.status);
        const claims = [...report.claims].sort((a, b) => Number(needsReview(b)) - Number(needsReview(a)));
        series(result, claims, claim => {
          const item = html("details");
          item.append(html("summary", "", labels[claim.status] + " · " + claim.text));
          for (const ref of claim.refs) showRef(item, ref);
          result.append(item);
        });
      } catch (error) {
        status.textContent = error.message;
      } finally {
        input.value = "";
        input.disabled = false;
      }
    };
  }
  function offlineNotes(notes) {
    localStorage.setItem(
      "How2Trust.notes." + graph.snapshot,
      JSON.stringify(notes),
    );
    state.notes = notes;
  }
  function targetsBlock(body, targets = graphKeys()) {
    body.append(heading("选中范围", targets.length));
    for (const id of targets) body.append(chip(id));
    if (!targets.length)
      body.append(html("p", "small-empty", "先点击节点或使用画布的圈选工具。"));
  }
  function showNotes(body) {
    targetsBlock(body);
    const form = html("form", "note-form"),
      text = html("textarea");
    text.placeholder = "为选中的模块、节点或关系添加批注";
    text.setAttribute("aria-label", "批注内容");
    text.rows = 3;
    text.value = noteDraft;
    text.required = true;
    const save = html("button", "form-btn primary", "保存批注");
    save.disabled = !chosen.size;
    text.oninput = () => (noteDraft = text.value);
    form.append(text, save);
    body.append(form);
    form.onsubmit = async (e) => {
      e.preventDefault();
      const content = text.value.trim(),
        targets = selectionTargets();
      if (!content || !targets.length) return;
      save.disabled = true;
      try {
        if (offline)
          offlineNotes([
            ...state.notes,
            {
              id: globalThis.crypto?.randomUUID?.() || String(Date.now()),
              targets,
              text: content,
            },
          ]);
        else state = await api("notes", "POST", { targets, text: content });
        noteDraft = "";
        renderInspector();
        toast("批注已保存");
      } catch (err) {
        toast(err.message);
        save.disabled = false;
      }
    };
    const selectedTargets = new Set(selectionTargets());
    const relevant = state.notes.filter((n) =>
      n.targets.some((id) => selectedTargets.has(targetKey(id))),
    );
    body.append(heading("此范围的批注", relevant.length));
    if (!relevant.length)
      body.append(html("p", "small-empty", "所选范围还没有批注。"));
    for (const note of [...relevant].reverse()) {
      const card = html("article", "note-card"),
        footer = html("footer");
      card.append(html("p", "", note.text));
      footer.append(
        html("span", "subtle", note.targets.map(readable).join(" · ")),
        action("删除", "", async () => {
          try {
            if (offline)
              offlineNotes(state.notes.filter((n) => n.id !== note.id));
            else
              state = await api(
                "notes/" + encodeURIComponent(note.id),
                "DELETE",
              );
            renderInspector();
          } catch (err) {
            toast(err.message);
          }
        }),
      );
      card.append(footer);
      body.append(card);
    }
  }
  function showChat(body) {
    const thread = state.threads.find((t) => t.id === currentThread),
      targets = thread ? [...new Set(thread.targets.map(targetKey))] : selectionTargets();
    const head = heading("对话记录", state.threads.length);
    body.append(head);
    const select = html("select", "thread-select");
    select.setAttribute("aria-label", "选择历史对话");
    const first = html("option", "", "新对话");
    first.value = "";
    select.append(first);
    for (const t of [...state.threads].reverse()) {
      const opt = html(
        "option",
        "",
        clip(t.messages.find((m) => m.role === "user")?.content || t.id, 44),
      );
      opt.value = t.id;
      select.append(opt);
    }
    select.value = currentThread;
    select.onchange = () => {
      currentThread = select.value;
      const selected = state.threads.find((t) => t.id === currentThread);
      if (selected) chosen = new Set(selected.targets);
      requestedSource = null;
      questionDraft = "";
      renderInspector();
      markSelection();
    };
    body.append(select);
    targetsBlock(body, targets);
    if (thread)
      for (const [i, m] of thread.messages.entries()) {
        const card = html(
          "article",
          "message " + (m.role === "user" ? "user" : "assistant"),
        );
        card.append(html("strong", "", m.role === "user" ? "你" : "模型"));
        const plain = html("p", "chat-plain", m.content);
        if (m.role === "assistant" && !m.needsRetry) {
          const reply = html("div", "chat-markdown");
          reply.append(plain);
          card.append(reply);
          import("/assets/chat-markdown.js")
            .then(({renderMarkdown}) => reply.replaceChildren(renderMarkdown(m.content)))
            .catch(() => {}); // The original reply stays readable if the renderer fails to load.
        } else card.append(plain);
        const previous = thread.messages[i - 1];
        if (m.needsRetry && previous?.role === "user" && !offline && state.llm?.configured) {
          if (retryError?.thread === currentThread && retryError.id === m.id) {
            const error = html("p", "", retryError.text);
            error.setAttribute("role", "alert");
            card.append(error);
          }
          const retry = action(
            pendingChat?.thread === currentThread && pendingChat.retryId === m.id ? "正在重新回答…" : "重新回答此问题",
            "form-btn",
            () => ask(previous.content, m.id),
          );
          retry.disabled = !!pendingChat;
          card.append(retry);
        }
        if (m.sources?.length) {
          const citations = html("details");
          citations.append(
            html("summary", "", "查看引用（" + m.sources.length + "）"),
          );
          for (const ref of m.sources) citations.append(proof(ref));
          card.append(citations);
        }
        body.append(card);
      }
    if (offline || !state.llm?.configured) {
      body.append(
        html(
          "div",
          "availability",
          offline
            ? "此文件是离线快照。使用本地服务打开仓库可进行模型问答。"
            : "配置问答模型后，即可基于选中的源码提问。",
        ),
      );
      if (!offline) body.append(action("配置问答模型", "form-btn", () => openModelSettings("chat")));
      return;
    }
    body.append(heading("向模型提问", state.llm.model));
    const form = html("form", "chat-form"),
      text = html("textarea");
    text.rows = 3;
    text.required = true;
    text.value = questionDraft;
    text.placeholder = thread ? "继续追问…" : "就选中的图和源码提问…";
    text.setAttribute("aria-label", "向模型提问");
    text.oninput = () => (questionDraft = text.value);
    const send = html(
      "button",
      "form-btn primary",
      pendingChat?.thread === currentThread && pendingChat.retryId === null ? "等待模型回复…" : "发送问题",
    );
    send.disabled = !!pendingChat || !targets.length;
    form.append(text, send);
    body.append(form);
    async function ask(question, retryId = null) {
      if (pendingChat || !targets.length || !question) return;
      const sentThread = currentThread;
      pendingChat = { thread: sentThread, retryId };
      retryError = null;
      renderInspector();
      try {
        const updated = await api("chat", "POST", {
          targets: thread ? thread.targets : targets,
          thread: sentThread,
          question,
          ...(retryId !== null && { retryMessageId: retryId }),
        });
        const sameScope =
          sentThread === currentThread &&
          (!!thread || JSON.stringify(targets) === JSON.stringify(selectionTargets()));
        const newThread =
          updated.activeThread ||
          sentThread ||
          updated.threads.find((t) => !state.threads.some((s) => s.id === t.id))
            ?.id;
        state = updated;
        if (sameScope) {
          currentThread = newThread || "";
          if (retryId === null && questionDraft.trim() === question) questionDraft = "";
        } else toast("模型已回复，可在对话记录中查看。");
      } catch (err) {
        const reason = err instanceof TypeError ? "无法连接工作台，请检查本地服务后重试。" : err.message;
        if (retryId !== null) retryError = { thread: sentThread, id: retryId, text: "重新回答失败。" + reason };
        else toast(reason);
      } finally {
        pendingChat = null;
        renderInspector();
        if (retryId === null && inspector === "chat" && currentThread)
          $("inspect-content").scrollTop = $("inspect-content").scrollHeight;
      }
    }
    form.onsubmit = (e) => {
      e.preventDefault();
      ask(text.value.trim());
    };
    text.onkeydown = (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        form.requestSubmit();
      }
    };
  }
  function renderInspector() {
    document.querySelectorAll("[data-inspect]").forEach((b) => {
      const active = b.dataset.inspect === inspector;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    const body = $("inspect-content");
    body.replaceChildren();
    if (inspector === "evidence") showEvidence(body);
    if (inspector === "notes") showNotes(body);
    if (inspector === "chat") showChat(body);
  }
  let modelProfiles;
  const modelDialog = () => $("model-dialog");
  function originalProfile() {
    return modelProfiles[$("model-stage").value === "chat" ? "chat" : "semantics"];
  }
  function retainsSessionKey() {
    const profile = originalProfile();
    return profile.sessionKey && profile.base_url === $("model-url").value.trim().replace(/\/+$/, "") &&
      profile.protocol === $("model-protocol").value;
  }
  function updateAuthFields() {
    const mode = $("model-auth").value;
    $("model-env-field").hidden = mode !== "env";
    $("model-env").required = mode === "env";
    $("model-secret-field").hidden = mode !== "session";
    const keep = mode === "session" && retainsSessionKey();
    $("model-secret").required = mode === "session" && !keep;
    $("model-secret").placeholder = keep ? "已设置，留空沿用" : "输入本次服务使用的 API Key";
  }
  function fillModelForm() {
    const profile = originalProfile();
    $("model-provider").value = profile.provider;
    $("model-url").value = profile.base_url;
    $("model-name").value = profile.model;
    $("model-protocol").value = profile.protocol;
    $("model-env").value = profile.api_key_env;
    $("model-secret").value = "";
    $("model-auth").value = profile.useSessionKey ? "session" : profile.api_key_env ? "env" : "none";
    updateAuthFields();
  }
  async function openModelSettings(stage) {
    $("model-form").hidden = true;
    $("model-offline").hidden = !offline;
    $("model-status").textContent = offline ? "" : "正在读取本机设置…";
    modelDialog().showModal();
    if (offline) return;
    try {
      modelProfiles = await api("models");
      $("model-stage").value = stage || (JSON.stringify(modelProfiles.chat) === JSON.stringify(modelProfiles.semantics) ? "both" : "semantics");
      fillModelForm();
      $("model-status").textContent = "";
      $("model-form").hidden = false;
      $("model-stage").focus();
    } catch (error) { $("model-status").textContent = error.message; }
  }
  function bindModelSettings() {
    $("model-settings").onclick = () => openModelSettings();
    $("model-close").onclick = $("model-cancel").onclick = () => modelDialog().close();
    modelDialog().onclose = () => { $("model-secret").value = ""; };
    $("model-stage").onchange = fillModelForm;
    $("model-auth").onchange = $("model-protocol").onchange = updateAuthFields;
    $("model-url").oninput = () => {
      const profile = originalProfile();
      if ($("model-url").value.trim().replace(/\/+$/, "") !== profile.base_url && $("model-env").value === profile.api_key_env)
        $("model-env").value = "";
      updateAuthFields();
    };
    $("model-provider").onchange = () => {
      const presets = {
        DeepSeek: ["https://api.deepseek.com", "openai", "deepseek-flash", "DEEPSEEK_API_KEY"],
        Ollama: ["http://127.0.0.1:11434/v1", "openai", "", ""],
        Anthropic: ["https://api.anthropic.com/v1", "anthropic", "", "ANTHROPIC_API_KEY"],
      };
      const preset = presets[$("model-provider").value];
      if (!Array.isArray(preset)) return;
      [$("model-url").value, $("model-protocol").value, $("model-name").value, $("model-env").value] = preset;
      $("model-secret").value = "";
      $("model-auth").value = preset[3] ? "env" : "none";
      updateAuthFields();
    };
    $("model-form").onsubmit = async (event) => {
      event.preventDefault();
      const mode = $("model-auth").value;
      $("model-save").disabled = true;
      $("model-status").textContent = "正在保存…";
      try {
        const result = await api("models", "POST", {
          stage: $("model-stage").value, provider: $("model-provider").value,
          base_url: $("model-url").value, model: $("model-name").value, protocol: $("model-protocol").value,
          api_key_env: mode === "env" ? $("model-env").value : "",
          key: mode === "session" ? $("model-secret").value : "", keepKey: mode === "session" && retainsSessionKey(),
        });
        state.llm = result.llm;
        state.semanticLlm = result.semanticLlm;
        showGeneration(generation);
        render();
        modelDialog().close();
        toast("模型设置已应用");
      } catch (error) { $("model-status").textContent = error.message; }
      finally { $("model-save").disabled = false; }
    };
  }
  async function init() {
    bindInspectorResize();
    document
      .querySelectorAll("i[data-icon]")
      .forEach((el) => drawIcon(el, el.dataset.icon));
    if (offline) {
      graph = window.H2T_GRAPH;
      let notes = [];
      try {
        const saved = JSON.parse(
          localStorage.getItem("How2Trust.notes." + graph.snapshot) || "[]",
        );
        if (Array.isArray(saved)) notes = saved;
      } catch {
        toast("浏览器未提供可用的本地批注记录。");
      }
      state = { notes, threads: [], llm: { configured: false } };
    } else {
      state = await api("state");
      graph = await api("graph");
      serviceProgress = state.generationProgress;
      if (["running", "failed"].includes(serviceProgress?.status)) generation = serviceProgress;
    }
    if (graph.sourceTree) graph = {...graph, tree: graph.sourceTree};
    indexGraph();
    indexSemantics();
    indexCollaboration();
    view = graph.semantics || generation ? "product" : "structure";
    scope = productRoot;
    chosen.add(scope.id);
    open.add(scope.id);
    $("connection").textContent = offline ? "离线快照" : "本地工作区";
    bindModelSettings();
    bindClaimComparison();
    refreshLocalHistory();
    $("local-history").onchange = e => {
      const item = graph.localBusinessViews.find(value => value.id === e.target.value);
      if (item) openLocal(item);
      else if (view === "local") leaveLocal();
    };
    $("generate-local").onclick = generateLocal;
    $("local-neighbors").onchange = () => { localError = ""; updateLocalTools(); };
    $("export").hidden = offline;
    $("review-link").hidden = offline || !graph.hasReview;
    $("snapshot").textContent = "#" + String(graph.snapshot).slice(0, 9);
    $("snapshot").title = graph.snapshot;
    document.title = graph.name + " · How2Trust";
    $("diag-count").textContent = graph.diagnostics.length;
    $("diagnostics").onclick = () => showDiagnostics(graph.diagnostics);
    $("root-layer").onchange = () => {
      setScope(productRoot.id);
      selectScopeInView();
      render();
    };
    $("generate-semantics").onclick = $("generation-retry").onclick = generateBusinessGraph;
    $("generation-settings").onclick = () => openModelSettings("semantics");
    $("diag-close").onclick = () => $("diag-dialog").close();
    $("home").onclick = (e) => {
      e.preventDefault();
      navigate(graph.tree.id);
    };
    $("up").onclick = () => {
      if (view === "local") leaveLocal();
      else if (!moduleDrill) navigate(parent.get(scope.id));
      else if (view === "module") returnToCollaboration();
      else showModuleView("module");
    };
    $("search").oninput = (e) => {
      query = e.target.value.trim().toLowerCase();
      renderTree();
    };
    $("collapse").onclick = () => {
      open.clear();
      open.add(graph.tree.id);
      renderTree();
    };
    function selectView(next) {
        if (graph.localOnly) return;
        leaveLocal(false);
        if (moduleDrill) {
          if (next === "class" || next === "call") return showModuleView(next);
          if (next === "collaboration") return returnToCollaboration();
          moduleDrill = null;
        }
        requestedSource = null;
        view = next;
        wholeCallGraph = false;
        if (["structure", "product", "collaboration"].includes(next)) setScope(productRoot.id);
        if (next === "collaboration") {
          const focus = $("module-focus").value;
          chosen = new Set(focus ? [focus] : []);
          inspector = "evidence";
        } else selectScopeInView();
        currentThread = "";
        render();
    }
    for (const b of document.querySelectorAll("[data-view]"))
      b.onclick = () => selectView(b.dataset.view);
    for (const id of ["module-focus", "module-direction"])
      $(id).onchange = () => { modulePage = 0; selectView("collaboration"); };
    $("module-prev").onclick = () => { modulePage--; selectView("collaboration"); };
    $("module-next").onclick = () => { modulePage++; selectView("collaboration"); };
    for (const b of document.querySelectorAll("[data-inspect]"))
      b.onclick = () => selectInspector(b.dataset.inspect);
    for (const mode of ["pan", "select"])
      $(mode).onclick = () => {
        tool = mode;
        syncTool();
      };
    $("plus").onclick = () => zoom(1.2);
    $("minus").onclick = () => zoom(1 / 1.2);
    $("fit").onclick = () => fitCanvas();
    $("call-range").onclick = () => {
      wholeCallGraph = !wholeCallGraph;
      if (!wholeCallGraph) {
        const selected = graphKeys()[0];
        if (chosen.size === 1 && scopedCode.items.some(n => n.id === selected))
          callFocus = selected;
        pick([callFocus]);
      }
      render();
    };
    $("call-dependencies").onclick = () => {
      dependencyCaller = dependencyCaller ? null : chosen.values().next().value;
      renderGraph(false);
    };
    bindCanvas();
    bindFullscreen();
    $("views").hidden = !!graph.localOnly;
    const initialLocal = graph.localBusinessViews?.find(item => item.id === graph.initialLocalView);
    if (initialLocal) openLocal(initialLocal);
    else render();
    function openLinkedSource() {
      if (graph.localOnly) return;
      const location = new URLSearchParams(window.location.hash.slice(1));
      if (location.get("settings") === "models") {
        if (!modelDialog().open) openModelSettings();
        return;
      }
      if (!location.has("file")) {
        const views = new Map([["structure", "structure"], ["business", "product"], ["collaboration", "collaboration"]]);
        if (views.has(location.get("view"))) selectView(views.get(location.get("view")));
        return;
      }
      const file = location.get("file");
      const line = Number(location.get("line"));
      const text = graph.files[file];
      if (typeof text !== "string") return;
      const leaf = moduleLeaves.get(file);
      if (!Number.isInteger(line) || line < 1 || line > text.split(/\r?\n/).length) {
        if (leaf) navigate(leaf.id);
        return;
      }
      const node = sourceNode(file, line);
      const ref = {file, line, end: line};
      if (node) locateSymbol(node, ref);
      else {
        requestedSource = ref;
        selectInspector("evidence");
      }
    }
    window.addEventListener("hashchange", openLinkedSource);
    openLinkedSource();
    if (generation) {
      showGeneration(generation);
      if (generation.status === "running") monitorGeneration(false);
    }
  }
  init().catch((err) => {
    $("connection").textContent = "加载失败";
    $("scope-name").textContent = "无法打开仓库图";
    $("metrics").textContent = err.message;
  });
})();
