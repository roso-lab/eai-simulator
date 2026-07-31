---
orphan: true
---

# README Media

This directory owns the product demonstrations used by the English README, Chinese README, and Sphinx guides.

## Feature Mapping

| File | README role | Deep guide |
| --- | --- | --- |
| `demo.gif` | Product overview | Project overview |
| `eai_env_diy.gif` | Compose environments | Environment guide |
| `eai-keyboard.gif` | Heterogeneous agents | Project overview |
| `gs-hub_demo.gif` | Perception and control | GS-Hub guide |
| `eai-nav.gif` | Collaborative experiment fallback | Getting started / Nav2 |

## Recording Contract

- Record a real, reproducible EAI workflow rather than a decorative loop.
- Prefer 960×540 at a 16:9 aspect ratio.
- Keep one demonstration focused on one user-visible result and between 8 and 15 seconds.
- Keep feature GIFs at or below 5 MB and the overview GIF at or below 8 MB when source quality permits.
- Keep labels, terminals, sensor output, and the controlled entity large enough to inspect.
- Update the English and Chinese `alt` text whenever the demonstrated behavior changes.
- Do not reference a new media file from either README until the file is committed and both relative paths have been checked.

## Collaboration Demo Replacement

Until a dedicated collaboration recording exists, both READMEs use `eai-nav.gif` for the Experiment feature. To replace it:

1. Add `eai-collaboration.gif` with a complete multi-agent or human-robot task outcome.
2. Replace the Experiment image path in `README.md` and `docs/README.zh-CN.md` in the same commit.
3. Keep the existing Fire Rescue or experiment guide link unless the new recording has a more specific reproducible guide.
4. Update the Feature Mapping and Current Baseline tables with the new path, byte count, and recomputed total.
5. Run the replacement checks below.

## Replacement Checks

Run from the repository root after changing any README media:

```bash
diff \
  <(rg -o 'docs/source/assets/media/[A-Za-z0-9_-]+\.gif' README.md | sed 's#docs/##') \
  <(rg -o 'source/assets/media/[A-Za-z0-9_-]+\.gif' docs/README.zh-CN.md)

mapfile -t readme_media < <(
  rg -o 'docs/source/assets/media/[A-Za-z0-9_-]+\.gif' README.md | sort -u
)
file "${readme_media[@]}"
stat --printf='%n %s\n' "${readme_media[@]}"
stat -c '%s' "${readme_media[@]}" | awk '{ total += $1 } END { print "Total bytes:", total }'

sphinx-build -W --keep-going -b html docs/source /tmp/eai-simulator-docs-media-build
```

## Current Baseline

The initial product README reuses the existing recordings without lossy recompression:

| File | Bytes |
| --- | ---: |
| `demo.gif` | 9,705,622 |
| `eai_env_diy.gif` | 6,798,839 |
| `eai-keyboard.gif` | 5,225,557 |
| `gs-hub_demo.gif` | 1,885,224 |
| `eai-nav.gif` | 8,169,454 |
| **Total** | **31,784,696** |

This baseline exceeds the 25 MB target. It is accepted for the initial redesign because the source recordings are not available and blind GIF-to-GIF recompression can make terminals and sensor output unreadable. New or replacement recordings must reduce the total toward the target instead of increasing it without a documented reason.
