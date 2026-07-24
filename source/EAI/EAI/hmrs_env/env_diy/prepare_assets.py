from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

from PIL import Image

from .paths import REPO_ROOT


DEFAULT_SOURCE_ROOT = REPO_ROOT / "usd" / "picture"
DEFAULT_OUTPUT_ROOT = DEFAULT_SOURCE_ROOT / "processed"
GROUPS = ("robot", "manipulator", "sensor", "tool")
MANIPULATOR_NAMES = frozenset({"ur5", "z1"})
OUTLINE_COLOR = (0, 183, 255, 255)
GLOW_COLOR = (0, 183, 255, 90)


def process_tree(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    background_threshold: int = 2,
    outline_radius: int = 3,
) -> list[Path]:
    written: list[Path] = []
    for group in GROUPS:
        source_dir = source_root / group
        target_dir = output_root / group
        if not source_dir.exists():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_dir.glob("*.png")):
            target_group = "manipulator" if group == "sensor" and source.stem in MANIPULATOR_NAMES else group
            target = output_root / target_group / source.name
            if group == "sensor" and target_group == "manipulator" and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            process_image(
                source,
                target,
                background_threshold=background_threshold,
                outline_radius=outline_radius,
            )
            written.append(target)
    return written


def process_image(
    source: Path,
    target: Path,
    *,
    background_threshold: int = 2,
    outline_radius: int = 3,
) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    width, height = image.size
    pixels = image.load()
    background = _edge_connected_background(image, background_threshold)
    foreground = {(x, y) for y in range(height) for x in range(width) if (x, y) not in background}
    outline = _outline_pixels(foreground, width, height, outline_radius) - foreground
    glow = _outline_pixels(foreground, width, height, outline_radius + 1) - foreground - outline

    result = Image.new("RGBA", image.size, (0, 0, 0, 0))
    result_pixels = result.load()
    for x, y in glow:
        result_pixels[x, y] = GLOW_COLOR
    for x, y in outline:
        result_pixels[x, y] = OUTLINE_COLOR
    for x, y in foreground:
        r, g, b, a = pixels[x, y]
        result_pixels[x, y] = (r, g, b, a)

    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)


def _edge_connected_background(image: Image.Image, threshold: int) -> set[tuple[int, int]]:
    width, height = image.size
    pixels = image.load()
    queue: deque[tuple[int, int]] = deque()
    background: set[tuple[int, int]] = set()

    def enqueue_if_black(x: int, y: int) -> None:
        point = (x, y)
        if point in background:
            return
        r, g, b, a = pixels[x, y]
        if a == 0 or max(r, g, b) <= threshold:
            background.add(point)
            queue.append(point)

    for x in range(width):
        enqueue_if_black(x, 0)
        enqueue_if_black(x, height - 1)
    for y in range(height):
        enqueue_if_black(0, y)
        enqueue_if_black(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                enqueue_if_black(nx, ny)
    return background


def _outline_pixels(
    foreground: set[tuple[int, int]],
    width: int,
    height: int,
    radius: int,
) -> set[tuple[int, int]]:
    outline: set[tuple[int, int]] = set()
    radius_squared = radius * radius
    for x, y in foreground:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx * dx + dy * dy > radius_squared:
                    continue
                nx = x + dx
                ny = y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    outline.add((nx, ny))
    return outline


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare transparent outlined assets for the Env DIY prototype.")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--background-threshold", type=int, default=2)
    parser.add_argument("--outline-radius", type=int, default=3)
    args = parser.parse_args()

    written = process_tree(
        args.source_root,
        args.output_root,
        background_threshold=args.background_threshold,
        outline_radius=args.outline_radius,
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
