#!/usr/bin/env python3
"""Ingest the 80 headgear items into the picgen library as 3-view composites (standalone, no app import).

Source: work-gifs/headgear/manifest.csv + labeled/ (raw grid cells, 335x505, cut by the other session's split.py).
Each cell: trim divider slivers, drop caption text bands at the bottom AND the top (text = thin band of short
dark runs), crop to the drawing, pad to a square, upscale to 768; then front|side|back -> one wide image.
"""
import csv, json, os, pathlib, collections, re, sys
from PIL import Image

# Source folder (manifest.csv + labeled/) and target library: HEADGEAR_SRC / LIBRARY_DIR, or --src / --lib.
SRC = pathlib.Path(sys.argv[sys.argv.index("--src") + 1] if "--src" in sys.argv else os.environ.get("HEADGEAR_SRC", "headgear"))
LIB = pathlib.Path(sys.argv[sys.argv.index("--lib") + 1] if "--lib" in sys.argv else os.environ.get("LIBRARY_DIR") or pathlib.Path(__file__).resolve().parent.parent / "library")
INDEX = LIB / "index.json"
OUT = 768

KEYS = [
 ("baseball cap", ["baseball cap", "baseball hat", "ball cap", "ballcap", "baseball player", "!cap", "!hat"]),
 ("snapback cap", ["snapback", "snapback cap", "snap back cap", "flat brim cap", "flat-brim cap", "flat brim hat", "fitted cap", "!cap"]),
 ("knit beanie", ["beanie", "knit beanie", "knit cap", "knit hat", "watch cap", "winter hat", "wooly hat", "woolly hat", "skully", "!toque"]),
 ("bucket hat", ["bucket hat", "fishing hat", "fisherman hat", "fisherman", "angler", "!bucket", "!hat"]),
 ("trucker hat", ["trucker hat", "trucker cap", "mesh cap", "mesh back cap", "mesh hat", "trucker", "!hat"]),
 ("flat cap", ["flat cap", "ivy cap", "ivy hat", "driving cap", "paddy cap", "golf cap", "peaky blinders cap", "peaky blinders", "scally cap", "!cap"]),
 ("beret", ["beret", "french beret", "artist beret", "painter's beret"]),
 ("sun visor", ["sun visor", "visor cap", "tennis visor", "golf visor", "tennis player", "sports visor", "!visor", "!tennis"]),
 ("bandana", ["bandana", "bandanna", "bandana on the head", "head bandana", "pirate bandana"]),
 ("straw sun hat", ["straw hat", "sun hat", "sunhat", "wide brim hat", "wide-brim hat", "wide brimmed hat", "beach hat", "floppy hat", "garden hat", "gardening hat", "gardener", "farmer", "!straw", "!hat"]),
 ("newsboy cap", ["newsboy cap", "newsboy hat", "newsboy", "baker boy cap", "bakerboy cap", "gatsby cap", "paperboy cap", "paperboy", "cabbie cap", "eight panel cap", "!cap"]),
 ("pom-pom beanie", ["pom pom beanie", "pom-pom beanie", "pompom beanie", "pom pom hat", "pompom hat", "bobble hat", "bobble beanie", "slouchy beanie", "beanie with pom pom", "beanie with a pom pom", "!beanie"]),
 ("american football helmet", ["football helmet", "american football helmet", "gridiron helmet", "nfl helmet", "football player", "quarterback", "linebacker", "nfl", "!football", "!helmet"]),
 ("baseball batting helmet", ["batting helmet", "baseball helmet", "batter's helmet", "batter helmet", "batter", "at bat", "!baseball", "!helmet"]),
 ("ice hockey helmet", ["hockey helmet", "ice hockey helmet", "hockey player", "!hockey", "!helmet"]),
 ("bicycle helmet", ["bicycle helmet", "bike helmet", "cycling helmet", "cycle helmet", "bmx helmet", "cyclist", "cycling", "!bike", "!bicycle", "!helmet"]),
 ("motorcycle helmet", ["motorcycle helmet", "motorbike helmet", "full face helmet", "full-face helmet", "biker helmet", "crash helmet", "moto helmet", "motorcyclist", "riding a motorcycle", "on a motorcycle", "motorcycle rider", "!motorcycle", "!motorbike", "!biker", "!helmet"]),
 ("ski helmet with goggles", ["ski helmet", "ski goggles", "snowboard helmet", "snow helmet", "skiing helmet", "skier", "snowboarder", "skiing", "snowboarding", "!ski", "!goggles", "!helmet"]),
 ("boxing headgear", ["boxing headgear", "boxing helmet", "sparring headgear", "sparring helmet", "head guard", "headguard", "mma headgear", "sparring", "!boxing", "!boxer"]),
 ("swim cap with goggles", ["swim cap", "swimming cap", "swim goggles", "swimming goggles", "bathing cap", "swimmer's cap", "swimmer", "lap swimming", "!goggles", "!swimming", "!cap"]),
 ("cycling cap", ["cycling cap", "cycling hat", "bike cap", "casquette", "cyclist cap", "!cyclist", "!cycling", "!cap"]),
 ("equestrian helmet", ["riding helmet", "equestrian helmet", "horse riding helmet", "horseback riding helmet", "jockey helmet", "riding hat", "equestrian", "jockey", "show jumping", "dressage", "!horse", "!riding", "!helmet"]),
 ("catcher's mask", ["catcher's mask", "catcher mask", "catchers mask", "catcher's helmet", "catcher helmet", "baseball mask", "catcher", "!mask"]),
 ("racing helmet", ["racing helmet", "race helmet", "open face helmet", "open-face helmet", "retro helmet", "cafe racer helmet", "racing driver", "race car driver", "formula 1", "formula one", "f1 driver", "go kart", "kart racing", "!racing", "!helmet"]),
 ("fedora", ["fedora", "fedora hat", "detective", "gangster", "mobster", "gangster hat", "noir hat", "indiana jones hat", "private eye"]),
 ("trilby", ["trilby", "trilby hat", "!hat"]),
 ("bowler hat", ["bowler hat", "bowler", "derby hat", "chaplin hat", "charlie chaplin", "!hat"]),
 ("top hat", ["top hat", "tophat", "stovepipe hat", "magician hat", "magician's hat", "magician", "ringmaster hat", "ringmaster", "victorian gentleman", "!hat"]),
 ("homburg hat", ["homburg", "homburg hat", "godfather hat", "!hat"]),
 ("panama hat", ["panama hat", "panama straw hat", "!panama", "!hat"]),
 ("straw boater hat", ["boater", "boater hat", "straw boater", "skimmer hat", "barbershop hat", "barbershop quartet", "!hat"]),
 ("pork pie hat", ["pork pie hat", "porkpie hat", "porkpie", "pork-pie hat", "heisenberg hat", "heisenberg", "!hat"]),
 ("viking helmet", ["viking helmet", "viking", "norse helmet", "nasal helmet", "norseman", "!helmet"]),
 ("roman galea helmet", ["galea", "roman helmet", "roman galea", "legionary helmet", "legionnaire helmet", "legionnaire", "legionary", "centurion", "centurion helmet", "roman soldier", "roman legionary", "!roman", "!helmet"]),
 ("great helm", ["great helm", "greathelm", "knight helmet", "knight's helmet", "knight", "crusader helmet", "crusader", "templar helmet", "templar", "bucket helm", "medieval knight", "!medieval", "!helmet"]),
 ("kettle hat", ["kettle hat", "kettle helmet", "chapel de fer", "war hat", "medieval infantry helmet", "medieval soldier", "man at arms", "!kettle", "!medieval", "!helmet"]),
 ("samurai kabuto", ["kabuto", "samurai helmet", "samurai kabuto", "samurai", "shogun helmet", "!helmet"]),
 ("conquistador morion", ["morion", "morion helmet", "conquistador", "conquistador helmet", "spanish helmet", "!helmet"]),
 ("tricorne hat", ["tricorne", "tricorn", "tricorne hat", "tricorn hat", "three cornered hat", "three-cornered hat", "colonial hat", "revolutionary war hat", "colonial soldier", "minuteman", "!hat"]),
 ("bicorne hat", ["bicorne", "bicorn", "bicorne hat", "napoleon hat", "napoleonic hat", "napoleon's hat", "napoleon", "napoleonic", "admiral hat", "admiral's hat", "!hat"]),
 ("pith helmet", ["pith helmet", "pith", "safari helmet", "safari hat", "explorer helmet", "explorer hat", "sun helmet", "colonial helmet", "safari", "!explorer", "!helmet"]),
 ("cowboy hat", ["cowboy hat", "cowgirl hat", "stetson", "western hat", "ten gallon hat", "ten-gallon hat", "ranch hat", "rancher hat", "cattleman hat", "sheriff", "cowgirl", "rancher", "wrangler hat", "!cowboy", "!western", "!hat"]),
 ("sombrero", ["sombrero", "mariachi hat", "mariachi", "mexican hat", "!mexican", "!hat"]),
 ("pharaoh nemes", ["nemes", "pharaoh headdress", "pharaoh", "egyptian headdress", "egyptian pharaoh", "king tut", "tutankhamun", "nemes headdress", "!egyptian"]),
 ("corinthian helmet", ["corinthian helmet", "corinthian", "spartan helmet", "spartan", "greek helmet", "hoplite helmet", "hoplite", "spartan warrior", "greek warrior", "!greek", "!helmet"]),
 ("royal crown", ["crown", "royal crown", "king's crown", "queen's crown", "gold crown", "golden crown", "jeweled crown", "jewelled crown", "king", "queen", "prince", "princess", "monarch", "royalty", "emperor", "empress", "!royal"]),
 ("wizard hat", ["wizard hat", "wizard's hat", "witch hat", "witch's hat", "pointed hat", "pointy hat", "sorcerer hat", "sorcerer's hat", "mage hat", "wizard", "witch", "sorcerer", "sorceress", "mage", "warlock", "!hat"]),
 ("pirate hat", ["pirate hat", "pirate captain hat", "pirate's hat", "skull hat", "jolly roger hat", "buccaneer hat", "corsair hat", "pirate", "pirate captain", "buccaneer", "corsair", "!captain", "!hat"]),
 ("construction hard hat", ["hard hat", "hardhat", "hard-hat", "construction helmet", "construction hat", "safety helmet", "builder's hat", "builder hat", "construction worker", "builder", "contractor", "!construction", "!helmet"]),
 ("firefighter helmet", ["firefighter helmet", "fireman helmet", "fire helmet", "fireman's helmet", "firefighter's helmet", "firefighter", "fireman", "firewoman", "fire fighter", "!helmet"]),
 ("combat helmet", ["combat helmet", "military helmet", "army helmet", "soldier helmet", "soldier's helmet", "kevlar helmet", "ballistic helmet", "modern combat helmet", "ach helmet", "mich helmet", "soldier", "infantry", "!marine", "!army", "!military", "!helmet"]),
 ("ww2 steel helmet", ["ww2 helmet", "wwii helmet", "steel helmet", "m1 helmet", "world war helmet", "world war 2 helmet", "world war ii helmet", "gi helmet", "ww2 soldier", "wwii soldier", "world war 2 soldier", "ww2", "wwii", "!soldier", "!helmet"]),
 ("police peaked cap", ["police cap", "police hat", "peaked cap", "officer's cap", "officer cap", "cop hat", "cop cap", "policeman's hat", "policeman hat", "police officer", "policeman", "policewoman", "cop", "police uniform", "!police", "!officer", "!cap"]),
 ("chef toque", ["chef hat", "chef's hat", "toque", "chef toque", "cook's hat", "cook hat", "chef", "head chef", "pastry chef", "!cook"]),
 ("surgical scrub cap", ["scrub cap", "surgical cap", "surgeon's cap", "surgeon cap", "surgical hat", "scrubs cap", "surgeon", "surgical scrubs", "in scrubs", "!surgical", "!scrubs", "!nurse", "!doctor", "!cap"]),
 ("welding mask", ["welding mask", "welding helmet", "welder's mask", "welder mask", "welder's helmet", "welder helmet", "welding", "welder", "!mask"]),
 ("mining helmet", ["mining helmet", "miner's helmet", "miner helmet", "miner's hat", "mining hat", "headlamp helmet", "miner", "headlamp", "head lamp", "coal miner", "!mining", "!helmet"]),
 ("aviator headset", ["aviator headset", "pilot headset", "aviation headset", "pilot's headset", "flight headset", "gaming headset", "headset with microphone", "headset with mic", "mic headset", "!headset", "!pilot", "!aviator"]),
 ("astronaut space helmet", ["astronaut helmet", "space helmet", "spacesuit helmet", "space suit helmet", "nasa helmet", "astronaut", "cosmonaut", "spacesuit", "space suit", "spacewalk", "!space", "!helmet"]),
 ("hazmat hood", ["hazmat hood", "hazmat suit", "hazmat", "biohazard suit", "biohazard hood", "chemical suit", "decontamination suit", "hazmat visor", "!biohazard"]),
 ("vr headset", ["vr headset", "vr goggles", "virtual reality headset", "virtual reality", "vr", "oculus", "meta quest", "quest headset", "vr visor", "!headset", "!goggles"]),
 ("cyberpunk visor", ["cyberpunk visor", "cyber visor", "glowing visor", "led visor", "neon visor", "cyberpunk goggles", "visor with glowing strip", "cyclops visor", "futuristic visor", "!cyberpunk", "!visor", "!neon", "!futuristic"]),
 ("sci-fi space helmet", ["sci-fi space helmet", "sci fi space helmet", "scifi space helmet", "sealed space helmet", "sci-fi helmet", "sci fi helmet", "scifi helmet", "space marine helmet", "futuristic space helmet", "sealed helmet", "!sci-fi", "!scifi", "!space", "!futuristic", "!helmet"]),
 ("mech pilot helmet", ["mech pilot helmet", "mech helmet", "hud helmet", "mecha helmet", "gundam helmet", "mech pilot", "mecha pilot", "pilot helmet", "!mech", "!mecha", "!pilot", "!hud", "!helmet"]),
 ("neon futuristic motorcycle helmet", ["futuristic motorcycle helmet", "futuristic motorcycle", "neon motorcycle helmet", "motorcycle helmet", "futuristic helmet", "neon helmet", "tron helmet", "sci-fi motorcycle helmet", "glowing helmet", "light up helmet", "led helmet", "cyberpunk helmet", "cyberpunk motorcycle helmet", "!futuristic", "!neon", "!tron", "!cyberpunk", "!helmet"]),
 ("tactical helmet", ["tactical helmet", "tactical combat helmet", "futuristic tactical helmet", "futuristic combat helmet", "sci-fi combat helmet", "ops core helmet", "fast helmet", "helmet with headset", "spec ops", "special forces", "swat", "tactical operator", "sci-fi soldier", "space soldier", "future soldier", "!tactical", "!helmet"]),
 ("AR glasses visor", ["ar glasses", "ar visor", "smart glasses", "holographic visor", "holographic glasses", "augmented reality glasses", "augmented reality", "hololens", "ar headset", "smartglasses", "!ar"]),
 ("LED baseball cap", ["led cap", "led baseball cap", "led hat", "light up cap", "light-up cap", "light up hat", "light-up hat", "glowing cap", "led display cap", "!led"]),
 ("turban", ["turban", "pagri", "dastar", "sikh turban", "sikh"]),
 ("kippah", ["kippah", "kippa", "yarmulke", "yarmulka", "jewish skullcap", "kipa", "!skullcap"]),
 ("fez", ["fez", "fez hat", "tarboosh", "tarbush"]),
 ("keffiyeh", ["keffiyeh", "kaffiyeh", "kufiya", "keffiyah", "shemagh", "ghutra", "ghutrah", "arab headscarf", "palestinian scarf", "agal", "igal", "bedouin"]),
 ("kufi cap", ["kufi", "kufi cap", "kufi hat", "taqiyah", "taqiya", "topi", "prayer cap", "muslim prayer cap", "muslim skullcap", "!skullcap", "!cap"]),
 ("ushanka", ["ushanka", "russian hat", "russian fur hat", "fur hat", "trapper hat", "fur earflap hat", "soviet hat", "russian winter hat", "shapka", "!russian", "!fur", "!hat"]),
 ("akubra hat", ["akubra", "akubra hat", "australian hat", "outback hat", "bush hat", "aussie hat", "crocodile dundee hat", "crocodile dundee", "drover's hat", "drover hat", "outback", "!australian", "!aussie", "!hat"]),
 ("deerstalker hat", ["deerstalker", "deerstalker hat", "deerstalker cap", "sherlock hat", "sherlock holmes hat", "sherlock holmes", "sherlock", "detective hat", "detective cap", "!hat"]),
 ("conical straw hat", ["conical hat", "conical straw hat", "asian conical hat", "rice hat", "rice paddy hat", "paddy hat", "non la", "bamboo hat", "vietnamese hat", "rice farmer", "conical", "!farmer", "!asian", "!straw", "!hat"]),
 ("chullo hat", ["chullo", "chullo hat", "chulo hat", "andean hat", "peruvian hat", "earflap beanie", "earflap hat", "knit hat with earflaps", "alpaca hat", "andean knit hat", "peruvian beanie", "!peruvian", "!andean", "!earflaps", "!hat"]),
 ("tam o' shanter", ["tam o shanter", "tam o' shanter", "tam", "scottish hat", "scottish bonnet", "scots bonnet", "tartan hat", "tartan cap", "plaid tam", "scotsman", "highlander", "!scottish", "!tartan", "!hat"]),
 ("headscarf", ["headscarf", "head scarf", "babushka", "kerchief", "head kerchief", "scarf tied under the chin", "scarf on her head", "scarf on the head", "hair scarf", "headscarf tied under the chin", "!scarf"]),
]
assert len(KEYS) == 80, len(KEYS)


def slug(label):
    t = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return t[:40] or "view"


import numpy as np


def _bands(ink):
    out, y0 = [], None
    for y, v in enumerate(list(ink) + [False]):
        if v and y0 is None: y0 = y
        if not v and y0 is not None: out.append((y0, y)); y0 = None
    return out


def _runs(row):
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return np.where(d == -1)[0] - np.where(d == 1)[0]


def _row_class(row):
    """blank / text / draw for one mask row."""
    if not row.any(): return "blank"
    r = _runs(row)
    if r.size >= 5 and np.median(r) <= 5 and r.max() <= 16: return "text"
    return "draw"


def _strand_like(dark, y0, y1):
    """Vertical strands (tassels, ties): nearly every row has the same number of dark runs at the same
    positions. Text lines change run count and positions from row to row."""
    counts, prev, sims = [], None, []
    for y in range(y0, y1):
        st = np.where(np.diff(np.concatenate(([0], dark[y].astype(np.int8)))) == 1)[0]
        counts.append(int(st.size))
        if prev is not None and st.size >= 2 and prev.size >= 2:
            m = sum(1 for s in st if np.abs(prev - s).min() <= 1)
            sims.append(m / max(st.size, prev.size))
        prev = st
    if not counts or not sims: return False
    modal = max(set(counts), key=counts.count)
    return counts.count(modal) / len(counts) >= 0.75 and float(np.mean(sims)) >= 0.8


def cut_cell(cell):
    """Drawing crop of one labeled grid cell. Removes: divider slivers; standalone caption bands at the
    bottom and the top (v2 logic); a caption block glued to the drawing's bottom; and a second drawing
    further down the cell (a duplicate hat). Text = thin band of many short dark runs, not vertical strands."""
    a = np.asarray(cell.convert("L")); H, W = a.shape
    dark = a < 120
    rp, cp = dark.mean(axis=1), dark.mean(axis=0)
    t = 0
    while t < 12 and rp[t] > 0.5: t += 1
    bt = H
    while bt > H - 12 and rp[bt - 1] > 0.5: bt -= 1
    l = 0
    while l < 12 and cp[l] > 0.5: l += 1
    r = W
    while r > W - 12 and cp[r - 1] > 0.5: r -= 1
    t += 3; bt -= 3; l += 3; r -= 3
    cell = cell.crop((l, t, r, bt)); a = np.asarray(cell.convert("L")); H, W = a.shape
    dark = a < 120
    raw = _bands(dark.sum(axis=1) >= 1)
    bands = []
    for bnd in raw:                                   # only anti-aliasing breaks (<= 3 px) are bridged
        if bands and bnd[0] - bands[-1][1] <= 3: bands[-1] = (bands[-1][0], bnd[1])
        else: bands.append(bnd)

    def is_text(bnd):
        y0, y1 = bnd; h = y1 - y0
        if h > 34 or h < 3: return False
        xs = np.where(dark[y0:y1].any(axis=0))[0]
        if xs.size == 0: return False
        best = None
        for y in (y0 + h // 4, y0 + h // 2, y0 + (3 * h) // 4):
            runs = _runs(dark[min(y, y1 - 1)])
            if runs.size and (best is None or runs.size > best[0]):
                best = (runs.size, float(np.median(runs)), int(runs.max()))
        if not best: return False
        strong = best[0] >= 10 and best[1] <= 4 and best[2] <= 16
        if (xs[-1] - xs[0] + 1) > 0.92 * W and not strong: return False
        return best[0] >= 4 and best[1] <= 8 and best[2] <= max(24, 0.09 * W)

    kinds = ["text" if is_text(bnd) else "draw" for bnd in bands]
    # standalone captions: leading text bands at the top, trailing text bands at the bottom
    i0 = 0
    while i0 < len(bands) and kinds[i0] == "text" and bands[i0][0] <= 0.4 * H: i0 += 1
    i1 = len(bands)
    while i1 > i0 and kinds[i1 - 1] == "text" and bands[i1 - 1][1] >= 0.55 * H: i1 -= 1
    inner = list(range(i0, i1))
    if not inner:
        return cell
    draws = [i for i in inner if kinds[i] == "draw"]
    if not draws:
        return cell
    main = next((i for i in draws if bands[i][1] - bands[i][0] >= 60), max(draws, key=lambda i: bands[i][1] - bands[i][0]))
    y0, y1 = bands[main]
    j = main + 1                                      # keep close trailing parts (chin strap), stop at a gap > 30 px
    while j < i1 and kinds[j] == "draw" and bands[j][0] - y1 <= 30:
        y1 = bands[j][1]; j += 1
    # a caption glued to the drawing's bottom: a block of text rows (not strands) right at the bottom
    y = y1 - 1; blk_top = blk_bot = None
    while y > y0 + 40:
        c = _row_class(dark[y])
        if c == "draw": break
        if c == "text":
            blk_top = y
            if blk_bot is None: blk_bot = y + 1
        elif blk_top is not None and (blk_top - y) > 6:
            break
        y -= 1
    if blk_top is not None and 5 <= blk_bot - blk_top <= 34 and not _strand_like(dark, blk_top, blk_bot):
        y1 = blk_top
    sub = dark[y0:y1]
    ys, xs = np.where(sub)
    if ys.size == 0:
        return cell
    x0, x1, yy0, yy1 = xs.min(), xs.max() + 1, ys.min() + y0, ys.max() + 1 + y0
    mg = int(0.06 * max(x1 - x0, yy1 - yy0)) + 4
    return cell.crop((max(0, x0 - mg), max(y0, yy0 - mg), min(W, x1 + mg), min(y1, yy1 + mg)))


def bg_of(cell):
    a = np.asarray(cell.convert("RGB"))
    patch = a[14:24, 14:24].reshape(-1, 3)
    return tuple(int(v) for v in np.median(patch, axis=0))


def square(img, bg):
    w, h = img.size; side = max(w, h)
    sq = Image.new("RGB", (side, side), bg); sq.paste(img, ((side - w) // 2, (side - h) // 2))
    return sq.resize((OUT, OUT), Image.LANCZOS)


def compose(parts, bg, gap=24):
    out = Image.new("RGB", (OUT * len(parts) + gap * (len(parts) - 1), OUT), bg); x = 0
    for p in parts:
        out.paste(p, (x, 0)); x += OUT + gap
    return out


rows = list(csv.DictReader(open(SRC / "manifest.csv")))
by = collections.OrderedDict()
for r in rows:
    n = int(r["n"]); slug_ = re.sub(r"[^a-z0-9]+", "_", r["garment"].lower()).strip("_")
    by.setdefault(n, {})[r["view"]] = SRC / "labeled" / f"{n:02d}_{slug_}_{r['view']}.png"
assert len(by) == 80 and all(p.exists() for v in by.values() for p in v.values())

STAGE = pathlib.Path(sys.argv[sys.argv.index("--stage") + 1]) if "--stage" in sys.argv else None
INSTALL = pathlib.Path(sys.argv[sys.argv.index("--install") + 1]) if "--install" in sys.argv else None
if STAGE:
    STAGE.mkdir(parents=True, exist_ok=True)
    for n, (label, kws) in enumerate(KEYS, start=1):
        cells = [Image.open(by[n][v]).convert("RGB") for v in ("front", "side", "back")]
        bg = bg_of(cells[0])
        compose([square(cut_cell(c), bg) for c in cells], bg).save(STAGE / f"{slug(label)}.png", optimize=True)
    print("staged 80 composites in", STAGE)
elif INSTALL:
    # copy staged PNGs over the library files of the SAME tags and refresh keywords; quick read-modify-write of the index
    import shutil
    idx = json.loads(INDEX.read_text())
    n_files = n_meta = 0
    for n, (label, kws) in enumerate(KEYS, start=1):
        tag = slug(label)
        if tag in idx and idx[tag].get("kind") == "headwear":
            shutil.copy(INSTALL / f"{tag}.png", LIB / f"{tag}.png"); n_files += 1
            kws = list(kws); lab = label.lower()
            if " " in lab and lab not in kws: kws.append(lab)
            idx[tag]["keywords"] = kws; idx[tag]["views"] = 3; n_meta += 1
        else:
            print("MISSING in index:", tag)
    INDEX.write_text(json.dumps(idx, indent=1))
    print("installed", n_files, "files, refreshed", n_meta, "entries; index has", len(idx))
else:
    print("use --stage DIR or --install DIR"); sys.exit(1)
