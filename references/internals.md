# Internals & gotchas — firearm-listing-import

Deeper reference for the workflow in `../SKILL.md`. Read when a step misbehaves
or you need exact field names / code paths.

## Where the data lives

**Serial No** (per physical gun) custom fields — defined in `ffl_core`:
- `image` (Attach Image, label "Photo") — single featured photo
- `image_gallery` (Table → `Item Image Gallery`: `image`, `is_primary`, `sort_order`, `caption`)
- `description` (Text, plain — newlines preserved; Woo renders them as `<br>`)
- `sell_price` (Currency) — per-serial tag price; `woo_product_id`, `woo_delisted`, `woo_reserved`, `status`

**Serial No `item_name` (the per-gun Woo title)** — a *standard* ERPNext field (not a custom field), `fetch_from: item_code.item_name`, `fetch_if_empty: 1`, `read_only: 1`. Normally it mirrors the parent `Item.item_name` (the shared model name). Writing a per-gun value via REST:
- **persists** — `fetch_if_empty=1` means the item_name fetch only fires when the field is empty, so a non-empty title we write is never overwritten on later saves (including edits to the parent Item).
- **is allowed over REST** — `read_only` only disables the Desk form input; `PUT /api/resource/Serial No/<serial>` sets it fine (it's permlevel 0).
- **survives the Woo write-back** — `push_serial_now` writes `woo_product_id` via `frappe.db.set_value` (a direct DB update, no `doc.save()`), so it never triggers a fetch that could clobber the title.

So `attach` sets it from the description's `Title:` line, and `settitle` sets it standalone. The title takes effect on the next `push` (the payload is rebuilt from the live Serial No each push).

**Item** (`ffl_core` custom fields) also has `image_gallery` + `image` + `description`, but those are for **uniform-price / non-firearm** items. Per-serial firearms (`is_firearm=1`, `uniform_sell_price=0`) keep per-gun data on the Serial No. One Item backs many serials → never put a single gun's photos on the Item.

Find serials for an item: `firearms_in_stock` report or
`frappe_list_documents("Serial No", filters=[["item_code","=",CODE]])`.

## File upload over REST (why not the MCP)

The MCP can't carry image bytes — base64-ing photos through tool calls explodes
context. Upload directly: `POST {BASE}/api/method/upload_file` (multipart:
`file`, `is_private=0`, `doctype=Serial No`, `docname=<serial>`) → returns
`message.file_url`. Frappe also auto-suffixes duplicate filenames with a hash
(many folders have `main.jpg` → `main5a0fd7.jpg`); harmless, each gallery row
references its own URL.

Set the gallery: `PUT {BASE}/api/resource/Serial No/<serial>` with
`{"description","image","image_gallery":[rows]}`. Sending the full list replaces
the child table. (The doctype name has a space → URL-encode `Serial%20No`.)

## Woo push routing

- Whitelisted per-serial entrypoint: `ffl_woo_sync.woocommerce.client_api.push_serial_now(serial_no)` → `{"ok":true,"woo_product_id":"<id>","raw":<WC>}`. Idempotent: writes back `woo_product_id`, so re-running updates instead of creating.
- `client_api.push_item_now(item_code)` (what MCP `woo_push_item` calls) routes per-serial firearms to **all** Active serials of that item_code — too broad for a curated import.
- Payload builder: `ffl_woo_sync.woocommerce.mapping.serial_to_product_payload` → product `name` from `Serial No.item_name` (falls back to `Item.item_name`, then `item_code`), `status:"publish"`, `regular_price` from `ffl_core.firearm.serial_sell_price` (uniform→Item.standard_rate, else Serial.sell_price; **0 → "0.00"**), SKU `item_code::serial`, stock 1, images from `_resolve_image_list` (is_primary row first = WC featured). The `name`-from-serial rule is the whole reason `attach` writes `item_name`: without a per-gun title every serial of one model lists under the same name. (Uniform-price firearms ignore the serial title — they share one item-level product, so their title always comes from `Item.item_name`.)

## The 30s sideload timeout (root cause of push failures)

`ffl_woo_sync.woocommerce.client.py` builds the WC client with
`timeout = WooCommerce Settings.request_timeout_seconds or 30`. On `create_product`
WooCommerce sideloads each image URL from the POS server; full-size photos make
that exceed 30s → `requests.exceptions.ReadTimeout` surfaced as a 500 from
`ffl_woo_sync (app)`. Two fixes, prefer the first:
1. **Resize images** (the skill's default) — keeps each photo small so sideload
   finishes fast. Best for page speed + bandwidth anyway.
2. Bump `request_timeout_seconds` in WooCommerce Settings (only if you truly need
   full-res on Woo). Resizing is still recommended.

## Verifying & cleaning up

- Orphan / duplicate check after a timed-out push: `osa-seo` MCP
  `woocommerce-products-list` `search_sku "<serial>"` — a timed-out create may or
  may not have actually created the product. `woo_product_id` unset on the Serial
  No means the write-back didn't complete; confirm on the WC side before retrying
  to avoid duplicates.
- Per-serial scope check: `search_sku "<item_code>::"` should return only the
  serials you imported (plus any pre-existing listings from before).
- Gallery primary integrity: `verify` subcommand. If `is_primary` drifts off the
  main photo (observed once after an interrupted/timed-out request), re-PUT the
  gallery with the sort-0 row flagged primary.
- **Old full-size originals:** if photos were uploaded full-size before resizing
  (or you re-attach with `--force`), the superseded Files stay attached to the
  Serial No but unreferenced. To reclaim space, find them via
  `frappe_list_documents("File", filters=[["attached_to_doctype","=","Serial No"],["attached_to_name","=",serial]])`
  and delete the ones whose `file_url` is no longer in the gallery. Capture old
  URLs before replacing so the delete is targeted (don't delete blindly).

## Serial name resolution quirks

Folder name often ≠ Serial No record name:
- case: `7408h` → `7408H` (MySQL collation is case-insensitive, so a `name=`
  filter still matches — the script resolves to the canonical name)
- prefix: `14078` → `N14078`
- wrong/changed serial: `CR1159` → actual `SYA1139` (only the user knows — ask)
Pass these via `--map` JSON `{folder: serial}`. The `resolve` subcommand flags
`UNRESOLVED` for any folder you still need to map.
