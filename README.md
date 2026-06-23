# firearm-listing-import

A **Claude Code / Codex skill** that imports per‑gun photos + descriptions from local
`with pictures/<serial>/` folders onto **Serial No** records in a GunStore‑POS
(Frappe/ERPNext) instance, and publishes each gun to WooCommerce as its own per‑serial product.

> 给 Claude Code / Codex 用的 skill：把本地 `with pictures/<序列号>/` 文件夹里每把枪的
> 照片 + 描述写到 POS 的 **Serial No** 上，并**按序列号**上架到 WooCommerce。

> **Companion repo:** [`gunstore-pos-mcp`](https://github.com/xuanji86/gunstore-pos-mcp) — the
> MCP server this skill uses for reads / small writes. (The skill also bundles a standalone
> Frappe‑REST script for the image‑upload + Woo push path, so it runs without the MCP too.)

## Contents

| File | What |
|---|---|
| `SKILL.md` | The runbook the agent reads (data model, safety, workflow). |
| `scripts/firearm_listings.py` | Portable Frappe‑REST tool: `resolve` / `attach` / `push` / `verify` / `testconn` / `setprice`. |
| `references/operator-guide.md` | Plain‑language, step‑by‑step guide for non‑technical operators (API key → folders → pricing → run → WooCommerce checks). |
| `references/internals.md` | Field names, code paths, gotchas. |

## How it works (the short version)

- Each folder is named after a firearm serial number and holds one description `.txt` + photos.
- Per‑gun data lives on the **Serial No** record (`image` / `image_gallery` / `description`), **not** the model Item (one Item backs many serials).
- Photos are **resized (~2000px/q80) before upload** — full‑size phone photos blow past the POS→Woo 30s image‑sideload timeout.
- Woo listing is **per serial** via `push_serial_now` (not `woo_push_item`, which would push every sibling under the same Item).
- Image bytes go over Frappe REST `upload_file` from the script (they can't pass through MCP tool calls without exploding the agent's context).

## Install

This skill drives a GunStore‑POS Frappe instance, so it needs that instance's API credentials.

**1. Make your agent discover the skill**
- **Claude Code:** clone/symlink into a skills dir, e.g. `~/.claude/skills/firearm-listing-import` (global) or `<project>/.claude/skills/firearm-listing-import` (project).
- **Codex:** clone/symlink into `~/.codex/skills/firearm-listing-import` (or the cross‑runtime `~/.agents/skills/`).

```bash
git clone https://github.com/xuanji86/firearm-listing-import.git
ln -sfn "$PWD/firearm-listing-import" ~/.codex/skills/firearm-listing-import
```

**2. Point it at your POS credentials.** The script reads `FRAPPE_BASE_URL` / `FRAPPE_API_KEY` / `FRAPPE_API_SECRET`. Either:
- export `FIREARM_ENV=/path/to/gunstore-pos/mcp/.env`, **or**
- run it from inside a gunstore‑pos checkout (the script auto‑finds `mcp/.env` by walking up).

Generate the key in Frappe Desk → **My Settings → API Access → Generate Keys** (see `references/operator-guide.md` 第一部分).

**3. Run** with `uv` (auto‑installs the one dependency via PEP 723 inline metadata) — never bare `python` (the system interpreter has no `requests`):

```bash
uv run scripts/firearm_listings.py testconn
uv run scripts/firearm_listings.py resolve --root "/path/to/with pictures N"
```

## Safety

- `attach` / `push` / `setprice` are **live writes** to a real POS + WooCommerce store. The tool prints the target before writing, refuses fuzzy serial matches, and the runbook mandates **resolve‑first + canary** (do one, verify, then batch).
- **No secrets in this repo** — credentials live only in your `mcp/.env` (or `FIREARM_ENV`). The operator guide shows placeholders only.

> Note: the "平台兼容（已配好/已做好）" lines in `SKILL.md` describe the original gunstore‑pos
> dev machine. For a fresh clone, follow the **Install** steps above.
