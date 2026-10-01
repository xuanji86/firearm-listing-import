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
  attach  --root DIR [--map map.json] [--only A,B] [--force] [--allow-portrait]
      Resize photos (~2000px/q80) -> upload to POS -> set image_gallery +
      image (primary) + description + item_name (the per-gun WooCommerce listing
      title, taken from the description's "Title:" line). Skips serials that
      already have a gallery unless --force. A folder holding a photo taller
      than wide (after EXIF rotation) is skipped whole unless --allow-portrait:
      the store's product grid and gallery are landscape. The PRIMARY photo
      (main.*, else first by name) must be landscape, no override. Resizing is mandatory
      (see push timeout note below).
  push    --root DIR [--map map.json] [--only A,B] [--channel woo|gunbroker]
      Publish each priced + Active + not-yet-listed serial as its own listing
      via the channel's whitelisted push_serial_now. Skips un-priced ($0) and
      already-listed. --channel gunbroker is refused against a non-local POS
      unless FIREARM_ALLOW_PROD=1 (see "the production gate" below).
  rotate  --root DIR --folder SERIAL --file NAME --degrees 90|180|270
      Turn one photo clockwise, in place, EXIF rotation baked in first. The
      original is kept next to it as NAME.orig (not an image extension, so the
      importer ignores it). This is how the agent fixes a sideways or
      upside-down primary after looking at it, instead of sending it back.
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

  login URL
      Sign in to the POS in the browser (its own OAuth) — no API key. The session
      is kept in ~/.config/firearm-listing-import/ and renews itself.

Credentials + target site: FIREARM_ENV (an API-key file, FRAPPE_BASE_URL/
API_KEY/API_SECRET — e.g. a dev site to rehearse), else the `login` session.
NOTE: production — attach/push are live writes.

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
import argparse, contextlib, json, os, re, sys, tempfile, time
from urllib.parse import quote, urlparse

# Windows pipes/files default to the ANSI code page in strict mode: a gun title
# outside cp1252/cp936 would abort the batch with UnicodeEncodeError.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
import requests

# `login` keeps the signed-in session here (the POS's own OAuth: no API key).
AUTH_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "firearm-listing-import", "auth.json")
# An explicit API-key file (FRAPPE_BASE_URL / FRAPPE_API_KEY / FRAPPE_API_SECRET) —
# how you point at a dev site to rehearse. Set, it wins over the login; there is
# no implicit file, so which POS a run writes to is never decided by the cwd.
ENV = os.environ.get("FIREARM_ENV") or None
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
# California compliance answers, read onto the Serial No fields the osa_ca_compliant
# POS extension owns. Same shape as Title: one line, colon, Yes or No — written at the
# top of the file by convention, matched anywhere so a file that puts them in the
# Specifications block still works. A line that parses is removed from the customer-
# facing description (the store renders the answers from the fields, not the prose);
# a line whose value is neither Yes nor No is LEFT IN PLACE as ordinary text and
# reported, because silently dropping a line nobody parsed is how a typo becomes
# an unanswered gun.
FLAG_FIELDS = {
    "ca legal": "osa_ca_legal",
    "compliant service": "osa_compliant_service",
}
FLAG_RE = re.compile(r"\s*(ca legal|compliant service)\s*:\s*(.+?)\s*$", re.I)
FLAG_VALUES = {"yes": "Yes", "no": "No"}
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


def _write_private(path, data):
    """Atomic (a write cut short leaves the old file, never half a file); mkstemp
    creates it 0600 — sessions hold refresh tokens."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# Two kinds of file, so no write ever races another: AUTH_FILE says only WHICH
# session is current (written by `login` alone); each session's tokens live in
# their own file (renewals write only that). A batch renewing store A's tokens
# therefore cannot undo a login to store B made meanwhile.
def _session_file(client_id):
    return os.path.join(os.path.dirname(AUTH_FILE), "sessions",
                        re.sub(r"[^\w.-]", "_", client_id) + ".json")


def load_auth():
    """The current session, or None (never signed in, signed out, unreadable)."""
    cid = (_read_json(AUTH_FILE) or {}).get("client_id")
    auth = _read_json(_session_file(cid)) if isinstance(cid, str) else None
    return auth if auth and auth.get("client_id") == cid and auth.get("access_token") else None


if ENV:
    CFG = load_cfg()
    try:
        BASE = CFG["FRAPPE_BASE_URL"].rstrip("/")
        H = {"Authorization": f"token {CFG['FRAPPE_API_KEY']}:{CFG['FRAPPE_API_SECRET']}"}
    except KeyError as e:
        sys.exit(f"FIREARM_ENV={ENV} has no {e.args[0]} — fix it, or unset FIREARM_ENV "
                 "and use `login`")
    AUTH = None
else:
    AUTH = load_auth()
    if AUTH:
        BASE = AUTH["base"]
        H = {"Authorization": f"Bearer {AUTH['access_token']}"}
if not ENV and not AUTH:
    AUTH = BASE = None
    H = {}


def _saved_same_session():
    """This run's session file as saved now (a sibling process may have renewed
    it), or None once it is gone (signed out)."""
    return _read_json(_session_file(AUTH["client_id"]))


# Renew this long before expiry. The first request for each gun (resolving its
# serial) renews, so a gun's uploads and writes never straddle a renewal.
RENEW_MARGIN = 900


def _adopt_sibling():
    """Take tokens another process sharing this session renewed (if the POS
    rotates refresh tokens, ours is then spent). True if it had newer ones."""
    saved = _saved_same_session()
    # A different token only: after a 401 the saved copy of the refused token
    # looks "newer" than our zeroed expiry and must not be taken back.
    if (saved and saved.get("access_token") not in (None, "", AUTH.get("access_token"))
            and saved.get("expires_at", 0) > AUTH.get("expires_at", 0)):
        AUTH.update(saved)
        return True
    return False


def _renew():
    hint = f"run: uv run scripts/firearm_listings.py login {AUTH['base']}"
    if _adopt_sibling():
        return
    try:
        r = requests.post(AUTH["token_endpoint"], data={
            "grant_type": "refresh_token", "refresh_token": AUTH["refresh_token"],
            "client_id": AUTH["client_id"]}, timeout=30)
    except Exception as e:  # network: stop cleanly, never read as a failed write
        sys.exit(f"could not reach the POS to renew the session ({type(e).__name__}) — the run "
                 "stopped before its next request; `verify` the gun it was on, then re-run")
    try:
        tok = r.json() if r.ok else {}
    except ValueError:  # a 200 that is not JSON (a proxy's error page)
        tok = {}
    if not isinstance(tok, dict) or not tok.get("access_token"):
        time.sleep(1)  # a sibling may have just rotated the refresh token
        if _adopt_sibling():
            return
        sys.exit(f"POS session expired and could not be renewed (HTTP {r.status_code}) — {hint}")
    _store_tokens(AUTH, tok)


def _h():
    """Request headers; a `login` session is renewed RENEW_MARGIN before expiry."""
    if AUTH:
        if AUTH.get("expires_at", 0) - time.time() < RENEW_MARGIN:
            _renew()
        H["Authorization"] = f"Bearer {AUTH['access_token']}"
    return H


def _call(method, url, headers=None, **kw):
    """Every POS request goes through here: current auth, and one renew-and-retry
    on 401 (the POS's clock or token lifetime can disagree with ours; a 401 means
    the request was refused before it did anything, so the retry is safe)."""
    send = getattr(requests, method)
    r = send(url, headers={**_h(), **(headers or {})}, **kw)
    if r.status_code == 401 and AUTH:
        AUTH["expires_at"] = 0
        for f in (kw.get("files") or {}).values():  # an upload's file was read to the end
            if isinstance(f, tuple) and hasattr(f[1], "seek"):
                f[1].seek(0)
        r = send(url, headers={**_h(), **(headers or {})}, **kw)
    return r


def _store_tokens(auth, tok, fresh=False):
    """Keep the new tokens in this session's own file. A renewal never creates
    it: a session the person signed out of stays gone (the run carries on with
    the tokens in memory)."""
    auth["access_token"] = tok["access_token"]
    auth["refresh_token"] = tok.get("refresh_token") or auth.get("refresh_token")
    auth["expires_at"] = time.time() + int(tok.get("expires_in") or 3600)
    path = _session_file(auth["client_id"])
    if fresh or os.path.exists(path):
        _write_private(path, auth)


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

    The signed-in POS is PRODUCTION, and this script is normally driven by an
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
          f"  Point FIREARM_ENV at an API-key file for the dev site to rehearse:\n"
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
    r = _call("get", f"{BASE}/api/resource/Serial No",
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
    r = _call("get", _res(name), timeout=30)
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


def split_desc(text):
    """(title, flags, body) for a description .txt.

    title = the value of the first 'Title:' line (the per-gun WooCommerce listing
    title), or None if the file has no such line.
    flags = {POS fieldname: "Yes"|"No"} from the first 'CA Legal:' / 'Compliant
    Service:' line of each kind; absent keys mean the file said nothing and the
    gun's existing answer must be left alone.
    body = the text with those lines removed so they aren't duplicated inside the
    product description. Other lines (incl. the rest of the Specifications block)
    are kept verbatim.

    A malformed flag value ("CA Legal: maybe") is not a flag: the line stays in the
    body and the caller reports it, rather than quietly dropping a line that looked
    parsed but wasn't."""
    title, flags, bad, kept = None, {}, [], []
    for line in text.splitlines():
        m = TITLE_RE.match(line)
        if title is None and m and m.group(1).strip():
            title = m.group(1).strip()
            continue  # drop the Title: line from the customer-facing description
        m = FLAG_RE.match(line)
        if m:
            field = FLAG_FIELDS[m.group(1).lower()]
            value = FLAG_VALUES.get(m.group(2).strip().lower())
            if value and field not in flags:
                flags[field] = value
                continue  # answered — the store shows it as a field, not as prose
            if not value:
                bad.append(line.strip())
        kept.append(line)
    return title, flags, unwrap_paragraphs("\n".join(kept).strip()), bad


def read_desc(desc_path):
    """(title, flags, body, bad_flag_lines) from a description file path.

    (None, {}, "", []) when path is None. Thin path->text bridge for
    cmd_resolve/cmd_attach over split_desc()."""
    if not desc_path:
        return None, {}, "", []
    return split_desc(open(desc_path, encoding="utf-8", errors="replace").read())


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
    out = open_photo(src)
    out.thumbnail((MAXPX, MAXPX))      # aspect kept, never upscales
    out.convert("RGB").save(dst, "JPEG", quality=QUALITY, optimize=True)


def open_photo(src):
    """Open a photo as the phone meant it: EXIF rotation baked into the pixels.

    Pillow is imported here so the stdlib-only test run needs no Pillow; .heic is
    in IMG_EXT and needs the pillow-heif plugin to open."""
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener
    register_heif_opener()
    with Image.open(src) as im:
        return ImageOps.exif_transpose(im)  # a copy; the JPEG save later drops EXIF


def portrait_photos(fp, imgs):
    """Names of the photos in ``imgs`` that are taller than wide once EXIF rotation
    is applied — the shape the store's landscape product grid and gallery crop or
    letterbox. A phone photo that only *looks* sideways because of its orientation
    tag is not portrait here; the transpose happens first, as it does in resize().
    Returns None when Pillow is not installed (the check cannot run)."""
    try:
        import PIL  # noqa: F401
        import pillow_heif  # noqa: F401
    except ImportError:
        return None
    out = []
    for name in imgs:
        w, h = open_photo(os.path.join(fp, name)).size
        if h > w:
            out.append(name)
    return out


def upload(serial, path):
    with open(path, "rb") as fh:
        r = _call("post", f"{BASE}/api/method/upload_file",
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
        portrait = portrait_photos(fp, imgs)
        if portrait:
            flags.append(f"PORTRAIT:{len(portrait)}")  # attach refuses these without --allow-portrait
            if imgs[0] in portrait:
                flags.append("MAIN-PORTRAIT")  # the featured image must be landscape; attach has no override
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
            title, ca_flags, _, bad_flags = read_desc(desc_path)
            if not title:
                flags.append("NO-TITLE")  # no 'Title:' line — Woo falls back to the shared Item name
            # Surface compliance answers here so a run can be checked before it writes:
            # CA=Yes/No (what will be set), BAD-FLAG (a line that looked like an answer
            # but wasn't one — it stays in the description and nothing is written).
            for field, value in ca_flags.items():
                flags.append(("CA" if field == "osa_ca_legal" else "SVC") + "=" + value)
            if bad_flags:
                flags.append("BAD-FLAG")
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
            title, ca_flags, description, bad_flags = read_desc(desc_path)
            # item_name = the per-gun WooCommerce title (see references/internals.md):
            # serial_to_product_payload uses Serial No.item_name, falling back to the
            # shared Item name. Only send it when a 'Title:' line is present so we never
            # blank out an existing title for an old-format folder with no title.
            title_field = {"item_name": title} if title else {}
            if not title:
                print(f"  [{f} -> {serial}] NO-TITLE — no 'Title:' line; Woo keeps the shared Item name")
            # CA Legal / Compliant Service (osa_ca_compliant extension). Only sent when
            # the file answered: an absent line means "not stated here", and overwriting
            # a counter-entered answer with a blank would be a silent downgrade. A gun
            # left unanswered here still inherits its Item's answer in the POS.
            for line in bad_flags:
                print(f"  [{f} -> {serial}] BAD-FLAG — {line!r} is not Yes/No; left in the description, nothing written")
            if ca_flags:
                print(f"  [{f} -> {serial}] flags: " + ", ".join(f"{k}={v}" for k, v in sorted(ca_flags.items())))
            imgs = list_images(fp)
            if not imgs:
                # No photos: set description (+ title) only — never blank out an existing image/gallery.
                _call("put", _res(serial), headers={"Content-Type": "application/json"},
                             data=json.dumps({"description": description, **title_field, **ca_flags}), timeout=120).raise_for_status()
                print(f"  [{f} -> {serial}] no photos — set description{' + title' if title else ''} only\n"); continue
            portrait = portrait_photos(fp, imgs) or []
            if imgs[0] in portrait:
                # The primary becomes the Woo featured image (grid thumbnail); no override.
                print(f"  [{f} -> {serial}] MAIN-PORTRAIT — primary photo {imgs[0]} is taller than wide; "
                      f"the featured image must be landscape: rotate it or name another photo main.* "
                      f"(skipped, nothing written)\n"); continue
            if portrait and not getattr(args, "allow_portrait", False):
                # Whole gun skipped, nothing written: a listing with half its photos is
                # worse than one that waits for the reshoot.
                print(f"  [{f} -> {serial}] PORTRAIT — {len(portrait)} photo(s) taller than wide: "
                      f"{', '.join(portrait)}; rotate or reshoot them, or pass --allow-portrait "
                      f"(skipped, nothing written)\n"); continue
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
            r = _call("put", _res(serial), headers={"Content-Type": "application/json"},
                             data=json.dumps({"description": description, "image": primary,
                                              "image_gallery": gallery, **title_field,
                                              **ca_flags}), timeout=120)
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
            r = _call("post", f"{BASE}/api/method/{method}",
                              headers={"Content-Type": "application/json"},
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
    r = _call("get", f"{BASE}/api/method/frappe.auth.get_logged_user", timeout=15)
    who = r.json().get("message") if r.ok else r.text[:120]
    print(f"BASE={BASE}\nstatus={r.status_code}  logged_in_as={who}")
    print("OK" if r.ok else ("FAILED — sign in again with `login`" if AUTH
                             else f"FAILED — check FRAPPE_API_KEY/SECRET in {ENV}"))


@contextlib.contextmanager
def _login_lock():
    """Cross-platform, stdlib: an exclusively created lock file. One left behind
    by a crash is taken over after 30 s (a login holds it for milliseconds)."""
    path = AUTH_FILE + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _ in range(300):
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > 30:
                    os.remove(path)
                    continue
            except OSError:
                continue
            time.sleep(0.1)
    else:
        sys.exit(f"another login holds {path} — wait for it, or delete the file")
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def cmd_login(args):
    """Sign in to the POS in the browser (its own OAuth, PKCE) — no API key.

    Registers this machine as an OAuth client (dynamic registration, a loopback
    redirect), opens the POS sign-in/approve page, and keeps the tokens in
    its own file under the config directory (mode 600), and makes it current. Writes then carry the signed-in person's own name and
    POS roles. Signing in to another store replaces the session."""
    import base64, hashlib, secrets, threading, webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlencode
    base = args.url.rstrip("/")
    if urlparse(base).scheme != "https" and not _is_local_base(base):
        sys.exit("refusing a plain-http POS URL — use https://pos.<store domain>")
    r = requests.get(f"{base}/.well-known/oauth-authorization-server", timeout=15)
    try:
        meta = r.json() if r.ok else {}
    except ValueError:
        meta = {}
    origin = "{0.scheme}://{0.netloc}".format(urlparse(base))
    keys = ("authorization_endpoint", "registration_endpoint", "token_endpoint")
    if not isinstance(meta, dict) or not all(isinstance(meta.get(k), str) for k in keys):
        sys.exit(f"{base} does not offer sign-in (HTTP {r.status_code}) — is "
                 "MCP Settings switched on there?")
    for k in keys:  # the code, the verifier and every refresh token go to these
        if "{0.scheme}://{0.netloc}".format(urlparse(meta[k])) != origin:
            sys.exit(f"{base} advertises {k}={meta[k]} — not the same https origin; refusing "
                     "(check the proxy sends X-Forwarded-Proto and host_name is set)")
    got, done = {}, threading.Event()
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(16)

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            # Threaded and keep-waiting: a browser's idle preconnect or its
            # favicon request must not use up the one answer we wait for.
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path != "/callback" or q.get("state") != state:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("Signed in — you can close this tab." if q.get("code") else
                              f"Sign-in failed ({q.get('error', 'no code')}) — see the terminal.").encode())
            got.update(q)
            done.set()  # after the page is sent: the process may exit right away

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
    srv.daemon_threads = True
    redirect = f"http://127.0.0.1:{srv.server_port}/callback"
    reg = requests.post(meta["registration_endpoint"], json={
        "client_name": "firearm-listing-import", "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": "all"}, timeout=15)
    try:
        client_id = reg.json()["client_id"] if reg.ok else None
    except (ValueError, KeyError, TypeError):
        client_id = None
    if not client_id:
        sys.exit(f"client registration refused (HTTP {reg.status_code}): {reg.text[:200]}")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = meta["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "scope": "all", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})
    print(f"Opening the POS sign-in page. If no browser opens, visit:\n  {url}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webbrowser.open(url)
    done.wait(300)  # the redirect back, or give up after 5 minutes
    srv.shutdown()
    srv.server_close()
    if "code" not in got:
        sys.exit(f"sign-in did not complete: {got.get('error') or 'no answer within 5 minutes'}")
    tok = requests.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
        "client_id": client_id, "code_verifier": verifier}, timeout=30)
    if not tok.ok:
        sys.exit(f"token exchange refused (HTTP {tok.status_code}): {tok.text[:200]}")
    auth = {"base": base, "client_id": client_id, "token_endpoint": meta["token_endpoint"]}
    with _login_lock():  # two logins finishing at once must not delete each other's session
        _store_tokens(auth, tok.json(), fresh=True)
        _write_private(AUTH_FILE, {"client_id": client_id})  # now the current session
        # Earlier sessions on this machine are superseded: drop their tokens. (They
        # cannot be revoked from here — public clients — but an admin can in the POS.)
        sdir = os.path.dirname(_session_file(client_id))
        for name in os.listdir(sdir):
            if name.endswith(".json") and name != os.path.basename(_session_file(client_id)):
                os.remove(os.path.join(sdir, name))
    who = requests.get(f"{base}/api/method/frappe.auth.get_logged_user",
                       headers={"Authorization": f"Bearer {auth['access_token']}"}, timeout=15)
    print(f"Signed in to {base} as {who.json().get('message') if who.ok else '?'} "
          f"(session kept in {AUTH_FILE})")
    if os.environ.get("FIREARM_ENV"):
        print("!! FIREARM_ENV is set, and it wins over this session — unset it to use the login.")


def cmd_setprice(args):
    """Set Serial No.sell_price over REST (so price-setting needs no Frappe MCP)."""
    serial, how = resolve_serial(args.serial, {})
    if not serial or how == "fuzzy":
        print(f"[{args.serial}] UNRESOLVED or ambiguous — pass an exact serial"); return
    print(f"BASE={BASE}")
    r = _call("put", _res(serial), headers={"Content-Type": "application/json"},
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
    r = _call("put", _res(serial), headers={"Content-Type": "application/json"},
                     data=json.dumps({"item_name": args.title}), timeout=30)
    r.raise_for_status()
    print(f"[{serial}] item_name (Woo title) set to {args.title!r}")


def rotate_photo(path, degrees):
    """Rotate one photo clockwise by 90/180/270 in place; keep ``path + '.orig'``.

    EXIF rotation is baked in first (open_photo), so the degrees apply to what a
    viewer shows, not to the stored pixels. Saved as JPEG when the source is
    .heic (Pillow cannot write it back); the .heic itself becomes the backup, so
    the folder holds one live copy of the photo."""
    from PIL import Image
    turn = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
            270: Image.Transpose.ROTATE_90}[int(degrees)]  # PIL turns counter-clockwise
    im = open_photo(path).transpose(turn)
    backup = path + ".orig"
    if not os.path.exists(backup):
        os.replace(path, backup)
    root, ext = os.path.splitext(path)
    if ext.lower() == ".heic":
        path = root + ".jpg"
    if ext.lower() in (".jpg", ".jpeg", ".heic"):
        im.convert("RGB").save(path, "JPEG", quality=95, optimize=True)
    else:
        im.save(path)
    return path, im.size


def cmd_rotate(args):
    path = os.path.join(args.root, args.folder, args.file)
    if not os.path.isfile(path):
        sys.exit(f"no such photo: {path}")
    new_path, (w, h) = rotate_photo(path, args.degrees)
    shape = "landscape" if w > h else ("portrait" if h > w else "square")
    print(f"[{args.folder}] {args.file} turned {args.degrees}° clockwise -> {os.path.basename(new_path)} "
          f"{w}x{h} ({shape}); original kept as {args.file}.orig")


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
            sp.add_argument("--allow-portrait", action="store_true",
                            help="upload photos taller than wide too (default: skip the gun and say which photos)")
        if name in ("push", "verify"):
            sp.add_argument("--channel", choices=sorted(PUSH_CHANNELS),
                            default=DEFAULT_CHANNEL,
                            help=f"sales channel (default: {DEFAULT_CHANNEL}). "
                                 f"gunbroker needs {ALLOW_PROD_ENV}=1 off a local site")
    sub.add_parser("testconn")  # bare connectivity/auth check
    sp = sub.add_parser("login")
    sp.add_argument("url", help="the store's POS, e.g. https://pos.oldsteelarsenal.com")
    sp = sub.add_parser("rotate")
    sp.add_argument("--root", required=True, help="folder of <serial>/ subfolders")
    sp.add_argument("--folder", required=True, help="serial folder name, exactly as on disk")
    sp.add_argument("--file", required=True, help="photo file name inside that folder")
    sp.add_argument("--degrees", required=True, type=int, choices=(90, 180, 270),
                    help="clockwise turn as seen in a viewer: 180 = upside down, 90/270 = lying on its side")
    sp = sub.add_parser("setprice")
    sp.add_argument("--serial", required=True, help="serial number (or folder name)")
    sp.add_argument("--price", required=True, type=float, help="sell price, e.g. 1234")
    sp = sub.add_parser("settitle")
    sp.add_argument("--serial", required=True, help="serial number (or folder name)")
    sp.add_argument("--title", required=True, help="per-gun Woo listing title (Serial No.item_name)")
    args = p.parse_args()
    if not BASE and args.cmd not in ("login", "rotate"):
        sys.exit("Not signed in to a POS. Run once:\n"
                 "  uv run scripts/firearm_listings.py login https://pos.oldsteelarsenal.com\n"
                 "(or set FIREARM_ENV to an API-key file)")
    {"login": cmd_login, "resolve": cmd_resolve, "attach": cmd_attach, "push": cmd_push, "verify": cmd_verify,
     "testconn": cmd_testconn, "setprice": cmd_setprice, "settitle": cmd_settitle,
     "rotate": cmd_rotate}[args.cmd](args)


if __name__ == "__main__":
    main()
