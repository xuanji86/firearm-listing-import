# firearm-listing-import

A **Claude Code / Codex skill** that imports per‑gun photos + descriptions from local
`with pictures/<serial>/` folders onto **Serial No** records in a GunStore‑POS
(Frappe/ERPNext) instance, and publishes each gun as its own per‑serial listing on WooCommerce
(and, per serial, on GunBroker).

> **Companion repo:** [`gunstore-pos-mcp`](https://github.com/xuanji86/gunstore-pos-mcp) — the
> MCP server this skill uses for reads / small writes. (The skill also bundles a standalone
> Frappe‑REST script for the image‑upload + Woo push path, so it runs without the MCP too.)

## Contents

| File | What |
|---|---|
| `SKILL.md` | The runbook the agent reads (data model, safety, workflow). |
| `scripts/firearm_listings.py` | Portable Frappe‑REST tool: `resolve` / `rotate` / `attach` / `push` / `verify` / `testconn` / `setprice` / `settitle`. |
| `references/operator-guide.md` | Plain‑language, step‑by‑step guide for non‑technical operators (API key → folders → pricing → run → WooCommerce checks → the new‑arrivals email). |
| `references/new-arrivals-email.md` | The post‑listing New Arrivals email: what to ask, and the `wp osa-growth new-arrivals` commands behind it. |
| `references/internals.md` | Field names, code paths, gotchas. |

## How it works (the short version)

- Per‑gun data lives on the **Serial No** record (`image` / `image_gallery` / `description` / `item_name`), **not** the model Item (one Item backs many serials).
- Photos taller than wide (after EXIF rotation) block `attach` for that gun (`resolve` flags `PORTRAIT:<n>`); the store's grid and gallery are landscape. Override with `--allow-portrait` — except the primary (`main.*`), which must be landscape. Whether the gun is upright in the primary photo is checked by the agent looking at it before `attach`, and fixed on the spot with `rotate --degrees 90|180|270` (original kept as `.orig`; SKILL.md step 1b).
- The `Title:` line of the description becomes `Serial No.item_name`, the per‑gun product name (falls back to the shared model name; `resolve` flags `NO-TITLE`).
- Photos are **resized (~2000px/q80) before upload** — full‑size phone photos blow past the POS→Woo 30s image‑sideload timeout.
- Listing is **per serial** via `push_serial_now` (not `woo_push_item`, which pushes every sibling under the same Item). `push --channel gunbroker` lists the same gun on GunBroker with the same photos, description and price.
- Image bytes go over Frappe REST `upload_file` from the script (they can't pass through MCP tool calls without exploding the agent's context).
- After the guns are live, the skill **asks whether to email the New Arrivals subscribers** and runs `wp osa-growth new-arrivals send` on the store host only on an explicit yes. Nothing is sent automatically.

## Folder and description format

One subfolder per gun, named after its serial number, holding `description.txt` plus the photos. The companion skill [product-description-seo](https://github.com/xuanji86/product-description-seo) writes these files in this exact shape.

**Description file format** (`description.txt`, UTF-8). Three kinds of line are structure the tool parses — `Title:`, `CA Legal:` and `Compliant Service:` — and are removed from the customer-facing text; everything else is free text written to the product description verbatim. Hard-wrapped prose (a paragraph cut into ~90-column lines) is merged back into one line per paragraph, because the store renders every remaining newline as a line break. Photos sit next to it in the same folder.

```
CA Legal: Yes
Compliant Service: No

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
- `CA Legal:` / `Compliant Service:` take `Yes` or `No` only (case-insensitive, any line, first of each kind wins) and are written to the Serial No fields of the `osa_ca_compliant` POS extension; the store shows them as fields. A missing line leaves the gun's existing answer alone. Any other value (`CA Legal: maybe`) is not an answer: the line stays in the description as text, nothing is written, and `resolve` / `attach` print `BAD-FLAG`.
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

Windows (PowerShell; a junction needs no admin rights):

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex\skills" | Out-Null
New-Item -ItemType Junction -Force -Path "$HOME\.codex\skills\firearm-listing-import" -Target "$PWD\firearm-listing-import"
```

**2. Sign in to the POS (once per machine, no API key).**

```bash
uv run scripts/firearm_listings.py login https://pos.oldsteelarsenal.com   # CGA: https://pos.caligunsandammo.com
```

A browser opens the POS sign-in page; sign in with your own POS account and approve. The session is kept in `~/.config/firearm-listing-import/` (mode 600) and renews itself; writes carry your name and POS roles. Signing in to the other store replaces it. Needs the store's **MCP Settings** switched on (it owns the POS's OAuth sign-in).

For a dev site, `FIREARM_ENV=/path/to/key.env` (`FRAPPE_BASE_URL` / `FRAPPE_API_KEY` / `FRAPPE_API_SECRET`) still works and **wins over the login** — remove an old `export FIREARM_ENV=…` from your shell profile after switching.

**3. Connect the gunstore-pos MCP** (reads and single-gun writes) as the store's remote connector — the commands for Claude Code and Codex are on the POS page **MCP Settings**. Nothing to install.

**4. Run** with `uv` (installs the dependencies via PEP 723 inline metadata) — never bare `python` (the system interpreter has no `requests`):

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
- **`push --channel gunbroker` is refused against any non-local POS** unless `FIREARM_ALLOW_PROD=1` (exactly `1`; anything else fails closed). A wrong Woo push can be unpublished; a wrong GunBroker push is a real firearm on a public marketplace. The gate keys on locality only — sandbox vs live is `GunBroker Settings.sandbox_mode` on the POS, which the script cannot read or set.
- **GunBroker MCP write tools** (`gb_push_serial` / `gb_end_listing`) are on the remote full connector of a store whose POS has GunBroker switched on (the POS deploy follows that switch); the read-only cpa connector never has them.
- **No secrets in this repo** — the session lives in your user config directory (or an explicit `FIREARM_ENV` key file).
