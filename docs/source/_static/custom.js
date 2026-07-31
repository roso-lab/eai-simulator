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

function startDocumentationUi() {
  enhanceVersionMenu();
  startHomepageMotion();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startDocumentationUi, { once: true });
} else {
  startDocumentationUi();
}
