"""picgen feedback loop: grade a picture, explain what went wrong, repair only the flaw,
and learn rules that change future generations.

Pieces
  * ISSUES      the flaw taxonomy (what a thumbs-down can name) with a repair recipe each
  * diagnose()  rule-based explanation from the job record (which reference, which guides,
                how many references, what the prompt said and did not say)
  * critic()    a vision model (Ollama cloud) looks at the picture against the request and
                returns the flaws it sees; optional, used for auto-critique and to pre-fill
                the thumbs-down form
  * lessons     rules with a trigger and an action (prompt text, reference choice, settings);
                built-in ones switch on when their category is reported, the learner can add
                new ones from accumulated flaws; every lesson keeps hits / wins / losses
  * build_repair()  an edit of the same picture that changes only the flagged things
  * sqlite      data/feedback.db: feedback, lessons, critiques
"""
import base64
import json
import re
import sqlite3
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

CFG = {"db": None, "out": None, "ollama": "http://127.0.0.1:11434", "critic_model": "gemma4:31b-cloud",
       "learner_model": "gemma4:31b-cloud", "max_refs": 4, "expr_words": lambda label: ""}
_lock = threading.Lock()

ISSUES = OrderedDict([
    ("anatomy",    {"label": "Extra or missing limbs, hands or fingers",
                    "repair": "Fix the anatomy: one person with exactly two arms and two hands, five fingers on each; remove any extra arm or hand{detail}."}),
    ("gaze",       {"label": "Looking in the wrong direction",
                    "repair": "Change where the person is looking{detail}."}),
    ("pose",       {"label": "Body pose or action not as asked",
                    "repair": "Change the body pose and action{detail}."}),
    ("expression", {"label": "Facial expression not as asked",
                    "repair": "Change the facial expression{detail}."}),
    ("garment",    {"label": "Clothing, shoes or headgear wrong or missing",
                    "repair": "Change the clothing{detail}."}),
    ("identity",   {"label": "Does not look like the character",
                    "repair": None}),
    ("scene",      {"label": "Background, setting, props or framing not as asked",
                    "repair": "Change the setting{detail}."}),
    ("style",      {"label": "Wrong art style or realism",
                    "repair": "Change the art style{detail}."}),
    ("artifact",   {"label": "Text, watermark, split screen, duplicated person, stray marks or glitches",
                    "repair": "Remove the artefacts{detail}: one single clean picture of one person, no text, no watermark, no split screen."}),
    ("other",      {"label": "Something else",
                    "repair": "{detail_bare}"}),
])
REGENERATE_ISSUES = ("identity", "style")        # an edit keeps the face and the look; these need a fresh picture

# head-direction phrases the pose library does not cover, with the explicit wording the model obeys
GAZE_RULES = [
    (r"look(?:s|ing|ed)? up(?:ward)?s?\b|gaz(?:es|ing) up|eyes (?:raised|on the sky|to the sky)|up (?:at|into|towards?) the (?:sky|clouds|stars|ceiling)|stares? (?:up )?at the (?:sky|clouds|stars)",
     "looking up: head tilted far back, chin raised high, face turned up toward the sky, eyes looking up"),
    (r"look(?:s|ing|ed)? down(?:ward)?s?\b|gaz(?:es|ing) down|eyes (?:lowered|downcast)|stares? at the (?:ground|floor)",
     "looking down: head tilted forward, eyes lowered"),
    (r"look(?:s|ing|ed)? (?:away|to the side|sideways|off to the (?:left|right))|glanc(?:es|ing) (?:away|to the side)",
     "looking away: head turned to the side, eyes off to the side"),
    (r"look(?:s|ing|ed)? (?:straight )?(?:at|into) the camera|eye contact|look(?:s|ing)? at (?:you|the viewer)",
     "looking straight into the camera, eye contact with the viewer"),
    (r"look(?:s|ing|ed)? (?:back )?over (?:his|her|their) shoulder|glanc(?:es|ing) back",
     "looking back over the shoulder, head turned back"),
    (r"eyes closed|closes? (?:his|her|their) eyes|eyes shut",
     "eyes closed"),
    (r"look(?:s|ing|ed)? (?:down )?at (?:the|a|his|her|their) (?:book|phone|screen|laptop|map|letter|paper|watch)",
     "looking down at the object in the hands"),
]
_HAND_ACTIONS = r"\b(?:hold(?:s|ing)?|carr(?:y|ies|ying)|typing|writing|reading|eating|drinking|pointing|waving|facepalm\w*|hand on|hands on|clapping|gripping|lifting|throwing|catching|playing|cooking|stirring|texting)\b"
_UNFRAMED = r"\breference\b|\bsame (?:man|woman|person|boy|girl|guy|lady|character|dog|cat|robot|creature|\w+ from)"


def root_job(job: dict, depth: int = 0) -> dict:
    """The picture a chain of fixes started from. A fix is an edit whose own prompt is the repair
    instruction, so the owner's real request lives on the picture the chain started from."""
    pid = job.get("repair_of")
    if not pid or depth > 8 or not CFG.get("out"):
        return job
    try:
        parent = json.loads((Path(CFG["out"]) / f"{Path(str(pid)).name}.json").read_text())
    except Exception:
        return job
    return root_job(parent, depth + 1)


def request_of(job: dict) -> str:
    return root_job(job).get("prompt") or job.get("prompt") or ""


def gaze_phrases(text: str) -> list[str]:
    t = " " + text.lower() + " "
    return [words for rx, words in GAZE_RULES if re.search(rx, t)]


# ------------------------------------------------------------- restyle requests
_RESTYLE = re.compile(r"\b(?:in the style of|(?:re)?drawn (?:as|in)|redraw\w*|restyl\w*|art style|anime|manga|chibi|cel[- ]shad\w*|ghibli|pixar|"
                      r"disney|dreamworks|simpsons|south park|lego|claymation|stop[- ]motion|watercolou?r|oil painting|pixel art|8[- ]bit|low[- ]poly|"
                      r"ukiyo[- ]e|comic[- ]book style|graphic novel style|film noir|photo(?:graph(?:ed|ic)?|[- ]?realistic)|hyper[- ]?realis\w*|"
                      r"realistic|3d render\w*|cgi|dragon ?ball|ghost in the shell)\b", re.I)


def is_restyle(prompt: str) -> bool:
    """The request asks for an art style other than the reference's ("redrawn as a Ghost in the Shell character")."""
    return bool(_RESTYLE.search(prompt or ""))


def _style_phrase(prompt: str) -> str:
    m = _RESTYLE.search(prompt or "")
    if not m:
        return ""
    s = re.split(r"[.,;:!?]", prompt[m.start():])[0].strip()
    return s if len(s) <= 90 else s[:90].rsplit(" ", 1)[0]


def _fits(lesson: dict, prompt: str) -> bool:
    """Whether a reported flaw should switch this lesson on: a style complaint about a restyle request
    means the new style did not take, so it must not switch on 'match the reference's style'."""
    w = (lesson.get("trigger") or {}).get("when")
    if w == "character_same_style":
        return not is_restyle(prompt)
    if w == "restyle_requested":
        return is_restyle(prompt)
    return True


# ------------------------------------------------- whose action a library view describes
_OTHERS = (r"dogs?|pupp(?:y|ies)|cats?|kittens?|birds?|horses?|cows?|ducks?|chickens?|goats?|sheep|parrots?|monkeys?|squirrels?|"
           r"kids?|child(?:ren)?|bab(?:y|ies)|people|crowd|friends?|others|someone|somebody|strangers?|neighbou?rs?")
_CONTENT_STOP = {"with", "while", "from", "into", "onto", "over", "under", "near", "their", "there", "this", "that", "both",
                 "hands", "hand", "person", "character", "same", "reference", "front", "side", "back", "view", "looking", "look"}


def _stem_word(w: str) -> str:
    w = w.lower()
    for suf in ("ing", "ies", "es", "ed", "er", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            b = w[: -len(suf)] + ("y" if suf == "ies" else "")
            if suf in ("ing", "ed", "er") and len(b) >= 4 and b[-1] == b[-2] and b[-1] not in "lsz":
                b = b[:-1]                                          # chopping -> chop, cutting -> cut
            return b
    return w


def _content_stems(text: str) -> set:
    return {_stem_word(w) for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 3 and w not in _CONTENT_STOP}


def _shares_words(a: str, b: str) -> bool:
    return bool(_content_stems(a) & _content_stems(b))


def words_about(prompt: str, label: str) -> tuple:
    """Where a view's label words occur in the request: (about the character, about someone else, clause).
    'a dog playing in the background' puts 'playing' in the second list."""
    p = (prompt or "").lower()
    mine, theirs, clause = [], [], ""
    for st in sorted(_content_stems(label)):
        for m in re.finditer(r"\b" + re.escape(st) + r"[a-z]*", p):
            before = re.split(r"[.,;:!?]|\bwhile\b|\band\b|\bbut\b|\bas\b", p[max(0, m.start() - 50): m.start()])[-1]
            if re.search(r"\b(?:" + _OTHERS + r")\b", before):
                theirs.append(m.group(0))
                if not clause:
                    end = re.search(r"[.,;:!?]|$", p[m.end():])
                    clause = (before + p[m.start(): m.end() + (end.start() if end else 0)]).strip()
            else:
                mine.append(m.group(0))
    return mine, theirs, clause


def _offsubject_slots(prompt: str, plan: dict) -> list:
    """Keyword-picked pose/face views whose matching words all describe someone else."""
    out = []
    for slot in ("pose", "expression"):
        g = (plan or {}).get(slot) or {}
        if g.get("why") == "keywords" and g.get("label"):
            mine, theirs, _ = words_about(prompt, g["label"])
            if theirs and not mine:
                out.append((slot, g, theirs))
    return out


ACTION_PROMPT = """An image generator draws one main character. From the request below, write what the main character's body is doing as a short phrase a pose library would use: a verb and its object, for example "chopping logs with an axe", "reading a book while looking up at the sky", "riding a bicycle". Ignore what other people or animals do, the place, the clothes and the face. If the request names no action, use "standing". If the complaint about an earlier attempt names a tool or object the character should be using (for example an axe), include it.
Request: "{prompt}"{context}
Reply in JSON only: {{"action": "..."}}"""


def target_action(prompt: str, context: str = "") -> str:
    """The character's own action in the request (cached): what a repair's pose guide must show.
    context = what was wrong with an earlier attempt ("there's still no axe")."""
    import hashlib
    if not prompt:
        return ""
    ctx = re.sub(r"\s+", " ", context or "").strip()[:300]
    def build():
        d = _vision_json(ACTION_PROMPT.format(prompt=prompt[:600], context=(f'\nComplaint about an earlier attempt: "{ctx}"' if ctx else "")), [], 80, 45) or {}
        a = re.sub(r"\s+", " ", str(d.get("action") or "")).strip(" .\"'").lower()
        return {"action": a[:80]} if a else None
    d = _described("action:" + hashlib.sha1((prompt + "|" + ctx).encode()).hexdigest()[:16], "action", build)
    return (d or {}).get("action") or ""


# ----------------------------------------------------------------- lessons
BUILTIN = [
    {"key": "character-framing", "category": "framing", "enabled": 1,
     "title": "Name the reference in character pictures",
     "trigger": {"when": "character_unframed"}, "action": {"prepend": "The same {species} from the reference. "},
     "why": "A character prompt that never mentions the reference lets the model treat the reference as a style sample instead of the person to draw."},
    {"key": "guide-subject-check", "category": "pose", "enabled": 1,
     "title": "Pose and face views must describe the character",
     "trigger": {"when": "offsubject_guide"}, "action": {"drop_offsubject_guides": True},
     "why": "The planner matches library views by words. On 2026-10-03 'a dog playing in the background' picked the owner's 'playing an acoustic guitar' view as the pose and identity picture, so he never cut the logs he was asked to cut. A view whose matching words all describe another person or animal is dropped."},
    {"key": "foreign-guide-identity", "category": "identity", "enabled": 1,
     "title": "Name the character's features when a pose picture shows someone else",
     "trigger": {"when": "foreign_guide"}, "action": {"traits_early": True},
     "why": "A pose or face view drawn from another person carries that person's head: on 2026-10-03 both a guided fix and a fresh picture of the owner came out with the guide's black hair. Naming the character's own features (read once from the reference picture) right after 'the same character from the reference' keeps the likeness."},
    {"key": "gaze-explicit", "category": "gaze", "enabled": 0,
     "title": "Spell out head direction and gaze",
     "trigger": {"when": "gaze_phrase"}, "action": {"gaze_expand": True, "guidance_min": 4.5, "steps_min": 28},
     "why": "Where someone looks is not a pose the library knows, so the plan never represents it; the model keeps the reference's gaze unless the head direction is stated in physical terms."},
    {"key": "pose-conflict-original", "category": "anatomy", "enabled": 0,
     "title": "Plain reference when the chosen view's hands conflict with the request",
     "trigger": {"when": "identity_pose_conflict"}, "action": {"identity_original": True},
     "why": "Using a view that already shows a hand action as the identity picture, then asking for different hand work, makes the model blend both: extra arms and hands."},
    {"key": "anatomy-guard", "category": "anatomy", "enabled": 0,
     "title": "Anatomy guard",
     "trigger": {"when": "always"}, "action": {"append": "One person with exactly two arms and two hands, five fingers on each; hands only where this description puts them.", "steps_min": 28},
     "why": "Stating the limb count and raising the step count removes most extra-hand blends."},
    {"key": "expression-explicit", "category": "expression", "enabled": 0,
     "title": "Describe the face in physical terms and raise guidance",
     "trigger": {"when": "expression_requested"}, "action": {"expr_expand": True, "guidance_min": 4.5, "steps_min": 28},
     "why": "At low guidance the model keeps the mouth it was given; naming eyebrows, eyes and mouth plus guidance 4.5 moves the face."},
    {"key": "garments-first", "category": "garment", "enabled": 0,
     "title": "Garments win the reference budget",
     "trigger": {"when": "garments_over_budget"}, "action": {"drop_guides_for_garments": True},
     "why": "With four references the last garment is the first thing the model drops; a named garment matters more than a pose guide."},
    {"key": "identity-lock", "category": "identity", "enabled": 0,
     "title": "Identity lock",
     "trigger": {"when": "always"}, "action": {"identity_original": True, "identity_traits": True, "append": "Keep the exact face, hair, glasses, facial hair, skin tone and body shape of the reference.", "guidance_max": 3.5},
     "why": "High guidance pulls toward the text and away from the reference; the plain reference, the character's features named in words (read once from the reference picture) and a guidance ceiling keep the likeness."},
    {"key": "scene-literal", "category": "scene", "enabled": 0,
     "title": "Scene follows the text literally",
     "trigger": {"when": "always"}, "action": {"append": "The setting, background and props follow this description exactly.", "guidance_min": 4.0},
     "why": "Reference pictures carry their own background; a stated setting needs emphasis and guidance to win."},
    {"key": "style-lock", "category": "style", "enabled": 0,
     "title": "Match the reference's art style",
     "trigger": {"when": "character_same_style"}, "action": {"append": "Same art style as the reference: same line weight, flat colours and outlines."},
     "why": "Guides from other pictures and plain prompts drift toward a generic style unless the reference's style is named. Never applied when the request asks for a different style."},
    {"key": "restyle-explicit", "category": "style", "enabled": 0,
     "title": "Restyle requests leave the reference's look behind",
     "trigger": {"when": "restyle_requested"}, "action": {"restyle": True, "guidance_min": 4.0},
     "why": "Kontext copies the look of its reference pictures: on 2026-10-03 three Ghost in the Shell attempts kept the reference's thick cartoon outlines. A restyle names the character's features, tells the model to drop the reference's line art and colouring, and raises guidance so the text wins."},
    {"key": "artifact-guard", "category": "artifact", "enabled": 0,
     "title": "Clean single picture",
     "trigger": {"when": "always"}, "action": {"append": "One single picture of one person, no text, no captions, no watermark, no split screen.", "steps_min": 28},
     "why": "Multi-reference prompts sometimes reproduce the layout of the references (captions, split screens) unless told not to."},
]


def _db():
    con = sqlite3.connect(CFG["db"], timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init(db_path: Path, out_dir: Path, **kw):
    CFG.update({"db": str(db_path), "out": Path(out_dir)}); CFG.update(kw)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, image_id TEXT, verdict TEXT, issues TEXT, note TEXT,
            diagnosis TEXT, job TEXT, lessons_applied TEXT, lessons_changed TEXT, critic TEXT, fix_image_id TEXT, created_at TEXT);
        CREATE INDEX IF NOT EXISTS fb_img ON feedback(image_id);
        CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, key TEXT UNIQUE, category TEXT, title TEXT, why TEXT,
            trigger TEXT, action TEXT, enabled INTEGER, source TEXT, hits INTEGER DEFAULT 0, applied INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, evidence TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS critiques(image_id TEXT PRIMARY KEY, result TEXT, model TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS outcomes(image_id TEXT PRIMARY KEY, feedback_id INTEGER, kind TEXT, flagged TEXT, cleared TEXT, remaining TEXT, created_at TEXT, introduced TEXT DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS descriptions(key TEXT PRIMARY KEY, kind TEXT, data TEXT, model TEXT, created_at TEXT);
        """)
        if "introduced" not in [r[1] for r in con.execute("PRAGMA table_info(outcomes)")]:
            con.execute("ALTER TABLE outcomes ADD COLUMN introduced TEXT DEFAULT '[]'")
        cols = [r[1] for r in con.execute("PRAGMA table_info(lessons)")]
        for c in ("critic_hits", "auto_wins", "auto_losses", "user_edited", "owner_off"):
            if c not in cols:
                con.execute(f"ALTER TABLE lessons ADD COLUMN {c} INTEGER DEFAULT 0")
        # keep built-in definitions current (actions/why change between versions; enabled state and counts are kept,
        # and a built-in the owner edited keeps the owner's text)
        for b in BUILTIN:
            con.execute("UPDATE lessons SET action=?, trigger=?, why=?, title=? WHERE key=? AND source='builtin' AND COALESCE(user_edited, 0)=0",
                        (json.dumps(b["action"]), json.dumps(b["trigger"]), b["why"], b["title"], b["key"]))
        now = time.strftime("%Y-%m-%d %H:%M")
        for b in BUILTIN:
            con.execute("INSERT OR IGNORE INTO lessons(key, category, title, why, trigger, action, enabled, source, evidence, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (b["key"], b["category"], b["title"], b["why"], json.dumps(b["trigger"]), json.dumps(b["action"]), b["enabled"], "builtin", "[]", now, now))


def _row(r) -> dict:
    d = dict(r)
    for k in ("trigger", "action", "evidence", "issues", "diagnosis", "job", "lessons_applied", "lessons_changed", "critic", "result"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def lessons(enabled_only: bool = False) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM lessons" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id").fetchall()
    return [_row(r) for r in rows]


def _num(v, lo: float, hi: float, cast=float):
    """A number from a rule, clamped to a sane range; None for anything that is not one ('4-5', '', None)."""
    if isinstance(v, bool):
        return None
    try:
        x = cast(float(v))
    except (TypeError, ValueError):
        return None
    return max(cast(lo), min(cast(hi), x))


NUMERIC = {"guidance_min": (1.0, 10.0, float), "guidance_max": (1.0, 10.0, float), "steps_min": (4, 60, int)}


def _clean_action(v) -> dict:
    a = dict(v) if isinstance(v, dict) else {"append": str(v)}
    for k, (lo, hi, cast) in NUMERIC.items():
        if k in a:
            n = _num(a[k], lo, hi, cast)
            if n is None:
                a.pop(k)
            else:
                a[k] = n
    return a


def _clean_trigger(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, (list, tuple)):
        return {"when": "keywords", "any": [str(x).strip() for x in v if str(x).strip()]}
    return {"when": "keywords", "any": [s.strip() for s in str(v).split(",") if s.strip()]}


def update_lesson(lid: int, fields: dict) -> dict | None:
    allowed = {"enabled": lambda v: 1 if v in (1, True, "1", "true") else 0, "title": str, "why": str,
               "trigger": lambda v: json.dumps(_clean_trigger(v)),
               "action": lambda v: json.dumps(_clean_action(v))}
    sets, vals = [], []
    for k, cast in allowed.items():
        if k in fields:
            sets.append(f"{k}=?"); vals.append(cast(fields[k]))
    if not sets:
        return None
    if any(k in fields for k in ("title", "why", "trigger", "action")):
        sets.append("user_edited=1")                                # a restart no longer resets this built-in's text
    if "enabled" in fields:
        sets.append("owner_off=?"); vals.append(0 if allowed["enabled"](fields["enabled"]) else 1)   # the automatic check never overrides an owner's off
    sets.append("updated_at=?"); vals.append(time.strftime("%Y-%m-%d %H:%M")); vals.append(lid)
    with _db() as con:
        con.execute(f"UPDATE lessons SET {', '.join(sets)} WHERE id=?", vals)
        r = con.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
    return _row(r) if r else None


def delete_lesson(lid: int) -> bool:
    with _db() as con:
        n = con.execute("DELETE FROM lessons WHERE id=? AND source!='builtin'", (lid,)).rowcount
        if n == 0:                                                  # built-ins are only ever switched off
            con.execute("UPDATE lessons SET enabled=0 WHERE id=?", (lid,))
    return True


def create_lesson(category: str, title: str, keywords: list[str], append: str = "", prepend: str = "",
                  guidance_min=None, steps_min=None, why: str = "", source: str = "user", enabled: int = 1,
                  evidence: list | None = None) -> dict:
    guidance_min, steps_min = _num(guidance_min, 1.0, 10.0), _num(steps_min, 4, 60, int)   # a learner may answer "4-5"
    key = re.sub(r"[^a-z0-9]+", "-", f"{source}-{title}".lower()).strip("-")[:60] or f"{source}-{int(time.time())}"
    trigger = {"when": "keywords", "any": [k for k in keywords if k]} if keywords else {"when": "always"}
    action = {k: v for k, v in (("append", append), ("prepend", prepend), ("guidance_min", guidance_min), ("steps_min", steps_min)) if v}
    now = time.strftime("%Y-%m-%d %H:%M")
    with _db() as con:
        n = 2; base = key
        while con.execute("SELECT 1 FROM lessons WHERE key=?", (key,)).fetchone():
            key = f"{base}-{n}"; n += 1
        con.execute("INSERT INTO lessons(key, category, title, why, trigger, action, enabled, source, evidence, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (key, category if category in ISSUES or category == "framing" else "other", title[:120], why[:600], json.dumps(trigger), json.dumps(action),
                     1 if enabled else 0, source, json.dumps(evidence or []), now, now))
        r = con.execute("SELECT * FROM lessons WHERE key=?", (key,)).fetchone()
    return _row(r)


def _bump(key: str, col: str, evidence_id: str | None = None):
    with _db() as con:
        if evidence_id:
            r = con.execute("SELECT evidence FROM lessons WHERE key=?", (key,)).fetchone()
            ev = json.loads(r["evidence"] or "[]") if r else []
            if evidence_id not in ev:
                ev = (ev + [evidence_id])[-12:]
            con.execute(f"UPDATE lessons SET {col}={col}+1, evidence=?, updated_at=? WHERE key=?", (json.dumps(ev), time.strftime("%Y-%m-%d %H:%M"), key))
        else:
            con.execute(f"UPDATE lessons SET {col}={col}+1, updated_at=? WHERE key=?", (time.strftime("%Y-%m-%d %H:%M"), key))


def strengthen(issues: list[str], image_id: str, prompt: str = "") -> list[dict]:
    """A thumbs-down in a category switches that category's built-in lessons on (first time)
    and counts a hit on them. Returns the lessons that changed, with what changed."""
    changed = []
    with _db() as con:
        rows = con.execute("SELECT * FROM lessons WHERE source='builtin'").fetchall()
    for r in rows:
        d = _row(r)
        if d["category"] not in issues or not _fits(d, prompt):
            continue
        was = d["enabled"]
        with _db() as con:
            con.execute("UPDATE lessons SET enabled=1 WHERE key=?", (d["key"],))
        _bump(d["key"], "hits", image_id)
        changed.append({"key": d["key"], "title": d["title"], "category": d["category"], "why": d["why"],
                        "change": "switched on" if not was else "strengthened", "hits": d["hits"] + 1,
                        "applies": _describe_trigger(d["trigger"])})
    return changed


def _describe_trigger(t: dict) -> str:
    w = (t or {}).get("when", "always")
    return {"always": "every picture", "character": "every character picture", "character_unframed": "character prompts that never mention the reference",
            "gaze_phrase": "prompts that say where the person looks", "identity_pose_conflict": "when the chosen view's hand action conflicts with the request",
            "expression_requested": "when a facial expression is asked for", "garments_over_budget": "when garments would not fit in the reference budget",
            "keywords": "prompts mentioning: " + ", ".join((t or {}).get("any", []))}.get(w, w)


CRITIC_SWITCH_ON = 3      # pictures the critic must flag in a category before a lesson switches itself on
AUTO_OK = {"gaze-explicit", "anatomy-guard", "expression-explicit", "artifact-guard", "pose-conflict-original"}   # narrow, prompt-level rules only


def note_critique(image_id: str, result: dict) -> list[dict]:
    """Self-learning from the automatic check: each MAJOR issue the critic finds counts toward the
    built-in lesson of that category; after CRITIC_SWITCH_ON different pictures a narrow lesson
    switches itself on. Broad rules (identity lock, garments first, scene/style) wait for the owner."""
    cats = {c.get("category") for c in (result or {}).get("issues") or [] if c.get("severity", "major") != "minor"}
    changed = []
    try:
        req = request_of(json.loads((Path(CFG["out"]) / f"{Path(image_id).name}.json").read_text()))
    except Exception:
        req = ""
    for cat in cats:
        if cat not in ISSUES:
            continue
        with _db() as con:
            rows = [_row(r) for r in con.execute("SELECT * FROM lessons WHERE source='builtin' AND category=?", (cat,)).fetchall()]
        for d in rows:
            ev = d.get("evidence") or []
            if image_id in ev or not _fits(d, req):
                continue                                            # a re-check of the same picture counts once
            n = int(d.get("critic_hits") or 0) + 1
            with _db() as con:
                con.execute("UPDATE lessons SET critic_hits=?, evidence=?, updated_at=? WHERE key=?",
                            (n, json.dumps((ev + [image_id])[-12:]), time.strftime("%Y-%m-%d %H:%M"), d["key"]))
                if not d["enabled"] and n >= CRITIC_SWITCH_ON and d["key"] in AUTO_OK and not d.get("owner_off"):
                    con.execute("UPDATE lessons SET enabled=1 WHERE key=?", (d["key"],))
                    changed.append({"key": d["key"], "title": d["title"], "category": cat, "change": f"switched on by the automatic check after {n} pictures"})
    return changed


def record_outcome(job: dict, result: dict) -> dict | None:
    """After a fix or a regenerate answering a thumbs-down, the automatic check says which of the
    flagged flaws cleared. That measures the fix strategy per flaw, and credits or debits the
    lessons that were applied to a regenerate (auto_wins / auto_losses, kept apart from your grades)."""
    fid = job.get("feedback_id")
    if not fid or not result or "error" in result:
        return None
    with _db() as con:
        r = con.execute("SELECT issues FROM feedback WHERE id=?", (int(fid),)).fetchone()
    if not r:
        return None
    flagged = [c for c in json.loads(r["issues"] or "[]") if c in ISSUES and c != "other"]
    if not flagged:
        return None
    seen = {c.get("category") for c in result.get("issues") or [] if c.get("severity", "major") != "minor"}
    remaining = [c for c in flagged if c in seen]
    cleared = [c for c in flagged if c not in seen]
    # a new major flaw the graded picture did not have is a regression: a fix that breaks the face is not a fix
    before = set()
    src = job.get("repair_of") or job.get("retry_of")
    if src:
        prev = get_critique(src) or {}
        before = {c.get("category") for c in prev.get("issues") or []}
    introduced = sorted(c for c in seen if c not in flagged and c not in before and c in ("identity", "anatomy", "artifact", "style"))
    kind = ("guided" if job.get("guide_used") else "fix") if job.get("repair_of") else "retry"
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO outcomes(image_id, feedback_id, kind, flagged, cleared, remaining, introduced, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (job["id"], int(fid), kind, json.dumps(flagged), json.dumps(cleared), json.dumps(remaining), json.dumps(introduced), time.strftime("%Y-%m-%d %H:%M")))
        for key in job.get("lessons_applied") or []:
            L = con.execute("SELECT category FROM lessons WHERE key=?", (key,)).fetchone()
            if L and L["category"] in flagged:
                con.execute(f"UPDATE lessons SET {'auto_wins' if L['category'] in cleared else 'auto_losses'}={'auto_wins' if L['category'] in cleared else 'auto_losses'}+1 WHERE key=?", (key,))
    return {"kind": kind, "flagged": flagged, "cleared": cleared, "remaining": remaining, "introduced": introduced}


ROUTES = ("fix", "guided", "retry")


def outcome_stats() -> dict:
    """Per flaw and route: tries, and how often the flaw cleared WITHOUT breaking something else."""
    with _db() as con:
        rows = con.execute("SELECT kind, flagged, cleared, introduced FROM outcomes").fetchall()
    out = {}
    for r in rows:
        cl = set(json.loads(r["cleared"] or "[]"))
        broke = json.loads(r["introduced"] or "[]")
        for c in json.loads(r["flagged"] or "[]"):
            d = out.setdefault(c, {k: {"tries": 0, "cleared": 0, "broke": 0} for k in ROUTES})
            k = r["kind"] if r["kind"] in ROUTES else "fix"
            d[k]["tries"] += 1
            d[k]["cleared"] += 1 if (c in cl and not broke) else 0
            d[k]["broke"] += 1 if broke else 0
    return out


GUIDED_PRIOR = {"gaze": 0.7, "pose": 0.6}      # head and body changes: a picture beats words (measured on 2026-10-03)
TEXT_PRIOR = {"gaze": 0.2, "pose": 0.35}


def choose_route(issues: list[str]) -> tuple[str, list[str]]:
    """Pick fix / guided for the flagged flaws from the measured record (with a small prior), and say why."""
    st = outcome_stats()
    if not any(c in ("gaze", "pose") for c in issues):
        return "fix", []
    why, score = [], {"fix": 0.0, "guided": 0.0}
    for c in issues:
        if c not in ("gaze", "pose"):
            continue
        d = st.get(c, {})
        for route, prior in (("fix", TEXT_PRIOR.get(c, 0.5)), ("guided", GUIDED_PRIOR.get(c, 0.5))):
            t = d.get(route, {}).get("tries", 0); w = d.get(route, {}).get("cleared", 0)
            rate = (w + 2 * prior) / (t + 2)                          # Beta-style smoothing: data wins once there is some
            score[route] += rate
            if t:
                why.append(f"{ISSUES[c]['label'].lower()}: {'targeted fix' if route == 'fix' else 'guided fix'} cleared it {w} of {t} times")
    return ("guided" if score["guided"] >= score["fix"] else "fix"), why


def outcome(lessons_applied: list, verdict: str, issues: list[str]):
    """Credit or debit the lessons that were applied to a picture that just got graded."""
    for key in lessons_applied or []:
        with _db() as con:
            r = con.execute("SELECT category FROM lessons WHERE key=?", (key,)).fetchone()
        if not r:
            continue
        if verdict == "up":
            _bump(key, "wins")
        elif r["category"] in issues:
            _bump(key, "losses")
        else:
            _bump(key, "wins")                                      # applied, and its own category stayed clean


# ------------------------------------------------------------- apply lessons
def apply(prompt: str, plan: dict | None, mode: str, species: str = "character", max_refs: int | None = None,
          character: str | None = None) -> dict:
    """Run the enabled lessons over one job before it is sent. Returns the new prompt, the
    (possibly changed) plan, setting floors/ceilings, and the lessons that fired."""
    max_refs = max_refs or CFG["max_refs"]
    text = " " + prompt.lower() + " "
    gaze = gaze_phrases(prompt)
    out = {"prompt": prompt, "plan": plan, "guidance_min": None, "guidance_max": None, "steps_min": None, "applied": [], "notes": []}
    expr_requested = bool(plan and plan.get("expression")) or bool(CFG["expr_words"](prompt))
    for L in lessons(enabled_only=True):
        t, a = L["trigger"] or {}, L["action"] or {}
        w = t.get("when", "always")
        fires = False
        if w == "always":
            fires = mode in ("character", "edit")
        elif w == "character":
            fires = mode == "character"
        elif w == "character_same_style":
            fires = mode == "character" and not is_restyle(prompt)
        elif w == "restyle_requested":
            fires = mode in ("character", "edit") and is_restyle(prompt)
        elif w == "character_unframed":
            fires = mode == "character" and not re.search(_UNFRAMED, text)
        elif w == "gaze_phrase":
            fires = bool(gaze)
        elif w == "expression_requested":
            fires = expr_requested and mode in ("character", "edit")
        elif w == "identity_pose_conflict":
            if plan and mode == "character":
                ident, pose = plan.get("identity") or {}, plan.get("pose")
                if ident.get("mode") == "own" and pose and pose.get("ref") == ident.get("ref"):
                    lbl = " " + (pose.get("label") or "").lower() + " "
                    asked = [m for m in re.findall(_HAND_ACTIONS, text) if m not in lbl]
                    fires = bool(gaze) or bool(asked)
        elif w == "garments_over_budget":
            if plan and mode == "character":
                g = plan.get("garments") or []
                n = 1 + (1 if plan.get("pose") and plan["pose"].get("ref") != plan["identity"].get("ref") else 0) \
                      + (1 if plan.get("expression") and plan["expression"].get("ref") != plan["identity"].get("ref") else 0) + len(g)
                fires = bool(g) and n > max_refs
        elif w == "foreign_guide":
            fires = bool(plan) and mode == "character" and any(((plan.get(s) or {}).get("mode") == "guide") for s in ("pose", "expression"))
        elif w == "offsubject_guide":
            fires = bool(plan) and mode in ("character", "edit") and bool(_offsubject_slots(prompt, plan))
        elif w == "keywords":
            fires = any(re.search(r"\b" + re.escape(k.lower()) + r"\b", text) for k in t.get("any", []) if k)
        if not fires:
            continue
        # ---- actions
        if a.get("identity_original") and plan and mode == "character":
            ident = plan.get("identity") or {}
            if ident.get("ref") != "original":
                old = ident.get("ref")
                plan["identity"] = {"ref": "original", "label": "original", "mode": "original"}
                if plan.get("pose") and plan["pose"].get("ref") == old:
                    plan["pose"] = None                             # the conflicting view is dropped, not chained as a guide
                out["notes"].append(f"{L['title']}: plain reference used instead of '{ident.get('label')}'")
        if a.get("drop_offsubject_guides") and plan:
            for slot, g, theirs in _offsubject_slots(prompt, plan):
                if (plan.get("identity") or {}).get("ref") == g.get("ref"):
                    plan["identity"] = {"ref": "original", "label": "original", "mode": "original"}
                plan[slot] = None
                out["notes"].append(f"{L['title']}: dropped the {slot} view '{g.get('label')}', matched only from '{theirs[0]}', which describes someone else in the request")
        if a.get("drop_guides_for_garments") and plan:
            while plan.get("garments"):
                n = 1 + (1 if plan.get("pose") and plan["pose"].get("ref") != plan["identity"].get("ref") else 0) \
                      + (1 if plan.get("expression") and plan["expression"].get("ref") != plan["identity"].get("ref") else 0) + len(plan["garments"])
                if n <= max_refs:
                    break
                if plan.get("expression") and plan["expression"].get("ref") != plan["identity"].get("ref"):
                    out["notes"].append(f"{L['title']}: expression guide dropped to fit the garments"); plan["expression"] = None
                elif plan.get("pose") and plan["pose"].get("ref") != plan["identity"].get("ref"):
                    out["notes"].append(f"{L['title']}: pose guide dropped to fit the garments"); plan["pose"] = None
                else:
                    break
        if a.get("prepend"):
            p = a["prepend"].replace("{species}", species or "character")
            if p.strip().lower() not in out["prompt"].lower():
                out["prompt"] = p + out["prompt"]
        if a.get("gaze_expand") and gaze and "head direction:" not in out["prompt"].lower():
            # the head direction leads the description: prompt order is weight, and the reference's own gaze wins otherwise
            head = "Head direction: " + "; ".join(g.split(": ", 1)[-1] for g in gaze) + ". "
            m = re.match(r"(\s*the same [^.]*?from the reference[.,]?\s*)", out["prompt"], flags=re.I)
            out["prompt"] = (m.group(1) + head + out["prompt"][m.end():]) if m else head + out["prompt"]
        if a.get("expr_expand"):
            ew = CFG["expr_words"](prompt)
            if ew and ew.strip(": ").lower() not in out["prompt"].lower():
                out["prompt"] = out["prompt"].rstrip() + " The face" + ew + "."
        if a.get("restyle") and "leave the reference" not in out["prompt"].lower():
            tr = [t for t in (character_traits(character) or {}).get("traits") or [] if not _negated(t, prompt)] if character else []
            s = ("Redraw this character completely in the art style the request asks for" + (f" ({_style_phrase(prompt)})" if _style_phrase(prompt) else "") +
                 (": keep who they are (" + ", ".join(tr) + ")" if tr else ": keep who they are") +
                 " but leave the reference's line art, outline weight and flat colouring behind. ")
            m = re.match(r"(\s*the same [^.]*?from the reference[^.]*\.\s*)", out["prompt"], flags=re.I)
            out["prompt"] = (m.group(1) + s + out["prompt"][m.end():]) if m else s + out["prompt"]
            out["notes"].append(f"{L['title']}: the request asks for a new style, so the reference's look is not kept")
        if a.get("traits_early") and character:
            early = [t for t in (character_traits(character) or {}).get("traits") or [] if not _negated(t, prompt)]
            feat = ", ".join(early)
            if early and feat.lower() not in out["prompt"].lower():
                m = re.match(r"(\s*the same [^.]*?from the reference)[.,]?\s*", out["prompt"], flags=re.I)
                out["prompt"] = (m.group(1) + ": " + feat + ". " + out["prompt"][m.end():]) if m else f"The same person as the reference: {feat}. " + out["prompt"]
                out["notes"].append(f"{L['title']}: named {feat}")
        tr = []
        if a.get("identity_traits") and character:                  # named features replace the generic sentence
            tr = [t for t in (character_traits(character) or {}).get("traits") or [] if not _negated(t, prompt)]
            if tr and ", ".join(tr).lower() in out["prompt"].lower():
                tr, a = [], {k: v for k, v in a.items() if k != "append"}   # already named early: nothing to add
        if a.get("append") and not tr:
            s = a["append"].strip()
            if s.lower() not in out["prompt"].lower():
                base = out["prompt"].rstrip()
                out["prompt"] = base + ("" if base.endswith((".", "!", "?")) else ".") + " " + s
        if tr:
            s = "Same person as the reference: " + ", ".join(tr) + "."
            if s.lower() not in out["prompt"].lower():
                base = out["prompt"].rstrip()
                out["prompt"] = base + ("" if base.endswith((".", "!", "?")) else ".") + " " + s
                out["notes"].append(f"{L['title']}: named {', '.join(tr)}")
        for k in ("guidance_min", "steps_min"):
            lo, hi, cast = NUMERIC[k]
            v = _num(a.get(k), lo, hi, cast)                        # a bad stored value is skipped, never raised inside a job
            if v is not None:
                out[k] = max(out[k] or 0, v)
        v = _num(a.get("guidance_max"), *NUMERIC["guidance_max"])
        if v is not None:
            out["guidance_max"] = min(out["guidance_max"] or 99, v)
        out["applied"].append({"key": L["key"], "title": L["title"], "category": L["category"]})
    if out["guidance_min"] is not None and out["guidance_max"] is not None and out["guidance_min"] > out["guidance_max"]:
        out["notes"].append(f"guidance {out['guidance_min']:g} for what this request asks beats the {out['guidance_max']:g} ceiling of a blanket rule")
    return out


def settle(guidance: float, steps: int, res: dict) -> tuple[float, int]:
    """Ceilings first, floors last: floors come from lessons fired by what this request asks for
    (a gaze, an expression); ceilings come from blanket rules such as the identity lock."""
    if res.get("guidance_max") is not None:
        guidance = min(guidance, res["guidance_max"])
    if res.get("guidance_min") is not None:
        guidance = max(guidance, res["guidance_min"])
    if res.get("steps_min") is not None:
        steps = max(steps, res["steps_min"])
    return guidance, steps


def count_applied(keys: list[str]):
    for k in keys:
        _bump(k, "applied")


# ---------------------------------------------------------------- diagnose
def _plan_summary(job: dict) -> str:
    refs = 1
    guides = job.get("guides_used") or []
    ident = job.get("reference_used") or "original"
    refs += sum(1 for g in guides if g.get("ref") != ident) + len(job.get("garments_used") or [])
    if job.get("edit_of") and not job.get("characters"):
        bits = [("targeted fix" if job.get("repair_of") else "edit") + f" of {job.get('edit_of')}"]
    else:
        bits = [f"identity: {job.get('reference_label') or ident} ({job.get('reference_mode') or 'original'})"]
    for g in guides:
        bits.append(f"{'face' if g.get('slot') == 'expression' else 'pose'}: {g.get('label')} ({g.get('mode')})")
    if job.get("garments_used"):
        bits.append("wearing: " + ", ".join(g.get("label", "") for g in job["garments_used"]))
    bits.append(f"{refs} reference{'s' if refs != 1 else ''} · guidance {job.get('guidance')} · {job.get('steps')} steps")
    return " · ".join(bits), refs


def diagnose(job: dict, issues: list[str], note: str = "", critic: dict | None = None) -> list[str]:
    """Why this picture probably went wrong, from what the plan did."""
    out = []
    prompt = request_of(job)
    sent = job.get("prompt_sent") or job.get("prompt") or prompt
    ident_mode = job.get("reference_mode") or "original"
    ident_lbl = job.get("reference_label") or "original"
    guides = job.get("guides_used") or []
    pose = next((g for g in guides if g.get("slot") == "pose"), None)
    expr = next((g for g in guides if g.get("slot") == "expression"), None)
    summary, nrefs = _plan_summary(job)
    gaze = gaze_phrases(prompt)
    verbatim = sent.strip() == prompt.strip()
    if "anatomy" in issues:
        if ident_mode == "own" and pose and pose.get("ref") == job.get("reference_used"):
            out.append(f"The identity reference was the character's own '{ident_lbl}' view, which already shows a hand action. The request asked for different hand work"
                       + (" and a different gaze" if gaze else "") + (", and the prompt went to the model verbatim with no instruction to replace that pose" if verbatim else "")
                       + ", so the model blended both poses: that is where the extra hand comes from.")
        elif nrefs >= 3:
            out.append(f"{nrefs} references were chained; with that many the model sometimes merges hands from two of them.")
        else:
            out.append(f"Extra limbs usually come from two conflicting hand instructions, or from too few steps ({job.get('steps')}) at guidance {job.get('guidance')}.")
    if any(c in issues for c in ("pose", "scene", "anatomy", "expression")):
        for g in guides:
            if g.get("why") != "keywords" or not g.get("label"):
                continue
            mine, theirs, clause = words_about(prompt, g["label"])
            also = " It was also the identity picture, so the whole figure was built on it." if g.get("ref") == job.get("reference_used") else ""
            if theirs and not mine:
                out.append(f"The {g['slot']} view '{g['label']}' was picked only from the word '{theirs[0]}' in '{clause}', which describes someone else, not the character; "
                           f"the picture followed that {g['slot']} instead of what was asked.{also} The lesson 'Pose and face views must describe the character' now drops such views.")
            elif g.get("slot") == "pose" and ("pose" in issues or "scene" in issues) and mine:
                out.append(f"The pose view '{g['label']}' was picked from the words {', '.join(repr(w) for w in sorted(set(mine))[:3])} in the request.{also} "
                           "If that is not what the character should be doing, that view pulled the picture away from the request.")
    if "gaze" in issues:
        if gaze:
            used = pose and pose.get("ref") != job.get("reference_used")
            out.append(f"'{gaze[0].split(':')[0]}' is a head-direction instruction. " + (f"The plan chained the pose view '{pose.get('label')}', but" if used else "The plan had no pose view for it, so")
                       + " the reference's own gaze (" + ident_lbl + ") won" + (", and the sent prompt did not emphasise the head direction" if verbatim else "") + ".")
            try:
                m = CFG["match_pose"](prompt) if CFG.get("match_pose") else None
            except Exception:
                m = None
            if m and not used:                                      # e.g. a pose guide drawn by an earlier fix
                out.append(f"The library now has a pose view for it ('{m[1]}'), so a guided fix and future pictures can use it.")
        else:
            out.append("The prompt never said where the person should look, so the reference's gaze was kept.")
    if "pose" in issues:
        rg = job.get("guide_used") or {}
        act = target_action(prompt) if rg.get("label") else ""
        if rg.get("label") and act and not _shares_words(rg["label"], act):
            out.append(f"This fix was steered by the pose picture '{rg['label']}', which does not show what was asked ({act}); "
                       "the fix could only move the body toward the wrong pose. Repair guides are now chosen from the character's own action, "
                       "never from the view that caused the flaw, and a missing pose is drawn first.")
        elif pose and pose.get("why") == "repair guide":
            out.append(f"This fix was steered by the pose picture '{pose.get('label')}' and still missed the pose; the outcome table records it, "
                       "so the next fix of this flaw weighs the other routes.")
        elif pose and pose.get("mode") == "guide":
            out.append(f"The pose '{pose.get('label')}' came from another character's library picture; cross-character pose guides transfer weakly. Rendering this character's own version of that pose (Characters tab) fixes it for good.")
        elif not pose:
            out.append("No pose view matched the wording, so only the plain reference steered the body. Naming the action with a library phrase (or picking a view by hand) gives the model something to copy.")
        else:
            out.append(f"The pose view '{pose.get('label')}' was used, but at guidance {job.get('guidance')} the model may follow the identity picture more than the pose.")
    if "expression" in issues:
        if expr and expr.get("mode") == "guide":
            out.append(f"The face '{expr.get('label')}' came from another character's picture and transfers weakly; an own view of that expression works far better.")
        elif not expr:
            out.append("No expression view matched the wording, so the face stayed as the reference shows it. Use a library expression word, or describe eyebrows, eyes and mouth.")
        else:
            out.append(f"The expression view was used but guidance {job.get('guidance')} may be too low to move the mouth away from the reference's.")
    if "garment" in issues:
        if not job.get("garments_used"):
            out.append("No garment was matched from the wording, so nothing was worn on purpose. Pick it in the Wearing row or use a library name.")
        elif nrefs >= 3:
            out.append(f"{nrefs} references competed for the model's attention and the garment is the first thing dropped. Fewer guides, or the character's own derived version of the garment, holds better.")
        else:
            out.append("The garment reference was present; the model ignored part of it. A repair edit usually restores it.")
    if "identity" in issues:
        bits = []
        if ident_mode != "original":
            bits.append(f"the identity picture was '{ident_lbl}' rather than the saved reference")
        if any(g.get("mode") == "guide" for g in guides):
            bits.append("a guide from another character was chained in")
        if (job.get("guidance") or 0) >= 4:
            bits.append(f"guidance {job.get('guidance')} pulls toward the text and away from the reference")
        out.append("Likeness slipped because " + (", ".join(bits) if bits else "the model drifted") + ". The plain reference, lower guidance and no cross-character guides keep the face.")
    if "scene" in issues:
        out.append("Reference pictures carry their own background and framing; a stated setting needs to come late in the prompt with emphasis, or the reference's scene wins.")
    if "style" in issues:
        if is_restyle(prompt):
            out.append("The request asks for a different art style, but Kontext copies the look of its reference pictures, so the reference's outlines and flat colours carried through. "
                       "A restyle needs the new style named in physical terms (line work, shading, proportions) and higher guidance; the lesson 'Restyle requests leave the reference's look behind' does that.")
        else:
            out.append("The style drifted because the prompt did not name the reference's art style; guides from other pictures and plain wording pull toward a generic look.")
    if "artifact" in issues:
        out.append(f"{nrefs} references were given; with several, the model sometimes copies their layout (captions, split screens, a second person).")
    if "other" in issues and note:
        out.append("Noted as a new kind of flaw; the learner can turn repeated notes like this into a rule.")
    if critic and critic.get("summary"):
        out.append("The critic model saw: " + str(critic["summary"]).strip())
    out.append("Plan used → " + summary + (" · sent verbatim" if verbatim else ""))
    return out


# ----------------------------------------------------------------- repair
def build_repair(job: dict, issues: list[str], note: str = "", critic: dict | None = None) -> dict:
    """An edit of the same picture that changes only what was flagged, plus whether a fresh
    picture is the better route (identity or style flaws)."""
    root = root_job(job)
    prompt = root.get("prompt") or job.get("prompt") or ""
    note = re.sub(r"\s+", " ", note or "").strip()
    if len(note) > 300:                                             # the generator cuts prompts at 2000 characters
        note = note[:300].rsplit(" ", 1)[0] + "…"
    details = {}
    for c in (critic or {}).get("issues") or []:
        cat = c.get("category")
        if cat in ISSUES and c.get("detail") and cat not in details:
            details[cat] = c["detail"].strip().rstrip(".")[:200]
    gaze = gaze_phrases(prompt)
    hands = (critic or {}).get("hands_list") or []
    if "anatomy" in issues and len(hands) > 2:                      # keep the hands doing what was asked, drop the rest
        req = {w for w in re.findall(r"[a-z]+", prompt.lower()) if len(w) > 3}
        keep = [h for h in hands if any(w in h.lower() for w in req)]
        drop = [h for h in hands if h not in keep]
        if keep and drop and len(keep) <= 2:
            details["anatomy"] = ("keep only the " + ("two hands " if len(keep) == 2 else "hand ") + " and ".join(keep) +
                                  "; remove the hand " + " and the hand ".join(drop) + " completely")
    parts = []
    order = {"gaze": 0, "pose": 1, "expression": 2}                 # the hardest change goes first: prompt order is weight
    for cat in sorted(issues, key=lambda c: order.get(c, 9)):
        if cat not in ISSUES:
            continue
        tmpl = ISSUES[cat]["repair"]
        if tmpl is None:
            continue
        d = details.get(cat, "")
        if cat == "gaze" and gaze:
            now = details.get("gaze", "")
            d = "; ".join(g.split(": ", 1)[-1] for g in gaze) + (f" (it is wrong now: {now[0].lower() + now[1:]})" if now else "")
        detail = (": " + d) if d else ""
        if cat == "style" and is_restyle(prompt):
            sp = _style_phrase(prompt)
            parts.append("Redraw the whole picture in the requested art style" + (f" ({sp})" if sp else "") +
                         ": change the line work, shading and proportions to that style and drop the thick black outlines and flat colours" + detail + ".")
            continue
        if cat == "other":
            parts.append((note or "Change it as described.").strip())
        else:
            parts.append(tmpl.format(detail=detail, detail_bare=note))
    if note and "other" not in issues:
        parts.append("The person who graded it said: " + note.strip().rstrip(".") + ".")
    moves = any(c in issues for c in ("gaze", "pose", "expression"))
    traits = character_traits(character_of(job))
    kept = keep_list(job.get("id"))
    named = keep_sentence(traits, kept, issues, critic, moves)
    if named:                                                       # concrete words keep the likeness; "keep the identity" alone did not
        keep = " " + named + (" Only the head, body or face changes named above happen." if moves else "")
    else:
        keep = (" Keep everything else the same: the scene, colours, art style, clothing and the person's identity (the same facial features, hair, glasses and facial hair); only the head, body or face changes named above happen."
                if moves else " Keep everything else exactly the same: the scene, composition, colours, art style, and the person's face, hair, body and clothing.")
    body = " ".join(parts)
    room = 1500 - len(keep)                                         # guide preface (~350) + body + keep stay under 2000
    if len(body) > room:
        body = body[:max(room, 200)].rsplit(" ", 1)[0].rstrip(",;:") + "."
    text = body + keep
    regen = [c for c in issues if c in REGENERATE_ISSUES]
    route, route_why = choose_route(issues)
    weak = []
    for c, d in outcome_stats().items():
        if route == "guided" and c in ("gaze", "pose"):
            continue                                                # the guided route is the answer to weak text fixes here
        f, r = d["fix"], d["retry"]
        if c in issues and f["tries"] >= 2 and f["cleared"] / f["tries"] < 0.5:
            weak.append((c, f, r))
    guide = None
    if route == "guided":
        head = [g.split(": ", 1)[-1] for g in gaze]
        what = ("body pose and head direction" if "pose" in issues else "pose and head direction") if "gaze" in issues else "body pose"
        action = target_action(prompt, "; ".join(x for x in (details.get("pose", ""), note) if x)) if "pose" in issues else ""   # the character's own action, not the dog's
        caused = {str(g.get("ref")) for g in (root.get("guides_used") or []) + (job.get("guides_used") or [])} | {str(root.get("reference_used"))}
        match = CFG["match_pose"](action or prompt) if CFG.get("match_pose") else None
        if match and (match[0] in caused or (action and not _shares_words(match[1], action))):
            match = None                                            # the view that caused the flaw, or one about something else
        if match:
            guide = {"tag": match[0].removeprefix("lib:"), "label": match[1], "drawn": False}
        else:
            clean = re.sub(r"(?i)\bthe same [^.,]*? from the reference[.,]?\s*", "", prompt).strip()
            what_to_draw = action or clean.rstrip(".")
            guide = {"synthesize": (f"A simple flat cartoon of one person {what_to_draw}. " + (" ".join(h[0].upper() + h[1:] + "." for h in head) + " " if head else "") +
                                    "The whole figure clearly visible, plain simple background, thick black outlines, flat colours."),
                     "label": (what_to_draw[:70].rstrip(" ,.") or "pose guide").lower(), "drawn": True}
        # Measured 2026-10-03 on one picture: "turn this person's head to match" copied the guide's whole head (black hair
        # on a bald, bearded character); "copy nothing from it, not their face" kept the likeness but froze the head.
        # What worked in a hand-made edit: put the person IN the guide's pose, take only the pose, and name the
        # character's own features (keep sentence below).
        lbl = (guide or {}).get("label") or ""
        text = (f"The first image is the picture to fix. The second image shows a different person in the {what} to use" + (f" ({lbl})" if lbl else "") + ". "
                f"Change the first picture: put the person in exactly the {what} of the second image, replacing their current {what}. "
                f"Take only the {what} from the second image, never that person's hair, glasses, facial hair, skin colour or clothes. " + text)
    out = {"mode": "edit", "prompt": text, "model": "flux-kontext-edit", "edit_of": job.get("id"), "guidance": 4.5, "steps": 28,
           "strategy": route, "guide": guide,
           "keeps": {"character": (traits or {}).get("name"), "traits": (traits or {}).get("traits") or [], "picture": kept or {}},
           "size": next((k for k, (w, h) in CFG.get("sizes", {}).items() if (w, h) == (job.get("w"), job.get("h"))), "square")}
    if regen:
        out["regenerate_recommended"] = True
        out["regenerate_why"] = "An edit keeps the face and the look of the picture, so " + " and ".join(ISSUES[c]["label"].lower() for c in regen) + " need a fresh picture; the lessons just switched on will apply to it."
    if route_why:
        out["track_record"] = route_why
    if weak:
        bits = [f"{ISSUES[c]['label'].lower()}: targeted fixes cleared it {f['cleared']} of {f['tries']} times" + (f", regenerating {r['cleared']} of {r['tries']}" if r["tries"] else "") for c, f, r in weak]
        better = [c for c, f, r in weak if r["tries"] >= 1 and r["cleared"] / r["tries"] > f["cleared"] / f["tries"]]
        out["track_record"] = (out.get("track_record") or []) + bits
        if better or not any(r["tries"] for c, f, r in weak):
            out["regenerate_recommended"] = True
            out["regenerate_why"] = "From past results — " + "; ".join(bits) + ". A fresh picture with the lessons is the better bet." + (" " + out.get("regenerate_why", "") if regen else "")
        else:
            out["regenerate_why"] = "From past results — " + "; ".join(bits) + ". Neither route has worked well for this flaw yet: describe it differently, or pick a pose view by hand."
            out["regenerate_recommended"] = out.get("regenerate_recommended", False)
    rsize = next((k for k, (w, h) in CFG.get("sizes", {}).items() if (w, h) == (root.get("w"), root.get("h"))), out["size"])
    out["regenerate"] = {"prompt": prompt, "model": root.get("model") or job.get("model"), "characters": root.get("characters") or job.get("characters") or [],
                         "reference": "original" if regen else "auto", "garments": "auto", "style": root.get("style") or "none", "size": rsize, "retry_of": job.get("id")}
    return out


# ------------------------------------------------------ who and what to keep
TRAITS_PROMPT = """This is the reference picture of a recurring character. List the permanent features someone needs in order to redraw exactly this character: head hair (or bald) and its colour, facial hair, eyewear, skin tone, apparent age and build, and any distinctive marks. Do NOT describe clothing, pose, expression, background or drawing style.
Reply in JSON only: {"subject": "one or two words for what the character is, for example man, woman, boy, girl, dog, robot", "traits": ["short concrete phrase", "..."]}
Give 3 to 6 traits, the most distinctive first, each only a few words."""

KEEP_PROMPT = """Describe only what must stay unchanged when this picture is edited. List the clothing and headgear the main person wears (each item with its colour), the objects they hold, and the setting: what they sit or stand on, and the background. Do NOT describe the person's face, hair, body, pose, gaze, hands or expression.
Reply in JSON only: {"clothing": ["..."], "holding": ["..."], "setting": ["..."]}
Each entry only a few words."""

_desc_guard = threading.Lock()
_desc_locks: dict = {}
_desc_failed: dict = {}                 # key -> time of the last failed attempt; retried after ten minutes


def _vision_json(prompt: str, images: list, num_predict: int = 400, timeout: int = 60) -> dict | None:
    payload = {"model": CFG["critic_model"], "stream": False, "keep_alive": "10m",
               "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(b).decode() for b in images]}],
               "options": {"temperature": 0.1, "num_predict": num_predict, "seed": 7}}
    try:
        req = urllib.request.Request(f"{CFG['ollama']}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        text = json.load(urllib.request.urlopen(req, timeout=timeout)).get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", text, flags=re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def _described(key: str, kind: str, build) -> dict | None:
    """One vision description per key: computed once, kept in the db, shared by concurrent callers."""
    def cached():
        with _db() as con:
            r = con.execute("SELECT data FROM descriptions WHERE key=?", (key,)).fetchone()
        return json.loads(r["data"]) if r else None
    d = cached()
    if d is not None or time.time() - _desc_failed.get(key, 0) < 600:
        return d
    with _desc_guard:
        lk = _desc_locks.setdefault(key, threading.Lock())
    with lk:
        d = cached()
        if d is not None:
            return d
        d = build()
        if not d:
            _desc_failed[key] = time.time()
            return None
        with _db() as con:
            con.execute("INSERT OR REPLACE INTO descriptions(key, kind, data, model, created_at) VALUES(?,?,?,?,?)",
                        (key, kind, json.dumps(d), CFG["critic_model"], time.strftime("%Y-%m-%d %H:%M")))
        return d


def _clean_list(v, limit: int = 6) -> list:
    out = []
    for x in v if isinstance(v, list) else []:
        s = re.sub(r"\s+", " ", str(x.get("item", x) if isinstance(x, dict) else x)).strip(" .,;:")
        if s and len(s) <= 60 and s.lower() not in [o.lower() for o in out]:
            out.append(s[0].lower() + s[1:])
    return out[:limit]


def _join(items: list) -> str:
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _negated(trait: str, prompt: str) -> bool:
    """'round glasses' is not named when the request says 'without glasses' or 'no beard'."""
    words = [w for w in re.findall(r"[a-z]+", trait.lower()) if len(w) > 3]
    return any(re.search(r"\b(?:without|no|remove[sd]?|removing|shaved?|clean[- ]shaven)\b[^.]{0,25}\b" + re.escape(w), prompt.lower()) for w in words[-1:])


def character_of(job: dict) -> str | None:
    for j in (job or {}, root_job(job or {})):
        c = (j.get("characters") or [None])[0]
        if c:
            return str(c)
    return None


def _chars_dir() -> Path:
    return Path(CFG.get("chars") or Path(CFG["out"]).parent / "characters")


def character_traits(cid: str | None) -> dict | None:
    """The character's permanent look in words (bald head, round glasses, ginger beard ...), read once
    from its reference picture and cached until the reference changes."""
    if not cid:
        return None
    ref = _chars_dir() / f"{Path(cid).name}.png"
    if not ref.exists():
        return None
    def build():
        d = _vision_json(TRAITS_PROMPT, [ref.read_bytes()], 300) or {}
        t = _clean_list(d.get("traits"))
        return {"subject": str(d.get("subject") or "person")[:20].lower(), "traits": t} if t else None
    d = _described(f"traits:{Path(cid).name}:{int(ref.stat().st_mtime)}", "traits", build)
    if d:
        d = dict(d)
        try:
            d["name"] = json.loads((_chars_dir() / f"{Path(cid).name}.json").read_text()).get("name")
        except Exception:
            d["name"] = None
    return d


def keep_list(image_id: str | None) -> dict | None:
    """What an edit of this picture must leave alone: the clothing, what is held, the setting."""
    if not image_id:
        return None
    p = Path(CFG["out"]) / f"{Path(str(image_id)).name}.png"
    if not p.exists():
        return None
    def build():
        d = _vision_json(KEEP_PROMPT, [p.read_bytes()], 300) or {}
        out = {k: _clean_list(d.get(k), 5) for k in ("clothing", "holding", "setting")}
        return out if any(out.values()) else None
    return _described(f"keep:{Path(str(image_id)).name}", "keep", build)


def keep_sentence(traits: dict | None, kept: dict | None, issues: list, critic: dict | None = None, moves: bool = True) -> str:
    """'Keep the person's bald head, round glasses, ginger beard, teal t-shirt, brown shorts, the chair,
    the clouds and the art style exactly the same.' Naming things is what kept the likeness in the
    hand-made fix of 2026-10-03; 'keep the person's identity' alone let the guide's person leak in."""
    kept = kept or {}
    tr = list((traits or {}).get("traits") or [])
    clothing = kept.get("clothing") or _clean_list((critic or {}).get("wearing"))
    nouns = {t.split()[-1] for t in tr if t.split()}
    clothing = [c for c in clothing if not any(c in t or t in c for t in tr) and (c.split() or [""])[-1] not in nouns]   # glasses are a trait, not clothing
    own = tr + ([] if "garment" in issues else clothing)
    around = ([] if "scene" in issues else kept.get("setting") or []) + ([] if "pose" in issues else kept.get("holding") or [])
    if not own and not around:
        return ""
    bits = (["the person's " + ", ".join(own)] if own else []) + ["the " + a for a in around]
    bits += ([] if moves else ["the composition"]) + ["the colours"] + ([] if "style" in issues else ["the art style"])
    return "Keep " + _join(bits) + " exactly the same."


def _traits_key(cid: str) -> str | None:
    ref = _chars_dir() / f"{Path(cid).name}.png"
    return f"traits:{Path(cid).name}:{int(ref.stat().st_mtime)}" if ref.exists() else None


def known_traits(cids: list) -> list:
    """Feature lists for the Learning tab, from the cache only (never calls the vision model)."""
    out = []
    for cid in cids:
        key = _traits_key(cid)
        if not key:
            continue
        with _db() as con:
            r = con.execute("SELECT data, created_at FROM descriptions WHERE key=?", (key,)).fetchone()
        try:
            name = json.loads((_chars_dir() / f"{Path(cid).name}.json").read_text()).get("name")
        except Exception:
            name = None
        d = json.loads(r["data"]) if r else {}
        out.append({"id": cid, "name": name or cid, "traits": d.get("traits") or [], "subject": d.get("subject"),
                    "edited": bool(d.get("edited")), "read": bool(r), "at": r["created_at"] if r else None})
    return out


def set_traits(cid: str, traits=None, reread: bool = False) -> dict | None:
    """The owner corrects a character's feature list (kept until the reference picture changes),
    or asks for a fresh reading of the reference picture (reread, or an empty list)."""
    key = _traits_key(cid)
    if not key:
        return None
    t = _clean_list(traits if isinstance(traits, list) else str(traits or "").split(","), 8)
    with _db() as con:
        old = con.execute("SELECT data FROM descriptions WHERE key=?", (key,)).fetchone()
        if reread or not t:
            con.execute("DELETE FROM descriptions WHERE key=?", (key,))
        else:
            subject = (json.loads(old["data"]).get("subject") if old else None) or "person"
            con.execute("INSERT OR REPLACE INTO descriptions(key, kind, data, model, created_at) VALUES(?,?,?,?,?)",
                        (key, "traits", json.dumps({"subject": subject, "traits": t, "edited": True}), "owner", time.strftime("%Y-%m-%d %H:%M")))
    _desc_failed.pop(key, None)
    if reread or not t:
        character_traits(cid)
    return next(iter(known_traits([cid])), None)


# ----------------------------------------------------------------- critic
CRITIC_PROMPT = """You are the quality checker for an AI image generator. Work in two steps.

STEP 1 - describe the picture before you read any request. List EVERY arm and EVERY hand you can see as separate entries, each with where it is and what it does (for example "hand holding the left side of the book", "hand resting on the cheek", "arm reaching from behind the torso"); include partly hidden ones. Then say where the eyes look (straight ahead / up / down / to the side / at an object) and how the head is tilted. Then list the clothes and headgear worn.

STEP 2 - compare with the request the picture was made from:
"{prompt}"
{identity_rule}

Answer in JSON only, no prose:
{{"hands": [{{"where": "..."}}], "arms": [{{"where": "..."}}], "looking": "...", "head": "...", "wearing": ["..."],
 "matches_request": true/false, "summary": "one sentence: what the picture shows and the main mismatch with the request, if any",
 "issues": [{{"category": "anatomy|gaze|pose|expression|garment|identity|scene|style|artifact", "detail": "what exactly is wrong, in physical terms", "severity": "minor|major"}}]}}
Categories: pose = what the character's body is doing and what they hold to do it (cutting logs, an axe in the hands, riding a bike); scene = the place, background, loose objects, other people or animals, framing; expression = the face; artifact = text, watermarks, split screens, duplicated people, glitches, and marks, blotches or rashes on skin or clothes that nobody asked for (red blotches are not "flushed cheeks").
Rules: a person has two arms and two hands; three or more listed, or fewer than two, is an anatomy issue. Report gaze only when the request says where to look and the picture differs. Report pose, expression, garment or scene only when the request asked for something specific that is missing or different. Do not invent issues; an empty list is a valid answer."""


def critic(image_path: Path, prompt: str, job: dict | None = None, reference_path: Path | None = None, timeout: int = 90) -> dict | None:
    """Ask the vision model what is wrong with the picture. None when the model is unavailable."""
    try:
        img = base64.b64encode(Path(image_path).read_bytes()).decode()
    except Exception:
        return None
    images = [img]
    identity_rule = "There is no reference picture; do not report identity."
    if reference_path and Path(reference_path).exists():
        images.append(base64.b64encode(Path(reference_path).read_bytes()).decode())
        identity_rule = "The second image is the character's reference: report identity only if the person clearly is not the same character (face, hair, glasses, facial hair)."
    payload = {"model": CFG["critic_model"], "stream": False, "keep_alive": "10m",
               "messages": [{"role": "user", "content": CRITIC_PROMPT.format(prompt=prompt, identity_rule=identity_rule), "images": images}],
               "options": {"temperature": 0.1, "num_predict": 900, "seed": 7}}
    try:
        req = urllib.request.Request(f"{CFG['ollama']}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        text = r.get("message", {}).get("content", "")
    except Exception as e:
        return {"error": str(e)[:200]}
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return {"error": "no json in reply", "raw": text[:300]}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"error": "bad json in reply", "raw": text[:300]}
    for k in ("hands", "arms"):
        v = d.get(k)
        if isinstance(v, list):
            d[k + "_list"] = [str(x.get("where", x) if isinstance(x, dict) else x)[:120] for x in v]
            d[k] = len(v)
    try:
        if int(d.get("hands") or 0) > 2 or int(d.get("arms") or 0) > 2:
            if not any(str(c.get("category", "")).lower() == "anatomy" for c in d.get("issues") or []):
                d.setdefault("issues", []).append({"category": "anatomy", "detail": f"{d.get('arms')} arms and {d.get('hands')} hands are visible", "severity": "major"})
            d["matches_request"] = False
    except (TypeError, ValueError):
        pass
    issues = []
    for c in d.get("issues") or []:
        cat = str(c.get("category", "")).lower().strip()
        if cat not in ISSUES:
            cat = "other"
        issues.append({"category": cat, "detail": str(c.get("detail", ""))[:300], "severity": str(c.get("severity", "major"))})
    d["issues"] = issues
    d["model"] = CFG["critic_model"]
    try:                                                            # warm the caches a thumbs-down will need, off the worker's path
        cid = character_of(job or {})
        if cid:
            threading.Thread(target=character_traits, args=(cid,), daemon=True).start()
        if issues:
            threading.Thread(target=keep_list, args=(Path(image_path).stem,), daemon=True).start()
    except Exception:
        pass
    return d


def save_critique(image_id: str, result: dict):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO critiques(image_id, result, model, created_at) VALUES(?,?,?,?)",
                    (image_id, json.dumps(result), result.get("model", ""), time.strftime("%Y-%m-%d %H:%M")))


def get_critique(image_id: str) -> dict | None:
    with _db() as con:
        r = con.execute("SELECT result FROM critiques WHERE image_id=?", (image_id,)).fetchone()
    return json.loads(r["result"]) if r else None


# --------------------------------------------------------------- feedback
def record(image_id: str, verdict: str, issues: list[str], note: str, job: dict, critic_res: dict | None) -> dict:
    issues = [i for i in issues if i in ISSUES]
    diag = diagnose(job, issues, note, critic_res) if verdict == "down" else []
    applied = [l["key"] if isinstance(l, dict) else l for l in (job.get("lessons_applied") or [])]
    outcome(applied, verdict, issues)
    changed = strengthen(issues, image_id, request_of(job)) if verdict == "down" else []
    repair = build_repair(job, issues, note, critic_res) if verdict == "down" else None
    with _db() as con:
        cur = con.execute("INSERT INTO feedback(image_id, verdict, issues, note, diagnosis, job, lessons_applied, lessons_changed, critic, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (image_id, verdict, json.dumps(issues), note or "", json.dumps(diag), json.dumps(job), json.dumps(applied),
                           json.dumps(changed), json.dumps(critic_res) if critic_res else None, time.strftime("%Y-%m-%d %H:%M")))
        fid = cur.lastrowid
    return {"id": fid, "image_id": image_id, "verdict": verdict, "issues": issues, "note": note, "diagnosis": diag,
            "lessons_changed": changed, "lessons_credited": applied if verdict == "up" else [], "repair": repair}


def for_images(ids: list[str]) -> dict:
    """Latest verdict per image + fix links, for the gallery."""
    if not ids:
        return {}
    out = {}
    with _db() as con:
        rows = con.execute("SELECT id, image_id, verdict, issues, fix_image_id, created_at FROM feedback ORDER BY id").fetchall()
    for r in rows:
        if r["image_id"] in ids:
            out[r["image_id"]] = {"feedback_id": r["id"], "verdict": r["verdict"], "issues": json.loads(r["issues"] or "[]"), "fix_image_id": r["fix_image_id"], "at": r["created_at"]}
    return out


def by_image(image_id: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM feedback WHERE image_id=? ORDER BY id DESC", (image_id,)).fetchall()
    return [_row(r) for r in rows]


def set_fix(feedback_id: int, fix_image_id: str):
    with _db() as con:
        con.execute("UPDATE feedback SET fix_image_id=? WHERE id=?", (fix_image_id, feedback_id))


def stats() -> dict:
    with _db() as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM feedback ORDER BY id DESC").fetchall()]
    week = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    def rate(rs):
        n = len(rs); up = sum(1 for r in rs if r["verdict"] == "up")
        return {"total": n, "up": up, "down": n - up, "approval": round(100 * up / n) if n else None}
    by_cat, by_model, by_mode, by_refs = {}, {}, {}, {}
    for r in rows:
        j = r.get("job") or {}
        for c in (r.get("issues") or []):
            by_cat[c] = by_cat.get(c, 0) + 1
        for key, dct in ((j.get("model") or "?", by_model), (j.get("reference_mode") or "original", by_mode), (str(_plan_summary(j)[1]) if j else "?", by_refs)):
            d = dct.setdefault(key, {"total": 0, "up": 0})
            d["total"] += 1; d["up"] += 1 if r["verdict"] == "up" else 0
    for dct in (by_model, by_mode, by_refs):
        for k, d in dct.items():
            d["approval"] = round(100 * d["up"] / d["total"]) if d["total"] else None
    recent = []
    for r in rows[:40]:
        recent.append({"id": r["id"], "image_id": r["image_id"], "verdict": r["verdict"], "issues": r.get("issues") or [], "note": r.get("note") or "",
                       "fix_image_id": r.get("fix_image_id"), "at": r.get("created_at"), "prompt": (r.get("job") or {}).get("prompt", ""),
                       "lessons_changed": [l.get("title") for l in (r.get("lessons_changed") or [])]})
    ls = lessons()
    with _db() as con:
        crit = [json.loads(r["result"]) for r in con.execute("SELECT result FROM critiques").fetchall()]
    auto = {"checked": len(crit), "with_issues": sum(1 for c in crit if c.get("issues")), "by_category": {}}
    for c in crit:
        for i in {x.get("category") for x in c.get("issues") or []}:
            auto["by_category"][i] = auto["by_category"].get(i, 0) + 1
    auto["by_category"] = dict(sorted(auto["by_category"].items(), key=lambda x: -x[1]))
    return {"overall": rate(rows), "last_7_days": rate([r for r in rows if (r.get("created_at") or "") >= week]), "auto_check": auto, "outcomes": outcome_stats(),
            "by_category": dict(sorted(by_cat.items(), key=lambda x: -x[1])), "by_model": by_model, "by_reference_mode": by_mode, "by_reference_count": by_refs,
            "lessons": ls, "recent": recent, "issue_labels": {k: v["label"] for k, v in ISSUES.items()}}


# ---------------------------------------------------------------- learner
LEARN_PROMPT = """You tune a local image generator (FLUX.1 Kontext drawing a saved character from reference pictures). Below are recent pictures the owner marked as wrong, with the request, what the planner used, what was wrong and the owner's notes. Find patterns that repeat and propose at most {n} GENERAL rules the generator should apply automatically next time. A rule has: a trigger (words in the request that should fire it) and an action (a short sentence to add to the prompt sent to the model, and/or minimum guidance 3.5-5 / minimum steps 24-32). Rules must be general, not about one picture, and must not repeat these existing rules: {existing}.
Reply in JSON only: [{{"title": "...", "category": "<anatomy|gaze|pose|expression|garment|identity|scene|style|artifact|other>", "trigger_keywords": ["..."], "prompt_addition": "...", "guidance_min": <number or null>, "steps_min": <number or null>, "rationale": "..."}}]
If nothing general can be learned, reply []."""


def learn(n_rules: int = 3, limit: int = 25) -> dict:
    """Turn accumulated thumbs-downs into proposed rules (stored disabled, for review)."""
    with _db() as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM feedback WHERE verdict='down' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        graded = {r["image_id"] for r in con.execute("SELECT image_id FROM feedback").fetchall()}
        crit_rows = con.execute("SELECT image_id, result FROM critiques ORDER BY created_at DESC LIMIT ?", (limit * 2,)).fetchall()
    auto_cases = []
    for r in crit_rows:                                             # flaws the automatic check found on pictures nobody graded
        res = json.loads(r["result"])
        if r["image_id"] in graded or not res.get("issues"):
            continue
        try:
            j = json.loads((CFG["out"] / f"{r['image_id']}.json").read_text())
        except Exception:
            continue
        auto_cases.append({"request": j.get("prompt"), "sent": (j.get("prompt_sent") or "")[:300], "plan": _plan_summary(j)[0],
                           "issues": sorted({x["category"] for x in res["issues"]}), "note": "(found by the automatic check)",
                           "critic": [x.get("detail") for x in res["issues"]][:4]})
    if len(rows) + len(auto_cases) < 2:
        return {"proposed": [], "reason": "need at least two flawed pictures (graded or found by the automatic check) to look for a pattern",
                "examined": len(rows) + len(auto_cases)}
    cases = []
    for r in rows:
        j = r.get("job") or {}
        c = (r.get("critic") or {})
        cases.append({"request": j.get("prompt"), "sent": (j.get("prompt_sent") or "")[:300], "plan": _plan_summary(j)[0], "issues": r.get("issues"),
                      "note": r.get("note"), "critic": [x.get("detail") for x in (c.get("issues") or [])][:4]})
    cases += auto_cases[:max(0, limit - len(cases))]
    existing = "; ".join(l["title"] for l in lessons())
    payload = {"model": CFG["learner_model"], "stream": False, "keep_alive": "10m",
               "messages": [{"role": "user", "content": LEARN_PROMPT.format(n=n_rules, existing=existing) + "\n\nCASES:\n" + json.dumps(cases, indent=1)[:12000]}],
               "options": {"temperature": 0.3, "num_predict": 1200}}
    try:
        req = urllib.request.Request(f"{CFG['ollama']}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        text = json.load(urllib.request.urlopen(req, timeout=180)).get("message", {}).get("content", "")
    except Exception as e:
        return {"proposed": [], "error": str(e)[:200], "examined": len(cases)}
    m = re.search(r"\[.*\]", text, flags=re.S)
    try:
        rules = json.loads(m.group(0)) if m else []
    except Exception:
        return {"proposed": [], "error": "learner replied without valid JSON", "raw": text[:300], "examined": len(cases)}
    created = []
    for r in rules[:n_rules]:
        if not isinstance(r, dict) or not r.get("title"):
            continue
        kws = [str(k) for k in (r.get("trigger_keywords") or []) if str(k).strip()][:8]
        L = create_lesson(str(r.get("category", "other")), str(r["title"]), kws, append=str(r.get("prompt_addition") or ""),
                          guidance_min=r.get("guidance_min"), steps_min=r.get("steps_min"), why=str(r.get("rationale") or ""),
                          source="ai", enabled=0, evidence=[x["image_id"] for x in rows[:6]])
        created.append(L)
    return {"proposed": created, "examined": len(cases), "from_owner": len(rows), "from_check": len(cases) - len(rows)}


def advice(limit: int = 8) -> str:
    """Lines for the chat helper and the prompt compiler: what the generator gets wrong and how to phrase around it."""
    lines = []
    for L in lessons(enabled_only=True):
        a = L.get("action") or {}
        if L["key"] == "character-framing":
            continue
        tip = a.get("append") or a.get("prepend") or ("state the head direction in physical terms (head tilted back, eyes raised)" if a.get("gaze_expand") else "")
        if not tip and a.get("expr_expand"):
            tip = "describe the expression as eyebrows, eyes and mouth"
        if tip:
            lines.append(f"- {L['title']}: {tip}")
        if len(lines) >= limit:
            break
    return ("\nKnown weaknesses of the generator (learned from graded pictures) and how to phrase around them:\n" + "\n".join(lines)) if lines else ""
