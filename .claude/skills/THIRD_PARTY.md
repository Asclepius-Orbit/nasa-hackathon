# Third-party skills

Frontend design skills vendored from their upstream repositories. They are copied as published, with
their licence next to them; update them by copying a newer upstream version, not by editing them here.

| Skill | Upstream | Path in upstream | Commit | Licence |
|---|---|---|---|---|
| `frontend-design` | https://github.com/anthropics/skills | `skills/frontend-design` | `ca45fc4` | see `frontend-design/LICENSE.txt` |
| `ui-ux-pro-max` | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill | `.claude/skills/ui-ux-pro-max` | `b7d5353` | MIT (`ui-ux-pro-max/LICENSE`) |
| `impeccable` | https://github.com/pbakaus/impeccable | `.claude/skills/impeccable` | `c8891a9` | Apache-2.0 (`impeccable/LICENSE`, `impeccable/NOTICE.md`) |
| `mcp-builder` | https://github.com/anthropics/skills | `skills/mcp-builder` | `ca45fc4` | Apache-2.0 (`mcp-builder/LICENSE.txt`) |
| `property-based-testing` | https://github.com/trailofbits/skills | `plugins/property-based-testing/skills/property-based-testing` | `470419e` | CC-BY-SA-4.0 (`property-based-testing/LICENSE`) |
| `modern-python` | https://github.com/trailofbits/skills | `plugins/modern-python/skills/modern-python` | `470419e` | CC-BY-SA-4.0 (`modern-python/LICENSE`) |
| `async-python-patterns` | https://github.com/wshobson/agents | `plugins/python-development/skills/async-python-patterns` | `4bde316` | MIT (`async-python-patterns/LICENSE`) |
| `python-testing-patterns` | https://github.com/wshobson/agents | `plugins/python-development/skills/python-testing-patterns` | `4bde316` | MIT (`python-testing-patterns/LICENSE`) |
| `python-type-safety` | https://github.com/wshobson/agents | `plugins/python-development/skills/python-type-safety` | `4bde316` | MIT (`python-type-safety/LICENSE`) |

Notes:
- `impeccable/scripts/impeccable` downloads its engine binary from the GitHub releases of
  pbakaus/impeccable on first run and refuses it unless its sha256 matches `engine-checksums.txt`.
- `ui-ux-pro-max` runs a local Python search over its CSV data (no network). Its commands use
  `${CLAUDE_PLUGIN_ROOT}`, which is only set for plugins: as a project skill, run
  `.claude/skills/ui-ux-pro-max/scripts/search.py` from the repository root.
- Trail of Bits skills are CC-BY-SA-4.0: keep the attribution and the licence file, and share changes under the same licence. Only the skill folder was copied; the plugin's hooks (pip/python/uv shims) and evals were not.
- `modern-python` recommends `ty` and prek; this repo uses pyright, so ignore that advice. `mcp-builder/scripts` needs `anthropic` and `mcp` (not installed by default).
