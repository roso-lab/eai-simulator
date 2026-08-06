#!/usr/bin/env node

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "..");
const runtimePath = path.join(
  repositoryRoot,
  "source/EAI/EAI/hmrs_env/env_diy/env_diy_app.html"
);

const lineageMarkup = [
  'data-lineage-column="scene"',
  'data-lineage-column="robot"',
  'data-lineage-column="payload"',
  'data-lineage-column="tool"',
  'data-lineage-column="controller"'
];

const runtimeMarkup = [
  ...lineageMarkup,
  "const pywebviewBridge",
  "window.pywebview",
  "submit_selection",
  "await bridge.submit_selection(exportPayload())",
  'addEventListener("pywebviewready"',
  "保存并运行",
  "new Blob",
  "link.download",
  "data-complete-selection",
  "data-download-json",
  "data-completion-download",
  'id="confirmation-dialog"',
  "DRAFT_KEY"
];

const retiredMarkup = [
  'data-testid="scene-canvas"',
  "Browser 2D",
  "palettePointerDrag",
  'addEventListener("dragover"',
  'addEventListener("drop"',
  'draggable="true"'
];

const runtimeTutorialMarkup = [
  "新手教程",
  "打开教程",
  "Env DIY 教程",
  "data-open-tutorial",
  'id="tutorial-dialog"',
  "tour-target-ring",
  "const tourSteps",
  "const startTour",
  "const finishTour",
  "const requestTutorialExit",
  "TOUR_KEY"
];

const errors = [];

const fail = message => errors.push(message);

const checkMarkup = (html, required, forbidden) => {
  for (const marker of required) {
    if (!html.includes(marker)) fail(`missing required marker: ${marker}`);
  }
  for (const marker of forbidden) {
    if (html.includes(marker)) fail(`retired interaction marker remains: ${marker}`);
  }
};

const checkUniqueIds = html => {
  const markup = html.replace(/<script(?:\s[^>]*)?>[\s\S]*?<\/script>/gi, "");
  const seen = new Map();
  const idPattern = /\bid\s*=\s*["']([^"']+)["']/g;
  for (const match of markup.matchAll(idPattern)) {
    const id = match[1].trim();
    if (!id) continue;
    seen.set(id, (seen.get(id) || 0) + 1);
  }
  for (const [id, count] of seen) {
    if (count > 1) fail(`duplicate id: ${id} (${count} occurrences)`);
  }
};

const checkLocalAssets = (html, htmlPath) => {
  const assets = new Set(
    html.match(/(?:env-diy-assets|(?:\.\.\/){5}usd\/picture\/(?:processed|scene))\/[A-Za-z0-9_./-]+\.png/g) || []
  );
  if (!assets.size) {
    fail("no local USD PNG references found");
    return;
  }
  for (const asset of assets) {
    const assetPath = path.resolve(path.dirname(htmlPath), asset);
    if (!fs.existsSync(assetPath)) fail(`missing local asset: ${asset}`);
  }
};

const checkInlineScripts = html => {
  const tempDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "env-diy-check-"));
  try {
    const scriptPattern = /<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi;
    let checked = 0;
    for (const match of html.matchAll(scriptPattern)) {
      const source = match[1].trim();
      if (!source) continue;
      checked += 1;
      const tempScript = path.join(tempDirectory, `inline-${checked}.js`);
      fs.writeFileSync(tempScript, source, "utf8");
      const result = spawnSync(process.execPath, ["--check", tempScript], {
        encoding: "utf8"
      });
      if (result.status !== 0) {
        const detail = (result.stderr || result.stdout).trim().split("\n").slice(-2).join(" ");
        fail(`inline script ${checked} has invalid JavaScript: ${detail}`);
      }
    }
    if (!checked) fail("no inline JavaScript found");
  } finally {
    fs.rmSync(tempDirectory, { recursive: true, force: true });
  }
};

const mode = process.argv[2] || "all";
if (mode !== "all") {
  console.error("Usage: node tools/check_env_diy_runtime.mjs all");
  process.exit(2);
}

if (!fs.existsSync(runtimePath)) {
  fail(`HTML not found: ${runtimePath}`);
} else {
  const runtimeHtml = fs.readFileSync(runtimePath, "utf8");
  checkMarkup(runtimeHtml, runtimeMarkup, [...retiredMarkup, ...runtimeTutorialMarkup]);
  checkUniqueIds(runtimeHtml);
  checkLocalAssets(runtimeHtml, runtimePath);
  checkInlineScripts(runtimeHtml);
}

if (errors.length) {
  for (const error of errors) console.error(`FAIL: ${error}`);
  process.exit(1);
}

console.log("PASS: Env DIY runtime HTML contract");
