#!/usr/bin/env python3
"""Validate cross-file documentation facts without starting simulator services."""

from __future__ import annotations

import ast
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
RELEASE = "v0.1.0-beta.1"
EXPECTED_ALGORITHMS = (
    "TeamWeaver",
    "emos",
    "global_planner",
    "keyboard",
    "multi_robot_navigation",
    "nav2",
)


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = dict(attrs)
        if values.get("src"):
            self.sources.append(str(values["src"]))


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise AssertionError(f"missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def _python_literal(relative: str, name: str):
    tree = ast.parse(_read(relative), filename=relative)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"{relative} does not assign {name}")


def _jinja_list(template: str, name: str) -> list[str]:
    match = re.search(r"{%\s*set\s+" + re.escape(name) + r"\s*=\s*(\[[^\n]+\])\s*%}", template)
    if not match:
        raise AssertionError(f"page template does not define {name}")
    value = ast.literal_eval(match.group(1))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AssertionError(f"page template {name} is not a string list")
    return value


def _check_image_references(relative: str) -> None:
    parser = _ImageParser()
    parser.feed(_read(relative))
    base = (ROOT / relative).parent
    for source in parser.sources:
        parsed = urlsplit(source)
        if parsed.scheme or source.startswith("//"):
            continue
        target = (base / parsed.path).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError as exc:
            raise AssertionError(f"{relative} image escapes repository: {source}") from exc
        if not target.is_file():
            raise AssertionError(f"{relative} references missing image: {source}")


def check_release_revision() -> None:
    source_default = _python_literal(
        "source/EAI_assets/EAI_assets/asset_resolver.py", "DEFAULT_HF_REVISION"
    )
    if source_default != RELEASE:
        raise AssertionError(f"asset resolver default is {source_default!r}, expected {RELEASE!r}")

    guide = _read("AGENTS.md")
    required = (
        "release asset revision `v0.1.0-beta.1`",
        'EAI_HF_REVISION="${EAI_ASSETS_HF_REVISION:-v0.1.0-beta.1}"',
        "EAI_ASSET_CANDIDATE_REVISION=v0.1.0-beta.1",
    )
    for text in required:
        if text not in guide:
            raise AssertionError(f"AGENTS.md is missing release-revision guidance: {text}")
    stale = (
        "fall back to `main`",
        "default `main` branch",
        'EAI_HF_REVISION="${EAI_ASSETS_HF_REVISION:-main}"',
        "EAI_ASSET_CANDIDATE_REVISION=main",
    )
    for text in stale:
        if text in guide:
            raise AssertionError(f"AGENTS.md retains stale moving-revision guidance: {text}")


def check_algorithm_inventory() -> None:
    for name in EXPECTED_ALGORITHMS:
        if not (ROOT / "algorithm" / name).is_dir():
            raise AssertionError(f"missing documented algorithm directory: algorithm/{name}")

    if "Six reusable algorithm packages" not in _read("README.md"):
        raise AssertionError("README.md does not describe the six maintained algorithm packages")
    if "6 个可复用算法包" not in _read(".github/README.zh-CN.md"):
        raise AssertionError(".github/README.zh-CN.md has a stale algorithm count")


def check_public_images() -> None:
    _check_image_references("README.md")
    _check_image_references(".github/README.zh-CN.md")


def check_community_language_navigation() -> None:
    files = (
        (
            ".github/CONTRIBUTING.md",
            ".github/CONTRIBUTING.zh-CN.md",
            '<p align="center">\n'
            '  <a href="https://github.com/roso-lab/eai-simulator/blob/main/'
            '.github/CONTRIBUTING.md">English</a> · '
            '<a href="https://github.com/roso-lab/eai-simulator/blob/main/'
            '.github/CONTRIBUTING.zh-CN.md">中文</a>\n'
            "</p>",
            "# Contributing to EAI Simulator",
            "# 为 EAI Simulator 做贡献",
        ),
        (
            ".github/CODE_OF_CONDUCT.md",
            ".github/CODE_OF_CONDUCT.zh-CN.md",
            '<p align="center">\n'
            '  <a href="https://github.com/roso-lab/eai-simulator/blob/main/'
            '.github/CODE_OF_CONDUCT.md">English</a> · '
            '<a href="https://github.com/roso-lab/eai-simulator/blob/main/'
            '.github/CODE_OF_CONDUCT.zh-CN.md">中文</a>\n'
            "</p>",
            "# Code of Conduct",
            "# 行为准则",
        ),
        (
            ".github/SECURITY.md",
            ".github/SECURITY.zh-CN.md",
            '<p align="center">\n'
            '  <a href="https://github.com/roso-lab/eai-simulator/blob/main/'
            '.github/SECURITY.md">English</a> · '
            '<a href="https://github.com/roso-lab/eai-simulator/blob/main/'
            '.github/SECURITY.zh-CN.md">中文</a>\n'
            "</p>",
            "# Security Policy",
            "# 安全策略",
        ),
    )
    for english, chinese, navigation, english_heading, chinese_heading in files:
        english_body = _read(english)
        chinese_body = _read(chinese)
        for relative, body in ((english, english_body), (chinese, chinese_body)):
            if not body.startswith(navigation):
                raise AssertionError(f"{relative} does not link both language pages")
        if chinese_heading in english_body or english_heading in chinese_body:
            raise AssertionError(f"{english} and {chinese} must remain separate language pages")


def check_hosted_docs() -> None:
    docs = ROOT / "docs"
    if not docs.is_dir():
        print("SKIP hosted documentation checks (docs/ is not present)")
        return

    if _python_literal("docs/source/conf.py", "release") != RELEASE.removeprefix("v"):
        raise AssertionError("Sphinx release does not match v0.1.0-beta.1")

    template = _read("docs/source/_templates/page.html")
    bilingual = _jinja_list(template, "bilingual_pages")
    english = _jinja_list(template, "english_pages")
    required_pages = {"emos", "teamweaver", "realsense_tutorial"}
    if not required_pages.issubset(bilingual):
        raise AssertionError("page template omits a required bilingual page")
    expected_english = {f"{name}_en" for name in bilingual}
    if set(english) != expected_english:
        raise AssertionError("page template English and bilingual page sets differ")

    community = _read("docs/community_workflow.md")
    community_zh = _read("docs/community_workflow.zh-CN.md")
    contributing_zh = _read("docs/CONTRIBUTING.zh-CN.md")
    pr_template = _read(".github/PULL_REQUEST_TEMPLATE.md")
    for text, label in (
        (community, "English community workflow"),
        (community_zh, "Chinese community workflow"),
        (contributing_zh, "Chinese contributing guide"),
        (pr_template, "pull request template"),
    ):
        if "github-pr/" not in text:
            raise AssertionError(f"{label} does not identify bridge-owned branches")
    if "server-side bridge" not in community or "服务端桥接器" not in community_zh:
        raise AssertionError("community workflow does not describe the server-side bridge")
    stale_workflow = {
        "docs/community_workflow.md": (
            "Mirroring repositories",
            "ports the patch to GitLab",
            "GitLab push mirror",
        ),
        "docs/community_workflow.zh-CN.md": (
            "Mirroring repositories",
            "cherry-pick、应用 patch",
            "GitLab push mirror",
        ),
        "docs/CONTRIBUTING.zh-CN.md": ("将 patch 搬运到 GitLab",),
        ".github/PULL_REQUEST_TEMPLATE.md": ("may be ported internally",),
    }
    for relative, fragments in stale_workflow.items():
        body = _read(relative)
        for fragment in fragments:
            if fragment in body:
                raise AssertionError(f"{relative} retains obsolete mirror guidance: {fragment}")

    for relative in ("docs/source/project_overview.md", "docs/source/project_overview_en.md"):
        overview = _read(relative)
        for name in EXPECTED_ALGORITHMS:
            if f"{name}/" not in overview:
                raise AssertionError(f"{relative} omits algorithm/{name}")
        for stale in ("algorithm/ros", "hmrs_env/update.sh"):
            if stale in overview:
                raise AssertionError(f"{relative} references nonexistent {stale}")

    if "6 个可复用算法包" not in _read("docs/README.zh-CN.md"):
        raise AssertionError("docs/README.zh-CN.md has a stale algorithm count")
    _check_image_references("docs/README.zh-CN.md")

    media = _read("docs/source/assets/media/README.md")
    public_media = {
        "asset-library.gif": ROOT / ".github/assets/asset-library.gif",
        "demo.gif": ROOT / ".github/assets/demo.gif",
        "env-diy.gif": ROOT / ".github/assets/env-diy.gif",
        "orsus-demo.gif": ROOT / ".github/assets/orsus-demo.gif",
    }
    documented: dict[str, int] = {}
    for line in media.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != 4 or not cells[1].startswith(chr(96)):
            continue
        name = cells[1].strip(chr(96))
        if name in public_media and cells[2].replace(",", "").isdigit():
            documented[name] = int(cells[2].replace(",", ""))
    actual = {name: path.stat().st_size for name, path in public_media.items()}
    if documented != actual:
        raise AssertionError(f"README media byte baseline is stale: documented={documented}, actual={actual}")
    total_match = re.search(r"\| \*\*Total\*\* \| \*\*([0-9,]+)\*\* \|", media)
    if not total_match or int(total_match.group(1).replace(",", "")) != sum(actual.values()):
        raise AssertionError("README media total is stale")


def main() -> int:
    checks = (
        ("release revision", check_release_revision),
        ("algorithm inventory", check_algorithm_inventory),
        ("public README images", check_public_images),
        ("community language navigation", check_community_language_navigation),
        ("hosted documentation", check_hosted_docs),
    )
    for label, check in checks:
        check()
        print(f"PASS {label}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL documentation consistency: {exc}", file=sys.stderr)
        raise SystemExit(1)
