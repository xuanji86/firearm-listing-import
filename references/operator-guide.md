# Operator Guide (no technical background needed)

Step-by-step guide for the person at the shop: first-time sign-in, listing guns, checking the result in WooCommerce. Every step can be done by the AI assistant (Claude Code or Codex). Where you see `👉 Tell the assistant:`, say that — no commands to type.

Note for the assistant: when the user is non-technical, new, needs to sign in for the first time, or says "walk me through it", follow this guide section by section, run the commands yourself, report each result in plain language, and ask before continuing. Prefer `scripts/firearm_listings.py` (works on any agent) over agent-specific tools.

---

## What it does

Takes a folder on your computer with one subfolder per gun (photos + a description), writes that onto the matching gun in the POS, and lists each gun as its own product on oldsteelarsenal.com.

## Before you start

1. The photo folder (format in Part 2).
2. Every gun to be listed has a **sell price** in the POS (unpriced guns are skipped and reported).
3. The project is installed and the AI assistant is open.

---

## Part 1: Sign in to the POS (one-time)

No key to copy. You sign in with your own POS account, the same one you use at the counter.

1. 👉 Tell the assistant: **"Sign in to the POS for listing"** (CGA: say "the CGA POS").
   It runs `login https://pos.oldsteelarsenal.com` (CGA: `https://pos.caligunsandammo.com`) and a browser page opens.
2. Sign in to the POS if asked, then click **Allow**. The page says "Signed in — you can close this tab."
3. Connect the assistant's POS tools the same way: 👉 **"Connect the gunstore-pos connector"**. A browser page opens; sign in and click **Allow**.
4. Verify: 👉 **"Test the POS and WooCommerce connections"**. It shows your name; "OK" means setup is done.

Everything you do is recorded under your name in the POS. If the assistant later says the POS session expired, repeat step 1.

---

## Part 2: Organize the photo folder

- **One subfolder per gun**, named after the gun's **serial number** (match the POS as closely as you can).
- Inside each subfolder:
  - **One description file** `description.txt`. Include a line `Title: <listing title>` (usually in the `Specifications` block). That becomes the gun's own product title on the store; the tool removes the line from the description body. Without it the gun is listed under the shared model name (every gun of that model gets the same title).
  - **Photos**: `.jpg` / `.jpeg` / `.png` / `.webp` / `.heic`.
- Name the **primary photo** (the one customers see first) **`main.jpg`** (any case). Without it, the first photo by filename is used. It must be a **landscape** shot (wider than tall) with the gun the right way up — barrel level, sights on top. A sideways or upside-down photo is turned by the assistant before upload (a copy of the original stays in the folder as `.orig`); a genuinely vertical composition that no turn can fix is refused for the main photo and needs a different shot.

Example `description.txt` (the `Title:` line can be anywhere; everything else is plain text shown to the customer as-is):

```
Type 38 Arisaka training rifle configured as a smoothbore blank-firing training gun. ...

Specifications
Title: Type 38 Arisaka Training Rifle w/ Bayonet - Smoothbore Blank-Fire
Manufacturer: Japanese production
Model: Type 38 Arisaka Training Rifle
Action: Bolt-action
```

```
my gun photos/
├── 2695v/
│   ├── description.txt
│   ├── main.jpg          ← primary
│   ├── IMG_1301.jpg
│   └── IMG_1302.jpg
└── 855242/
    ├── description.txt
    ├── main.jpg
    └── ...
```

Phone originals are fine — the tool resizes them (originals are too large and time out on the store).

---

## Part 3: Confirm prices

Each gun needs a **Sell Price** in the POS or it lists at **$0.00**.

- Set it on the gun (by serial) in the POS, or
- 👉 **"Set the sell price of serial 2695v to 3989"**.
- Unpriced guns are **skipped** at the listing step and reported back to you. Price them and ask again.

---

## Part 4: List the guns

👉 **"Use firearm-listing-import to update the guns from `/Users/me/Desktop/my gun photos` and list them on woocommerce"**

The assistant will:

1. **Resolve** — show which folder maps to which gun, with price, photo count, and any problems. Nothing is written.
2. Wait for your **OK**.
3. **Canary** — process the first gun only, for you to check.
4. Wait for your **OK** again, then process the rest.
5. When done, **ask whether to send the New Arrivals email** (Part 6).

If a folder name does not match a serial (`UNRESOLVED`), the assistant asks you for the correct serial.

> Terminal users: `resolve` → `attach` → `push` → `verify`, each with `--root "<folder>"`, run as `uv run scripts/firearm_listings.py <command> …` (never bare `python`). Details in SKILL.md.

---

## Part 5: Check the result in WooCommerce

1. Open **https://oldsteelarsenal.com/wp-admin** and log in.
2. **Products → All Products**.
3. Search the **serial number** (SKU is `model::serial`, e.g. `P08-9MM::2695v`).
4. Open the product and check:
   - **Status = Published**
   - **Title** is the gun's `Title:` from the description, not the generic model name. To fix: 👉 **"Change the title of serial X to …"** (uses `settitle`, then re-lists).
   - **Price**
   - **Product image** is the `main` photo; **gallery** has the rest.
   - **Description**
   - **Stock**: In stock, quantity 1.

**Temporarily hide a gun:** open the product → set **Status** to **Draft** → **Update**.

**Wrong price or photos:** POS is the source of truth; do not fix it only on the store (the next sync may overwrite it).

- Wrong price → fix the sell price in the POS → 👉 **"Re-list serial X"** (updates the existing product, no duplicate).
- Wrong photos → fix the local folder → 👉 **"Re-attach photos for serial X with --force, then re-list"**.

---

## Part 5b: GunBroker (third sales channel)

GunBroker is an auction marketplace, a second storefront next to the website. The same photos, description, and POS sell price are reused — Parts 2 and 3 need no repeating.

**Currently in trial: only the local test site is allowed.** If the assistant answers "REFUSED", the guard is working as intended. Do not work around it.

**Why more care than the website:** a wrong website listing becomes invisible as a Draft. A GunBroker listing is a real gun on a public marketplace; a buyer can commit immediately, and ending it early needs a human on the GunBroker site. So: **one gun at a time (canary)**, check the live page, then the next.

👉 **"List serial X on GunBroker"** (one gun, not "all"). Open the returned listing link and check title, price, photos, description.

### A GunBroker-listed gun sold at the counter

The POS ends the listing by itself when the gun is sold at the counter, and refuses the sale if GunBroker already sold it. Check that it is gone; if it is still up:

👉 **"End the GunBroker listing for serial X"**

The only proof is that you can no longer find the listing on GunBroker. All of these mean **not ended** — log in to GunBroker and click **End Item Early** on the listing:

| Assistant says | Meaning |
|---|---|
| "pending_manual" or "the gun can still be bought" | It tried and failed |
| "I don't have that tool" / no `gb_end_listing` | GunBroker write tools are off on this machine; it did not try |
| Error, timeout, or anything unclear | Treat as not ended |

> The GunBroker tools are on the store's full connector when that store has GunBroker switched on in the POS. "I don't have that tool" means you are on the read-only connector or GunBroker is off for this store.

A gun sold at the counter and still on GunBroker can be sold twice.

### "Skipped" is not an error

- **Unpriced** → set the sell price (Part 3).
- **Already listed** → it is already on GunBroker.
- **Reserved by another channel** → a website order exists for it; wait for that order.

---

## Part 6: New Arrivals email

Subscribers to new-arrival alerts can get a digest with the guns just listed, with photos.

**Nothing is sent automatically.** The assistant asks; it sends only when you say so.

1. After listing, the assistant tells you which guns, the subject line, and how many recipients.
2. You can say:
   - 👉 **"Send it"**
   - 👉 **"Send a test to my inbox first"** — one real email to you only, nothing else changes.
   - 👉 **"Change the subject to '…'"**
   - 👉 **"Only include these serials: …"**
   - 👉 **"Don't send for this batch"** — e.g. photo fixes, price changes, re-lists. Say it, or those guns end up in the next email.

- One email holds at most **8 guns**. List 20 → the first email has 8, the remaining 12 stay queued for another send.
- Guns without photos show a grey placeholder. The assistant flags them; add photos first.

---

## Quick phrases

| Situation | 👉 Tell the assistant |
|---|---|
| First-time setup | "Sign me in to the POS and walk me through it" |
| Check the plan only | "Resolve `<folder>` without writing anything" |
| List everything | "Update and list the guns in `<folder>` on woo" |
| Unpriced (`UNPRICED $0`) | "Set the sell price of serial X to 1234, then list it" |
| Wrong / missing title (`NO-TITLE`) | "Change the title of serial X to '…' and re-list" |
| Serial mismatch (`UNRESOLVED`) | "Folder CR1159 is actually serial SYA1139" |
| Check after listing | "Confirm these guns are listed correctly on woocommerce" |
| GunBroker (trial) | "List serial X on GunBroker" (one at a time) |
| GunBroker "REFUSED" | Guard working; do not bypass |
| "pending_manual" / no `gb_end_listing` tool | Log in to GunBroker and End Item Early; tell the deployer to set `GUNSTORE_MCP_GUNBROKER_ACTIONS=1` |
| Hide a gun | Set Status to Draft in wp-admin, or ask the assistant to delist |
| Send the email | "Send the new arrivals email to subscribers" |
| Preview the email | "Send a test new arrivals email to my inbox" |
| Skip the email | "No new arrivals email for this batch, clear the queue" |

---

## Safety

- This edits the **live store**. The assistant shows the plan and does one gun first; when unsure, stop and ask.
- GunBroker is restricted to the test site. "REFUSED" is the guard; do not bypass it.
- Never share your POS password. If a laptop is lost, an administrator revokes its session in the POS (OAuth Bearer Token list).
