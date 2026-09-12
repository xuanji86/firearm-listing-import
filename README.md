# firearm-listing-import

A **Claude Code / Codex skill** that imports per‑gun photos + descriptions from local
`with pictures/<serial>/` folders onto **Serial No** records in a GunStore‑POS
(Frappe/ERPNext) instance, and publishes each gun to WooCommerce as its own per‑serial product.

> **Companion repo:** [`gunstore-pos-mcp`](https://github.com/xuanji86/gunstore-pos-mcp) — the
> MCP server this skill uses for reads / small writes. (The skill also bundles a standalone
> Frappe‑REST script for the image‑upload + Woo push path, so it runs without the MCP too.)

## Contents

| File | What |
|---|---|
| `SKILL.md` | The runbook the agent reads (data model, safety, workflow). |
| `scripts/firearm_listings.py` | Portable Frappe‑REST tool: `resolve` / `attach` / `push` / `verify` / `testconn` / `setprice` / `settitle`. |
| `references/operator-guide.md` | Plain‑language, step‑by‑step guide for non‑technical operators (API key → folders → pricing → run → WooCommerce checks → the new‑arrivals email). |
| `references/new-arrivals-email.md` | The post‑listing New Arrivals email: what to ask, and the `wp osa-growth new-arrivals` commands behind it. |
| `references/internals.md` | Field names, code paths, gotchas. |

## How it works (the short version)

- Each folder is named after a firearm serial number and holds one description `.txt` + photos.
- Per‑gun data lives on the **Serial No** record (`image` / `image_gallery` / `description` / `item_name`), **not** the model Item (one Item backs many serials).
- Each gun gets **its own WooCommerce listing title** from a `Title:` line in the description `.txt` → written to `Serial No.item_name` (the field the Woo payload builder uses for the product name, falling back to the shared model name). No `Title:` line ⇒ the gun keeps the shared model name (`resolve` flags `NO-TITLE`).
- Photos are **resized (~2000px/q80) before upload** — full‑size phone photos blow past the POS→Woo 30s image‑sideload timeout.
- Woo listing is **per serial** via `push_serial_now` (not `woo_push_item`, which would push every sibling under the same Item).
- Image bytes go over Frappe REST `upload_file` from the script (they can't pass through MCP tool calls without exploding the agent's context).
- After the guns are live, the skill **asks whether to email the New Arrivals subscribers** and, only on an explicit yes, runs `wp osa-growth new-arrivals send` on the store host. Nothing is sent automatically — a timer used to fire an hour after the first listing, which is exactly when titles, prices and photos are still being corrected.

## Folder and description format

One subfolder per gun, named after its serial number, holding `description.txt` plus the photos.

**Description file format** (`description.txt`, UTF-8). One `Title:` line is the only structure the tool parses; everything else is free text, written to the product description verbatim (newlines preserved). Photos sit next to it in the same folder.

```
<one or two paragraphs written for the buyer>

Specifications
Title: Type 38 Arisaka Training Rifle w/ Bayonet - Smoothbore Blank-Fire
Manufacturer: Japanese production
Country of origin: Japan
Model: Type 38 Arisaka Training Rifle
Action: Bolt-action
...
```

- `Title:` may be on any line; case-insensitive; space after the colon optional. Only the first match is used, and that line is removed from the description.
- The other `Key: value` lines are not parsed; they stay as text.
- No Markdown or HTML (it would show literally).
- Accepted photo extensions: `.jpg .jpeg .png .webp .heic`. `main.*` is the primary; otherwise the first by filename.

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

Generate the key in Frappe Desk → **My Settings → API Access → Generate Keys** (see `references/operator-guide.md`, Part 1).

**3. Run** with `uv` (installs the dependencies via PEP 723 inline metadata) — never bare `python` (the system interpreter has no `requests`):

```bash
uv run scripts/firearm_listings.py testconn
uv run scripts/firearm_listings.py resolve --root "/path/to/with pictures N"
```

## Tests

The production gate on `push --channel gunbroker` (see Safety below) has a
regression suite. Stdlib only — no install, no `uv` needed, and it never makes
an HTTP request:

```bash
python3 -m unittest discover -s tests -v
```

## Safety

- `attach` / `push` / `setprice` are **live writes** to a real POS + WooCommerce store. The tool prints the target before writing, refuses fuzzy serial matches, and the runbook mandates **resolve‑first + canary** (do one, verify, then batch).
- The New Arrivals email reaches every subscriber at once and cannot be recalled. The runbook forbids `send` without an explicit yes from the operator, and puts `preview` (read‑only) and `test --to=` (one address, no state touched) in front of it.
- **`push --channel gunbroker` is refused against any non-local POS** unless `FIREARM_ALLOW_PROD=1` is set explicitly (exactly `1`; a typo fails closed). A mistaken Woo push can be unpublished — a mistaken GunBroker push puts a real firearm on a public marketplace where a buyer can commit before anyone notices. Note this gate keys on *locality*, not on sandbox-vs-live: which GunBroker gets contacted is `GunBroker Settings.sandbox_mode` on the POS, which this script can neither read nor set.
- **The GunBroker MCP tools are a deployment prerequisite, not an option.** Any POS MCP instance that will list or end GunBroker listings must be started with `GUNSTORE_MCP_GUNBROKER_ACTIONS=1` — `gb_push_serial` and `gb_end_listing` are not registered without it (`gb_test_connection` / `gb_listing_status` always are). **Push and end are the same switch.** An instance that can list but not end is parked on the worst square: a gun sells at the counter, the assistant answers "I don't have that tool", and the listing stays up for a second buyer. Automatic delisting on a counter sale does not exist yet — it lands with PR-2/PR-3.
- **No secrets in this repo** — credentials live only in your `mcp/.env` (or `FIREARM_ENV`). The operator guide shows placeholders only.
