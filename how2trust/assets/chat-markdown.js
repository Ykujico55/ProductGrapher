import { lexer } from "./vendor/marked.esm.js";

const containers = { paragraph: "p", blockquote: "blockquote", strong: "strong", em: "em", del: "del" };

function element(tag, parent) {
  const node = document.createElement(tag);
  parent.append(node);
  return node;
}

function decodeEntities(text) {
  // Only character references enter the HTML decoder. Model text and HTML
  // tokens themselves are always appended as text nodes.
  return text.replace(/&(?:#[0-9]+|#x[\da-f]+|[a-z][\da-z]*);/gi, reference => {
    const decoder = document.createElement("textarea");
    decoder.innerHTML = reference;
    return decoder.value;
  });
}

function appendLink(parent, token) {
  const href = decodeEntities(token.href);
  let target = parent;
  if (/^(?:https?:\/\/|mailto:)/i.test(href)) {
    target = element("a", parent);
    target.href = href;
    target.target = "_blank";
    target.rel = "noopener noreferrer";
    if (token.title) target.title = decodeEntities(token.title);
  }
  // Show an image as a link to its source, without fetching it on receipt.
  if (token.type === "image") target.append(decodeEntities(token.text || token.href));
  else appendTokens(target, token.tokens);
}

function appendTable(parent, token) {
  const wrapper = element("div", parent);
  wrapper.className = "chat-table-scroll";
  const table = element("table", wrapper);
  function row(section, cells, tag) {
    const tr = element("tr", section);
    for (const cell of cells) {
      const td = element(tag, tr);
      if (cell.align) td.style.textAlign = cell.align;
      appendTokens(td, cell.tokens);
    }
  }
  row(element("thead", table), token.header, "th");
  const body = element("tbody", table);
  for (const cells of token.rows) row(body, cells, "td");
}

function appendTokens(parent, tokens, loose = false) {
  for (const token of tokens) {
    const tag = containers[token.type];
    if (tag) {
      appendTokens(element(tag, parent), token.tokens);
      continue;
    }
    switch (token.type) {
      case "heading":
        appendTokens(element("h" + token.depth, parent), token.tokens);
        break;
      case "list": {
        const list = element(token.ordered ? "ol" : "ul", parent);
        if (token.ordered && token.start !== 1) list.start = token.start;
        for (const item of token.items) appendTokens(element("li", list), item.tokens, token.loose);
        break;
      }
      case "checkbox": {
        const check = element("input", parent);
        check.type = "checkbox";
        check.disabled = true;
        check.checked = token.checked;
        break;
      }
      case "table":
        appendTable(parent, token);
        break;
      case "code":
        element("code", element("pre", parent)).textContent = token.text;
        break;
      case "codespan":
        element("code", parent).textContent = token.text;
        break;
      case "link":
      case "image":
        appendLink(parent, token);
        break;
      case "br":
      case "hr":
        element(token.type, parent);
        break;
      case "text":
        if (token.tokens) appendTokens(loose ? element("p", parent) : parent, token.tokens);
        else parent.append(decodeEntities(token.text));
        break;
      case "html":
      case "escape":
        parent.append(token.text);
        break;
    }
  }
}

export function renderMarkdown(text) {
  const result = document.createDocumentFragment();
  appendTokens(result, lexer(text, { gfm: true }));
  return result;
}
