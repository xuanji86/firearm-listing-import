#!/usr/bin/env python3
"""firearm_listings.py — attach per-gun photos + descriptions from local
"with pictures" folders to Serial No records on the POS, and (optionally)
publish each as its own WooCommerce product.

Each subfolder under --root is named after a firearm serial number and holds
ONE description .txt plus several photos. Photos go on the **Serial No** record
(image / image_gallery / description) — never the Item (a shared model SKU).

Subcommands (run `resolve` first — it is read-only):
  resolve --root DIR [--map map.json]
      Read-only plan: folder -> Serial No, status, price, photo/primary, flags.
  attach  --root DIR [--map map.json] [--only A,B] [--force]
      Resize photos (~2000px/q80) -> upload to POS -> set image_gallery +
      image (primary) + description + item_name (the per-gun WooCommerce listing
      title, taken from the description's "Title:" line). Skips serials that
      already have a gallery unless --force. Resizing is mandatory (see push
      timeout note below).
  push    --root DIR [--map map.json] [--only A,B] [--channel woo|gunbroker]
      Publish each priced + Active + not-yet-listed serial as its own listing
      via the channel's whitelisted push_serial_now. Skips un-priced ($0) and
      already-listed. --channel gunbroker is refused against a non-local POS
      unless FIREARM_ALLOW_PROD=1 (see "the production gate" below).
  verify  --root DIR [--map map.json] [--only A,B] [--channel woo|gunbroker]
      Show gallery primary integrity + that channel's listing id per serial.
  testconn
      Connectivity + auth check against the POS (use instead of any MCP
      "test connection" tool — works on any agent via the shell).
  setprice --serial S --price N
      Set a serial's sell_price over REST (so price-setting needs no MCP).
  settitle --serial S --title "..."
      Set a serial's per-gun WooCommerce title (Serial No.item_name) over REST,
      e.g. to fix a title without re-running attach. Re-push to take effect.

--map is a JSON object of {folder_name: actual_serial_no} for folders whose name
differs from the Serial No record (case/prefix/typo, or a corrected serial).

Credentials + target site come from mcp/.env (FRAPPE_BASE_URL/API_KEY/API_SECRET).
NOTE: that .env points at PRODUCTION. attach/push are live writes.

Channels: `push`/`verify` take --channel woo (default) or gunbroker. Both call a
whitelisted method on the POS, which owns the credentials — and for GunBroker
owns the sandbox-vs-live choice too (GunBroker Settings.sandbox_mode). This
script cannot select an environment; pointing it at dev.localhost is how you
rehearse. GunBroker pushes against a non-local site are refused unless
FIREARM_ALLOW_PROD=1 is set explicitly: a stray Woo push can be unpublished, a
stray GunBroker listing can be bought.

Portable by design: pure Python + Frappe REST, no agent-specific tools — runs the
same under Claude Code or Codex. Run with `uv run` (auto-installs the deps via
the inline metadata below) or the repo venv `mcp/.venv/bin/python`; a bare
`python` will fail (the system interpreter has no `requests`/`pillow`).
"""
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31,<3", "pillow>=10,<13", "pillow-heif>=0.16,<1"]
# ///
from __future__ import annotations
import argparse, json, os, re, sys, tempfile
from urllib.parse import quote, urlparse

# Windows pipes/files default to the ANSI code page in strict mode: a gun title
# outside cp1252/cp936 would abort the batch with UnicodeEncodeError.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
import requests

def _find_env():
    """Walk up from this script to the repo root (the dir containing mcp/.env).

    Uses realpath so this works when the skill is invoked through a symlink
    (e.g. Codex loading it from ~/.codex/skills/ -> the repo). Override with the
    FIREARM_ENV env var if the layout differs (e.g. the skill was copied, not
    symlinked, onto a machine where the repo lives elsewhere)."""
    if os.environ.get("FIREARM_ENV"):
        return os.environ["FIREARM_ENV"]
    return _find_env_from(os.path.dirname(os.path.realpath(__file__)))  # realpath: resolve symlinks


def _find_env_from(d):
    """Walk up from `d` looking for mcp/.env. Split out so the loop is testable."""
    while True:
        cand = os.path.join(d, "mcp", ".env")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:  # filesystem root ("/" or "C:\\") — stop, don't spin
            break
        d = parent
    raise FileNotFoundError(
        "mcp/.env not found above script. If this skill was copied (not symlinked) "
        "outside the repo, set FIREARM_ENV=/path/to/gunstore-pos/mcp/.env")


ENV = _find_env()
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAXPX, QUALITY = 2000, 80          # resize target: long edge px, JPEG quality
# Sales channels `push`/`verify` can drive. Each is (whitelisted POS method,
# the Serial No field holding that channel's listing id, HTTP timeout seconds).
# Both go through the POS: it owns the credentials and, for GunBroker, the
# sandbox-vs-live choice (`GunBroker Settings.sandbox_mode`). This script never
# talks to WooCommerce or GunBroker directly.
PUSH_CHANNELS = {
    "woo": ("ffl_woo_sync.woocommerce.client_api.push_serial_now",
            "woo_product_id", 240),
    "gunbroker": ("ffl_integrations.gunbroker.client_api.push_serial_now",
                  "gb_item_id", 180),
}
DEFAULT_CHANNEL = "woo"
# Opt-in required to push to GunBroker from a non-local site. Exactly "1" —
# anything else is a no, so a typo fails closed.
ALLOW_PROD_ENV = "FIREARM_ALLOW_PROD"
# Per-gun WooCommerce title: a "Title: ..." line in the description .txt (colon may
# have no following space; matched anywhere, line-anchored, case-insensitive).
TITLE_RE = re.compile(r"\s*title\s*:\s*(.+?)\s*$", re.I)
# A "Key: value" spec line (Manufacturer: ..., Country of origin: ...). Never
# merged with its neighbours by unwrap_paragraphs — each one is its own line on
# the store. Key = 1–3 capitalised-start words, so wrapped prose that happens to
# carry a colon ("is near excellent: the stock ..." / "Chamberings followed the
# customer: ...") is still prose.
KV_RE = re.compile(r"\s*[A-Z][\w/&'-]*(?:[ \t]+[\w/&'-]+){0,2}:\s")
# A bare heading such as "Specifications": 1–3 words, no punctuation at all.
HEADING_RE = re.compile(r"\s*[A-Za-z][\w&/-]*(?:[ \t]+[\w&/-]+){0,2}\s*$")
# Sentence-final characters. A prose line that ends with none of these was cut
# mid-sentence by a hard wrap.
SENTENCE_END = tuple(".!?:;\"'\u201d\u2019)")


def load_cfg():
    cfg = {}
    with open(ENV, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = load_cfg()
BASE = CFG["FRAPPE_BASE_URL"].rstrip("/")
H = {"Authorization": f"token {CFG['FRAPPE_API_KEY']}:{CFG['FRAPPE_API_SECRET']}"}


# --- the production gate ---------------------------------------------------

def _is_local_base(url):
    """True only for a POS running on this machine.

    Compares the parsed hostname, never a substring of the URL: `in` would let
    `dev.localhost.example.com` read as local, and it would also be fooled by
    userinfo (`http://localhost@evil.example.com/` — the real host is
    evil.example.com). `.localhost` is a reserved TLD (RFC 6761) and cannot be
    registered, so the suffix is safe to trust.

    A URL we cannot parse a hostname out of returns False, i.e. it is treated
    as production. Fail closed: the cost of being wrong in the other direction
    is a real gun on a real marketplace."""
    # rstrip("."): "dev.localhost." is the fully-qualified spelling of the same
    # host, so refusing it would be a false alarm, not extra safety.
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return (host in ("localhost", "127.0.0.1", "::1")
            or host.endswith(".localhost"))


def _prod_gate_blocks(channel):
    """Refuse a GunBroker push against a non-local POS unless told otherwise.

    mcp/.env points at PRODUCTION, and this script is normally driven by an
    agent working through a folder of guns. On Woo a mistaken push publishes a
    product that can be unpublished; on GunBroker it puts a firearm up for sale
    on a public marketplace where a buyer can commit before anyone notices, and
    ending a listing early is a manual chore on GunBroker's own site. So the
    GunBroker channel is local-only until a person says otherwise out loud.

    Returns True (and explains itself) when the push must not happen."""
    if channel != "gunbroker" or _is_local_base(BASE):
        return False
    if os.environ.get(ALLOW_PROD_ENV) == "1":
        print(f"!! {ALLOW_PROD_ENV}=1 — pushing to GunBroker via {BASE}.\n"
              "   These are real listings on the live marketplace.\n")
        return False
    print(f"REFUSED — push --channel gunbroker against {BASE}\n"
          f"\n"
          f"  That site is not local, so this would list real firearms on the\n"
          f"  live GunBroker marketplace. Sandbox vs live is decided by\n"
          f"  GunBroker Settings.sandbox_mode on the POS, not by this script,\n"
          f"  so pointing at production is the whole risk.\n"
          f"\n"
          f"  Point mcp/.env (or FIREARM_ENV) at the dev site to rehearse:\n"
          f"      http://dev.localhost:8000\n"
          f"\n"
          f"  If you really mean production, say so explicitly:\n"
          f"      {ALLOW_PROD_ENV}=1 uv run scripts/firearm_listings.py push \\\n"
          f"      (PowerShell: $env:{ALLOW_PROD_ENV}=1 ; uv run ...)\n"
          f"          --channel gunbroker --root ... --only ONE_SERIAL\n"
          f"\n"
          f"  Ask the user first, and go one gun at a time.")
    return True


# --- helpers ---------------------------------------------------------------

def _res(serial):
    return f"{BASE}/api/resource/Serial No/{quote(serial, safe='')}"


def _serial_names(filt):
    """GET Serial No names matching a Frappe filter; raise on HTTP/auth error so
    a 401/500 surfaces clearly instead of masquerading as UNRESOLVED."""
    r = requests.get(f"{BASE}/api/resource/Serial No", headers=H,
                     params={"filters": json.dumps(filt),
                             "fields": json.dumps(["name"])}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def resolve_serial(folder, overrides):
    """Folder name -> (canonical Serial No name or None, how).

    how is 'override' | 'exact' | 'fuzzy' | None. A 'fuzzy' result came from a
    single LIKE %folder% match and is NOT trusted for writes: callers that mutate
    (attach/push/setprice) refuse it and ask for an explicit --map entry — binding
    one gun's photos/price to the wrong serial on the live store is costly.
    """
    cand = overrides.get(folder, folder)
    rows = _serial_names([["name", "=", cand]])  # exact (case-insensitive collation)
    if rows:
        return rows[0]["name"], ("override" if folder in overrides else "exact")
    if folder in overrides:
        return None, None  # explicit override given but not found — surface it
    rows = _serial_names([["name", "like", f"%{folder}%"]])
    if len(rows) == 1:
        return rows[0]["name"], "fuzzy"
    return None, None


def get_serial(name):
    r = requests.get(_res(name), headers=H, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def is_main(n):
    n = n.lower()
    return n.startswith("main.") or n.startswith("mian.")  # 'mian' = seen typo


def list_images(fp):
    files = [f for f in os.listdir(fp) if os.path.splitext(f)[1].lower() in IMG_EXT]
    return sorted([f for f in files if is_main(f)], key=str.lower) + \
           sorted([f for f in files if not is_main(f)], key=str.lower)  # primary first


def pick_desc(fp):
    txts = [f for f in os.listdir(fp) if f.lower().endswith(".txt")]
    if not txts:
        return None
    for t in txts:
        if t.lower() == "description.txt":
            return os.path.join(fp, t)
    return os.path.join(fp, txts[0])


def split_title(text):
    """(title, body) for a description .txt.

    title = the value of the first 'Title:' line (the per-gun WooCommerce listing
    title), or None if the file has no such line. body = the text with that line
    removed so the title isn't duplicated inside the product description. Other
    lines (incl. the rest of the Specifications block) are kept verbatim."""
    title, kept = None, []
    for line in text.splitlines():
        m = TITLE_RE.match(line)
        if title is None and m and m.group(1).strip():
            title = m.group(1).strip()
            continue  # drop the Title: line from the customer-facing description
        kept.append(line)
    return title, unwrap_paragraphs("\n".join(kept).strip())


def unwrap_paragraphs(body):
    """Merge hard-wrapped prose back into one line per paragraph.

    The POS→Woo payload turns EVERY newline in Serial No.description into <br>,
    so a description.txt wrapped at ~90 columns (text pasted from a terminal or
    an editor with hard wrap) shows sentences cut mid-line on the store.

    A block (lines between blank lines) counts as wrapped when it holds at least
    two prose lines and one of them does not end a sentence. Inside a wrapped
    block a prose line is glued onto the previous one with a space, except:
    "Key: value" spec lines and bare headings ("Specifications") always keep
    their own line, and a sentence-ending line clearly shorter than the block's
    wrap width is the end of a paragraph, so the next line starts fresh. A file
    already written one paragraph per line comes back unchanged.
    # lazy: pure heuristics; a paragraph whose every wrapped line ends in a
    # period is left alone, and a 1–3 word line with no punctuation is taken for
    # a heading. Add an explicit marker to the file format if either bites."""
    def own_line(l):
        return KV_RE.match(l) or HEADING_RE.match(l)

    out = []
    for block in re.split(r"\n[ \t]*\n", body):
        lines = [l.strip() for l in block.split("\n")]
        prose = [l for l in lines if l and not own_line(l)]
        wrapped = len(prose) >= 2 and any(not l.endswith(SENTENCE_END) for l in prose)
        width = max((len(l) for l in prose), default=0)
        merged, last = [], ""   # last = the previous ORIGINAL line, pre-merge
        for l in lines:
            glue = (wrapped and merged and l and last
                    and not own_line(l) and not own_line(last)
                    and not (last.endswith(SENTENCE_END) and len(last) < 0.7 * width))
            if glue:
                merged[-1] += " " + l
            else:
                merged.append(l)
            last = l
        out.append("\n".join(merged))
    return "\n\n".join(out)


def resize(src, dst):
    """Long edge -> MAXPX, JPEG q=QUALITY. Pillow rather than macOS `sips`, so a
    listing run behaves the same on Windows/Linux/macOS.

    Two deliberate differences from the old `sips -s format jpeg -Z 2000`:
    photos smaller than MAXPX are left alone (sips upscaled them, which invents
    no detail and only grows the upload), and EXIF rotation is baked into the
    pixels (sips kept the orientation tag, so a stripped tag meant a sideways
    gun on the store)."""
    from PIL import Image, ImageOps          # imported here so the stdlib-only
    from pillow_heif import register_heif_opener  # test run needs no Pillow
    register_heif_opener()  # .heic is in IMG_EXT; Pillow needs this plugin to open it
    with Image.open(src) as im:
        out = ImageOps.exif_transpose(im)  # bake in phone rotation; the JPEG save drops EXIF
        out.thumbnail((MAXPX, MAXPX))      # aspect kept, never upscales
        out.convert("RGB").save(dst, "JPEG", quality=QUALITY, optimize=True)


def upload(serial, path):
    with open(path, "rb") as fh:
        r = requests.post(f"{BASE}/api/method/upload_file", headers=H,
                          files={"file": (os.path.basename(path), fh, "image/jpeg")},
                          data={"is_private": "0", "doctype": "Serial No", "docname": serial},
                          timeout=180)
    r.raise_for_status()
    return r.json()["message"]["file_url"]


def folders(root):
    return sorted([d for d in os.listdir(root)
                   if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")], key=str.lower)


def targets(args):
    only = set(args.only.split(",")) if getattr(args, "only", None) else None
    for f in folders(args.root):
        if only and f not in only:
            continue
        yield f


# --- subcommands -----------------------------------------------------------

def cmd_resolve(args):
    ov = json.load(open(args.map, encoding="utf-8")) if args.map else {}
    print(f"BASE={BASE}\n")
    print(f"{'FOLDER':<14}{'SERIAL':<14}{'STATUS':<10}{'PRICE':<8}{'#img':<5}{'PRIMARY':<22}{'FLAGS':<26}TITLE")
    seen_items = {}
    for f in targets(args):
        fp = os.path.join(args.root, f)
        serial, how = resolve_serial(f, ov)
        imgs = list_images(fp)
        primary = imgs[0] if imgs else "(none)"
        flags = []
        status = price = "-"
        if not serial:
            flags.append("UNRESOLVED")
        else:
            if how == "fuzzy":
                flags.append("FUZZY?")  # substring match — confirm via --map before writing
            d = get_serial(serial)
            status = d.get("status")
            price = d.get("sell_price") or 0
            if status != "Active":
                flags.append(f"status={status}")
            if not price:
                flags.append("UNPRICED($0)")
            if d.get("image_gallery"):
                flags.append("has-gallery")
            if d.get("woo_product_id"):
                flags.append(f"woo#{d['woo_product_id']}")
            seen_items.setdefault(d.get("item_code"), []).append(serial)
        desc_path = pick_desc(fp)
        title = None
        if not desc_path:
            flags.append("NO-DESC")
        else:
            title, _ = read_desc(desc_path)
            if not title:
                flags.append("NO-TITLE")  # no 'Title:' line — Woo falls back to the shared Item name
        print(f"{f:<14}{(serial or '?'):<14}{str(status):<10}{str(price):<8}{len(imgs):<5}{primary:<22}{' '.join(flags):<26}{title or ''}")
    shared = {ic: ss for ic, ss in seen_items.items() if len(ss) > 1}
    if shared:
        print("\nNote — folders sharing one Item (fine: photos live per-serial):")
        for ic, ss in shared.items():
            print(f"  {ic}: {', '.join(ss)}")


def cmd_attach(args):
    ov = json.load(open(args.map, encoding="utf-8")) if args.map else {}
    print(f"BASE={BASE}  (LIVE writes)\n")
    tmp = os.path.join(tempfile.gettempdir(), "firearm_resized")
    for f in targets(args):
        fp = os.path.join(args.root, f)
        serial, how = resolve_serial(f, ov)
        if not serial:
            print(f"[{f}] UNRESOLVED — skip (add to --map)"); continue
        if how == "fuzzy":
            print(f"[{f}] FUZZY match -> {serial}; not trusted for writes — confirm via --map, then re-run"); continue
        try:
            d = get_serial(serial)
            if d.get("image_gallery") and not args.force:
                print(f"[{f} -> {serial}] SKIP — already has gallery (use --force)"); continue
            desc_path = pick_desc(fp)
            title, description = read_desc(desc_path)
            # item_name = the per-gun WooCommerce title (see references/internals.md):
            # serial_to_product_payload uses Serial No.item_name, falling back to the
            # shared Item name. Only send it when a 'Title:' line is present so we never
            # blank out an existing title for an old-format folder with no title.
            title_field = {"item_name": title} if title else {}
            if not title:
                print(f"  [{f} -> {serial}] NO-TITLE — no 'Title:' line; Woo keeps the shared Item name")
            imgs = list_images(fp)
            if not imgs:
                # No photos: set description (+ title) only — never blank out an existing image/gallery.
                requests.put(_res(serial), headers={**H, "Content-Type": "application/json"},
                             data=json.dumps({"description": description, **title_field}), timeout=120).raise_for_status()
                print(f"  [{f} -> {serial}] no photos — set description{' + title' if title else ''} only\n"); continue
            outdir = os.path.join(tmp, serial); os.makedirs(outdir, exist_ok=True)
            gallery, primary = [], None
            for i, name in enumerate(imgs):
                dst = os.path.join(outdir, os.path.splitext(name)[0] + ".jpg")
                resize(os.path.join(fp, name), dst)
                url = upload(serial, dst)
                if i == 0:
                    primary = url
                gallery.append({"image": url, "is_primary": 1 if i == 0 else 0, "sort_order": i, "caption": ""})
                print(f"    {name} -> {url}{'  [PRIMARY]' if i == 0 else ''}")
            r = requests.put(_res(serial), headers={**H, "Content-Type": "application/json"},
                             data=json.dumps({"description": description, "image": primary,
                                              "image_gallery": gallery, **title_field}), timeout=120)
            r.raise_for_status()
            print(f"  [{f} -> {serial}] set description + {len(gallery)} resized photos, primary set"
                  f"{', title=' + repr(title) if title else ''}\n")
        except Exception as exc:
            # Don't let one bad photo / transient 5xx abort the whole batch (matches cmd_push).
            print(f"  [{f} -> {serial}] ERROR — {exc} (skipped; may have left partial uploads)\n")
            continue


def cmd_push(args):
    channel = getattr(args, "channel", DEFAULT_CHANNEL)
    if _prod_gate_blocks(channel):
        return
    method, id_field, timeout = PUSH_CHANNELS[channel]
    ov = json.load(open(args.map, encoding="utf-8")) if args.map else {}
    print(f"BASE={BASE}  channel={channel}")
    if channel == "gunbroker":
        # Deliberately NOT "local" vs "LIVE". A local dev site holding
        # production credentials lists real guns: sandbox vs live is
        # GunBroker Settings.sandbox_mode on the POS, which this script can
        # neither read nor set. The refusal path already says so; saying
        # something friendlier here would contradict it.
        print("   Sandbox or live is decided by GunBroker Settings.sandbox_mode\n"
              "   on the POS — not by this script, and not by the URL above.\n"
              "   Check the `sandbox` field from gb_test_connection first.\n")
    else:
        where = "local" if _is_local_base(BASE) else "LIVE"
        print(f"   (publishes {where} products)\n")
    for f in targets(args):
        serial, how = resolve_serial(f, ov)
        if not serial:
            print(f"[{f}] UNRESOLVED — skip"); continue
        if how == "fuzzy":
            print(f"[{f}] FUZZY match -> {serial}; not trusted for writes — confirm via --map"); continue
        d = get_serial(serial)
        if d.get("status") != "Active":
            print(f"[{serial}] SKIP — status={d.get('status')}"); continue
        if not (d.get("sell_price") or 0):
            print(f"[{serial}] SKIP — unpriced ($0); set sell_price first"); continue
        if d.get(id_field):
            print(f"[{serial}] SKIP — already listed ({id_field}={d[id_field]})"); continue
        try:
            r = requests.post(f"{BASE}/api/method/{method}",
                              headers={**H, "Content-Type": "application/json"},
                              data=json.dumps({"serial_no": serial}), timeout=timeout)
            if not r.ok:
                print(f"[{serial}] HTTP {r.status_code} {r.text[:200]}"); continue
            m = r.json().get("message") or {}
            # A guard refusal is a normal answer, not an error: the POS returns
            # {"ok": false, "skipped": ..., "message": ...} rather than throwing,
            # so the reason survives. Print the reason — re-running won't change it.
            if m.get("skipped"):
                print(f"[{serial}] SKIP — {m['skipped']}: {m.get('message', '')}"); continue
            print(f"[{serial}] ok={m.get('ok')} {id_field}={m.get(id_field)}")
            for w in (m.get("warnings") or []):
                print(f"[{serial}]   warning: {w}")
        except Exception as exc:
            print(f"[{serial}] EXC {exc}")
            if channel == "gunbroker":
                # POST /Items is not idempotent and is never auto-retried
                # (spec §4.12). A read timeout most often means the listing WAS
                # created and the answer got lost, so "just run it again" — the
                # obvious reaction — is the one reaction that double-lists.
                print(f"[{serial}]   ^ the listing may already exist on GunBroker. "
                      f"Do NOT re-push:\n"
                      f"[{serial}]     check gb_listing_status first, or let the "
                      f"hourly sweep claim it by SKU.")


def cmd_verify(args):
    channel = getattr(args, "channel", DEFAULT_CHANNEL)
    _, id_field, _ = PUSH_CHANNELS[channel]
    ov = json.load(open(args.map, encoding="utf-8")) if args.map else {}
    for f in targets(args):
        serial, _ = resolve_serial(f, ov)
        if not serial:
            print(f"[{f}] UNRESOLVED"); continue
        d = get_serial(serial)
        g = d.get("image_gallery") or []
        prim = [r["image"] for r in g if r["is_primary"]]
        s0 = [r["image"] for r in g if r.get("sort_order") == 0]
        ok = prim and s0 and prim[0] == s0[0]
        print(f"[{serial}] {id_field}={d.get(id_field)} title={d.get('item_name')!r} "
              f"imgs={len(g)} primary={prim} {'OK' if ok else '*** MISMATCH'}")


def cmd_testconn(args):
    """Connectivity + auth check (no MCP needed — works on any agent via shell)."""
    r = requests.get(f"{BASE}/api/method/frappe.auth.get_logged_user", headers=H, timeout=15)
    who = r.json().get("message") if r.ok else r.text[:120]
    print(f"BASE={BASE}\nstatus={r.status_code}  logged_in_as={who}")
    print("OK" if r.ok else "FAILED — check FRAPPE_API_KEY/SECRET in mcp/.env")


def cmd_setprice(args):
    """Set Serial No.sell_price over REST (so price-setting needs no Frappe MCP)."""
    serial, how = resolve_serial(args.serial, {})
    if not serial or how == "fuzzy":
        print(f"[{args.serial}] UNRESOLVED or ambiguous — pass an exact serial"); return
    print(f"BASE={BASE}")
    r = requests.put(_res(serial), headers={**H, "Content-Type": "application/json"},
                     data=json.dumps({"sell_price": args.price}), timeout=30)
    r.raise_for_status()
    print(f"[{serial}] sell_price set to {args.price}")


def cmd_settitle(args):
    """Set the per-gun WooCommerce title (Serial No.item_name) over REST.

    For a per-serial firearm the Woo product title comes from Serial No.item_name
    (falling back to the shared Item name), so this overrides the model name with a
    per-gun title without re-running attach. Re-push the serial for it to take effect."""
    serial, how = resolve_serial(args.serial, {})
    if not serial or how == "fuzzy":
        print(f"[{args.serial}] UNRESOLVED or ambiguous — pass an exact serial"); return
    print(f"BASE={BASE}")
    r = requests.put(_res(serial), headers={**H, "Content-Type": "application/json"},
                     data=json.dumps({"item_name": args.title}), timeout=30)
    r.raise_for_status()
    print(f"[{serial}] item_name (Woo title) set to {args.title!r}")


def main():
    p = argparse.ArgumentParser(description="Attach firearm photos/descriptions and publish to Woo")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("resolve", "attach", "push", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--root", required=True, help="folder of <serial>/ subfolders")
        sp.add_argument("--map", help="JSON {folder: serial} overrides")
        sp.add_argument("--only", help="comma-separated folder names to limit to")
        if name == "attach":
            sp.add_argument("--force", action="store_true", help="re-attach even if gallery exists")
        if name in ("push", "verify"):
            sp.add_argument("--channel", choices=sorted(PUSH_CHANNELS),
                            default=DEFAULT_CHANNEL,
                            help=f"sales channel (default: {DEFAULT_CHANNEL}). "
                                 f"gunbroker needs {ALLOW_PROD_ENV}=1 off a local site")
    sub.add_parser("testconn")  # bare connectivity/auth check
    sp = sub.add_parser("setprice")
    sp.add_argument("--serial", required=True, help="serial number (or folder name)")
    sp.add_argument("--price", required=True, type=float, help="sell price, e.g. 1234")
    sp = sub.add_parser("settitle")
    sp.add_argument("--serial", required=True, help="serial number (or folder name)")
    sp.add_argument("--title", required=True, help="per-gun Woo listing title (Serial No.item_name)")
    args = p.parse_args()
    {"resolve": cmd_resolve, "attach": cmd_attach, "push": cmd_push, "verify": cmd_verify,
     "testconn": cmd_testconn, "setprice": cmd_setprice, "settitle": cmd_settitle}[args.cmd](args)


if __name__ == "__main__":
    main()
