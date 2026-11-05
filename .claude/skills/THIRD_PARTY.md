# Third-party skills

Frontend design skills vendored from their upstream repositories. They are copied as published, with
their licence next to them; update them by copying a newer upstream version, not by editing them here.

| Skill | Upstream | Path in upstream | Commit | Licence |
|---|---|---|---|---|
| `frontend-design` | https://github.com/anthropics/skills | `skills/frontend-design` | `ca45fc4` | see `frontend-design/LICENSE.txt` |
| `ui-ux-pro-max` | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill | `.claude/skills/ui-ux-pro-max` | `b7d5353` | MIT (`ui-ux-pro-max/LICENSE`) |
| `impeccable` | https://github.com/pbakaus/impeccable | `.claude/skills/impeccable` | `c8891a9` | Apache-2.0 (`impeccable/LICENSE`, `impeccable/NOTICE.md`) |

Notes:
- `impeccable/scripts/impeccable` downloads its engine binary from the GitHub releases of
  pbakaus/impeccable on first run and refuses it unless its sha256 matches `engine-checksums.txt`.
- `ui-ux-pro-max` runs a local Python search over its CSV data (no network). Its commands use
  `${CLAUDE_PLUGIN_ROOT}`, which is only set for plugins: as a project skill, run
  `.claude/skills/ui-ux-pro-max/scripts/search.py` from the repository root.
