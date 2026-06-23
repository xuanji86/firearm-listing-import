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
      image (primary) + description. Skips serials that already have a gallery
      unless --force. Resizing is mandatory (see push timeout note below).
  push    --root DIR [--map map.json] [--only A,B]
      Publish each priced + Active + not-yet-listed serial as its own WC product
      via the whitelisted push_serial_now. Skips un-priced ($0) and already-listed.
  verify  --root DIR [--map map.json] [--only A,B]
      Show gallery primary integrity + woo_product_id for each serial.
  testconn
      Connectivity + auth check against the POS (use instead of any MCP
      "test connection" tool — works on any agent via the shell).
  setprice --serial S --price N
      Set a serial's sell_price over REST (so price-setting needs no MCP).

--map is a JSON object of {folder_name: actual_serial_no} for folders whose name
differs from the Serial No record (case/prefix/typo, or a corrected serial).

Credentials + target site come from mcp/.env (FRAPPE_BASE_URL/API_KEY/API_SECRET).
NOTE: that .env points at PRODUCTION. attach/push are live writes.

Portable by design: pure Python + Frappe REST, no agent-specific tools — runs the
same under Claude Code or Codex. Run with `uv run` (auto-installs the one dep via
the inline metadata below) or the repo venv `mcp/.venv/bin/python`; a bare
`python` will fail (the system interpreter has no `requests`).
"""
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
from __future__ import annotations
import argparse, json, os, subprocess
from urllib.parse import quote
import requests

def _find_env():
    """Walk up from this script to the repo root (the dir containing mcp/.env).

    Uses realpath so this works when the skill is invoked through a symlink
    (e.g. Codex loading it from ~/.codex/skills/ -> the repo). Override with the
    FIREARM_ENV env var if the layout differs (e.g. the skill was copied, not
    symlinked, onto a machine where the repo lives elsewhere)."""
    if os.environ.get("FIREARM_ENV"):
        return os.environ["FIREARM_ENV"]
    d = os.path.dirname(os.path.realpath(__file__))  # realpath: resolve symlinks
    while d != "/":
        cand = os.path.join(d, "mcp", ".env")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    raise FileNotFoundError(
        "mcp/.env not found above script. If this skill was copied (not symlinked) "
        "outside the repo, set FIREARM_ENV=/path/to/gunstore-pos/mcp/.env")


ENV = _find_env()
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAXPX, QUALITY = 2000, 80          # resize target: long edge px, JPEG quality
PUSH_METHOD = "ffl_woo_sync.woocommerce.client_api.push_serial_now"


def load_cfg():
    cfg = {}
    with open(ENV) as fh:
        for line in fh:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = load_cfg()
BASE = CFG["FRAPPE_BASE_URL"].rstrip("/")
H = {"Authorization": f"token {CFG['FRAPPE_API_KEY']}:{CFG['FRAPPE_API_SECRET']}"}


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


def resize(src, dst):
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(QUALITY),
                    "-Z", str(MAXPX), src, "--out", dst], check=True, capture_output=True)


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
    ov = json.load(open(args.map)) if args.map else {}
    print(f"BASE={BASE}\n")
    print(f"{'FOLDER':<14}{'SERIAL':<14}{'STATUS':<10}{'PRICE':<8}{'#img':<5}{'PRIMARY':<22}{'FLAGS'}")
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
        if not pick_desc(fp):
            flags.append("NO-DESC")
        print(f"{f:<14}{(serial or '?'):<14}{str(status):<10}{str(price):<8}{len(imgs):<5}{primary:<22}{' '.join(flags)}")
    shared = {ic: ss for ic, ss in seen_items.items() if len(ss) > 1}
    if shared:
        print("\nNote — folders sharing one Item (fine: photos live per-serial):")
        for ic, ss in shared.items():
            print(f"  {ic}: {', '.join(ss)}")


def cmd_attach(args):
    ov = json.load(open(args.map)) if args.map else {}
    print(f"BASE={BASE}  (LIVE writes)\n")
    tmp = os.path.join("/tmp", "firearm_resized")
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
            description = open(desc_path, encoding="utf-8", errors="replace").read().strip() if desc_path else ""
            imgs = list_images(fp)
            if not imgs:
                # No photos: set description only — never blank out an existing image/gallery.
                requests.put(_res(serial), headers={**H, "Content-Type": "application/json"},
                             data=json.dumps({"description": description}), timeout=120).raise_for_status()
                print(f"  [{f} -> {serial}] no photos — set description only\n"); continue
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
                                              "image_gallery": gallery}), timeout=120)
            r.raise_for_status()
            print(f"  [{f} -> {serial}] set description + {len(gallery)} resized photos, primary set\n")
        except Exception as exc:
            # Don't let one bad photo / transient 5xx abort the whole batch (matches cmd_push).
            print(f"  [{f} -> {serial}] ERROR — {exc} (skipped; may have left partial uploads)\n")
            continue


def cmd_push(args):
    ov = json.load(open(args.map)) if args.map else {}
    print(f"BASE={BASE}  (publishes LIVE products)\n")
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
        if d.get("woo_product_id"):
            print(f"[{serial}] SKIP — already listed (woo#{d['woo_product_id']})"); continue
        try:
            r = requests.post(f"{BASE}/api/method/{PUSH_METHOD}",
                              headers={**H, "Content-Type": "application/json"},
                              data=json.dumps({"serial_no": serial}), timeout=240)
            if not r.ok:
                print(f"[{serial}] HTTP {r.status_code} {r.text[:200]}"); continue
            m = r.json().get("message", {})
            print(f"[{serial}] ok={m.get('ok')} woo_product_id={m.get('woo_product_id')}")
        except Exception as exc:
            print(f"[{serial}] EXC {exc}")


def cmd_verify(args):
    ov = json.load(open(args.map)) if args.map else {}
    for f in targets(args):
        serial, _ = resolve_serial(f, ov)
        if not serial:
            print(f"[{f}] UNRESOLVED"); continue
        d = get_serial(serial)
        g = d.get("image_gallery") or []
        prim = [r["image"] for r in g if r["is_primary"]]
        s0 = [r["image"] for r in g if r.get("sort_order") == 0]
        ok = prim and s0 and prim[0] == s0[0]
        print(f"[{serial}] woo#{d.get('woo_product_id')} imgs={len(g)} primary={prim} {'OK' if ok else '*** MISMATCH'}")


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
    sub.add_parser("testconn")  # bare connectivity/auth check
    sp = sub.add_parser("setprice")
    sp.add_argument("--serial", required=True, help="serial number (or folder name)")
    sp.add_argument("--price", required=True, type=float, help="sell price, e.g. 1234")
    args = p.parse_args()
    {"resolve": cmd_resolve, "attach": cmd_attach, "push": cmd_push, "verify": cmd_verify,
     "testconn": cmd_testconn, "setprice": cmd_setprice}[args.cmd](args)


if __name__ == "__main__":
    main()
