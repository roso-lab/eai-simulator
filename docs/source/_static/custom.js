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

function startDocumentationUi() {
  enhanceVersionMenu();
  startHomepageMotion();
  syncArchitectureFrameHeight();
  syncArchitectureFrameTheme();
  localizeEnglishPageChrome();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startDocumentationUi, { once: true });
} else {
  startDocumentationUi();
}
