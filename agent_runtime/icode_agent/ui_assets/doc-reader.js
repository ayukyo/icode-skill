/* Local, read-only document reader. The request token stays inside this closure. */
(() => {
  "use strict";

  const createSlugger = () => {
    const used = new Set();
    return (title) => {
      const base = String(title).normalize("NFC").trim().toLowerCase()
        .replace(/[^\p{L}\p{N}\p{M}_\s-]/gu, "").replace(/\s+/g, "-") || "section";
      let candidate = base;
      let count = 0;
      while (used.has(candidate)) candidate = `${base}-${++count}`;
      used.add(candidate);
      return candidate;
    };
  };
  // Unknown identities remain separate; a display name never establishes identity.
  const bookFamily = (book) => book.kind === "module"
    ? `module:${book.module_key || book.id}` : `project:${book.project_id || book.id}`;
  const parseRoute = (href) => {
    const url = new URL(href);
    let fragment = "";
    try { fragment = decodeURIComponent(url.hash.slice(1)); } catch (_error) { /* Invalid fragment has no target. */ }
    return { active: url.searchParams.get("view") === "docs", bookId: url.searchParams.get("doc_book") || "", chapterId: url.searchParams.get("doc_chapter") || "", fragment };
  };
  const routeURL = (href, route) => {
    const url = new URL(href);
    url.searchParams.set("view", "docs");
    if (route.bookId) url.searchParams.set("doc_book", route.bookId); else url.searchParams.delete("doc_book");
    if (route.chapterId) url.searchParams.set("doc_chapter", route.chapterId); else url.searchParams.delete("doc_chapter");
    url.hash = route.fragment ? encodeURIComponent(route.fragment) : "";
    return url.href;
  };
  const safeExternalLink = (href) => /^(https?:|mailto:)/i.test(String(href));
  const scrollKey = (book, chapterId) => `icode-doc-scroll:${JSON.stringify([book.id, book.version || book.branch || "", chapterId])}`;
  const isMetadataLabel = (label) => /^(?:(?:项目|工程|模块)?元信息|元数据|KEYS)(?:\s|$|[（(：:])/i.test(String(label).trim());
  const versionLabel = (book) => book.kind === "project" ? `分支 ${book.branch || "未记录"}`
    : `${book.branch ? `${book.branch} · ` : ""}快照 ${(book.version || "未记录").slice(0, 12)}`;
  const integrityText = (status) => ({ verified: "摘要一致", modified: "正文已变化", unverified: "缺少摘要或未核验" }[status] || "缺少摘要或未核验");
  const chapterSourceText = (source = {}) => `${source.commit ? `章节基线 ${String(source.commit).slice(0, 12)}` : "章节基线未记录"}${source.source_id ? ` · 来源 ${String(source.source_id).slice(0, 12)}` : ""}`;
  const mappedValue = (mapping, key) => {
    if (!mapping) return undefined;
    if (Object.prototype.hasOwnProperty.call(mapping, key)) return mapping[key];
    // Marked URI-encodes Unicode links. Match only server-approved equivalents,
    // preserving reserved separators so encoded path segments never become new paths.
    const decoded = (value) => { try { return decodeURI(value); } catch (_error) { return value; } };
    const equivalent = Object.keys(mapping).find((candidate) => decoded(candidate) === decoded(key));
    return equivalent === undefined ? undefined : mapping[equivalent];
  };
  const safeDiagramCSS = (css) => {
    if (typeof css !== "string" || /@(?:import|font-face)\b/i.test(css)) return false;
    const withoutLocalURLs = css.replace(/url\(\s*(['"]?)(#[\w-]+)\1\s*\)/gi, "");
    return !/url\s*\(/i.test(withoutLocalURLs);
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { createSlugger, bookFamily, parseRoute, routeURL, safeExternalLink, scrollKey, isMetadataLabel, versionLabel, mappedValue, integrityText, chapterSourceText };
  }
  if (typeof document === "undefined") return;

  const byId = (id) => document.getElementById(id);
  const root = byId("doc-library");
  if (!root) return;
  const token = document.querySelector('meta[name="icode-ui-token"]').content;
  const state = { books: [], book: null, chapter: null, projectId: document.body.dataset.projectId || null, catalogLoaded: false,
    catalogEpoch: 0, bookEpoch: 0, chapterEpoch: 0, searchEpoch: 0, searchTimer: null,
    urls: [], reading: false, currentKey: null, renderId: 0,
    originBook: null, dependencyBooks: new Map(), expandedDependencies: new Set(), dependencyErrors: new Map(), pendingRoute: null, pendingRestore: null,
    treeOwnerId: null, treePositions: new Map() };
  const request = async (endpoint, params = {}) => {
    const response = await fetch(`/api/v1/docs/${endpoint}?${new URLSearchParams(params)}`, { headers: { "X-ICODE-UI-Token": token } });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "文档读取失败");
    return payload;
  };
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const statusText = (status) => ({ verified: "记录已审核", partial: "部分文档", legacy: "旧格式 / 版本未核验",
    missing: "缺失", ambiguous: "绑定有歧义", modified: "正文已变化", unverified: "未核验", exact: "精确快照",
    invalid: "元信息异常", source_mismatch: "来源不匹配", error: "读取异常" }[status] || status || "未核验");
  const setMessage = (text, danger = false) => {
    byId("doc-message").textContent = text;
    byId("doc-message").classList.toggle("danger-text", danger);
  };
  const stored = (key, storage = "sessionStorage") => { try { return globalThis[storage].getItem(key); } catch (_error) { return null; } };
  const store = (key, value, storage = "sessionStorage") => {
    try { globalThis[storage].setItem(key, value); } catch (_error) { /* Reading remains available with storage disabled. */ }
  };
  const panelKey = "icode-doc-panels:v1";
  const panels = { sidebar: false, tree: false, outline: false };
  try {
    const saved = JSON.parse(stored(panelKey, "localStorage"));
    if (saved && typeof saved === "object") Object.keys(panels).forEach((key) => {
      if (typeof saved[key] === "boolean") panels[key] = saved[key];
    });
  } catch (_error) { /* Ignore malformed nonsecret preferences. */ }
  let focusSnapshot = null;
  const applyPanels = () => {
    const collapsed = focusSnapshot && state.reading ? { sidebar: true, tree: true, outline: true } : panels;
    const definitions = [
      ["sidebar", "sidebar-toggle", "sidebar-navigation", "工程导航", "导航"],
      ["tree", "doc-tree-toggle", "doc-tree", "章节导航", "章节"],
      ["outline", "doc-outline-toggle", "doc-outline", "章内目录", "目录"],
    ];
    definitions.forEach(([key, buttonId, panelId, label, shortLabel]) => {
      const button = byId(buttonId), panel = byId(panelId);
      // Never leave keyboard focus in a region that is about to disappear.
      if (collapsed[key] && panel.contains(document.activeElement)) button.focus({ preventScroll: true });
      panel.hidden = collapsed[key];
      button.setAttribute("aria-expanded", String(!collapsed[key]));
      button.setAttribute("aria-label", `${collapsed[key] ? "展开" : "收起"}${label}`);
      button.textContent = `${collapsed[key] ? "展开" : "收起"}${shortLabel}`;
    });
    document.body.dataset.sidebarCollapsed = String(collapsed.sidebar);
    root.dataset.treeCollapsed = String(collapsed.tree);
    root.dataset.outlineCollapsed = String(collapsed.outline);
    root.classList.toggle("doc-focused", Boolean(focusSnapshot));
    byId("doc-focus").setAttribute("aria-pressed", String(Boolean(focusSnapshot)));
    byId("doc-focus").textContent = focusSnapshot ? "退出专注" : "专注阅读";
  };
  const changePanels = (change) => {
    const content = byId("doc-content");
    // Keep the visible paragraph in place when a wider column reflows text.
    // Pending chapter/image restoration retains ownership of its own scroll intent.
    const anchor = state.reading && !state.pendingRestore
      ? [...byId("doc-body").children].find((element) => element.getBoundingClientRect().bottom > content.getBoundingClientRect().top)
      : null;
    const offset = anchor && anchor.getBoundingClientRect().top - content.getBoundingClientRect().top;
    change();
    applyPanels();
    if (anchor) content.scrollTop += anchor.getBoundingClientRect().top - content.getBoundingClientRect().top - offset;
    if (state.reading) {
      rememberScroll();
      markActiveHeading();
    }
  };
  const rememberScroll = () => {
    if (!state.currentKey) return;
    // An unloaded image can clamp scrollTop. Preserve the captured target until
    // layout settles or the reader deliberately chooses a new position.
    if (state.pendingRestore && state.pendingRestore.epoch === state.chapterEpoch) return;
    store(state.currentKey, String(byId("doc-content").scrollTop));
  };
  const markActiveHeading = () => {
    const headings = [...byId("doc-body").querySelectorAll("h1,h2,h3,h4,h5,h6")].filter((heading) => heading.getClientRects().length);
    if (!headings.length) return;
    const top = byId("doc-content").getBoundingClientRect().top + 64;
    const active = headings.filter((heading) => heading.getBoundingClientRect().top <= top).pop() || headings[0];
    byId("doc-toc").querySelectorAll("a").forEach((anchor) => {
      anchor.setAttribute("aria-current", anchor.hash === `#${encodeURIComponent(active.id)}` ? "location" : "false");
    });
  };
  const resetSearch = () => {
    state.searchEpoch += 1;
    clearTimeout(state.searchTimer);
    byId("doc-search").value = "";
    byId("doc-search-results").replaceChildren();
    byId("doc-search-results").hidden = true;
  };
  const clearChapter = () => {
    rememberScroll();
    state.pendingRestore = null;
    state.currentKey = null;
    state.chapter = null;
    state.chapterEpoch += 1;
    state.urls.forEach((url) => URL.revokeObjectURL(url));
    state.urls = [];
    byId("doc-body").replaceChildren();
    byId("doc-toc").replaceChildren();
    byId("doc-content").scrollTop = 0;
    byId("doc-content").setAttribute("aria-busy", "false");
    byId("doc-prev").disabled = true;
    byId("doc-next").disabled = true;
    byId("doc-copy-link").disabled = true;
    byId("doc-chapter-state").textContent = "";
    byId("doc-chapter-state").removeAttribute("title");
  };
  const setWorkspace = (workspace) => {
    rememberScroll();
    if (state.reading && workspace !== "docs") {
      // Hidden readers cannot finish a navigation or rewrite the ticket URL.
      // Keep completed DOM intact; pendingRoute records an interrupted selection.
      ++state.catalogEpoch;
      ++state.bookEpoch;
      ++state.chapterEpoch;
      ++state.searchEpoch;
      clearTimeout(state.searchTimer);
    }
    state.reading = workspace === "docs";
    document.body.dataset.workspace = workspace;
    root.hidden = !state.reading;
    applyPanels();
    byId("workspace-docs").setAttribute("aria-selected", String(state.reading));
    byId("workspace-tickets").setAttribute("aria-selected", String(!state.reading));
    document.dispatchEvent(new CustomEvent("icode:workspace-selected", { detail: { workspace } }));
  };
  const updateHistory = (fragment = "", mode = "push") => {
    if (!state.reading || !state.book) return;
    const url = new URL(routeURL(location.href, { bookId: state.book.id, chapterId: state.chapter && state.chapter.id, fragment }));
    if (state.originBook && state.originBook.id !== state.book.id) {
      url.searchParams.set("doc_origin", state.originBook.id);
      if (state.originBook.returnChapterId) url.searchParams.set("doc_origin_chapter", state.originBook.returnChapterId);
    } else { url.searchParams.delete("doc_origin"); url.searchParams.delete("doc_origin_chapter"); }
    if (url.href === location.href) return;
    history[mode === "replace" ? "replaceState" : "pushState"]({ icodeDocs: true }, "", url.href);
  };
  const visibleBooks = () => state.books.filter((book) => {
    const scope = byId("doc-scope").value;
    return scope === "module" ? book.kind === "module" : scope === "project"
      ? book.kind === "project" && Boolean(state.projectId) && book.linked_project_id === state.projectId
      : book.kind === "project";
  });
  const renderSelectors = (selectedId = "") => {
    const books = visibleBooks();
    const groups = new Map();
    books.forEach((book) => { const key = bookFamily(book); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(book); });
    const chosen = books.find((book) => book.id === selectedId) || books[0];
    const select = byId("doc-book");
    select.replaceChildren();
    const displayBook = (entries) => entries.find((book) => book.kind === "module" && book.version && book.version !== "legacy" && book.name) || entries[0];
    groups.forEach((entries, family) => {
      const first = displayBook(entries);
      const duplicate = [...groups.values()].filter((group) => displayBook(group).name === first.name).length > 1;
      const identity = duplicate ? ` · ${first.project_id || first.module_key || first.id}` : "";
      const option = node("option", `${first.name || first.module_key || "未命名文档集"}${identity}`);
      option.value = family;
      option.title = first.module_key || first.project_id || first.id;
      select.append(option);
    });
    select.disabled = !chosen;
    if (chosen) select.value = bookFamily(chosen);
    const version = byId("doc-version");
    version.replaceChildren();
    if (chosen) groups.get(bookFamily(chosen)).forEach((book) => {
      const identity = versionLabel(book);
      const option = node("option", `${identity} · ${statusText(book.status)}`);
      option.value = book.id;
      option.title = `${book.branch || ""} ${book.version || ""}`.trim();
      version.append(option);
    });
    version.disabled = !chosen;
    if (chosen) version.value = chosen.id;
    byId("doc-book-label").firstChild.textContent = byId("doc-scope").value === "module" ? "模块族" : "工程文档集";
    return chosen;
  };
  const renderMetadata = (book) => {
    const meta = book.metadata || {};
    byId("doc-book-title").textContent = book.name || book.module_key || "文档集";
    byId("doc-book-status").textContent = statusText(book.status);
    byId("doc-provenance").textContent = `文档完整性：${integrityText(meta.integrity)} · 当前源码：${meta.source_status === "verified" ? "已核验" : "未核验"}`;
    const details = byId("doc-metadata");
    details.replaceChildren();
    const labels = { branch: "分支", commit: "记录提交", source_id: "来源 ID", snapshot_id: "快照 ID", template_version: "模板版本", scope: "范围", source_status: "源码核验", integrity: "完整性" };
    Object.entries(labels).forEach(([key, label]) => {
      const row = node("div");
      const value = key === "source_status" ? (meta[key] === "verified" ? "已核验" : "未核验") : key === "integrity" ? integrityText(meta[key]) : typeof meta[key] === "object" ? JSON.stringify(meta[key]) : meta[key] || "未记录";
      row.append(node("dt", label), node("dd", value));
      details.append(row);
    });
  };
  const rememberTreeScroll = () => {
    if (state.treeOwnerId) state.treePositions.set(state.treeOwnerId, byId("doc-tree").scrollTop);
  };
  const renderTree = () => {
    const tree = byId("doc-tree");
    rememberTreeScroll();
    tree.replaceChildren(node("h3", "章节"));
    if (!state.book) { state.treeOwnerId = null; return; }
    const owner = state.originBook || state.book;
    byId("doc-return-project").hidden = !state.originBook || state.originBook.id === state.book.id;
    (owner.chapters || []).forEach((chapter) => {
      const button = node("button", chapter.title || chapter.name, "doc-tree-item");
      button.type = "button";
      button.dataset.chapterId = chapter.id;
      button.setAttribute("aria-current", owner.id === state.book.id && state.chapter && state.chapter.id === chapter.id ? "page" : "false");
      button.addEventListener("click", () => navigate(owner.id, chapter.id));
      tree.append(button);
    });
    const dependencies = owner.dependencies || [];
    if (dependencies.length) tree.append(node("h3", "绑定的模块"));
    dependencies.forEach((dependency) => {
      const button = node("button", `${dependency.name} · ${statusText(dependency.status)}`, "doc-tree-item doc-dependency");
      button.type = "button";
      button.disabled = !dependency.book_id;
      if (dependency.book_id) {
        button.dataset.dependencyBook = dependency.book_id;
        button.setAttribute("aria-expanded", String(state.expandedDependencies.has(dependency.book_id)));
        button.addEventListener("click", async () => {
          const bookId = dependency.book_id;
          if (state.expandedDependencies.has(bookId)) { state.expandedDependencies.delete(bookId); renderTree(); return; }
          state.expandedDependencies.add(bookId);
          const epoch = state.bookEpoch;
          renderTree();
          try {
            const payload = await request("book", { id: bookId });
            if (epoch !== state.bookEpoch || !state.expandedDependencies.has(bookId)) return;
            state.dependencyBooks.set(bookId, payload.book);
            state.dependencyErrors.delete(bookId);
            renderTree();
          } catch (error) {
            if (epoch !== state.bookEpoch) return;
            state.dependencyErrors.set(bookId, error.message);
            renderTree();
          }
        });
      }
      tree.append(button);
      if (dependency.book_id && state.expandedDependencies.has(dependency.book_id)) {
        const group = node("div", undefined, "doc-module-chapters");
        const moduleBook = state.dependencyBooks.get(dependency.book_id);
        if (state.dependencyErrors.has(dependency.book_id)) group.append(node("p", `模块不可读：${state.dependencyErrors.get(dependency.book_id)}`, "doc-image-error"));
        else if (!moduleBook) group.append(node("p", "正在读取模块章节…", "empty-state"));
        else (moduleBook.chapters || []).forEach((chapter) => {
          const item = node("button", chapter.title || chapter.name, "doc-tree-item");
          item.type = "button";
          item.dataset.moduleChapter = chapter.id;
          item.setAttribute("aria-current", state.book.id === dependency.book_id && state.chapter && state.chapter.id === chapter.id ? "page" : "false");
          item.addEventListener("click", () => {
            if (owner.kind === "project") state.originBook = { ...owner, returnChapterId: owner.id === state.book.id && state.chapter ? state.chapter.id : owner.returnChapterId };
            void navigate(dependency.book_id, chapter.id);
          });
          group.append(item);
        });
        tree.append(group);
      }
    });
    state.treeOwnerId = owner.id;
    tree.scrollTop = state.treePositions.get(owner.id) || 0;
    // Move only this navigation scroller. scrollIntoView would also move the
    // containing page and disturb the independently restored reading position.
    const selected = tree.querySelector('[data-module-chapter][aria-current="page"]');
    if (selected) {
      const item = selected.getBoundingClientRect();
      const bounds = tree.getBoundingClientRect();
      if (item.top < bounds.top) tree.scrollTop += item.top - bounds.top;
      else if (item.bottom > bounds.bottom) tree.scrollTop += item.bottom - bounds.bottom;
    }
    rememberTreeScroll();
  };
  const jumpTo = (fragment) => {
    if (!fragment) return false;
    let heading = byId(fragment);
    if (!heading || !byId("doc-body").contains(heading)) {
      heading = [...byId("doc-body").querySelectorAll("[id]")].find((item) => item.id.toLowerCase() === fragment.toLowerCase());
    }
    if (!heading) return false;
    const collapsed = heading.closest("details");
    if (collapsed) collapsed.open = true;
    const content = byId("doc-content");
    content.scrollTop += heading.getBoundingClientRect().top - content.getBoundingClientRect().top - 20;
    return true;
  };
  const restoreAfterLayout = (top, fragment, layoutReady) => {
    const intent = { epoch: state.chapterEpoch, top, fragment };
    state.pendingRestore = intent;
    byId("doc-content").setAttribute("aria-busy", "true");
    const apply = () => {
      if (intent.top !== null) byId("doc-content").scrollTop = intent.top;
      else jumpTo(intent.fragment);
    };
    apply();
    void layoutReady.then(() => requestAnimationFrame(() => {
      if (intent.epoch !== state.chapterEpoch || !state.reading) return;
      byId("doc-content").setAttribute("aria-busy", "false");
      if (state.pendingRestore !== intent) return;
      apply();
      state.pendingRestore = null;
      rememberScroll();
      markActiveHeading();
    }));
  };
  const cancelRestore = () => { state.pendingRestore = null; rememberScroll(); };
  const decorateHeadings = (body) => {
    const slug = createSlugger();
    const toc = byId("doc-toc");
    body.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach((heading) => {
      heading.id = slug(heading.textContent);
      const anchor = node("a", heading.textContent, `doc-toc-level-${heading.tagName.slice(1)}`);
      anchor.href = `#${encodeURIComponent(heading.id)}`;
      anchor.addEventListener("click", (event) => { event.preventDefault(); cancelRestore(); jumpTo(heading.id); updateHistory(heading.id); rememberScroll(); });
      toc.append(anchor);
    });
    // Presentation-only folding keeps the original Markdown and server hashes intact.
    [...body.querySelectorAll("h1,h2,h3,h4,h5,h6")].forEach((heading) => {
      if (!isMetadataLabel(heading.textContent)) return;
      const details = node("details", undefined, "doc-source-metadata");
      const summary = node("summary", heading.textContent);
      heading.before(details);
      details.append(summary, heading);
      const level = Number(heading.tagName.slice(1));
      let next = details.nextSibling;
      while (next && !(next.nodeType === 1 && /^H[1-6]$/.test(next.tagName) && Number(next.tagName.slice(1)) <= level)) {
        const following = next.nextSibling;
        details.append(next);
        next = following;
      }
    });
    [...body.querySelectorAll("blockquote")].forEach((block) => {
      const label = block.querySelector("strong");
      if (!label || !isMetadataLabel(label.textContent)) return;
      const details = node("details", undefined, "doc-source-metadata");
      details.append(node("summary", label.textContent));
      block.before(details);
      details.append(block);
    });
  };
  const loadImages = async (body, chapter, epoch) => {
    // Many Markdown references may point at one vetted asset. Share both the
    // in-flight request and its object URL within this captured render epoch.
    const assets = new Map();
    const objectURLFor = (assetId) => {
      if (!assets.has(assetId)) assets.set(assetId, (async () => {
        const response = await fetch(`/api/v1/docs/asset?${new URLSearchParams({ id: assetId })}`, { headers: { "X-ICODE-UI-Token": token } });
        if (!response.ok || !/^image\/(png|jpeg|gif|webp)$/.test((response.headers.get("Content-Type") || "").split(";")[0])) throw new Error("图片格式不可用");
        const blob = await response.blob();
        if (epoch !== state.chapterEpoch) return null;
        const objectURL = URL.createObjectURL(blob);
        state.urls.push(objectURL);
        return objectURL;
      })());
      return assets.get(assetId);
    };
    await Promise.all([...body.querySelectorAll("img[data-doc-src]")].map(async (image) => {
      const source = image.getAttribute("data-doc-src");
      const assetId = mappedValue(chapter.assets, source);
      if (!assetId) { image.replaceWith(node("span", `[图片不可用：${image.alt || "未收录资源"}]`, "doc-image-error")); return; }
      try {
        const objectURL = await objectURLFor(assetId);
        if (!objectURL || epoch !== state.chapterEpoch) return;
        image.src = objectURL;
        await image.decode();
        if (epoch !== state.chapterEpoch) return;
        image.removeAttribute("data-doc-src");
      } catch (_error) {
        if (epoch === state.chapterEpoch) image.replaceWith(node("span", `[图片读取失败：${image.alt || "资源"}]`, "doc-image-error"));
      }
    }));
  };
  const renderMermaid = async (body, epoch) => {
    if (!globalThis.ICODEDiagramRenderer) return;
    for (const code of body.querySelectorAll("pre > code.language-mermaid")) {
      if (epoch !== state.chapterEpoch) return;
      const source = code.textContent;
      // Diagram directives may override security settings. Callbacks and raw HTML are never needed by a reader.
      if (/(?:%%\{|^\s*---|\bclick\s|<\s*\/?\s*(?:script|iframe|img|a)\b)/im.test(source)) {
        code.parentElement.before(node("p", "此图含不支持的交互或配置，保留源码。", "doc-image-error"));
        continue;
      }
      try {
        const result = await globalThis.ICODEDiagramRenderer.render(`icode-diagram-${++state.renderId}`, source);
        if (epoch !== state.chapterEpoch) return;
        const container = node("div", undefined, "doc-diagram");
        container.innerHTML = globalThis.DOMPurify.sanitize(result.svg, { USE_PROFILES: { svg: true, svgFilters: true }, FORBID_TAGS: ["style", "foreignObject", "script", "a", "image"], FORBID_ATTR: ["style", "href", "xlink:href", "onclick", "onload"] });
        // Only locally rendered, sanitized diagram styles receive the server nonce.
        // Markdown HTML and style attributes never receive this capability.
        const nonce = document.querySelector('meta[name="icode-style-nonce"]');
        const svg = container.querySelector("svg");
        // Mermaid emits width="100%". Use its own viewBox width so large diagrams
        // scroll at readable label size instead of scaling every label down.
        if (svg && svg.viewBox.baseVal.width > 0) svg.setAttribute("width", String(svg.viewBox.baseVal.width));
        if (nonce && svg) (result.styles || []).forEach((css) => {
          if (!safeDiagramCSS(css)) return;
          const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
          style.setAttribute("nonce", nonce.content);
          style.textContent = css;
          svg.prepend(style);
        });
        code.parentElement.replaceWith(container);
      } catch (_error) {
        if (epoch === state.chapterEpoch) code.parentElement.before(node("p", "图示解析失败，保留源码。", "doc-image-error"));
      }
    }
  };
  const renderMarkdown = (chapter, epoch) => {
    if (!globalThis.marked || !globalThis.DOMPurify) throw new Error("本地 Markdown 渲染资源未加载，请刷新重试");
    const renderer = new globalThis.marked.Renderer();
    renderer.html = () => "";
    renderer.image = ({ href, text }) => {
      const escape = (value) => String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      // src is deliberately absent until an authenticated, mapped asset is fetched.
      return `<img data-doc-src="${escape(href)}" alt="${escape(text)}">`;
    };
    const html = globalThis.marked.parse(chapter.markdown || "", { renderer, gfm: true, breaks: false, async: false });
    const body = byId("doc-body");
    body.innerHTML = globalThis.DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ["p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "blockquote", "ul", "ol", "li", "pre", "code", "table", "thead", "tbody", "tr", "th", "td", "a", "img", "input"],
      ALLOWED_ATTR: ["href", "title", "alt", "class", "data-doc-src", "start", "colspan", "rowspan", "align", "type", "checked", "disabled"],
      ALLOW_DATA_ATTR: false, FORBID_ATTR: ["style", "src", "srcset", "id", "name", "form", "formaction"],
    });
    body.querySelectorAll("input").forEach((input) => { input.type = "checkbox"; input.disabled = true; });
    body.querySelectorAll("a").forEach((anchor) => {
      const href = anchor.getAttribute("href") || "";
      const mapped = mappedValue(chapter.links, href);
      if (mapped && mapped.book_id === state.book.id && mapped.chapter_id === chapter.id) {
        anchor.href = `#${encodeURIComponent(mapped.fragment || "")}`;
        anchor.addEventListener("click", (event) => { event.preventDefault(); cancelRestore(); jumpTo(mapped.fragment || ""); updateHistory(mapped.fragment || ""); rememberScroll(); });
      } else if (mapped) {
        anchor.href = routeURL(location.href, { bookId: mapped.book_id, chapterId: mapped.chapter_id, fragment: mapped.fragment || "" });
        anchor.addEventListener("click", (event) => { event.preventDefault(); navigate(mapped.book_id, mapped.chapter_id, mapped.fragment || ""); });
      } else if (href.startsWith("#")) {
        anchor.addEventListener("click", (event) => {
          event.preventDefault();
          cancelRestore();
          let fragment;
          try { fragment = decodeURIComponent(href.slice(1)); } catch (_error) { return; }
          jumpTo(fragment); updateHistory(fragment); rememberScroll();
        });
      } else if (safeExternalLink(href)) {
        anchor.rel = "noopener noreferrer";
        anchor.target = "_blank";
      } else {
        anchor.removeAttribute("href");
        anchor.title = "此链接未收录到文档快照";
        anchor.classList.add("doc-unavailable-link");
      }
    });
    decorateHeadings(body);
    // These asynchronous tasks only mutate nodes belonging to their captured chapter epoch.
    return Promise.allSettled([loadImages(body, chapter, epoch), renderMermaid(body, epoch)]);
  };
  const openChapter = async (chapterId, fragment = "", historyMode = "push", restore = false) => {
    if (!state.book) return;
    state.pendingRoute = { bookId: state.book.id, chapterId, fragment };
    ++state.catalogEpoch;
    clearChapter();
    byId("doc-content").setAttribute("aria-busy", "true");
    const epoch = state.chapterEpoch;
    const bookId = state.book.id;
    setMessage("正在读取章节…");
    try {
      const payload = await request("chapter", { book_id: bookId, chapter_id: chapterId });
      if (epoch !== state.chapterEpoch || !state.book || bookId !== state.book.id) return;
      state.chapter = payload.chapter;
      const layoutReady = renderMarkdown(state.chapter, epoch);
      state.currentKey = scrollKey(state.book, chapterId);
      const scrollTop = Number(stored(state.currentKey) || 0);
      const restoreTop = (restore && stored(state.currentKey) !== null) || !fragment ? scrollTop : null;
      restoreAfterLayout(restoreTop, fragment, layoutReady);
      renderTree();
      markActiveHeading();
      const chapters = state.book.chapters || [];
      const index = chapters.findIndex((chapter) => chapter.id === chapterId);
      byId("doc-prev").disabled = index <= 0;
      byId("doc-next").disabled = index < 0 || index >= chapters.length - 1;
      byId("doc-copy-link").disabled = false;
      byId("doc-chapter-state").textContent = `正文：${integrityText(state.chapter.integrity)}${state.chapter.sha256 ? ` · SHA ${state.chapter.sha256.slice(0, 12)}` : " · 无摘要记录"} · ${chapterSourceText(state.chapter.source)}`;
      const source = state.chapter.source || {};
      byId("doc-chapter-state").title = `章节提交：${source.commit || "未记录"}\n章节来源：${source.source_id || "未记录"}\n章节模板：${source.template_version || "未记录"}`;
      setMessage(state.chapter.title || state.chapter.name || "章节已载入");
      if (historyMode !== "none") updateHistory(fragment, historyMode);
      state.pendingRoute = null;
    } catch (error) {
      if (epoch !== state.chapterEpoch) return;
      byId("doc-content").setAttribute("aria-busy", "false");
      state.chapter = null;
      byId("doc-body").replaceChildren(node("p", `章节无法读取：${error.message}`, "doc-read-error"));
      setMessage("章节读取失败，请重新选择或刷新目录。", true);
    }
  };
  const openBook = async (bookId, chapterId = "", fragment = "", historyMode = "push", restore = false) => {
    state.pendingRoute = { bookId, chapterId, fragment };
    ++state.catalogEpoch;
    const epoch = ++state.bookEpoch;
    clearChapter();
    resetSearch();
    state.book = null;
    rememberTreeScroll();
    state.treeOwnerId = null;
    byId("doc-tree").replaceChildren();
    byId("doc-book-title").textContent = "正在读取文档集…";
    byId("doc-book-status").textContent = "";
    byId("doc-provenance").textContent = "";
    byId("doc-metadata").replaceChildren();
    byId("doc-return-project").hidden = true;
    const book = state.books.find((item) => item.id === bookId);
    if (book) {
      if (book.kind === "module") byId("doc-scope").value = "module";
      else if (!visibleBooks().some((item) => item.id === bookId)) byId("doc-scope").value = "all";
      renderSelectors(bookId);
    }
    setMessage("正在读取文档集…");
    try {
      const payload = await request("book", { id: bookId });
      if (epoch !== state.bookEpoch) return;
      state.book = payload.book;
      renderMetadata(state.book);
      renderTree();
      const chapter = chapterId || ((state.book.chapters || [])[0] || {}).id;
      if (chapter) await openChapter(chapter, fragment, historyMode, restore);
      else { state.pendingRoute = null; setMessage("此文档集暂无可读章节。"); if (historyMode !== "none") updateHistory("", historyMode); }
    } catch (error) {
      if (epoch !== state.bookEpoch) return;
      byId("doc-book-title").textContent = "文档集不可用";
      setMessage(`文档集无法读取：${error.message}`, true);
    }
  };
  const navigate = async (bookId, chapterId, fragment = "", historyMode = "push", restore = false) => {
    if (state.book && state.book.id === bookId) return openChapter(chapterId, fragment, historyMode, restore);
    return openBook(bookId, chapterId, fragment, historyMode, restore);
  };
  const loadCatalog = async (route = parseRoute(location.href)) => {
    const epoch = ++state.catalogEpoch;
    setMessage("正在发现本地文档集…");
    try {
      const payload = await request("catalog");
      if (epoch !== state.catalogEpoch) return;
      state.books = payload.books || [];
      state.catalogLoaded = true;
      byId("doc-catalog-errors").textContent = (payload.errors || []).join("；");
      const requested = state.books.find((book) => book.id === route.bookId);
      const originId = new URL(location.href).searchParams.get("doc_origin");
      state.originBook = null;
      if (originId && originId !== route.bookId && state.books.some((book) => book.id === originId && book.kind === "project")) {
        try {
          const origin = await request("book", { id: originId });
          if (epoch !== state.catalogEpoch) return;
          state.originBook = origin.book;
          state.originBook.returnChapterId = new URL(location.href).searchParams.get("doc_origin_chapter") || "";
        } catch (_error) { byId("doc-catalog-errors").textContent = "来源工程当前不可读，仍可独立阅读模块快照。"; }
        if (epoch !== state.catalogEpoch) return;
      }
      if (requested && requested.kind === "module") byId("doc-scope").value = "module";
      const selected = renderSelectors(route.bookId);
      if (route.bookId) await openBook(route.bookId, route.chapterId, route.fragment, "replace", true);
      else if (selected) await openBook(selected.id, "", "", "replace");
      else { clearChapter(); state.book = null; renderTree(); byId("doc-book-title").textContent = "暂无文档"; setMessage("此入口暂无文档，请选择全部工程或独立模块。"); }
    } catch (error) {
      if (epoch !== state.catalogEpoch) return;
      state.catalogLoaded = false;
      state.book = null;
      clearChapter();
      renderTree();
      setMessage(`文档目录读取失败：${error.message}`, true);
    }
  };
  const search = async () => {
    const query = byId("doc-search").value.trim();
    const epoch = ++state.searchEpoch;
    const resultBox = byId("doc-search-results");
    resultBox.replaceChildren();
    resultBox.hidden = !query;
    if (!query || !state.book) return;
    const bookId = state.book.id;
    resultBox.append(node("p", "正在搜索标题和正文…"));
    try {
      const payload = await request("search", { book_id: bookId, q: query });
      if (epoch !== state.searchEpoch || !state.book || state.book.id !== bookId) return;
      resultBox.replaceChildren();
      if (!(payload.results || []).length) resultBox.append(node("p", "没有匹配的章节。"));
      (payload.results || []).forEach((result) => {
        const button = node("button", undefined, "doc-search-result");
        button.type = "button";
        button.append(node("strong", result.title), node("span", result.snippet));
        button.addEventListener("click", () => { resultBox.hidden = true; navigate(result.book_id, result.chapter_id); });
        resultBox.append(button);
      });
      if (payload.truncated) resultBox.append(node("p", "结果已截断，请缩小检索范围。"));
    } catch (error) {
      if (epoch === state.searchEpoch) resultBox.replaceChildren(node("p", `搜索失败：${error.message}`));
    }
  };
  const openLibrary = () => {
    setWorkspace("docs");
    if (!state.catalogLoaded) void loadCatalog();
    else if (state.pendingRoute) { const route = state.pendingRoute; void navigate(route.bookId, route.chapterId, route.fragment); }
    else if (state.book && state.chapter) {
      updateHistory("", "push");
      const interrupted = state.pendingRestore;
      const layoutReady = Promise.allSettled([loadImages(byId("doc-body"), state.chapter, state.chapterEpoch), renderMermaid(byId("doc-body"), state.chapterEpoch)]);
      if (interrupted) restoreAfterLayout(interrupted.top, interrupted.fragment, layoutReady);
    }
    else void loadCatalog();
  };
  byId("workspace-docs").addEventListener("click", openLibrary);
  byId("workspace-tickets").addEventListener("click", () => {
    setWorkspace("tickets");
    const url = new URL(location.href);
    ["view", "doc_book", "doc_chapter", "doc_origin", "doc_origin_chapter"].forEach((key) => url.searchParams.delete(key));
    url.hash = "";
    history.pushState({}, "", url);
  });
  byId("project-docs-button").addEventListener("click", () => {
    ++state.catalogEpoch;
    state.originBook = null;
    setWorkspace("docs");
    byId("doc-scope").value = "project";
    if (!state.catalogLoaded) void loadCatalog({ active: true, bookId: "", chapterId: "", fragment: "" });
    else {
      const selected = renderSelectors();
      if (selected) void openBook(selected.id);
      else { ++state.bookEpoch; state.book = null; clearChapter(); resetSearch(); renderTree(); byId("doc-book-title").textContent = "当前工程暂无文档"; setMessage("当前工程尚无已关联文档，请选择全部工程查看历史文档。"); }
    }
  });
  byId("doc-refresh").addEventListener("click", () => { rememberScroll(); void loadCatalog(); });
  byId("doc-scope").addEventListener("change", () => {
    ++state.catalogEpoch;
    state.originBook = null;
    const selected = renderSelectors();
    if (selected) void openBook(selected.id);
    else { ++state.bookEpoch; state.book = null; clearChapter(); resetSearch(); renderTree(); byId("doc-book-title").textContent = "暂无文档"; byId("doc-provenance").textContent = ""; byId("doc-book-status").textContent = ""; byId("doc-metadata").replaceChildren(); setMessage("此入口暂无文档，请切换范围。"); }
  });
  byId("doc-book").addEventListener("change", () => {
    state.originBook = null;
    const selected = visibleBooks().find((book) => bookFamily(book) === byId("doc-book").value);
    if (selected) void openBook(selected.id);
  });
  byId("doc-version").addEventListener("change", () => { state.originBook = null; void openBook(byId("doc-version").value); });
  byId("doc-return-project").addEventListener("click", () => {
    const origin = state.originBook;
    if (!origin) return;
    state.originBook = null;
    void openBook(origin.id, origin.returnChapterId || "");
  });
  byId("doc-search").addEventListener("input", () => {
    ++state.searchEpoch;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(search, 220);
  });
  byId("doc-content").addEventListener("scroll", () => { rememberScroll(); markActiveHeading(); }, { passive: true });
  ["wheel", "touchstart", "pointerdown"].forEach((name) => byId("doc-content").addEventListener(name, cancelRestore, { passive: true }));
  byId("doc-content").addEventListener("keydown", (event) => {
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) cancelRestore();
  });
  ["prev", "next"].forEach((direction) => byId(`doc-${direction}`).addEventListener("click", () => {
    if (!state.book || !state.chapter) return;
    const chapters = state.book.chapters || [];
    const index = chapters.findIndex((chapter) => chapter.id === state.chapter.id);
    const chapter = chapters[index + (direction === "prev" ? -1 : 1)];
    if (chapter) void openChapter(chapter.id);
  }));
  byId("doc-focus").addEventListener("click", () => {
    changePanels(() => {
      if (focusSnapshot) { Object.assign(panels, focusSnapshot); focusSnapshot = null; }
      else focusSnapshot = { ...panels };
    });
  });
  ["sidebar", "tree", "outline"].forEach((key) => {
    byId(key === "sidebar" ? "sidebar-toggle" : `doc-${key}-toggle`).addEventListener("click", () => {
      changePanels(() => {
        // An individual expansion exits focus mode and preserves its entry snapshot.
        const collapsed = focusSnapshot ? true : panels[key];
        if (focusSnapshot) { Object.assign(panels, focusSnapshot); focusSnapshot = null; }
        panels[key] = !collapsed;
        store(panelKey, JSON.stringify(panels), "localStorage");
      });
    });
  });
  byId("doc-copy-link").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); setMessage("阅读链接已复制。"); }
    catch (_error) { setMessage("复制不可用，可从浏览器地址栏复制当前链接。", true); }
  });
  document.addEventListener("icode:project-selected", (event) => { state.projectId = event.detail.project_id || null; });
  globalThis.addEventListener("beforeunload", rememberScroll);
  globalThis.addEventListener("popstate", () => {
    const route = parseRoute(location.href);
    setWorkspace(route.active ? "docs" : "tickets");
    if (route.active) void loadCatalog(route);
  });
  const initialRoute = parseRoute(location.href);
  setWorkspace(initialRoute.active ? "docs" : "tickets");
  if (initialRoute.active) void loadCatalog(initialRoute);
})();
