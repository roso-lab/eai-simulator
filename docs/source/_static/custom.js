import { animate, stagger } from "./vendor/anime.esm.min.js";

const entranceSelector = [
  ".eai-home-eyebrow",
  ".eai-home-lede",
  ".eai-home-summary",
  ".eai-home-actions",
  ".eai-home-demo",
  ".eai-workflow-card",
  ".eai-command-dock",
].join(", ");

function enhanceVersionMenu() {
  const menu = document.querySelector(".eai-version-menu");
  const summary = menu?.querySelector("summary");

  if (!menu || !summary) {
    return;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !menu.open) {
      return;
    }

    menu.open = false;
    summary.focus();
  });

  document.addEventListener("click", (event) => {
    if (menu.open && !menu.contains(event.target)) {
      menu.open = false;
    }
  });
}

function startHomepageMotion() {
  const targets = document.querySelectorAll(entranceSelector);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (targets.length === 0 || reduceMotion) {
    return;
  }

  animate(targets, {
    opacity: { from: 0 },
    translateY: { from: 10 },
    duration: 280,
    delay: stagger(45, { start: 40 }),
    ease: "out(3)",
  });
}

function syncArchitectureFrameHeight() {
  const frame = document.querySelector(".eai-architecture-frame");

  if (!frame) {
    return;
  }

  function applyHeight(height) {
    if (!Number.isFinite(height)) {
      return;
    }

    frame.style.height = `${Math.max(480, Math.ceil(height))}px`;
  }

  function measureFrame() {
    applyHeight(frame.contentDocument?.documentElement.scrollHeight);
  }

  window.addEventListener("message", (event) => {
    if (
      event.source !== frame.contentWindow
      || event.data?.type !== "eai-architecture-resize"
      || !Number.isFinite(event.data.height)
    ) {
      return;
    }

    applyHeight(event.data.height);
  });

  frame.addEventListener("load", measureFrame);
  measureFrame();
}

function syncArchitectureFrameTheme() {
  const frame = document.querySelector(".eai-architecture-frame");
  const themeTarget = document.body;

  if (!frame || !themeTarget) {
    return;
  }

  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

  function sendTheme() {
    const theme = themeTarget.dataset.theme || "auto";

    // furo uses "auto" for system-following; resolve it to a concrete value.
    const isDark = theme === "dark" || (theme === "auto" && systemTheme.matches);
    const resolvedTheme = isDark ? "dark" : "light";

    try {
      if (frame.contentDocument?.documentElement) {
        frame.contentDocument.documentElement.dataset.theme = resolvedTheme;
      }
    } catch {
      // postMessage below remains available if the iframe is served cross-origin.
    }

    frame.contentWindow?.postMessage(
      { type: "eai-architecture-theme", theme: resolvedTheme },
      "*"
    );
  }

  const observer = new MutationObserver(sendTheme);
  observer.observe(themeTarget, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });

  systemTheme.addEventListener("change", sendTheme);
  frame.addEventListener("load", sendTheme);
  sendTheme();
}

function localizeEnglishPageChrome() {
  const englishMarker = document.querySelector(
    '.eai-language-switch__item.is-current[lang="en"]'
  );

  if (!englishMarker) {
    return;
  }

  document.documentElement.lang = "en";

  document.querySelectorAll("a.headerlink").forEach((link) => {
    link.title = "Link to this heading.";
  });

  const indexLink = document.querySelector('link[rel="index"]');
  const searchLink = document.querySelector('link[rel="search"]');

  if (indexLink) {
    indexLink.title = "Index";
  }

  if (searchLink) {
    searchLink.title = "Search";
  }
}

function enhanceCommunityForum() {
  const forum = document.querySelector("[data-eai-community]");

  if (!forum) {
    return;
  }

  const search = forum.querySelector("[data-eai-community-search]");
  const filters = [...forum.querySelectorAll("[data-eai-community-filter]")];
  const topics = [...forum.querySelectorAll("[data-eai-community-topic]")];
  const count = forum.querySelector("[data-eai-community-count]");
  const empty = forum.querySelector("[data-eai-community-empty]");
  const countLabel = forum.dataset.countLabel || "topics";
  let activeCategory = "all";

  function applyFilters() {
    const query = search?.value.trim().toLocaleLowerCase() || "";
    let visibleCount = 0;

    topics.forEach((topic) => {
      const matchesCategory = activeCategory === "all"
        || topic.dataset.category === activeCategory;
      const matchesQuery = !query
        || topic.textContent.toLocaleLowerCase().includes(query);
      const isVisible = matchesCategory && matchesQuery;

      topic.hidden = !isVisible;
      visibleCount += isVisible ? 1 : 0;
    });

    if (count) {
      count.textContent = `${visibleCount} ${countLabel}`;
    }

    if (empty) {
      empty.hidden = visibleCount !== 0;
    }
  }

  filters.forEach((filter) => {
    filter.addEventListener("click", () => {
      activeCategory = filter.dataset.eaiCommunityFilter || "all";

      filters.forEach((candidate) => {
        const isActive = candidate === filter;
        candidate.classList.toggle("is-active", isActive);
        candidate.setAttribute("aria-pressed", String(isActive));
      });

      applyFilters();
    });
  });

  search?.addEventListener("input", applyFilters);
  applyFilters();
}

function setupForumCategories() {
  const forumArea = document.querySelector(".eai-forum");
  const thread = forumArea?.querySelector("#cusdis_thread");
  const categories = [...(forumArea?.querySelectorAll("[data-eai-forum-category]") || [])];
  const title = forumArea?.querySelector("[data-eai-forum-category-title]");
  const description = forumArea?.querySelector("[data-eai-forum-category-description]");
  const activeIcon = forumArea?.querySelector("[data-eai-forum-category-icon]");
  const allPanel = forumArea?.querySelector("[data-eai-forum-all]");
  const allStatus = allPanel?.querySelector("[data-eai-forum-all-status]");
  const allList = allPanel?.querySelector("[data-eai-forum-all-list]");

  if (!forumArea || !thread || categories.length === 0) {
    return;
  }

  const categoryBySlug = new Map(categories.map((category) => [category.dataset.category, category]));
  const categoryByPageId = new Map(
    categories
      .filter((category) => category.dataset.pageId)
      .map((category) => [category.dataset.pageId, category]),
  );
  let renderRequest = 0;
  let allRequest = 0;
  let focusRequest = 0;

  function setAllStatus(message) {
    if (allStatus) {
      allStatus.textContent = message;
      allStatus.hidden = !message;
    }
  }

  function createAllDiscussion(comment, category) {
    const article = document.createElement("article");
    const meta = document.createElement("div");
    const categoryLabel = document.createElement("span");
    const nickname = document.createElement("strong");
    const timestamp = document.createElement("time");
    const content = document.createElement("p");
    const footer = document.createElement("div");
    const replyCount = document.createElement("span");
    const openButton = document.createElement("button");
    const createdAt = new Date(comment.createdAt);
    const replies = Number(comment.replies?.commentCount) || 0;

    article.className = "eai-forum-all__item";
    meta.className = "eai-forum-all__meta";
    categoryLabel.className = "eai-forum-all__category";
    categoryLabel.textContent = category.dataset.categoryLabel || "";
    nickname.textContent = comment.by_nickname || "Anonymous";
    timestamp.dateTime = Number.isNaN(createdAt.getTime()) ? "" : createdAt.toISOString();
    timestamp.textContent = Number.isNaN(createdAt.getTime())
      ? ""
      : new Intl.DateTimeFormat(forumArea.dataset.lang || "en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(createdAt);
    content.className = "eai-forum-all__content";
    content.textContent = comment.content || "";
    footer.className = "eai-forum-all__footer";
    replyCount.textContent = `${replies} ${allPanel?.dataset.repliesLabel || "replies"}`;
    openButton.type = "button";
    openButton.textContent = allPanel?.dataset.openLabel || "View and reply";
    openButton.addEventListener("click", () => {
      activateCategory(category, true);
      focusDiscussion(comment, category);
    });

    meta.append(categoryLabel, nickname, timestamp);
    footer.append(replyCount, openButton);
    article.append(meta, content, footer);
    return article;
  }

  async function findDiscussionLocation(commentId, category, requestId) {
    const baseUrl = new URL("/api/open/comments", thread.dataset.host);
    baseUrl.searchParams.set("appId", thread.dataset.appId);
    baseUrl.searchParams.set("pageId", category.dataset.pageId);

    let page = 1;
    let pageCount = 1;

    do {
      baseUrl.searchParams.set("page", String(page));
      const response = await fetch(baseUrl, {
        headers: { "x-timezone-offset": String(-new Date().getTimezoneOffset()) },
      });

      if (!response.ok) {
        throw new Error(`Cusdis returned ${response.status}`);
      }

      const result = (await response.json())?.data;

      if (requestId !== focusRequest) {
        return null;
      }

      const comments = result?.data || [];
      const index = comments.findIndex((comment) => comment.id === commentId);

      if (index >= 0) {
        return { index, page };
      }

      pageCount = Math.min(Math.max(Number(result?.pageCount) || 1, 1), 50);
      page += 1;
    } while (page <= pageCount);

    return null;
  }

  async function focusDiscussion(comment, category) {
    const requestId = ++focusRequest;
    let location;

    try {
      location = await findDiscussionLocation(comment.id, category, requestId);
    } catch (error) {
      location = null;
    }

    if (requestId !== focusRequest) {
      return;
    }

    if (!location) {
      thread.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    const startedAt = Date.now();
    let requestedPage = location.page === 1;

    function focusWhenReady() {
      if (requestId !== focusRequest) {
        return;
      }

      const frame = thread.querySelector("iframe");
      const doc = frame?.contentDocument;

      if (!doc?.body) {
        if (Date.now() - startedAt < 12000) {
          setTimeout(focusWhenReady, 100);
        }
        return;
      }

      if (!requestedPage) {
        const pageButton = [...doc.querySelectorAll("#root div.my-8 button")]
          .find((button) => button.textContent.trim() === String(location.page));

        if (pageButton) {
          requestedPage = true;
          pageButton.click();
        }

        if (Date.now() - startedAt < 12000) {
          setTimeout(focusWhenReady, 120);
        }
        return;
      }

      const discussions = [
        ...doc.querySelectorAll("#root div.mt-4.px-1 > div.my-4"),
      ];
      const target = discussions[location.index];

      if (!target) {
        if (Date.now() - startedAt < 12000) {
          setTimeout(focusWhenReady, 100);
        }
        return;
      }

      doc.querySelectorAll(".eai-comment-target").forEach((candidate) => {
        candidate.classList.remove("eai-comment-target");
      });
      target.dataset.eaiCommentId = comment.id;
      target.classList.add("eai-comment-target");
      target.tabIndex = -1;

      const offset = frame.getBoundingClientRect().top
        + target.getBoundingClientRect().top
        + window.scrollY
        - 88;
      window.scrollTo({ top: Math.max(0, offset), behavior: "smooth" });
      target.focus({ preventScroll: true });

      setTimeout(() => target.classList.remove("eai-comment-target"), 3600);
    }

    focusWhenReady();
  }

  async function loadAllDiscussions() {
    if (!allPanel || !allList) {
      return;
    }

    const requestId = ++allRequest;
    const loadingLabel = allPanel.dataset.loadingLabel || "Loading discussions...";
    setAllStatus(loadingLabel);
    allList.replaceChildren();

    try {
      const baseUrl = new URL("/api/open/comments", thread.dataset.host);
      baseUrl.searchParams.set("appId", thread.dataset.appId);
      baseUrl.searchParams.set("page", "1");

      const firstResponse = await fetch(baseUrl, {
        headers: { "x-timezone-offset": String(-new Date().getTimezoneOffset()) },
      });

      if (!firstResponse.ok) {
        throw new Error(`Cusdis returned ${firstResponse.status}`);
      }

      const firstPayload = await firstResponse.json();
      const firstPage = firstPayload?.data;
      const pageCount = Math.min(Math.max(Number(firstPage?.pageCount) || 1, 1), 50);
      const remainingPages = await Promise.all(
        Array.from({ length: pageCount - 1 }, async (_, index) => {
          const pageUrl = new URL(baseUrl);
          pageUrl.searchParams.set("page", String(index + 2));
          const response = await fetch(pageUrl, {
            headers: { "x-timezone-offset": String(-new Date().getTimezoneOffset()) },
          });

          if (!response.ok) {
            throw new Error(`Cusdis returned ${response.status}`);
          }

          return (await response.json())?.data?.data || [];
        }),
      );

      if (requestId !== allRequest) {
        return;
      }

      const comments = [firstPage?.data || [], ...remainingPages]
        .flat()
        .filter((comment) => categoryByPageId.has(comment.page?.slug))
        .sort((left, right) => new Date(right.createdAt) - new Date(left.createdAt));

      if (comments.length === 0) {
        setAllStatus(allPanel.dataset.emptyLabel || "No discussions yet.");
        return;
      }

      setAllStatus("");
      allList.append(...comments.map((comment) => (
        createAllDiscussion(comment, categoryByPageId.get(comment.page.slug))
      )));
    } catch (error) {
      if (requestId === allRequest) {
        setAllStatus(allPanel.dataset.errorLabel || "Discussions could not be loaded.");
      }
    }
  }

  function renderThread(category) {
    const requestId = ++renderRequest;
    const startedAt = Date.now();

    function renderWhenReady() {
      if (requestId !== renderRequest || category.dataset.category !== forumArea.dataset.activeForumCategory) {
        return;
      }

      if (window.CUSDIS?.renderTo) {
        thread.setAttribute("aria-busy", "true");
        window.CUSDIS.renderTo(thread);
        thread.removeAttribute("aria-busy");
        return;
      }

      if (Date.now() - startedAt < 10000) {
        setTimeout(renderWhenReady, 100);
      }
    }

    renderWhenReady();
  }

  function activateCategory(category, shouldRender, options = {}) {
    focusRequest += 1;
    const pageId = category.dataset.pageId;
    const pageTitle = category.dataset.pageTitle;
    const categoryName = category.dataset.categoryLabel;
    const categoryDescription = category.dataset.categoryDescription;
    const categorySlug = category.dataset.category || "all";
    const categoryIcon = category.querySelector(".eai-forum-category__icon");
    const isAll = categorySlug === "all";

    if (!categoryName || (!isAll && (!pageId || !pageTitle))) {
      return;
    }

    categories.forEach((candidate) => {
      const isActive = candidate === category;
      candidate.classList.toggle("is-active", isActive);
      candidate.setAttribute("aria-pressed", String(isActive));
    });

    if (title) {
      title.textContent = categoryName;
    }

    if (description) {
      description.textContent = categoryDescription || "";
    }

    if (activeIcon && categoryIcon) {
      activeIcon.replaceChildren(
        ...[...categoryIcon.childNodes].map((node) => node.cloneNode(true)),
      );
    }

    forumArea.dataset.activeForumCategory = categorySlug;
    allPanel?.toggleAttribute("hidden", !isAll);
    thread.toggleAttribute("hidden", isAll);

    if (isAll) {
      renderRequest += 1;
      thread.replaceChildren();
      loadAllDiscussions();
    } else {
      thread.dataset.pageId = pageId;
      thread.dataset.pageTitle = pageTitle;

      const categoryUrl = new URL(window.location.href);
      categoryUrl.hash = `discussion-${categorySlug}`;
      thread.dataset.pageUrl = categoryUrl.href;

      if (shouldRender) {
        renderThread(category);
      }
    }

    if (shouldRender) {
      forumArea.dispatchEvent(new CustomEvent("eai:forum-category-changed", {
        detail: { category: categorySlug, openForm: Boolean(options.openForm) },
      }));

      if (options.openForm && !isAll) {
        forumArea.dispatchEvent(new CustomEvent("eai:forum-open-form"));
      }
    }
  }

  categories.forEach((category) => {
    category.addEventListener("click", () => {
      if (category.classList.contains("is-active")) {
        return;
      }

      activateCategory(category, true);
    });
  });

  forumArea.addEventListener("eai:forum-select-category", (event) => {
    const category = categoryBySlug.get(event.detail?.category);

    if (category && category.dataset.category !== "all") {
      activateCategory(category, true, { openForm: Boolean(event.detail?.openForm) });
    }
  });

  const initialCategory = categories.find((category) => category.classList.contains("is-active"))
    || categories[0];
  activateCategory(initialCategory, false);
}

function syncForumTheme() {
  const forumArea = document.querySelector(".eai-forum");

  if (!forumArea) {
    return;
  }

  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

  function resolveTheme() {
    const theme = document.body.dataset.theme || "auto";
    const isDark = theme === "dark" || (theme === "auto" && systemTheme.matches);
    return isDark ? "dark" : "light";
  }

  function applyTheme() {
    if (window.CUSDIS?.setTheme) {
      window.CUSDIS.setTheme(resolveTheme());
    }
  }

  const observer = new MutationObserver(applyTheme);
  observer.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });
  systemTheme.addEventListener("change", applyTheme);

  // Cusdis 脚本异步注入,轮询等待 API 就绪后应用一次主题;10s 兜底停止。
  const poll = setInterval(() => {
    if (window.CUSDIS?.setTheme) {
      clearInterval(poll);
      applyTheme();
    }
  }, 300);
  setTimeout(() => clearInterval(poll), 10000);
}

// 向 Cusdis 评论 iframe 注入样式:主评论之间加分隔线,
// 回复强化缩进与左边线、与主评论视觉归组。iframe 被重建时由
// expandForumFrame 的 attachObserver 重新注入(id 守卫避免重复)。
function injectForumStyles(doc) {
  if (!doc?.head || doc.getElementById("eai-forum-style")) {
    return;
  }

  const style = doc.createElement("style");
  style.id = "eai-forum-style";
  style.textContent = `
    /* ==== EAI 文档风格评论区主题 ==== */

    @font-face {
      font-family: "EAI DINish";
      src: url("_static/fonts/DINish-Variable.woff2") format("woff2");
      font-style: oblique 0deg 12deg;
      font-weight: 100 900;
      font-stretch: 75% 125%;
      font-display: swap;
    }

    :root {
      --eai-c-page: #ffffff;
      --eai-c-surface: #f1f4f7;
      --eai-c-surface-strong: #e7ecf1;
      --eai-c-ink: #121820;
      --eai-c-text: #242b33;
      --eai-c-muted: #5d6975;
      --eai-c-border: #d2d9e0;
      --eai-c-border-strong: #aeb8c2;
      --eai-c-accent: #1554a0;
      --eai-c-accent-interactive: #0f478a;
      --eai-c-accent-contrast: #ffffff;
      --eai-c-radius: 5px;
    }

    /* Cusdis 暗色时给 widget 根节点加 .dark 类,这里切换同一套变量 */
    .dark {
      --eai-c-page: #000000;
      --eai-c-surface: #171717;
      --eai-c-surface-strong: #202020;
      --eai-c-ink: #ffffff;
      --eai-c-text: #ededed;
      --eai-c-muted: #989898;
      --eai-c-border: #262626;
      --eai-c-border-strong: #3a3a3a;
      --eai-c-accent: #ffffff;
      --eai-c-accent-interactive: #d7d7d7;
      --eai-c-accent-contrast: #000000;
    }

    body {
      font-family: "EAI DINish", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", system-ui, sans-serif;
      color: var(--eai-c-text);
      letter-spacing: 0;
    }

    /* 发帖表单:卡片化 */
    #root > div > div.grid.grid-cols-1.gap-4 {
      box-sizing: border-box;
      padding: 16px;
      border: 1px solid var(--eai-c-border);
      border-radius: var(--eai-c-radius);
      background: var(--eai-c-surface);
    }

    html.eai-form-hidden #root > div > div.grid.grid-cols-1.gap-4 {
      display: none !important;
    }

    html.eai-github-authenticated .eai-github-identity-row,
    html.eai-github-authenticated .eai-github-identity-field,
    html.eai-github-authenticated .eai-github-identity-input,
    html.eai-github-authenticated #root div.grid.grid-cols-1.gap-4
      > div.grid.grid-cols-2.gap-4 {
      display: none !important;
    }

    html:not(.eai-github-authenticated) #root div.mt-4.px-1 textarea,
    html:not(.eai-github-authenticated) #root div.mt-4.px-1 button.text-sm.bg-gray-200 {
      display: none !important;
    }

    /* 表单标签 */
    #root label {
      color: var(--eai-c-muted) !important;
      font-size: 12px !important;
      font-weight: 700 !important;
    }

    /* 输入框与文本域 */
    #root input,
    #root textarea {
      border-color: var(--eai-c-border) !important;
      border-radius: var(--eai-c-radius) !important;
      background: var(--eai-c-page) !important;
      color: var(--eai-c-text) !important;
      font: inherit !important;
      font-size: 14px !important;
    }

    #root input:focus,
    #root textarea:focus {
      border-color: var(--eai-c-accent) !important;
      outline: 2px solid color-mix(in srgb, var(--eai-c-accent) 18%, transparent) !important;
      outline-offset: 1px;
    }

    /* 发送按钮:对齐站点主按钮 */
    #root button.text-sm.bg-gray-200 {
      box-sizing: border-box;
      min-height: 40px;
      padding: 8px 16px !important;
      border: 1px solid var(--eai-c-accent) !important;
      border-radius: var(--eai-c-radius) !important;
      background: var(--eai-c-accent) !important;
      color: var(--eai-c-accent-contrast) !important;
      font-weight: 700 !important;
      font-size: 14px !important;
      transition: background-color 200ms ease, border-color 200ms ease, color 200ms ease;
    }

    #root button.text-sm.bg-gray-200:hover {
      border-color: var(--eai-c-accent-interactive) !important;
      background: var(--eai-c-accent-interactive) !important;
    }

    /* 主评论:分隔 + 留白 */
    #root div.mt-4.px-1 > div.my-4 {
      margin-bottom: 8px !important;
      padding: 12px 4px;
      border-bottom: 1px solid var(--eai-c-border);
    }

    #root div.mt-4.px-1 > div.my-4:last-child {
      border-bottom: 0;
    }

    #root div.mt-4.px-1 > div.my-4.eai-comment-target {
      border-radius: var(--eai-c-radius);
      background: color-mix(in srgb, var(--eai-c-accent) 10%, transparent);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--eai-c-accent) 42%, transparent);
      outline: none;
    }

    /* 昵称 */
    #root div.flex.items-center {
      color: var(--eai-c-ink) !important;
    }

    #root div.mr-2.font-medium {
      color: var(--eai-c-ink) !important;
      font-weight: 700 !important;
    }

    /* 发布时间 */
    #root div.text-gray-500.text-sm {
      color: var(--eai-c-muted) !important;
      font-size: 12px !important;
    }

    /* 评论内容 */
    #root div.text-gray-500.my-2 {
      color: var(--eai-c-text) !important;
      line-height: 1.7;
    }

    #root div.text-gray-500.my-2 p {
      margin: 0 0 6px;
    }

    /* 回复按钮:对齐站点链接色 */
    #root button.font-medium.text-sm.text-gray-500 {
      color: var(--eai-c-accent) !important;
      font-weight: 700 !important;
      padding: 4px 0 !important;
      font-size: 13px !important;
    }

    /* 回复与主评论归组:更明显的左缩进与边线 */
    #root div.my-4.pl-4.border-l-2 {
      margin-top: 10px;
      padding-left: 20px !important;
      border-left: 2px solid var(--eai-c-accent) !important;
    }

    /* 提交成功横幅 */
    #root div.bg-blue-500 {
      background: color-mix(in srgb, var(--eai-c-accent) 12%, transparent) !important;
      border: 1px solid color-mix(in srgb, var(--eai-c-accent) 38%, transparent);
      border-radius: var(--eai-c-radius);
      color: var(--eai-c-accent) !important;
      font-size: 13px !important;
    }

    /* 底部 "Powered by Cusdis" */
    #root div.text-center.text-gray-500.text-xs {
      color: var(--eai-c-muted) !important;
      font-size: 11px !important;
    }

    #root div.text-center.text-gray-500.text-xs a {
      color: var(--eai-c-muted) !important;
    }

    /* 分页按钮(评论多时出现) */
    #root div.my-8 button {
      color: var(--eai-c-accent) !important;
      border: 1px solid var(--eai-c-border) !important;
      border-radius: var(--eai-c-radius) !important;
      background: var(--eai-c-page) !important;
      font-weight: 700 !important;
      font-size: 13px !important;
      min-height: 34px;
      padding: 6px 12px !important;
    }
  `;
  doc.head.appendChild(style);
}

function expandForumFrame() {
  const forumArea = document.querySelector(".eai-forum");

  if (!forumArea) {
    return;
  }

  let resizeTimer = null;
  let shrinkTimer = null;
  let heightObserver = null;
  let sizeObserver = null;
  let observedDoc = null;
  let observedFrame = null;
  let lastHeight = 0;

  function measure() {
    const frame = forumArea.querySelector("iframe");
    const doc = frame?.contentDocument;
    const height = Math.max(
      doc?.documentElement?.scrollHeight || 0,
      doc?.body?.scrollHeight || 0,
      doc?.querySelector("#root")?.scrollHeight || 0,
    );

    return { doc, frame, height: Math.ceil(height) };
  }

  function commitHeight(frame, height) {
    if (!frame || !Number.isFinite(height) || height < 80) {
      return;
    }

    frame.style.setProperty("height", `${height}px`, "important");
    lastHeight = height;
  }

  function applyHeight(allowShrink = false) {
    const measurement = measure();

    if (!measurement.frame || measurement.height < 80) {
      return measurement.doc;
    }

    if (lastHeight === 0 || measurement.height >= lastHeight || allowShrink) {
      commitHeight(measurement.frame, measurement.height);
    }

    return measurement.doc;
  }

  function scheduleApply() {
    clearTimeout(resizeTimer);
    clearTimeout(shrinkTimer);

    resizeTimer = setTimeout(() => {
      applyHeight();
      // 提交和分类切换会短暂清空 Cusdis 根节点。只有较小高度稳定
      // 400ms 后才允许收缩，避免把 iframe 压成近零高度造成白屏。
      shrinkTimer = setTimeout(() => applyHeight(true), 400);
    }, 80);
  }

  function attachObserver() {
    const frame = forumArea.querySelector("iframe");
    const doc = frame?.contentDocument;

    if (!frame || !doc?.body) {
      return false;
    }

    injectForumStyles(doc);

    if (heightObserver && observedDoc === doc && observedFrame === frame) {
      return true;
    }

    heightObserver?.disconnect();
    sizeObserver?.disconnect();

    observedDoc = doc;
    observedFrame = frame;
    lastHeight = 0;
    heightObserver = new MutationObserver(scheduleApply);
    heightObserver.observe(doc.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    if (window.ResizeObserver) {
      sizeObserver = new ResizeObserver(scheduleApply);
      sizeObserver.observe(doc.documentElement);
      sizeObserver.observe(doc.body);
    }

    scheduleApply();
    return true;
  }

  const thread = forumArea.querySelector("#cusdis_thread");

  if (thread) {
    const watcher = new MutationObserver(() => {
      setTimeout(attachObserver, 120);
    });
    watcher.observe(thread, { childList: true });
  }

  const startedAt = Date.now();

  const poll = setInterval(() => {
    if (Date.now() - startedAt > 15000) {
      clearInterval(poll);
      return;
    }

    if (attachObserver()) {
      clearInterval(poll);
    }
  }, 200);
}

function setupGithubCommentLogin() {
  const forumArea = document.querySelector(".eai-forum");
  const auth = forumArea?.querySelector("[data-eai-github-auth]");
  const loginButton = auth?.querySelector("[data-eai-github-login]");
  const user = auth?.querySelector("[data-eai-github-user]");
  const avatar = auth?.querySelector("[data-eai-github-avatar]");
  const identity = auth?.querySelector("[data-eai-github-identity]");
  const logoutButton = auth?.querySelector("[data-eai-github-logout]");
  const status = auth?.querySelector("[data-eai-github-status]");
  const thread = forumArea?.querySelector("#cusdis_thread");
  const rawWorkerUrl = forumArea?.dataset.githubOauthUrl?.trim();

  if (!forumArea || !auth || !loginButton || !user || !identity
      || !logoutButton || !status || !thread || !rawWorkerUrl) {
    return;
  }

  let workerOrigin;

  try {
    const workerUrl = new URL(rawWorkerUrl);
    const localWorker = workerUrl.protocol === "http:"
      && ["127.0.0.1", "localhost"].includes(workerUrl.hostname);

    if (workerUrl.protocol !== "https:" && !localWorker) {
      throw new Error("The OAuth Worker must use HTTPS");
    }

    workerOrigin = workerUrl.origin;
  } catch (error) {
    auth.hidden = true;
    return;
  }

  const isEnglish = forumArea.dataset.lang === "en";
  const pendingKey = "eai.github.oauth.pending.v1";
  const profileKey = "eai.github.profile.v1";
  const messages = isEnglish ? {
    blocked: "The GitHub sign-in window was blocked.",
    cancelled: "GitHub sign-in was not completed.",
    denied: "GitHub authorization was cancelled.",
    failed: "GitHub sign-in failed. Please try again.",
    signedIn: "GitHub profile synced.",
    signedOut: "Signed out of GitHub.",
    waiting: "Waiting for GitHub authorization...",
  } : {
    blocked: "GitHub 登录窗口被浏览器拦截。",
    cancelled: "GitHub 登录未完成。",
    denied: "已取消 GitHub 授权。",
    failed: "GitHub 登录失败，请重试。",
    signedIn: "GitHub 用户信息已同步。",
    signedOut: "已退出 GitHub 登录。",
    waiting: "正在等待 GitHub 授权...",
  };
  let oauthPopup = null;
  let popupTimer = null;
  let statusTimer = null;
  let formObserver = null;
  let observedDocument = null;
  let forceFillPending = false;

  function readSession(key) {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null");
    } catch (error) {
      return null;
    }
  }

  function writeSession(key, value) {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (error) {
      return false;
    }
  }

  function removeSession(key) {
    try {
      sessionStorage.removeItem(key);
    } catch (error) {
      // The current page can still use the in-memory profile when storage is unavailable.
    }
  }

  function normalizeProfile(value) {
    if (!value || typeof value !== "object") {
      return null;
    }

    const login = typeof value.login === "string" ? value.login.trim() : "";
    const email = typeof value.email === "string" ? value.email.trim() : "";
    const name = typeof value.name === "string" && value.name.trim()
      ? value.name.trim()
      : login;
    let avatarUrl = "";

    try {
      const candidate = new URL(value.avatarUrl || "");
      const trustedHost = candidate.hostname === "avatars.githubusercontent.com"
        || candidate.hostname.endsWith(".githubusercontent.com");
      avatarUrl = candidate.protocol === "https:" && trustedHost ? candidate.href : "";
    } catch (error) {
      avatarUrl = "";
    }

    if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,78}[A-Za-z0-9])?$/u.test(login)
        || login.length > 80 || email.length > 320 || !email.includes("@")) {
      return null;
    }

    return { avatarUrl, email, login, name: name.slice(0, 200) };
  }

  let profile = normalizeProfile(readSession(profileKey));
  let pending = readSession(pendingKey);

  if (!pending || typeof pending.nonce !== "string"
      || Date.now() - Number(pending.createdAt) > 10 * 60 * 1000) {
    pending = null;
    removeSession(pendingKey);
  }

  function setStatus(message = "", state = "") {
    clearTimeout(statusTimer);
    status.textContent = message;
    status.hidden = !message;
    status.dataset.state = state;
  }

  function clearStatusLater() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => setStatus(), 5000);
  }

  function renderProfile() {
    const isSignedIn = Boolean(profile);
    forumArea.dataset.githubAuthenticated = String(isSignedIn);
    loginButton.hidden = isSignedIn;
    user.hidden = !isSignedIn;
    forumArea.dispatchEvent(new CustomEvent("eai:github-auth-changed", {
      detail: { authenticated: isSignedIn },
    }));

    if (!profile) {
      identity.textContent = "";
      identity.removeAttribute("title");
      avatar?.removeAttribute("src");
      avatar?.setAttribute("hidden", "");
      return;
    }

    identity.textContent = `@${profile.login}`;
    identity.title = `@${profile.login}`;

    if (avatar && profile.avatarUrl) {
      avatar.src = profile.avatarUrl;
      avatar.hidden = false;
    } else {
      avatar?.removeAttribute("src");
      avatar?.setAttribute("hidden", "");
    }
  }

  function updateInput(input, value) {
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function applyProfile(force = false) {
    const doc = forumArea.querySelector("iframe")?.contentDocument;
    const nicknameInputs = [...(doc?.querySelectorAll('input[name="nickname"]') || [])];
    const emailInputs = [...(doc?.querySelectorAll('input[name="email"]') || [])];

    if (nicknameInputs.length === 0 || emailInputs.length === 0) {
      return false;
    }

    const isSignedIn = Boolean(profile);
    doc.documentElement.classList.toggle("eai-github-authenticated", isSignedIn);

    doc.querySelectorAll(".eai-github-identity-row").forEach((candidate) => {
      candidate.classList.remove("eai-github-identity-row");
    });

    nicknameInputs.forEach((nicknameInput) => {
      let candidate = nicknameInput.parentElement;

      while (candidate && candidate !== doc.body) {
        const containsEmail = emailInputs.some((emailInput) => candidate.contains(emailInput));

        if (containsEmail) {
          if (!candidate.querySelector("textarea")) {
            candidate.classList.toggle("eai-github-identity-row", isSignedIn);
          }
          break;
        }

        candidate = candidate.parentElement;
      }
    });

    [...nicknameInputs, ...emailInputs].forEach((input) => {
      const label = input.closest("label") || (input.id
        ? [...doc.querySelectorAll("label")].find((candidate) => candidate.htmlFor === input.id)
        : null);
      input.readOnly = isSignedIn;
      input.classList.toggle("eai-github-identity-input", isSignedIn);
      label?.classList.toggle("eai-github-identity-field", isSignedIn);
    });

    [...doc.querySelectorAll("label")].forEach((label) => {
      const isIdentityLabel = /nickname|email|\u6635\u79f0|\u90ae\u7bb1/iu
        .test(label.textContent || "");
      label.classList.toggle("eai-github-identity-field", isSignedIn && isIdentityLabel);
    });

    if (!profile) {
      return false;
    }

    nicknameInputs.forEach((input) => {
      if (force || !input.value.trim()) {
        updateInput(input, profile.login);
      }
    });

    emailInputs.forEach((input) => {
      if (force || !input.value.trim()) {
        updateInput(input, profile.email);
      }
    });

    forceFillPending = false;
    return true;
  }

  function clearMatchingProfile(oldProfile) {
    const doc = forumArea.querySelector("iframe")?.contentDocument;

    doc?.querySelectorAll('input[name="nickname"]').forEach((input) => {
      if (input.value === oldProfile?.login) {
        updateInput(input, "");
      }
    });

    doc?.querySelectorAll('input[name="email"]').forEach((input) => {
      if (input.value === oldProfile?.email) {
        updateInput(input, "");
      }
    });
  }

  function connectForm() {
    const frame = forumArea.querySelector("iframe");
    const doc = frame?.contentDocument;

    if (!doc?.body) {
      return false;
    }

    if (formObserver && observedDocument !== doc) {
      formObserver.disconnect();
      formObserver = null;
    }

    if (!formObserver) {
      observedDocument = doc;
      formObserver = new MutationObserver(() => applyProfile(forceFillPending));
      formObserver.observe(doc.body, { childList: true, subtree: true });
      frame.addEventListener("load", () => setTimeout(connectForm, 0), { once: true });
    }

    applyProfile(forceFillPending);
    return true;
  }

  function createNonce() {
    if (typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }

    const bytes = crypto.getRandomValues(new Uint8Array(24));
    let binary = "";

    bytes.forEach((byte) => {
      binary += String.fromCharCode(byte);
    });

    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
  }

  function setBusy(isBusy) {
    loginButton.disabled = isBusy;
    loginButton.setAttribute("aria-busy", String(isBusy));
  }

  function watchPopup(nonce) {
    clearInterval(popupTimer);
    popupTimer = setInterval(() => {
      if (oauthPopup && !oauthPopup.closed) {
        return;
      }

      clearInterval(popupTimer);
      oauthPopup = null;
      setTimeout(() => {
        if (pending?.nonce !== nonce) {
          return;
        }

        pending = null;
        removeSession(pendingKey);
        setBusy(false);
        setStatus(messages.cancelled, "error");
      }, 500);
    }, 400);
  }

  function startLogin() {
    const nonce = createNonce();
    const authorizeUrl = new URL("/oauth/github/start", workerOrigin);
    pending = { createdAt: Date.now(), nonce };
    writeSession(pendingKey, pending);
    authorizeUrl.searchParams.set("origin", window.location.origin);
    authorizeUrl.searchParams.set("state", nonce);
    authorizeUrl.searchParams.set("lang", isEnglish ? "en" : "zh-CN");

    setBusy(true);
    setStatus(messages.waiting);
    oauthPopup = window.open(
      authorizeUrl.href,
      "eai-github-oauth",
      "popup=yes,width=560,height=720,resizable=yes,scrollbars=yes",
    );

    if (!oauthPopup) {
      pending = null;
      removeSession(pendingKey);
      setBusy(false);
      setStatus(messages.blocked, "error");
      return;
    }

    oauthPopup.focus();
    watchPopup(nonce);
  }

  loginButton.addEventListener("click", startLogin);
  forumArea.addEventListener("eai:github-login-request", startLogin);
  logoutButton.addEventListener("click", () => {
    const oldProfile = profile;
    profile = null;
    forceFillPending = false;
    removeSession(profileKey);
    clearMatchingProfile(oldProfile);
    renderProfile();
    connectForm();
    setStatus(messages.signedOut);
    clearStatusLater();
  });

  window.addEventListener("message", (event) => {
    const data = event.data;

    if (event.origin !== workerOrigin || !data || data.source !== "eai-github-oauth"
        || typeof data.state !== "string" || data.state !== pending?.nonce
        || (oauthPopup && event.source !== oauthPopup)) {
      return;
    }

    pending = null;
    removeSession(pendingKey);
    clearInterval(popupTimer);
    oauthPopup = null;
    setBusy(false);

    if (data.error) {
      setStatus(data.error === "authorization_denied" ? messages.denied : messages.failed, "error");
      return;
    }

    const receivedProfile = normalizeProfile(data.profile);

    if (!receivedProfile) {
      setStatus(messages.failed, "error");
      return;
    }

    profile = receivedProfile;
    writeSession(profileKey, profile);
    forceFillPending = true;
    renderProfile();
    applyProfile(true);
    setStatus(messages.signedIn, "success");
    clearStatusLater();
  });

  const threadObserver = new MutationObserver(() => setTimeout(connectForm, 120));
  threadObserver.observe(thread, { childList: true });
  forumArea.addEventListener("eai:forum-category-changed", () => setTimeout(connectForm, 120));
  renderProfile();

  const startedAt = Date.now();
  const poll = setInterval(() => {
    if (connectForm() || Date.now() - startedAt > 15000) {
      clearInterval(poll);
    }
  }, 200);
}

function setupForumFormToggle() {
  const forumArea = document.querySelector(".eai-forum");
  const toggle = forumArea?.querySelector("[data-eai-forum-toggle]");
  const composer = forumArea?.querySelector("[data-eai-forum-compose]");
  const composerOptions = [
    ...(composer?.querySelectorAll("[data-eai-forum-compose-option]") || []),
  ];
  const composerClose = composer?.querySelector("[data-eai-forum-compose-close]");
  const thread = forumArea?.querySelector("#cusdis_thread");

  if (!forumArea || !toggle || !composer || composerOptions.length === 0 || !thread) {
    return;
  }

  const showLabel = toggle.dataset.labelShow || "Write a comment";
  const hideLabel = toggle.dataset.labelHide || "Collapse";
  const loginLabel = toggle.dataset.labelLogin || "Sign in to comment";
  let formHidden = true;
  const scrollButtons = [...document.querySelectorAll("[data-eai-forum-scroll]")];
  let formObserver = null;
  let formObservedDoc = null;
  let lockedScrollPosition = null;

  function isAuthenticated() {
    return forumArea.dataset.githubAuthenticated === "true";
  }

  function requestLogin() {
    forumArea.dispatchEvent(new CustomEvent("eai:github-login-request"));
  }

  function syncToggle() {
    const canComment = isAuthenticated();
    const isExpanded = canComment && (composer.open || !formHidden);
    toggle.textContent = canComment ? (isExpanded ? hideLabel : showLabel) : loginLabel;
    toggle.setAttribute("aria-expanded", String(isExpanded));
    toggle.dataset.authenticated = String(canComment);
  }

  function syncForm() {
    const doc = forumArea.querySelector("iframe")?.contentDocument;
    const canComment = isAuthenticated();

    if (doc?.documentElement) {
      injectForumStyles(doc);
      doc.documentElement.classList.toggle("eai-github-authenticated", canComment);
      doc.documentElement.classList.toggle("eai-form-hidden", formHidden || !canComment);
    }

    syncToggle();
  }

  function lockPageScroll() {
    if (lockedScrollPosition) {
      return;
    }

    lockedScrollPosition = { left: window.scrollX, top: window.scrollY };
    document.body.style.setProperty(
      "--eai-forum-locked-scroll-x",
      `${lockedScrollPosition.left}px`,
    );
    document.body.style.setProperty(
      "--eai-forum-locked-scroll-y",
      `${lockedScrollPosition.top}px`,
    );
    document.documentElement.classList.add("eai-forum-composer-open");
    document.body.classList.add("eai-forum-composer-open");
  }

  function unlockPageScroll() {
    const scrollPosition = lockedScrollPosition;
    lockedScrollPosition = null;
    document.documentElement.classList.remove("eai-forum-composer-open");
    document.body.classList.remove("eai-forum-composer-open");
    document.body.style.removeProperty("--eai-forum-locked-scroll-x");
    document.body.style.removeProperty("--eai-forum-locked-scroll-y");

    if (scrollPosition) {
      window.scrollTo({
        behavior: "instant",
        left: scrollPosition.left,
        top: scrollPosition.top,
      });
    }
  }

  function closeComposer() {
    if (composer.open) {
      composer.close();
    }

    unlockPageScroll();
    syncToggle();
  }

  function showComposer(event) {
    event?.preventDefault();

    if (!isAuthenticated()) {
      requestLogin();
      return;
    }

    const activeCategory = forumArea.dataset.activeForumCategory || "all";
    const preferredOption = composerOptions.find(
      (option) => option.dataset.eaiForumComposeOption === activeCategory,
    ) || composerOptions[0];

    formHidden = true;
    syncForm();

    lockPageScroll();

    if (!composer.open) {
      if (typeof composer.showModal === "function") {
        composer.showModal();
      } else {
        composer.setAttribute("open", "");
      }
    }

    syncToggle();
    requestAnimationFrame(() => {
      preferredOption.focus({ preventScroll: true });
    });
  }

  function openForm() {
    if (!isAuthenticated()) {
      formHidden = true;
      syncForm();
      requestLogin();
      return;
    }

    closeComposer();
    formHidden = false;
    syncForm();
    const startedAt = Date.now();
    const scrollPosition = { left: window.scrollX, top: window.scrollY };

    function focusWhenReady() {
      syncForm();
      const textarea = forumArea
        .querySelector("iframe")
        ?.contentDocument?.querySelector("textarea");

      if (textarea) {
        textarea.focus({ preventScroll: true });
        window.scrollTo({
          behavior: "instant",
          left: scrollPosition.left,
          top: scrollPosition.top,
        });
      } else if (Date.now() - startedAt < 10000) {
        setTimeout(focusWhenReady, 100);
      }
    }

    focusWhenReady();
  }

  toggle.addEventListener("click", () => {
    if (composer.open) {
      closeComposer();
    } else if (!formHidden) {
      formHidden = true;
      syncForm();
    } else {
      showComposer();
    }
  });

  scrollButtons.forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      forumArea.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  composerOptions.forEach((option) => {
    option.addEventListener("click", () => {
      const category = option.dataset.eaiForumComposeOption;

      closeComposer();
      forumArea.dispatchEvent(new CustomEvent("eai:forum-select-category", {
        detail: { category, openForm: true },
      }));
    });
  });

  composer.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeComposer();
  });

  composer.addEventListener("click", (event) => {
    if (event.target === composer) {
      closeComposer();
    }
  });

  composer.addEventListener("close", () => {
    unlockPageScroll();
    syncToggle();
  });

  composerClose?.addEventListener("click", closeComposer);

  forumArea.addEventListener("eai:forum-category-changed", () => {
    closeComposer();
    formHidden = true;
    syncForm();
  });

  forumArea.addEventListener("eai:forum-open-form", openForm);
  forumArea.addEventListener("eai:github-auth-changed", (event) => {
    if (!event.detail?.authenticated) {
      formHidden = true;
      closeComposer();
    }

    syncForm();
  });

  function connect() {
    const doc = forumArea.querySelector("iframe")?.contentDocument;

    if (!doc?.body) {
      return;
    }

    syncForm();

    if (formObserver && formObservedDoc === doc) {
      return;
    }

    if (formObserver) {
      formObserver.disconnect();
    }

    formObservedDoc = doc;
    doc.addEventListener("click", (event) => {
      const replyButton = event.target.closest?.(
        "button.font-medium.text-sm.text-gray-500",
      );

      if (!isAuthenticated() && replyButton) {
        event.preventDefault();
        event.stopImmediatePropagation();
        requestLogin();
      }
    }, true);
    doc.addEventListener("submit", (event) => {
      if (!isAuthenticated()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        requestLogin();
      }
    }, true);
    formObserver = new MutationObserver(syncForm);
    formObserver.observe(doc.body, {
      childList: true,
      subtree: true,
    });
  }

  const watcher = new MutationObserver(() => setTimeout(connect, 120));
  watcher.observe(thread, { childList: true });

  const startedAt = Date.now();

  const poll = setInterval(() => {
    if (Date.now() - startedAt > 15000) {
      clearInterval(poll);
      return;
    }

    connect();
  }, 200);
}

function startDocumentationUi() {
  enhanceVersionMenu();
  startHomepageMotion();
  syncArchitectureFrameHeight();
  syncArchitectureFrameTheme();
  localizeEnglishPageChrome();
  enhanceCommunityForum();
  setupForumCategories();
  setupGithubCommentLogin();
  syncForumTheme();
  expandForumFrame();
  setupForumFormToggle();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startDocumentationUi, { once: true });
} else {
  startDocumentationUi();
}
