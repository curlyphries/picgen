"""The picgen API contract (OpenAPI 3.1), kept as Python so it stays readable and consistent.

    python3 tools/openapi_spec.py        # writes docs/openapi.json (build_docs.py does this too)

When you add or change a route in app.py, change it here in the same commit.
"""

import json
from pathlib import Path

VERSION = "1.0.0"


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(props: dict, required=(), desc=""):
    o = {"type": "object", "properties": props}
    if required:
        o["required"] = list(required)
    if desc:
        o["description"] = desc
    return o


def s(typ, desc="", **kw):
    return {"type": typ, "description": desc, **kw}


def arr(items, desc=""):
    return {"type": "array", "items": items, "description": desc}


def jbody(schema, example=None, required=True):
    m = {"schema": schema}
    if example is not None:
        m["example"] = example
    return {"required": required, "content": {"application/json": m}}


def form(schema):
    return {"required": True, "content": {"multipart/form-data": {"schema": schema}}}


def ok(desc, schema=None, example=None, ctype="application/json"):
    r = {"description": desc}
    if schema is not None or example is not None:
        m = {}
        if schema is not None:
            m["schema"] = schema
        if example is not None:
            m["example"] = example
        r["content"] = {ctype: m}
    return r


def err(desc, example=None):
    return ok(desc, ref("Error"), example or {"error": desc})


PNG = {"description": "The PNG image.", "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}


def path_param(name, desc, pattern=None):
    sc = {"type": "string"}
    if pattern:
        sc["pattern"] = pattern
    return {"name": name, "in": "path", "required": True, "description": desc, "schema": sc}


def q(name, desc, typ="string", required=False, **kw):
    return {"name": name, "in": "query", "required": required, "description": desc, "schema": {"type": typ, **kw}}


IMAGE_ID = path_param("image_id", "Picture id: `<epoch seconds>-<4 digits>`, for example `1791022789-4284`.", r"^\d+-\d+$")
CHAR_ID = path_param("character_id", "Character id: `c` followed by digits, for example `c1791012478462`.", r"^c\d+$")

# ------------------------------------------------------------------ examples (trimmed from a live server, 2026-10-03)
EX_STATUS = {"comfy_up": True, "current": None, "queued": 0, "idle_minutes": 5, "critic": "gemma4:31b-cloud",
             "ollama_loaded": [], "default_image_model": "z-image-turbo"}
EX_OPTIONS = {"styles": ["none", "photo", "illustration", "cinematic", "cartoon"],
              "sizes": [{"id": "square", "label": "square", "w": 1024, "h": 1024}, {"id": "landscape", "label": "landscape", "w": 1216, "h": 832},
                        {"id": "portrait", "label": "portrait", "w": 832, "h": 1216}, {"id": "wide", "label": "wide", "w": 1344, "h": 768}],
              "max_characters": 1, "sheet_tags": ["front-sad", "front-neutral", "front-smile", "front-laugh", "front-surprised", "front-angry",
                                                  "three-quarter", "profile", "over-shoulder", "back-view", "full-body-standing", "walking-side", "sitting"],
              "library_kinds": ["expression", "pose", "activity", "outfit", "top", "pants", "shorts", "underwear", "socks", "footwear", "headwear", "accessory", "prop"],
              "garment_kinds": ["outfit", "top", "pants", "shorts", "underwear", "socks", "footwear", "headwear", "accessory", "prop"],
              "max_garments": 2, "sheet_seconds_each": 65}
EX_IMAGE_MODEL = {"id": "z-image-turbo", "label": "Z-Image Turbo", "installed": True, "mode": "txt2img", "needs_character": False,
                  "steps": 8, "guidance": None, "speed": "about 10 s a picture", "license": "Apache 2.0 (commercial OK)",
                  "best": "Following the prompt literally: scenes with several things in them, specific positions and counts, readable signs and text, quick iteration.",
                  "avoid": "Slightly less skin and fabric realism than FLUX on close portraits.",
                  "tips": "8 steps is the sweet spot. Write plain sentences, subject first.",
                  "install": "HF Comfy-Org/z_image_turbo: z_image_turbo_bf16 -> diffusion_models, qwen_3_4b_fp8_mixed -> text_encoders, ae -> vae"}
EX_PICTURE = {"id": "1791022789-4284", "prompt": "The same woman from the reference stands angry with her arms crossed in a flannel shirt, plain studio background.",
              "model": "flux-kontext", "characters": ["c1791012478462"], "reference_used": "original", "reference_label": "original",
              "reference_mode": "original", "garments_used": [{"tag": "flannel-shirt", "label": "flannel shirt", "kind": "top"}],
              "guides_used": [{"slot": "pose", "ref": "lib:arms-crossed", "label": "arms crossed", "mode": "guide", "why": "keywords"},
                              {"slot": "expression", "ref": "lib:angry", "label": "angry", "mode": "guide", "why": "keywords"}],
              "boosted": True, "plan_notes": [], "lessons_applied": ["character-framing"], "lesson_notes": [],
              "prompt_sent": "The first image shows the character to draw. The second image is an action guide of a different person (arms crossed)…",
              "edit_of": None, "repair_of": None, "retry_of": None, "feedback_id": None, "critique": None, "critic_learned": None, "outcome": None,
              "guide": None, "guide_used": None, "style": "none", "w": 1024, "h": 1024, "steps": 28, "seed": 36539100, "guidance": 4.0,
              "seconds": 308, "at": "2026-10-03 05:19", "file": "1791022789-4284.png", "feedback": None}
EX_PLAN = {"identity": {"ref": "original", "label": "original", "mode": "original"},
           "pose": {"ref": "lib:running-side-view", "label": "running side view", "mode": "guide", "why": "keywords"},
           "expression": {"ref": "lib:scared", "label": "scared", "mode": "guide", "why": "keywords"},
           "garments": [{"tag": "brown-pullover-hoodie", "label": "brown pullover hoodie", "kind": "top"}],
           "notes": [], "ref_count": 4, "boost": True, "guidance": 4.0, "steps": 28, "boosted": True,
           "lessons": [{"key": "character-framing", "title": "Name the reference in character pictures", "category": "framing"}],
           "lesson_notes": [], "prompt_preview": "The same woman from the reference. runs away scared in a hoodie", "seconds": 255}
EX_CHARACTER = {"id": "c1791019587671", "name": "Stock woman", "notes": "shoulder-length wavy dark brown hair, hoop earrings, lavender tee.",
                "species": "woman", "at": "2026-10-03 04:26", "file": "c1791019587671.png",
                "views": {"happy-smile": {"file": "happy-smile.png", "label": "happy smile", "keywords": ["happy smile", "smile"], "source": "uploaded", "kind": "expression"},
                          "sitting": {"file": "sitting.png", "label": "sitting", "keywords": [], "source": "generated", "kind": "pose"}},
                "sheet": {"happy-smile": "happy-smile.png", "sitting": "sitting.png"}, "sheet_pending": [],
                "sheet_wanted": ["three-quarter", "profile", "over-shoulder", "back-view", "full-body-standing", "walking-side", "sitting"], "sheet_total": 7}
EX_LIB_ITEM = {"tag": "shocked", "kind": "expression", "label": "shocked", "keywords": ["shocked", "shocked startled", "startled"],
               "source": "c1791007342591", "file": "shocked.png", "source_name": "Sam"}
EX_LESSON = {"id": 2, "key": "gaze-explicit", "category": "gaze", "title": "Spell out head direction and gaze",
             "why": "Where someone looks is not a pose the library knows; the model keeps the reference's gaze unless the head direction is stated in physical terms.",
             "trigger": {"when": "gaze_phrase"}, "action": {"gaze_expand": True, "guidance_min": 4.5, "steps_min": 28}, "enabled": 1, "source": "builtin",
             "hits": 2, "applied": 4, "wins": 1, "losses": 0, "evidence": ["1791047030-6999", "1791048381-8016"],
             "created_at": "2026-10-03 12:19", "updated_at": "2026-10-03 12:51", "critic_hits": 4, "auto_wins": 2, "auto_losses": 4}
EX_CRITIQUE = {"hands": 3, "arms": 2, "looking": "to the side", "head": "tilted slightly to the left", "wearing": ["teal t-shirt", "brown shorts", "glasses"],
               "matches_request": False, "summary": "A man reading a book, looking to the side rather than up at the sky, with an extra hand.",
               "issues": [{"category": "anatomy", "detail": "Three hands are visible (one on the cheek, two on the book).", "severity": "major"},
                          {"category": "gaze", "detail": "Looking to the side instead of up at the sky.", "severity": "minor"}],
               "hands_list": ["resting on the right cheek", "holding the bottom left of the book", "holding the right edge of the book"],
               "arms_list": ["bent upwards to the face", "reaching to the book"], "model": "gemma4:31b-cloud"}
EX_REPAIR = {"mode": "edit", "prompt": "The first image is the picture to fix: keep its scene, composition, colours, art style, clothing and the person's identity. … Keep everything else the same.",
             "model": "flux-kontext-edit", "edit_of": "1791047030-6999", "guidance": 4.5, "steps": 28, "strategy": "guided",
             "guide": {"tag": "looking-up-at-the-sky-while-holding-a-bo", "label": "looking up at the sky while holding a book", "drawn": False},
             "size": "square", "track_record": ["looking in the wrong direction: targeted fix cleared it 0 of 2 times"],
             "regenerate": {"prompt": "reading a book with a pensive look on his face looking up at the sky", "model": "flux-kontext",
                            "characters": ["c1791007342591"], "reference": "auto", "garments": "auto", "style": "none", "size": "square",
                            "retry_of": "1791047030-6999"}}

ISSUES = ["anatomy", "gaze", "pose", "expression", "garment", "identity", "scene", "style", "artifact", "other"]

SCHEMAS = {
    "Error": obj({"error": s("string", "What went wrong, in words.")}, ["error"]),
    "ImageModel": obj({
        "id": s("string", "Model id to pass as `model`."), "label": s("string", "Display name."),
        "installed": s("boolean", "All model files are present and picgen has a workflow for it."),
        "mode": s("string", "`txt2img` draws from text, `character` needs a saved character, `edit` changes an existing picture.", enum=["txt2img", "character", "edit"]),
        "needs_character": s("boolean", "True for `character` mode."),
        "steps": s("integer", "Default steps."), "guidance": {"type": ["number", "null"], "description": "Default guidance (null: the model ignores it)."},
        "speed": s("string", "Typical time per picture on the reference GPU."), "license": s("string", "Model license. Check it before commercial use."),
        "best": s("string", "What it is best at."), "avoid": s("string", "What to avoid."), "tips": s("string", "Prompting tips."),
        "install": s("string", "Where the model files come from."),
    }),
    "PlanSlot": obj({"ref": s("string", "`original`, an own view tag, or `lib:<tag>` for a library item."), "label": s("string", "Human label."),
                     "mode": s("string", "`original`, `own` (this character's own picture), `guide` (another character's picture) or `edit`."),
                     "why": s("string", "Why it was chosen: `keywords`, `picked by hand`, the matched words, or `repair guide`.")}),
    "Garment": obj({"tag": s("string", "Library tag."), "label": s("string", "Label."), "kind": s("string", "Garment kind, e.g. `footwear`.")}),
    "Plan": obj({
        "identity": ref("PlanSlot"), "pose": {"oneOf": [ref("PlanSlot"), {"type": "null"}], "description": "Body pose or activity reference, if any."},
        "expression": {"oneOf": [ref("PlanSlot"), {"type": "null"}], "description": "Facial expression reference, if any."},
        "garments": arr(ref("Garment"), "Garments to wear (at most `max_garments`, and only while references remain)."),
        "notes": arr({"type": "string"}, "Warnings, e.g. garments dropped for lack of reference slots."),
        "ref_count": s("integer", "References the picture will use (1–4). Each beyond the first adds about 55 s."),
        "boost": s("boolean", "A pose or face guide is in play, so guidance and steps are raised if you left them at the defaults."),
        "guidance": s("number", "Guidance that will actually be used, after boost and lessons."), "steps": s("integer", "Steps that will actually be used."),
        "boosted": s("boolean", "Guidance/steps were raised automatically."),
        "lessons": arr(obj({"key": s("string"), "title": s("string"), "category": s("string")}), "Learned lessons that will apply."),
        "lesson_notes": arr({"type": "string"}, "What those lessons changed in the plan."),
        "prompt_preview": s("string", "The prompt after lessons, before reference wording is added."),
        "seconds": s("integer", "Rough time estimate on the reference GPU."),
    }),
    "Picture": obj({
        "id": s("string", "Picture id."), "file": s("string", "PNG file name; fetch it from `/img/<file>`."),
        "prompt": s("string", "The prompt as submitted."), "model": s("string", "Model id."),
        "characters": arr({"type": "string"}, "Character ids used."),
        "reference_used": {"type": ["string", "null"], "description": "Identity reference (`original`, a view tag or `lib:<tag>`)."},
        "reference_label": {"type": ["string", "null"]}, "reference_mode": {"type": ["string", "null"], "description": "`original`, `own` or `guide`."},
        "garments_used": {"type": ["array", "null"], "items": ref("Garment")},
        "guides_used": {"type": ["array", "null"], "items": ref("PlanSlot"), "description": "Pose/expression references, each with its `slot`."},
        "boosted": {"type": ["boolean", "null"]}, "plan_notes": {"type": ["array", "null"], "items": {"type": "string"}},
        "lessons_applied": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Keys of lessons that changed this picture."},
        "lesson_notes": {"type": ["array", "null"], "items": {"type": "string"}},
        "prompt_sent": {"type": ["string", "null"], "description": "The exact text the model received."},
        "edit_of": {"type": ["string", "null"], "description": "Source picture id, or `char:<cid>[:<tag>]`, for edits."},
        "extra_refs": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Further reference pictures passed with the request."},
        "loras": {"type": ["array", "null"], "items": {"type": "object"}, "description": "LoRAs applied, each `{name, strength}`."},
        "repair_of": {"type": ["string", "null"], "description": "Picture this one fixes."}, "retry_of": {"type": ["string", "null"], "description": "Picture this one regenerates."},
        "feedback_id": {"type": ["integer", "null"]}, "critique": {"oneOf": [ref("Critique"), {"type": "null"}]},
        "critic_learned": {"type": ["array", "null"]}, "outcome": {"type": ["object", "null"], "description": "For fixes/retries: which flagged flaws `cleared`, `remaining` or were `introduced`."},
        "guide": {"type": ["object", "null"]}, "guide_used": {"type": ["object", "null"], "description": "Pose picture used to steer a guided fix."},
        "style": s("string"), "w": s("integer", "Requested width."), "h": s("integer", "Requested height."), "steps": s("integer", "Steps actually used."),
        "seed": s("integer"), "guidance": s("number", "Guidance actually used."), "seconds": s("integer", "Time taken."), "at": s("string", "Local time, `YYYY-MM-DD HH:MM`."),
        "feedback": {"type": ["object", "null"], "description": "Gallery only: the latest grade `{feedback_id, verdict, issues, fix_image_id, at}`."},
    }),
    "Job": obj({
        "id": s("string"), "status": s("string", "Where the job is.", enum=["queued", "starting model", "drawing", "drawing a pose guide", "checking", "done", "failed", "unknown"]),
        "error": s("string", "Present when `status` is `failed`."), "file": s("string", "PNG file name once done."),
        "seconds": s("integer", "Time taken, once done."),
    }, desc="A job. While queued or running it is the live job record; once done it is the full `Picture` record plus `status` and `file`."),
    "View": obj({"file": s("string"), "label": s("string"), "keywords": arr({"type": "string"}),
                 "source": s("string", "`generated` (sheet), `uploaded`, `derived` (drawn from a library item) or `taught` (an approved picture saved with `POST /api/feedback/teach`)."),
                 "kind": s("string", "`expression`, `pose` or `activity`, or a garment kind for derived views.")}),
    "Character": obj({
        "id": s("string"), "name": s("string"), "notes": s("string"), "species": s("string", "What it is (`woman`, `dog`, `robot`…); used in prompts."),
        "at": s("string"), "file": s("string", "Reference PNG; fetch from `/char/<file>`."), "from_image": s("string", "Gallery picture it was made from, if any."),
        "views": {"type": "object", "additionalProperties": ref("View"), "description": "Every view by tag. Fetch a view from `/char/<id>/sheet/<file>`."},
        "sheet": {"type": "object", "additionalProperties": {"type": "string"}, "description": "`{tag: file}` for every view."},
        "sheet_pending": arr({"type": "string"}, "View tags queued or rendering."),
        "sheet_wanted": arr({"type": "string"}, "Sheet tags this character should have."), "sheet_total": s("integer"),
    }),
    "LibraryItem": obj({
        "tag": s("string", "Unique id; use `lib:<tag>` as a reference."), "kind": s("string", "One of `library_kinds`."), "label": s("string"),
        "keywords": arr({"type": "string"}, "Words the planner matches. Several words = a phrase. A leading `!` marks a weak garment word that can rank but not qualify an item."),
        "source": s("string", "Character drawn in it (its identity for that character, a guide for others), `\"\"` for generic, or `synthesized`."),
        "source_name": s("string"), "file": s("string", "Fetch from `/lib/<file>`."), "views": s("integer", "Panels in the picture (3 for front/side/back composites)."),
    }),
    "Critique": obj({
        "hands": s("integer"), "arms": s("integer"), "looking": s("string"), "head": s("string"), "wearing": arr({"type": "string"}),
        "matches_request": s("boolean"), "summary": s("string"),
        "issues": arr(obj({"category": s("string", enum=ISSUES), "detail": s("string"), "severity": s("string", enum=["major", "minor"])})),
        "hands_list": arr({"type": "string"}), "arms_list": arr({"type": "string"}), "model": s("string"),
        "error": s("string", "Present instead of the fields above when the check could not run."),
    }, desc="What the vision model saw, compared with the request."),
    "Repair": obj({
        "mode": s("string"), "prompt": s("string", "Edit instruction that changes only the flagged things. You may reword it."),
        "model": s("string"), "edit_of": s("string"), "guidance": s("number"), "steps": s("integer"),
        "strategy": s("string", "`fix` (words only) or `guided` (also steered by a pose picture).", enum=["fix", "guided"]),
        "guide": {"type": ["object", "null"], "description": "`{tag, label, drawn:false}` (library pose) or `{synthesize, label, drawn:true}` (drawn first)."},
        "size": s("string"), "regenerate_recommended": s("boolean"), "regenerate_why": s("string"), "track_record": arr({"type": "string"}),
        "regenerate": obj({"prompt": s("string"), "model": s("string"), "characters": arr({"type": "string"}), "reference": s("string"),
                           "garments": s("string"), "style": s("string"), "size": s("string"), "retry_of": s("string")},
                          desc="A fresh-picture alternative; submit it to `POST /api/generate` with `feedback_id`."),
        "keeps": {"type": "object", "description": "What the fix is told to keep: the character's permanent features (read once from its reference picture), and the picture's clothing, held objects and setting."},
        "feedback_id": s("integer", "Only from `POST /api/feedback`."), "repair_of": s("string", "Only from `POST /api/feedback`."),
    }, desc="A ready-to-submit `POST /api/generate` body that fixes a flagged picture."),
    "Lesson": obj({
        "id": s("integer"), "key": s("string"), "category": s("string", "An issue category or `framing`."), "title": s("string"), "why": s("string"),
        "trigger": obj({"when": s("string", "`always`, `character`, `keywords` (with `any`), or a built-in condition such as `gaze_phrase`."),
                        "any": arr({"type": "string"})}),
        "action": obj({"prepend": s("string"), "append": s("string"), "guidance_min": s("number"), "guidance_max": s("number"), "steps_min": s("integer")},
                      desc="What it changes. Built-ins can also use structural actions (`gaze_expand`, `expr_expand`, `identity_original`, `drop_guides_for_garments`)."),
        "enabled": s("integer", "1 on, 0 off."), "source": s("string", "`builtin`, `user` or `ai` (proposed by the learner; starts off).", enum=["builtin", "user", "ai"]),
        "hits": s("integer"), "applied": s("integer"), "wins": s("integer"), "losses": s("integer"), "critic_hits": s("integer"),
        "auto_wins": s("integer"), "auto_losses": s("integer"), "evidence": arr({"type": "string"}, "Example picture ids."),
        "user_edited": s("integer", "1 when you changed a built-in's text, trigger or action; it is then kept across restarts."),
        "owner_off": s("integer", "1 when you switched it off; the automatic check will not switch it back on."),
        "created_at": s("string"), "updated_at": s("string"),
    }, desc="A rule applied before drawing when its trigger matches."),
}

TAGS = [
    {"name": "System", "description": "Health, options and catalogs. Read these first to learn what this server can do."},
    {"name": "Pictures", "description": "Queue a picture, follow it, fetch it, list and delete pictures. Generation is asynchronous: `POST /api/generate` returns an id; poll `GET /api/job/{id}` until `done` or `failed`."},
    {"name": "Queue", "description": "One job runs at a time on the GPU. See and cancel what is waiting."},
    {"name": "Planning and prompts", "description": "Dry-run the reference planner and get help writing prompts. None of these draw a picture."},
    {"name": "Characters", "description": "Saved characters, their sheets and views."},
    {"name": "Library", "description": "Shared references any character can use: expressions, poses, activities and garments."},
    {"name": "Feedback", "description": "Grade pictures, run the automatic check, and get a diagnosis with a ready-made repair."},
    {"name": "Lessons", "description": "Rules learned from feedback (or written by hand) that adjust future prompts and settings."},
]


def build() -> dict:
    P = {}

    # ---------------------------------------------------------- System
    P["/api/status"] = {"get": {"tags": ["System"], "operationId": "getStatus", "summary": "Server and GPU status",
        "description": "Cheap enough to poll every few seconds. `comfy_up` says whether the image engine is loaded.",
        "responses": {"200": ok("Status.", obj({"comfy_up": s("boolean", "Image engine running."), "current": {"type": ["string", "null"], "description": "Id of the job drawing now."},
                                                "queued": s("integer", "Jobs waiting."), "idle_minutes": s("integer", "Engine stops after this many idle minutes."),
                                                "critic": {"type": ["string", "null"], "description": "Vision model used for the automatic check, or null when it is off."},
                                                "ollama_loaded": arr({"type": "string"}, "Language models resident right now."),
                                                "default_image_model": s("string")}), EX_STATUS)}}}
    P["/api/options"] = {"get": {"tags": ["System"], "operationId": "getOptions", "summary": "Allowed values and limits",
        "description": "Styles, sizes, library kinds and the per-picture limits. Use these instead of hard-coding values.",
        "responses": {"200": ok("Options.", None, EX_OPTIONS)}}}
    P["/api/image_models"] = {"get": {"tags": ["System"], "operationId": "listImageModels", "summary": "Image models and what each is for",
        "responses": {"200": ok("The catalog and the default model id.", obj({"models": arr(ref("ImageModel")), "default": s("string")}),
                                {"models": [EX_IMAGE_MODEL], "default": "z-image-turbo"})}}}
    P["/api/models"] = {"get": {"tags": ["System"], "operationId": "listChatModels", "summary": "Chat models available in Ollama",
        "responses": {"200": ok("Model names and the default.", obj({"models": arr({"type": "string"}), "default": s("string")}),
                                {"models": ["qwen2.5:7b", "qwen3:32b"], "default": "qwen2.5:7b"})}}}
    P["/api/openapi.json"] = {"get": {"tags": ["System"], "operationId": "getOpenAPI", "summary": "This API description (OpenAPI 3.1)",
        "description": "Feed it to code generators, Postman or Swagger UI. An explorer is served at `/api/docs`.",
        "responses": {"200": ok("The spec.", {"type": "object"})}}}

    # ---------------------------------------------------------- Pictures
    P["/api/erase"] = {"post": {"tags": ["Pictures"], "operationId": "erase", "summary": "Magic erase: remove what is under a mask and fill in the background",
        "description": (
            "Like a phone's magic eraser. Send a picture and a mask (white = erase, black = keep); the masked pixels are replaced by "
            "Qwen-Image-Edit's reconstruction of the background and **every pixel outside the mask is returned unchanged**. "
            "Runs synchronously on the GPU (about a minute; the request blocks). Optional `prompt` hints at what is behind the object "
            "(\"the wooden desk and the chalkboard\"); `feather` (px, default 6) softens the seam; `seed` makes it repeatable. "
            "Used by the GIMP plug-in *Filters → Faraway → Magic Erase*, which sends the current selection as the mask."),
        "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": obj({
            "image": {"type": "string", "format": "binary", "description": "PNG or JPEG."},
            "mask": {"type": "string", "format": "binary", "description": "PNG, same size as the image (resized if not); white = erase."},
            "prompt": s("string", "What is behind the erased thing (optional)."),
            "feather": s("number", "Edge softening in pixels, default 6."), "seed": s("integer")}, ["image", "mask"])}}},
        "responses": {"200": {"description": "The edited picture.", "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
                      "400": {"description": "Missing image or mask."}, "500": {"description": "Model not installed or ComfyUI failed."}}}}
    P["/api/generate"] = {"post": {"tags": ["Pictures"], "operationId": "generate", "summary": "Queue a picture",
        "description": (
            "Adds a job to the queue and returns its id at once. The **model's mode** decides what kind of picture it is:\n\n"
            "| Mode | Needs | Example |\n|---|---|---|\n"
            "| `txt2img` | `prompt` | `{\"prompt\": \"a lighthouse at dusk\", \"model\": \"z-image-turbo\"}` |\n"
            "| `character` | `prompt`, `characters` | `{\"prompt\": \"runs away scared\", \"model\": \"flux-kontext\", \"characters\": [\"c1791012478462\"]}` |\n"
            "| `edit` | `prompt`, `edit_of` | `{\"prompt\": \"make it night\", \"model\": \"flux-kontext-edit\", \"edit_of\": \"1791022789-4284\"}` |\n\n"
            "Characters are ignored by `txt2img` models, so pass a `character`-mode model when you pass a character. "
            "`qwen-image` / `qwen-image-edit` are the commercially licensed character and edit models; both accept `extra_refs` (up to two more pictures, "
            "for a second character or a prop) and keep 8 steps / cfg 1 whatever you pass. "
            "Steps are clamped to 1–60 and guidance to 0–15. Leave them out to let picgen choose (it raises them when pose or face guides are used on the FLUX models)."),
        "requestBody": jbody(obj({
            "prompt": s("string", "What to draw (or, for edits, what to change). Up to 2000 characters.", maxLength=2000),
            "model": s("string", "Image model id from `/api/image_models`. Default: the server default."),
            "size": s("string", "Size id.", enum=["square", "landscape", "portrait", "wide"], default="square"),
            "style": s("string", "Style suffix.", enum=["none", "photo", "illustration", "cinematic", "cartoon"], default="none"),
            "steps": s("integer", "Sampling steps (1–60). Default: the model's."), "guidance": s("number", "Guidance (0–15). Default: the model's."),
            "seed": s("integer", "Seed for repeatable results. Default: random."),
            "characters": arr({"type": "string"}, "Character ids (at most `max_characters`, currently 1). Character mode only."),
            "reference": s("string", "Identity/pose reference: `auto` (planner decides), `original`, an own view tag, or `lib:<tag>`.", default="auto"),
            "garments": {"description": "`\"auto\"` (wear what the prompt names) or a list of library tags (at most `max_garments`; `[]` for none).",
                         "oneOf": [{"type": "string", "enum": ["auto"]}, {"type": "array", "items": {"type": "string"}}], "default": "auto"},
            "edit_of": s("string", "Edit mode: the picture to change, as a picture id or `char:<character id>[:<view tag>]`."),
            "guides": {"type": "boolean", "default": True, "description": "Character mode: `false` skips the planner's pose/expression library guides and uses only the identity reference (plus `extra_refs`). Use with `qwen-image`, which treats every reference as a character to draw."},
            "loras": arr({"oneOf": [{"type": "string"}, {"type": "object", "properties": {"name": {"type": "string"}, "strength": {"type": "number"}}}]},
                         "Up to three LoRA files from `models/loras` (`z-image-turbo` only), each as a file name or `{name, strength}` (strength 0–2, default 1). Put each LoRA's trigger word in the prompt. Unknown files are dropped."),
            "extra_refs": arr({"type": "string"}, "Up to two further reference pictures (picture ids or `char:<cid>[:<view>]`) appended after the planner's references: a second character, a prop. Character and edit modes; `qwen-image` sees up to three pictures in all."),
            "repair_of": s("string", "Picture this job fixes (from a `Repair`)."), "retry_of": s("string", "Picture this job regenerates."),
            "feedback_id": s("integer", "Feedback row the fix or retry answers."), "guide": {"type": "object", "description": "Repair pose guide (only with `repair_of`)."},
        }, ["prompt"]), {"prompt": "A red fox asleep in fresh snow at dawn, soft pink light.", "model": "z-image-turbo", "size": "landscape"}),
        "responses": {"200": ok("Queued.", obj({"id": s("string")}), {"id": "1791055012-4821"}),
                      "400": err("Empty prompt, model not installed, no character for a character model, or no picture for an edit.",
                                 {"error": "pick a picture to edit"})}}}
    P["/api/job/{job_id}"] = {"get": {"tags": ["Pictures"], "operationId": "getJob", "summary": "Follow a job",
        "description": "Poll every 1–2 s. `checking` means the picture exists and the automatic check is running; it becomes `done` within about a minute. "
                       "Failed jobs keep their `error` until the server restarts (the last 50 are kept in memory).",
        "parameters": [path_param("job_id", "Job id from `POST /api/generate`.")],
        "responses": {"200": ok("The job.", ref("Job"), {"id": "1791055012-4821", "status": "drawing", "prompt": "A red fox asleep in fresh snow at dawn, soft pink light.", "model": "z-image-turbo"}),
                      "404": ok("No such job.", ref("Job"), {"status": "unknown"})}}}
    P["/api/gallery"] = {"get": {"tags": ["Pictures"], "operationId": "listGallery", "summary": "Newest 60 pictures",
        "description": "Newest first, each with its latest grade under `feedback`. Older pictures stay reachable through `GET /api/job/{id}` and `/img/{id}.png`.",
        "responses": {"200": ok("Pictures.", arr(ref("Picture")), [EX_PICTURE])}}}
    P["/img/{file}"] = {"get": {"tags": ["Pictures"], "operationId": "getImage", "summary": "Download a picture",
        "parameters": [path_param("file", "`<picture id>.png`.")], "responses": {"200": PNG, "404": {"description": "No such picture."}}}}
    P["/api/image/{image_id}"] = {"delete": {"tags": ["Pictures"], "operationId": "deletePicture", "summary": "Delete a picture",
        "description": "Removes the PNG, its record and the image engine's own copy. Cannot be undone.",
        "parameters": [IMAGE_ID],
        "responses": {"200": ok("Files removed.", obj({"deleted": arr({"type": "string"})}), {"deleted": ["1791022789-4284.png", "1791022789-4284.json", "comfy/picgen-1791022789-4284_00001_.png"]}),
                      "400": err("Malformed id.", {"error": "bad id"}), "404": ok("Nothing to delete.", None, {"deleted": []})}}}

    # ---------------------------------------------------------- Queue
    brief = obj({"id": s("string"), "status": s("string"), "model": s("string"), "characters": arr({"type": "string"}),
                 "sheet_for": {"type": ["string", "null"], "description": "Character id when this is a sheet or derive job."},
                 "sheet_tag": {"type": ["string", "null"]}, "edit_of": {"type": ["string", "null"]}, "at": s("string"),
                 "prompt": s("string", "First 120 characters.")})
    P["/api/queue"] = {
        "get": {"tags": ["Queue"], "operationId": "getQueue", "summary": "What is drawing and waiting",
                "responses": {"200": ok("Queue.", obj({"current": {"oneOf": [brief, {"type": "null"}]}, "queued": arr(brief)}), {"current": None, "queued": []})}},
        "delete": {"tags": ["Queue"], "operationId": "cancelAll", "summary": "Cancel everything waiting",
                   "description": "Includes character-sheet jobs. The job drawing now finishes.",
                   "responses": {"200": ok("How many were cancelled.", obj({"cancelled": s("integer")}), {"cancelled": 3})}}}
    P["/api/queue/{job_id}"] = {"delete": {"tags": ["Queue"], "operationId": "cancelJob", "summary": "Cancel one waiting job",
        "parameters": [path_param("job_id", "Job id.")], "responses": {"200": ok("0 or 1.", obj({"cancelled": s("integer")}), {"cancelled": 1})}}}
    P["/api/queue/sheet/{character_id}"] = {"delete": {"tags": ["Queue"], "operationId": "cancelSheet", "summary": "Cancel a character's waiting sheet and derive jobs",
        "parameters": [CHAR_ID], "responses": {"200": ok("How many were cancelled.", obj({"cancelled": s("integer")}), {"cancelled": 9})}}}

    # ---------------------------------------------------------- Planning and prompts
    P["/api/plan"] = {"get": {"tags": ["Planning and prompts"], "operationId": "plan", "summary": "Dry run: which references and settings a picture would use",
        "description": "Runs the same planner and lessons as the worker, without the GPU. Use it to check a prompt before spending two minutes on it. "
                       "For edits pass an edit model and `edit_of` instead of `character`.",
        "parameters": [q("prompt", "The prompt.", required=True), q("character", "Character id (character mode)."),
                       q("model", "Model id. Default `flux-kontext`; an edit model plans an edit."),
                       q("reference", "`auto`, `original`, an own view tag or `lib:<tag>`."),
                       q("garments", "`auto`, `none`, or comma-separated library tags."),
                       q("steps", "Steps you intend to send.", "integer"), q("guidance", "Guidance you intend to send.", "number"),
                       q("edit_of", "Edit mode: picture id or `char:<cid>[:<tag>]`.")],
        "responses": {"200": ok("The plan.", ref("Plan"), EX_PLAN), "400": err("Empty prompt or unknown character.", {"error": "no such character"})}}}
    P["/api/compile"] = {"post": {"tags": ["Planning and prompts"], "operationId": "compilePrompt", "summary": "Rewrite a rough prompt for the image models",
        "description": "One call to the chat model. With a character, the reply also carries the plan for the new prompt.",
        "requestBody": jbody(obj({"prompt": s("string", "Rough idea, up to 2000 characters."), "characters": arr({"type": "string"}, "Optional character id (first one used)."),
                                  "model": s("string", "Chat model. Default: the server default.")}, ["prompt"]),
                             {"prompt": "dash runs off scared from a dog in the park", "characters": ["c1791012478462"]}),
        "responses": {"200": ok("Rewritten prompt.", obj({"prompt": s("string"), "plan": ref("Plan")}),
                                {"prompt": "The same woman from the reference runs away in fear, looking back over her shoulder at a barking dog, in a sunny park."}),
                      "400": err("Empty prompt."), "502": err("The chat model failed.", {"error": "ollama: timed out"})}}}
    P["/api/chat"] = {"post": {"tags": ["Planning and prompts"], "operationId": "chat", "summary": "Talk an idea through with the prompt helper",
        "description": "Send the whole conversation each time (the server keeps no chat state). The helper puts its suggested prompt in a ```` ```prompt ```` fenced block. "
                       "If the image engine is idle the server stops it so the chat model gets the GPU.",
        "requestBody": jbody(obj({"messages": arr(obj({"role": s("string", enum=["user", "assistant"]), "content": s("string")}), "Conversation so far, oldest first."),
                                  "model": s("string", "Chat model."), "characters": arr({"type": "string"}, "Names of selected characters (up to 2)."),
                                  "character_ids": arr({"type": "string"}, "Character ids, so the helper knows their own views.")}, ["messages"]),
                             {"messages": [{"role": "user", "content": "a cosy picture of my character reading in the rain"}]}),
        "responses": {"200": ok("The reply.", obj({"reply": s("string")}), {"reply": "Here is a prompt:\n```prompt\nThe same woman from the reference reads a book by a rain-streaked window…\n```"}),
                      "400": err("No messages."), "502": err("The chat model failed.")}}}

    # ---------------------------------------------------------- Characters
    P["/api/characters"] = {
        "get": {"tags": ["Characters"], "operationId": "listCharacters", "summary": "All characters with their views",
                "responses": {"200": ok("Characters, newest first.", arr(ref("Character")), [EX_CHARACTER])}},
        "post": {"tags": ["Characters"], "operationId": "createCharacter", "summary": "Create a character from an uploaded picture",
                 "description": "Use a colour, front-facing, clearly lit picture. JPEG and WebP are converted to PNG. With `build_sheet` (default on) the 13 sheet views are queued, about a minute each.",
                 "requestBody": form(obj({"name": s("string", "Up to 60 characters."), "image": s("string", "The reference picture (up to 40 MB).", format="binary"),
                                          "notes": s("string", "Up to 400 characters."), "species": s("string", "What it is, e.g. `woman`, `dachshund`. Default `character`."),
                                          "build_sheet": s("string", "`1` (default) or `0`.")}, ["name", "image"])),
                 "responses": {"200": ok("Created.", obj({"id": s("string"), "sheet_queued": s("integer")}), {"id": "c1791055321482", "sheet_queued": 13}),
                               "400": err("Missing name or unreadable image.", {"error": "need a name and an image"}), "413": err("Image too large.")}}}
    P["/api/characters/from_image"] = {"post": {"tags": ["Characters"], "operationId": "characterFromPicture", "summary": "Create a character from a gallery picture",
        "requestBody": jbody(obj({"image_id": s("string"), "name": s("string"), "notes": s("string"), "species": s("string"), "build_sheet": s("string", "`1` or `0`.")}, ["image_id", "name"]),
                             {"image_id": "1791022789-4284", "name": "Maya", "species": "woman", "build_sheet": "1"}),
        "responses": {"200": ok("Created.", obj({"id": s("string"), "sheet_queued": s("integer")}), {"id": "c1791055400113", "sheet_queued": 13}),
                      "400": err("Unknown picture or no name.", {"error": "need an existing picture and a name"})}}}
    P["/api/characters/{character_id}"] = {"delete": {"tags": ["Characters"], "operationId": "deleteCharacter", "summary": "Delete a character and all its views",
        "description": "Pictures already made with it stay in the gallery.", "parameters": [CHAR_ID],
        "responses": {"200": ok("Removed files.", obj({"deleted": arr({"type": "string"})}), {"deleted": ["sheet/", "c1791055400113.png", "c1791055400113.json"]}),
                      "404": err("No such character.")}}}
    P["/api/characters/{character_id}/sheet"] = {"post": {"tags": ["Characters"], "operationId": "buildSheet", "summary": "Queue the missing sheet views",
        "parameters": [CHAR_ID], "requestBody": jbody({"type": "object"}, {}, required=False),
        "responses": {"200": ok("Queued views.", obj({"queued": s("integer"), "pending": arr({"type": "string"}), "have": arr({"type": "string"})}),
                                {"queued": 4, "pending": ["profile", "back-view", "walking-side", "sitting"], "have": ["three-quarter", "over-shoulder", "full-body-standing"]}),
                      "404": err("No such character.")}}}
    P["/api/characters/{character_id}/sheet/{tag}"] = {"delete": {"tags": ["Characters"], "operationId": "deleteView", "summary": "Delete one view",
        "parameters": [CHAR_ID, path_param("tag", "View tag.", r"^[a-z0-9][a-z0-9-]*$")],
        "responses": {"200": ok("Deleted.", obj({"deleted": arr({"type": "string"})}), {"deleted": ["profile"]}), "404": err("No such view.")}}}
    P["/api/characters/{character_id}/views"] = {"post": {"tags": ["Characters"], "operationId": "uploadViews", "summary": "Upload your own views of a character",
        "description": "Each file can be a strip or grid of panels, which is split into separate views. Labels are used one per panel, in order; `skip` drops a panel.",
        "parameters": [CHAR_ID],
        "requestBody": form(obj({"image": arr({"type": "string", "format": "binary"}, "One or more pictures (120 MB total)."),
                                 "labels": s("string", "One label per panel, separated by commas or new lines."),
                                 "grid": s("string", "`strip` (default), `auto`, `none` or `CxR` such as `4x2`."),
                                 "split": s("string", "Legacy: `0` means `grid=none`."), "caption": s("string", "Fraction cut off the bottom of each panel for captions (0.06 strips, 0.15 grids)."),
                                 "kind": s("string", "`expression` (default), `pose` or `activity`.")}, ["image"])),
        "responses": {"200": ok("Views added.", obj({"added": arr(obj({"tag": s("string"), "label": s("string")})), "count": s("integer")}),
                                {"added": [{"tag": "wink-smirk", "label": "wink smirk"}], "count": 1}),
                      "400": err("No image files."), "404": err("No such character."), "413": err("Upload too large.")}}}
    P["/api/characters/{character_id}/derive"] = {"post": {"tags": ["Characters"], "operationId": "deriveViews", "summary": "Draw the character's own versions of library items",
        "description": "Queues one job per item (about 140 s each). Own versions work much better than borrowed guides for a recurring character.",
        "parameters": [CHAR_ID],
        "requestBody": jbody(obj({"tags": arr({"type": "string"}, "Library tags."), "kinds": arr({"type": "string"}, "Whole kinds, e.g. `[\"expression\", \"pose\"]`.")}),
                             {"kinds": ["pose"]}),
        "responses": {"200": ok("Queued.", obj({"queued": s("integer"), "pending": arr({"type": "string"})}), {"queued": 44, "pending": ["arms-crossed", "dab"]}),
                      "404": err("No such character.")}}}
    P["/char/{file}"] = {"get": {"tags": ["Characters"], "operationId": "getCharacterImage", "summary": "A character's reference picture",
        "parameters": [path_param("file", "`<character id>.png`.")], "responses": {"200": PNG, "404": {"description": "Not found."}}}}
    P["/char/{character_id}/sheet/{file}"] = {"get": {"tags": ["Characters"], "operationId": "getViewImage", "summary": "One view of a character",
        "parameters": [CHAR_ID, path_param("file", "`<tag>.png`.")], "responses": {"200": PNG, "404": {"description": "Not found."}}}}

    # ---------------------------------------------------------- Library
    P["/api/library"] = {"get": {"tags": ["Library"], "operationId": "listLibrary", "summary": "Every library item",
        "responses": {"200": ok("Items and kinds.", obj({"items": arr(ref("LibraryItem")), "kinds": arr({"type": "string"})}),
                                {"items": [EX_LIB_ITEM], "kinds": EX_OPTIONS["library_kinds"]})}}}
    P["/api/library/views"] = {"post": {"tags": ["Library"], "operationId": "uploadLibrary", "summary": "Add items to the library",
        "description": "Sheets of panels are split automatically. With `group=3`, every three panels (front, side, back) become one wide item.",
        "requestBody": form(obj({"image": arr({"type": "string", "format": "binary"}, "One or more pictures (200 MB total)."),
                                 "kind": s("string", "One of `library_kinds`. Default `pose`."), "labels": s("string", "One label per item; `skip` drops one."),
                                 "source": s("string", "Character id drawn in the pictures, if any."),
                                 "grid": s("string", "`auto` (default), `strip`, `none` or `CxR`."),
                                 "caption": s("string", "Bottom crop fraction (default 0.15; 0.06 for strips)."), "top": s("string", "Top crop fraction (default 0.03)."),
                                 "group": s("string", "Panels per item, 1–12 (default 1).")}, ["image"])),
        "responses": {"200": ok("Items added.", obj({"added": arr(obj({"tag": s("string"), "label": s("string"), "kind": s("string")})), "count": s("integer")}),
                                {"added": [{"tag": "red-rain-boot", "label": "red rain boot", "kind": "footwear"}], "count": 1}),
                      "400": err("No image files."), "413": err("Upload too large.")}}}
    P["/api/library/{tag}"] = {"delete": {"tags": ["Library"], "operationId": "deleteLibraryItem", "summary": "Remove a library item",
        "description": "Characters keep any own versions they already drew from it.", "parameters": [path_param("tag", "Item tag.")],
        "responses": {"200": ok("Removed.", obj({"deleted": arr({"type": "string"})}), {"deleted": ["red-rain-boot"]}), "404": err("No such item.")}}}
    P["/lib/{file}"] = {"get": {"tags": ["Library"], "operationId": "getLibraryImage", "summary": "A library item's picture",
        "parameters": [path_param("file", "`<tag>.png`.")], "responses": {"200": PNG, "404": {"description": "Not found."}}}}

    # ---------------------------------------------------------- Feedback
    fb_row = obj({"id": s("integer"), "image_id": s("string"), "verdict": s("string", enum=["up", "down"]), "issues": arr({"type": "string"}), "note": s("string"),
                  "diagnosis": arr({"type": "string"}), "job": ref("Picture"), "lessons_applied": arr({"type": "string"}),
                  "lessons_changed": arr({"type": "object"}), "critic": {"oneOf": [ref("Critique"), {"type": "null"}]},
                  "fix_image_id": {"type": ["string", "null"]}, "created_at": s("string")})
    P["/api/feedback"] = {
        "get": {"tags": ["Feedback"], "operationId": "listFeedback", "summary": "Every grade given to one picture",
                "parameters": [q("image_id", "Picture id.", required=True)],
                "responses": {"200": ok("Grades, newest first.", arr(fb_row))}},
        "post": {"tags": ["Feedback"], "operationId": "grade", "summary": "Grade a picture",
                 "description": "A thumbs-up credits the lessons that were applied. A thumbs-down records what went wrong, switches on matching built-in lessons, and returns a diagnosis and a ready-made `repair`. "
                                "Submit `repair` (a targeted edit) or `repair.regenerate` (a fresh picture) to `POST /api/generate`, adding `feedback_id`.",
                 "requestBody": jbody(obj({"image_id": s("string"), "verdict": s("string", enum=["up", "down"]),
                                           "issues": arr({"type": "string", "enum": ISSUES}, "What is wrong. If omitted on a thumbs-down, the automatic check's findings are used."),
                                           "note": s("string", "In your own words (up to 1000 characters).")}, ["image_id", "verdict"]),
                                      {"image_id": "1791047030-6999", "verdict": "down", "issues": ["anatomy", "gaze"], "note": "he should be looking up at the sky"}),
                 "responses": {"200": ok("Recorded.", obj({"id": s("integer", "Feedback id; pass it as `feedback_id` with a follow-up."), "image_id": s("string"), "verdict": s("string"),
                                                         "issues": arr({"type": "string"}), "note": s("string"), "diagnosis": arr({"type": "string"}),
                                                         "lessons_changed": arr({"type": "object"}, "Lessons switched on or strengthened."),
                                                         "lessons_credited": arr({"type": "string"}, "Lessons credited by a thumbs-up."),
                                                         "repair": {"oneOf": [ref("Repair"), {"type": "null"}]}}),
                                         {"id": 7, "image_id": "1791047030-6999", "verdict": "down", "issues": ["anatomy", "gaze"], "note": "he should be looking up at the sky",
                                          "diagnosis": ["'looking up' is a head-direction instruction. The library has no view for it, so the reference's own gaze won."],
                                          "lessons_changed": [{"key": "gaze-explicit", "title": "Spell out head direction and gaze", "change": "switched on"}],
                                          "lessons_credited": [], "repair": dict(EX_REPAIR, feedback_id=7, repair_of="1791047030-6999")}),
                               "400": err("Missing or unknown verdict.", {"error": "verdict must be 'up' or 'down'"}),
                               "404": err("No such picture.")}}}
    P["/api/feedback/diagnose"] = {"get": {"tags": ["Feedback"], "operationId": "diagnose", "summary": "Explain a flaw and propose a fix, without recording anything",
        "parameters": [q("image_id", "Picture id.", required=True), q("issues", "Comma-separated issue keys."), q("note", "Your words.")],
        "responses": {"200": ok("Diagnosis.", obj({"diagnosis": arr({"type": "string"}), "repair": {"oneOf": [ref("Repair"), {"type": "null"}]},
                                                   "critique": {"oneOf": [ref("Critique"), {"type": "null"}]}, "issue_labels": {"type": "object"}}),
                                {"diagnosis": ["Plan used → identity: original · 1 reference · guidance 3.5 · 24 steps · sent verbatim"], "repair": EX_REPAIR,
                                 "critique": EX_CRITIQUE, "issue_labels": {"gaze": "Looking in the wrong direction"}}),
                      "404": err("No such picture.")}}}
    P["/api/feedback/critique"] = {"post": {"tags": ["Feedback"], "operationId": "critique", "summary": "Run the automatic check on a picture now",
        "description": "Synchronous: takes up to about 90 s. If the check cannot run, `critique` carries an `error`.",
        "requestBody": jbody(obj({"image_id": s("string")}, ["image_id"]), {"image_id": "1791047030-6999"}),
        "responses": {"200": ok("Result.", obj({"critique": ref("Critique"), "suggested_issues": arr({"type": "string"}), "diagnosis": arr({"type": "string"}),
                                                "repair": {"oneOf": [ref("Repair"), {"type": "null"}]}}),
                                {"critique": EX_CRITIQUE, "suggested_issues": ["anatomy", "gaze"], "diagnosis": ["…"], "repair": EX_REPAIR}),
                      "404": err("No such picture."), "502": err("The picture could not be read.", {"error": "critic unavailable"})}}}
    traits = obj({"id": s("string"), "name": s("string"), "traits": arr({"type": "string"}, "Permanent features, e.g. `bald head`, `round glasses`."),
                  "subject": {"type": ["string", "null"], "description": "What the vision model saw (`man`, `dog`…)."},
                  "edited": s("boolean", "The owner's wording, kept until the reference picture changes."),
                  "read": s("boolean", "The reference has been read by the vision model."), "at": {"type": ["string", "null"]}})
    ex_traits = {"id": "c1791007342591", "name": "Sam", "traits": ["bald head", "brown beard", "round glasses", "light skin tone", "middle-aged"],
                 "subject": "man", "edited": False, "read": True, "at": "2026-10-03 13:08"}
    P["/api/feedback/traits"] = {
        "get": {"tags": ["Feedback"], "operationId": "listTraits", "summary": "Each character's permanent features",
                "description": "The features a repair must keep (face, hair, glasses…), read once from each character's reference picture. Cached only: this never calls the vision model.",
                "responses": {"200": ok("Characters, newest first.", obj({"characters": arr(traits)}),
                                        {"characters": [ex_traits, {"id": "c1791012478462", "name": "Maya", "traits": [], "subject": None, "edited": False, "read": False, "at": None}]})}},
        "post": {"tags": ["Feedback"], "operationId": "setTraits", "summary": "Correct or re-read a character's features",
                 "description": "Saves your wording (`edited: true`) until the character's reference picture changes. With `reread: true`, or an empty `traits`, the cached reading is dropped and the vision model reads the reference again (a few seconds).",
                 "requestBody": jbody(obj({"character": s("string", "Character id."),
                                           "traits": {"oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}], "description": "A list, or one comma-separated string."},
                                           "reread": s("boolean", "Ask the vision model again.", default=False)}, ["character"]),
                                      {"character": "c1791007342591", "traits": ["bald head", "ginger beard", "round glasses"]}),
                 "responses": {"200": ok("The updated entry.", traits, dict(ex_traits, traits=["bald head", "ginger beard", "round glasses"], edited=True)),
                               "404": err("No such character."), "502": err("A re-read failed.", {"error": "the vision model could not read the reference picture"})}}}
    P["/api/feedback/teach"] = {
        "get": {"tags": ["Feedback"], "operationId": "teachCheck", "summary": "Should this picture become one of the character's own views?",
                "description": "Read-only. `worth` is true only for a fix, or a picture drawn from another person's pose picture, that the automatic check passed, and never when the character already has the view. "
                               "`why` gives the reason for the verdict either way, for example that the check has not looked at the picture yet or still sees a flaw. "
                               "`label` and `kind` are suggestions for `POST /api/feedback/teach`.",
                "parameters": [q("image_id", "Picture id.", required=True)],
                "responses": {"200": ok("The suggestion.", obj({"character": {"type": ["string", "null"], "description": "Character id, or null when the picture is not of a saved character."},
                                                               "name": s("string"), "label": s("string", "Suggested label: what the character is doing."),
                                                               "kind": s("string", enum=["pose", "activity", "expression"]),
                                                               "already": s("boolean", "This picture is already one of the character's views."),
                                                               "worth": s("boolean", "Teaching it is likely to help future pictures."), "why": s("string", "The reason for `worth`, in words.")}),
                                        {"character": "c1791012478462", "name": "Maya", "label": "sitting on a chair reading a book", "kind": "pose", "already": False, "worth": True,
                                         "why": "the automatic check passed this picture and its pose came from a fix or from another person's pose picture"}),
                              "404": err("No such picture.")}},
        "post": {"tags": ["Feedback"], "operationId": "teach", "summary": "Save an approved picture as one of the character's own views",
                 "description": "The planner then uses it as both identity and pose for matching requests, so later pictures of that pose start from the character itself. "
                                "Undo with `DELETE /api/characters/{character_id}/sheet/{tag}`.",
                 "requestBody": jbody(obj({"image_id": s("string"), "label": s("string", "What the character is doing; this is what prompts will match."),
                                           "kind": s("string", enum=["pose", "activity", "expression"], default="pose")}, ["image_id", "label"]),
                                      {"image_id": "1791052254-3535", "label": "reading a spellbook in a candlelit library", "kind": "activity"}),
                 "responses": {"200": ok("Saved.", obj({"character": s("string"), "added": arr(obj({"tag": s("string"), "label": s("string")}))}),
                                         {"character": "c1791012478462", "added": [{"tag": "reading-a-spellbook-in-a-candlelit-libra", "label": "reading a spellbook in a candlelit library"}]}),
                               "400": err("No label, or the picture is not of a saved character.", {"error": "name the pose: what the character is doing"}),
                               "404": err("No such picture.")}}}
    P["/api/feedback/stats"] = {"get": {"tags": ["Feedback"], "operationId": "feedbackStats", "summary": "Approval rates, flaw counts, fix outcomes and lessons",
        "responses": {"200": ok("Statistics.", obj({"overall": {"type": "object"}, "last_7_days": {"type": "object"}, "auto_check": {"type": "object"},
                                                    "outcomes": {"type": "object", "description": "`{flaw: {fix|guided|retry: {tries, cleared, broke}}}`"},
                                                    "by_category": {"type": "object"}, "by_model": {"type": "object"}, "by_reference_mode": {"type": "object"},
                                                    "by_reference_count": {"type": "object"}, "lessons": arr(ref("Lesson")), "recent": arr({"type": "object"}),
                                                    "issue_labels": {"type": "object"}}),
                                {"overall": {"total": 4, "up": 2, "down": 2, "approval": 50}, "last_7_days": {"total": 4, "up": 2, "down": 2, "approval": 50},
                                 "auto_check": {"checked": 8, "with_issues": 8, "by_category": {"gaze": 5, "expression": 3}},
                                 "outcomes": {"gaze": {"fix": {"tries": 2, "cleared": 0, "broke": 0}, "guided": {"tries": 1, "cleared": 0, "broke": 1}, "retry": {"tries": 3, "cleared": 0, "broke": 1}}},
                                 "by_category": {"gaze": 2, "anatomy": 1}, "by_model": {"flux-kontext": {"total": 2, "up": 1, "approval": 50}},
                                 "by_reference_mode": {"original": {"total": 3, "up": 2, "approval": 67}}, "by_reference_count": {"1": {"total": 4, "up": 2, "approval": 50}},
                                 "lessons": [EX_LESSON], "recent": [], "issue_labels": {"gaze": "Looking in the wrong direction"}})}}}

    # ---------------------------------------------------------- Lessons
    P["/api/lessons"] = {
        "get": {"tags": ["Lessons"], "operationId": "listLessons", "summary": "All lessons",
                "responses": {"200": ok("Lessons and issue labels.", obj({"lessons": arr(ref("Lesson")), "issue_labels": {"type": "object"}}), {"lessons": [EX_LESSON], "issue_labels": {}})}},
        "post": {"tags": ["Lessons"], "operationId": "createLesson", "summary": "Add your own rule",
                 "description": "Created switched on. Without `keywords` it applies to every character and edit picture. `guidance_min` is clamped to 1–10 and `steps_min` to 4–60.",
                 "requestBody": jbody(obj({"title": s("string"), "category": s("string", "An issue key or `framing`.", enum=ISSUES + ["framing"]),
                                           "keywords": arr({"type": "string"}, "Whole words that trigger it."), "append": s("string", "Text added to the prompt."),
                                           "prepend": s("string", "Text put before the prompt."), "guidance_min": s("number"), "steps_min": s("integer"), "why": s("string")}, ["title"]),
                                      {"title": "Waving needs a clear hand", "category": "anatomy", "keywords": ["waving", "waves"],
                                       "append": "One raised hand with five fingers.", "steps_min": 28, "why": "Waves came out with fused fingers."}),
                 "responses": {"200": ok("The new lesson.", ref("Lesson"))}}}
    P["/api/lessons/{lesson_id}"] = {
        "patch": {"tags": ["Lessons"], "operationId": "updateLesson", "summary": "Switch a lesson on or off, or change it",
                  "description": "Allowed fields: `enabled`, `title`, `why`, `trigger` (an object such as `{\"when\": \"keywords\", \"any\": [\"waving\"]}`, or simply a list or comma-separated string of keywords), `action` (an object). "
                                 "Numbers are clamped: `guidance_min`/`guidance_max` 1–10, `steps_min` 4–60; values that are not numbers are dropped. "
                                 "Editing a built-in lesson's text, trigger or action marks it as edited, so the change survives restarts. "
                                 "Switching a lesson off sets `owner_off`, so the automatic check never switches it back on.",
                  "parameters": [path_param("lesson_id", "Lesson id.")],
                  "requestBody": jbody(obj({"enabled": s("integer", enum=[0, 1]), "title": s("string"), "why": s("string"),
                                            "trigger": {"oneOf": [{"type": "object"}, {"type": "array", "items": {"type": "string"}}, {"type": "string"}]}, "action": {"type": "object"}}), {"enabled": 0}),
                  "responses": {"200": ok("The updated lesson.", ref("Lesson")), "400": err("Bad id."), "404": err("No such lesson or nothing to change.")}},
        "delete": {"tags": ["Lessons"], "operationId": "deleteLesson", "summary": "Delete a lesson (built-ins are switched off instead)",
                   "parameters": [path_param("lesson_id", "Lesson id.")],
                   "responses": {"200": ok("Done.", obj({"deleted": s("integer")}), {"deleted": 11}), "400": err("Bad id.")}}}
    P["/api/lessons/learn"] = {"post": {"tags": ["Lessons"], "operationId": "learnLessons", "summary": "Ask the vision model to propose lessons from recent feedback",
        "description": "Proposals arrive switched off (`source: ai`) for you to review. Needs at least two flawed pictures. Takes up to 3 minutes.",
        "requestBody": jbody(obj({"n": s("integer", "Rules to propose.", default=3), "limit": s("integer", "Recent cases to study.", default=25)}), {"n": 3}, required=False),
        "responses": {"200": ok("Proposals, or a reason there are none.", obj({"proposed": arr(ref("Lesson")), "examined": s("integer"), "reason": s("string"), "error": s("string")}),
                                {"proposed": [], "reason": "need at least two flawed pictures to learn from", "examined": 1})}}}

    return {
        "openapi": "3.1.0",
        "info": {"title": "picgen API", "version": VERSION,
                 "description": "The HTTP API behind picgen studio. Everything the studio page does goes through these endpoints. "
                                "JSON in and out, except uploads (multipart) and images (PNG). Errors are JSON `{\"error\": \"...\"}` with a 4xx or 5xx status. "
                                "There is no authentication in v1.0: run picgen on a private network or behind an authenticating reverse proxy.",
                 "license": {"name": "Proprietary"}},
        "servers": [{"url": "/", "description": "This server"}, {"url": "http://127.0.0.1:8070", "description": "Local default"}],
        "tags": TAGS,
        "paths": P,
        "components": {"schemas": SCHEMAS},
    }


def write(path: Path | None = None) -> Path:
    path = path or Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
    path.write_text(json.dumps(build(), indent=1) + "\n")
    return path


if __name__ == "__main__":
    p = write()
    spec = build()
    print(f"wrote {p}: {sum(1 for ops in spec['paths'].values() for _ in ops)} operations")
