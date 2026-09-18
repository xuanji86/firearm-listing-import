---
name: firearm-listing-import
description: Use when importing per-gun photos + descriptions from local "with pictures" folders (each subfolder named after a firearm serial number, holding a description .txt + photos) onto Serial No records in the POS, and/or publishing those guns to WooCommerce or GunBroker. Also covers first-time setup (Frappe API key, mcp/.env), post-listing checks in the WooCommerce admin, and asking the operator whether to send the New Arrivals email after a listing run. Includes a plain-language operator guide for non-technical users. Triggers on "attach firearm photos by serial", "update the guns from the with pictures folder", "upload serial number photos and descriptions", "push these guns to woocommerce", "list guns on the store", "set up the POS API key", "walk me through listing", "send the new arrivals email". Photos live on the Serial No (per gun), NOT the Item.
---

# Firearm Listing Import (photos + descriptions → POS → WooCommerce / GunBroker)

## Overview

Writes each gun's photos and description from a local `with pictures` folder onto the matching **Serial No** record in the POS, then optionally publishes each gun as its own WooCommerce product (and, per serial, to GunBroker). After a listing run, ask the user whether to send the New Arrivals email (step 5).

**Folder layout:** `<root>/<serial>/` — one subfolder per gun, named after its serial number, holding one description `.txt` (`description.txt` or a numbered `.txt`) and photos. `main.*` (or the common misspelling `mian.*`) is the primary photo; without one, the first photo by filename is primary.

**Title line:** the `Title:` value becomes the gun's own WooCommerce product title. `attach` writes it to `Serial No.item_name` (the Woo payload falls back to the shared `Item.item_name` model name only when empty; see `references/internals.md`). Without it, `item_name` is left alone, the product keeps the model name, and `resolve` flags `NO-TITLE`.

**Description file format** (`description.txt`, UTF-8). Three lines are structure the tool parses — `Title:`, `CA Legal:` and `Compliant Service:` — and everything else is free text written to the product description. Blank lines separate paragraphs and `Key: value` lines stay one per line; hard-wrapped prose (a paragraph cut into ~90-column lines, e.g. pasted from a terminal) is merged back into one line per paragraph, because the store renders every remaining newline as a line break. Photos sit next to it in the same folder.

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
- `CA Legal:` and `Compliant Service:` take **`Yes` or `No`** and write the matching fields on the Serial No (the `osa_ca_compliant` POS extension owns them; the store shows them in OSA Catalog Details). Write them at the top of the file by convention — they are matched anywhere, case-insensitively, first answer of each kind wins — and a parsed line is removed from the description, because the store renders the answer as a field rather than as prose.
  - **Leave the line out and nothing is written.** The gun keeps whatever the counter entered, and a gun with no answer of its own inherits its model's (the Item's) answer in the POS. Only answer a gun you have actually checked.
  - A value that is not `Yes`/`No` (`CA Legal: maybe`) is not an answer: the line stays in the description as ordinary text, nothing is written, and both `resolve` and `attach` print `BAD-FLAG`.
  - Prose that happens to mention CA legality ("this rifle is CA legal in most configurations") is not a flag — only a line that *starts* with the key is.
- The other `Key: value` lines are not parsed; they stay as text.
- No Markdown or HTML (it would show literally).
- Accepted photo extensions: `.jpg .jpeg .png .webp .heic`. `main.*` is the primary; otherwise the first by filename.

**Tool:** `scripts/firearm_listings.py` with subcommands `resolve` / `attach` / `push` / `verify` / `testconn` / `setprice` / `settitle`. Credentials and target site come from `mcp/.env` (or `FIREARM_ENV`).

**Run it with** `uv run scripts/firearm_listings.py <subcommand>` (PEP 723 inline deps; `uv` installs `requests`, `pillow`, `pillow-heif`). Never bare `python` — the system interpreter lacks `requests`.

> **Non-technical operator?** If the user is new, needs the API key set up, asks to be walked through, or clearly does not use a terminal: follow `references/operator-guide.md` section by section, run the commands for them, report each result in plain language, and ask before continuing.

## Platforms

Works the same on Claude Code and Codex, on macOS / Linux / Windows (no platform-specific dependencies; image resizing uses Pillow). Two paths coexist:

- **gunstore-pos MCP** — reads and small writes (`find_item`, `frappe_*`, `woo_*`, `set_serial_title`, `firearms_in_stock`, `gb_*`). Registered in the repo's `.mcp.json` (Claude Code) or `~/.codex/config.toml` (Codex). Tool-name prefix follows each client's convention.
- **Portable script** — batch photo upload, gallery build, listing. Pure Python + Frappe REST; runs on any agent.

Skill discovery: Claude Code finds project skills under `.claude/skills/`; Codex reads only `~/.codex/skills/` or `~/.agents/skills/`, so symlink the skill there. The script locates `mcp/.env` via `realpath`, so symlinks work; if the skill was copied elsewhere, set `FIREARM_ENV=/path/to/gunstore-pos/mcp/.env`.

**MCP or script?**

| Action | gunstore-pos MCP | Script |
|---|---|---|
| Test connection | `woo_test_connection` / any `frappe_get_document` | `testconn` |
| Look up serial / stock / Item | `find_item`, `frappe_list_documents`, `firearms_in_stock` | `resolve` |
| Set price (`sell_price`) | `frappe_update_document` | `setprice` |
| Set per-gun Woo title (`item_name`) | `set_serial_title` / `frappe_update_document` | `settitle` (or `attach` from the `Title:` line) |
| **List on Woo by serial** | `frappe_run_method` → `ffl_woo_sync.woocommerce.client_api.push_serial_now(serial_no)`. **Not** `woo_push_item` — it pushes every sibling serial under the item_code | `push` (same `push_serial_now`, long timeout, skips listed/unpriced) |
| **List on GunBroker by serial** | `gb_push_serial(serial_no, confirm=true)`; `gb_listing_status`; `gb_end_listing` | `push --channel gunbroker` (refused against non-local POS unless `FIREARM_ALLOW_PROD=1`) |
| **Upload photos + description + gallery** | Not possible — image bytes cannot pass through MCP calls | `attach` (only path; both channels reuse the result) |
| Inspect Woo products | osa-seo `woocommerce-products-list` (where that MCP is installed) | wp-admin SKU search / WC REST |
| **Send New Arrivals email** | Not reachable from the POS | `wp osa-growth new-arrivals …` on the Woo host over SSH (see `references/new-arrivals-email.md`) |

Rules: single-gun listing via MCP is fine; batches use the script (MCP clients default to a 30 s timeout, which multi-photo guns can exceed). Photo upload always goes through the script (it also does the mandatory resize).

## Development

`push --channel gunbroker` has a production gate with regression tests. Stdlib `unittest`, no install, never makes HTTP requests (`requests` is stubbed):

```bash
python3 -m unittest discover -s tests -v
```

Covered: non-local BASE without `FIREARM_ALLOW_PROD` → refused; undeterminable locality (malformed BASE, no scheme) → treated as prod (fail-closed); `FIREARM_ALLOW_PROD=1` → allowed; substring spoofing (`dev.localhost.evil.example.com`), userinfo (`http://localhost@evil.example.com/`), `[::1]`, trailing-dot FQDN, uppercase; `PUSH_CHANNELS` id fields do not overlap.

Run the suite before and after touching `cmd_push`, `_is_local_base`, or `_prod_gate_blocks`. This gate is the only thing between an agent batch-processing a folder of guns and a real firearm on a public auction site.

## Safety rails

1. `mcp/.env` points at **production** (`https://pos.oldsteelarsenal.com`); the Woo store is live. `attach` / `push` are customer-facing writes that are hard to undo.
2. Before any batch write, run `resolve` (read-only) and get the plan confirmed by the user.
3. **Canary first:** `attach` / `push` one gun, `verify`, have the user eyeball it, then batch.
4. `push` publishes immediately (`status=publish`). Price comes from `Serial No.sell_price`; a 0 price lists at $0.00, so the script skips unpriced guns and reports them.
5. Batch `attach` / `push` is slow (uploads + Woo sideload). Run in the background with a log.
6. **GunBroker is validated against the local site only.** `push --channel gunbroker` refuses any non-local POS unless `FIREARM_ALLOW_PROD=1`. A wrong Woo push can be unpublished; a wrong GunBroker push puts a real gun on a public marketplace where a buyer can commit before anyone notices, and ending a listing early requires a human on the GunBroker site. Sandbox vs live is decided solely by `GunBroker Settings.sandbox_mode` on the POS; neither the script nor the MCP can choose. Point `mcp/.env` at `http://dev.localhost:8000` to rehearse.
7. **The New Arrivals email goes out only after the user explicitly says "send".** It reaches every subscriber at once and cannot be recalled. If the user does not bring it up, ask — it is the last step of the listing flow, not optional.

## Data model (read this or you will write to the wrong record)

- Folder name = serial number = ERPNext **Serial No** record. A gun's photos / description / title live on the Serial No (`image`, `image_gallery`, `description`, `item_name`), **not** the Item.
- **Item is the shared model SKU** (e.g. `CZ85`, `M1-30`) backing dozens of serials. Several folders may map to one Item, so never write per-gun data to the Item.
- **Per-gun Woo title = `Serial No.item_name`.** The payload builder uses it and falls back to `Item.item_name` only when empty. The field is `fetch_if_empty=1`, so a non-empty value is never overwritten by the model name later; `read_only` only affects the Desk UI, REST writes work.
- Woo listing is **per serial**: one WC product per gun, SKU `item_code::serial`, via `push_serial_now(serial_no)`. Never `woo_push_item(item_code)` — it pushes every Active serial under that item_code.
- Featured image = the `image_gallery` row with `is_primary=1` (`_resolve_image_list` puts it at `images[0]`). The script puts the primary at `sort_order 0`, `is_primary 1`, and also sets `Serial No.image`.

## Workflow

### 1. resolve (read-only, always first)

```bash
uv run scripts/firearm_listings.py resolve --root "/path/to/with pictures N"
```

Prints per folder: folder → Serial No, status, price, photo count, primary, FLAGS, TITLE. Flags:

- `UNRESOLVED` — no Serial No found. Folder names often differ from records (`7408h`→`7408H`, `14078`→`N14078`, or entirely different `CR1159`→`SYA1139`). Ask the user for the correct serial and pass `--map` (JSON `{"CR1159":"SYA1139"}`).
- `FUZZY?` — not an exact match, only a unique substring hit. Untrusted: `attach` / `push` / `setprice` refuse it. Confirm the serial and bind it with `--map`.
- `status=Consumed` etc. — sold or not in stock. Ask whether to proceed (re-run `resolve` if it was just re-stocked).
- `UNPRICED($0)` — `attach` works, `push` skips it. User must price it first.
- `has-gallery` / `woo#<id>` — already has photos / already listed (skipped by default).
- `NO-DESC` — no `.txt` in the folder.
- `NO-TITLE` — no `Title:` line; the gun keeps the shared model name unless `settitle` is used.

Check the `TITLE` column for every gun before listing — it is the product name customers see. Present the plan to the user and get confirmation.

### 2. attach (writes POS: resize + upload + gallery / description / title)

```bash
uv run scripts/firearm_listings.py attach --root "/path/..." [--map map.json] [--only SERIAL_A,SERIAL_B] [--force]
```

- Photos are resized by default (~2000 px long edge, JPEG q80; smaller images untouched). Never upload originals — see "Why resize".
- Per gun: resize → upload to POS (public File attached to the Serial No) → PUT `description` (minus the `Title:` line), `image_gallery`, `image`, and `item_name` (from `Title:`, if present).
- A changed title only reaches Woo on the next `push`. After `attach --force` or `settitle` on a listed gun, `push` again.
- Idempotent: serials with a gallery are skipped unless `--force`.
- Canary: `--only <one serial>`, then `verify` (prints `title=…`), then the rest.

### 3. push (list on Woo, per serial)

```bash
uv run scripts/firearm_listings.py push --root "/path/..." [--map map.json] [--only ...]
```

- Calls `push_serial_now` for each serial that is priced, Active, and not yet listed. Skips and reports the rest.
- Single gun via MCP: `frappe_run_method` → `ffl_woo_sync.woocommerce.client_api.push_serial_now`, `kwargs={"serial_no": "<serial>"}`. Same call as the script.
- Product goes live immediately with price, stock 1, description, resized images (primary = featured), category, attributes.
- Canary first; batch in the background.

### 3b. push --channel gunbroker (list on GunBroker, per serial)

```bash
# Rehearse: point mcp/.env (or FIREARM_ENV) at the local dev site
uv run scripts/firearm_listings.py push --channel gunbroker --root "/path/..." --only ONE_SERIAL
```

Fixed-price Buy Now at the same `Serial No.sell_price`, reusing the photos and description `attach` already wrote. Success writes `Serial No.gb_item_id`, which the script uses to skip already-listed guns (same idempotency as `woo_product_id`).

**Confirm which GunBroker you are hitting first:** `gb_test_connection` via MCP and read the `sandbox` field.

**Non-local sites are refused** (safety rail 6). To hit production: ask the user, set `FIREARM_ALLOW_PROD=1` explicitly, and canary one gun:

```bash
FIREARM_ALLOW_PROD=1 uv run scripts/firearm_listings.py push \
    --channel gunbroker --root "/path/..." --only ONE_SERIAL
```

Then check the printed `gb_item_id` (or `verify --channel gunbroker`) and show the user the live `gb_url` before batching.

**Ending a listing is not in this script:** use MCP `gb_end_listing(serial_no, confirm=true)`. Read `confirmed`, not `ok`. `confirmed=false` always comes with `pending_manual` and `gb_url`: the gun can still be bought and someone must end it on the GunBroker site.

Two current limitations:

- **Counter sales do not auto-end GunBroker listings yet** (the `serial_channel_exit` hook lands in PR-2/PR-3). Until then, a gun sold at the counter must be ended manually with `gb_end_listing`, or it can sell twice.
- **`GUNSTORE_MCP_GUNBROKER_ACTIONS=1` is a deployment prerequisite.** `gb_push_serial` and `gb_end_listing` share this switch; without it the MCP does not register either (`gb_test_connection` / `gb_listing_status` are always present). "I don't have that tool" means this machine cannot end listings — a human must click **End Item Early** on GunBroker. An instance that can list but not end is the most dangerous configuration.

Skip reasons are printed verbatim from the guard (`{"ok": false, "skipped": ..., "message": ...}`): unpriced, not Active, already listed, reserved by another channel (a Woo order exists). Retrying does not change them.

### 4. verify

```bash
uv run scripts/firearm_listings.py verify --root "/path/..." [--only ...] [--channel woo|gunbroker]
```

Prints per serial: gallery primary alignment, `title` (= `Serial No.item_name`), and the channel's listing id (`woo_product_id` by default, `gb_item_id` with `--channel gunbroker`). On the Woo side, confirm only your serials were listed (not all siblings), and that title / price / status / featured image are right with no duplicates: wp-admin SKU search `item_code::serial`, WC REST, or osa-seo `woocommerce-products-list` (`search_sku "<item_code>::"`).

### 5. New Arrivals email (ask; send only on explicit yes)

Once `verify` passes the guns are buyable. Ask the user whether to email subscribers — the listing flow is not finished until this question is asked.

Show facts first, read-only:

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals preview'
```

It prints which products the email will include (order, price, real photo or placeholder), the subject line, and the recipient count. Then ask along these lines:

> The 6 guns just listed are queued for the New Arrivals email. It would go to 151 subscribers with subject `6 new arrivals just hit the floor`, sorted by price: <list>.
> **Send now?** Or: send a test to your inbox / change the subject / skip this batch (clear the queue).

Only after an explicit "send":

```bash
ssh oldsteel 'cd ~/domains/oldsteelarsenal.com/public_html && wp osa-growth new-arrivals send --yes'
```

`--yes` skips the plugin's own prompt (no TTY over SSH), not the user's confirmation.

- No explicit yes → do not run `send`. "Let me look first", "later", "after I fix the title" all mean stop.
- User wants to see it → `test --to=<their address>` (real email, queue and stats untouched).
- Only some guns → `send --only=CZ85::7408H,M1-30::4711 --yes` (accepts SKUs).
- Custom subject → `send --subject="…" --yes`.
- Not worth announcing (re-attach, price fix, re-push, test products) → `clear --yes`, or they leak into the next email.
- Report `Sent N, failed N` verbatim, plus any "N product(s) stay queued".

One email holds at most 8 products; the rest stay queued for the next send. Full command reference and pitfalls: `references/new-arrivals-email.md`.

## Why resize

Woo sideloads every gallery image from the POS server when creating a product, and the POS→Woo client has a hard 30 s timeout. Phone originals (3–4 MB, 4000 px) push a few images past 30 s → `ReadTimeout` 500, no product (and possibly an orphan or duplicate). At ~2000 px / q80 (~500–800 KB) sideload takes seconds. Resize on `attach`; never upload originals and fix later.

More in `references/internals.md`: fields, code paths, `push_serial_now` return value, timeout diagnosis, orphan check, cleanup of superseded originals.
