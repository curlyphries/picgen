# API guide and reference

Everything the studio does is an HTTP call you can make yourself. This chapter shows how to use the API; the full endpoint reference follows it.

## The essentials

| | |
|---|---|
| **Base URL** | `http://127.0.0.1:8070` on the host itself, or your reverse-proxy address, for example `https://pics.example.com`. The examples below use `$PICGEN` for it. |
| **Format** | JSON in and out. Uploads are `multipart/form-data`; images are PNG. |
| **Errors** | A 4xx or 5xx status with `{"error": "what went wrong"}`. Unknown `/api/` paths answer `404 {"error": "no such endpoint: GET /api/..."}`. |
| **Authentication** | None in v1.0. Access is controlled by the network: keep picgen on `127.0.0.1` and publish it only through a proxy on a private network, or behind a proxy that authenticates. See [Operations](07-operations.md#security). |
| **Spec** | OpenAPI 3.1 at `/api/openapi.json`. |
| **Explorer** | `/api/docs`, with every endpoint and live **Try it out**. Those requests are real: POST and DELETE change data and use the GPU. |
| **Python client** | `tools/picgen_client.py`, standard library only. Use it as a command or import it. |

### Generation is asynchronous

Pictures take from 4 seconds to several minutes, so the API never makes you hold a connection open:

1. `POST /api/generate` queues the job and returns `{"id": "..."}` immediately.
2. Poll `GET /api/job/{id}` every one or two seconds. `status` moves through `queued` → `starting model` → `drawing` → (`checking`) → `done`, or `failed` with an `error`.
3. Fetch the picture from `/img/{id}.png`.

One job draws at a time, first in, first out. The queue is saved to disk, so a server restart does not lose waiting jobs; a job that was drawing during a restart starts again from the beginning.

### Ids

| Thing | Format | Example |
|---|---|---|
| Picture / job | `<epoch seconds>-<4 digits>` | `1791022789-4284` |
| Character | `c` + digits | `c1791012478462` |
| Character view | lowercase tag | `over-shoulder`, `wink-smirk` |
| Library item | lowercase tag; `lib:<tag>` when used as a reference | `lib:arms-crossed` |
| Lesson | integer | `2` |

## Quick start with curl

```bash
PICGEN=http://127.0.0.1:8070

# Is it up, and what can it do?
curl -s $PICGEN/api/status
curl -s $PICGEN/api/image_models | python3 -m json.tool

# Queue a picture
curl -s -X POST $PICGEN/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "A red fox asleep in fresh snow at dawn, soft pink light.", "model": "z-image-turbo", "size": "landscape"}'
# -> {"id": "1791055012-4821"}

# Follow it
curl -s $PICGEN/api/job/1791055012-4821
# -> {"id": "...", "status": "drawing", ...}  then  {"status": "done", "file": "1791055012-4821.png", "seconds": 11, ...}

# Download it
curl -s -o fox.png $PICGEN/img/1791055012-4821.png
```

## Quick start with the Python client

```bash
tools/picgen_client.py status
tools/picgen_client.py generate "A red fox asleep in fresh snow at dawn" --size landscape --wait --out fox.png
tools/picgen_client.py characters
tools/picgen_client.py plan c1791012478462 "runs away scared in a hoodie"
tools/picgen_client.py generate "runs away scared in a hoodie" --character c1791012478462 --wait --out run.png
tools/picgen_client.py edit 1791055012-4821 "make it night, keep everything else the same" --wait
```

Set `PICGEN_URL` (or pass `--url`) to reach a server other than `127.0.0.1:8070`. As a module:

```python
from picgen_client import Picgen, PicgenError

pg = Picgen("http://127.0.0.1:8070")
job = pg.generate("A lighthouse at dusk, storm clouds", size="wide", wait=True,
                  on_status=lambda j: print(j["status"]))
pg.download(job["id"], "lighthouse.png")
print(job["seconds"], "s, seed", job["seed"])
```

`wait=True` polls every 2 seconds and raises `PicgenError` if the job fails or takes longer than 30 minutes.

## Recipes

### A character in a scene

Check the plan first; it is free. Then generate with a character-mode model.

```bash
curl -s "$PICGEN/api/plan?character=c1791012478462&prompt=runs%20away%20scared%20in%20a%20hoodie"
# pose: running side view (guide) · expression: scared (guide) · garments: brown pullover hoodie
# ref_count 4 · guidance 4.0 · steps 28 · seconds 255

curl -s -X POST $PICGEN/api/generate -H 'Content-Type: application/json' -d '{
  "prompt": "runs away scared in a hoodie",
  "model": "flux-kontext",
  "characters": ["c1791012478462"]
}'
```

Leave `reference` and `garments` out (both default to `auto`) to let the planner choose. To pin choices, pass `"reference": "lib:arms-crossed"` or an own view tag, and `"garments": ["red-rain-boot"]`, or `[]` for none. Leave `steps` and `guidance` out so picgen can raise them when guides are used.

### Edit a picture

```bash
curl -s -X POST $PICGEN/api/generate -H 'Content-Type: application/json' -d '{
  "prompt": "make it night, keep everything else the same",
  "model": "flux-kontext-edit",
  "edit_of": "1791055012-4821"
}'
```

`edit_of` can also be a character's reference (`char:c1791012478462`) or one of its views (`char:c1791012478462:profile`).

### Commercial character work and two characters in one picture

`qwen-image` (character) and `qwen-image-edit` (edit) are the Apache-licensed pair, so use them for anything that will be sold or published. They take the same fields as the FLUX pair, plus `extra_refs`: up to two more pictures (picture ids or `char:<cid>[:<view>]`) appended after the planner's references, so a second saved character can appear as image 2 or 3. Say which image is which and name each character's anchors; the model keeps what the prompt names.

```bash
curl -s -X POST $PICGEN/api/generate -H 'Content-Type: application/json' -d '{
  "prompt": "The skeleton woman from image 1 (red flower, gold hoops, ID badge) stands in the doorway on the left. The tiny grandmother from image 2 (silver bun, beaded glasses chain, gray cardigan, white apron) reaches up to touch her cheek. Same flat card style.",
  "model": "qwen-image",
  "characters": ["c1791012478462"],
  "extra_refs": ["char:c1791007342591"],
  "size": "portrait"
}'
```

Steps and guidance are fixed for these two models (8 steps, cfg 1, set by the Lightning LoRA); change the `seed` to get a different take.

Two things to know when `qwen-image` draws a scene rather than a portrait. It treats **every** reference picture as a character to put in the scene, so pass `"guides": false` to keep the planner from adding a library pose or expression guide (the guide model would appear as an extra person; seen 2026-10-05). And it copies the reference's framing, so for a frameless scene use a frameless view as the reference: `"reference": "three-quarter"` (or any sheet tag) and `char:<cid>:<tag>` in `extra_refs`, not the original card.

### Magic erase (remove something and fill in the background)

`POST /api/erase` works like a phone's magic eraser: a picture plus a mask (white = erase), and back comes the picture with the masked thing gone and the background continued behind it. Everything outside the mask is returned pixel for pixel. Under the hood the masked area is painted flat magenta, Qwen-Image-Edit is told to remove the magenta and fill in what is behind it, and only the masked pixels are taken from the result. About a minute; the request blocks while it runs.

```bash
curl -s -o out.png -F "image=@panel.png" -F "mask=@mask.png" \
  -F "prompt=the wooden desk top and the classroom behind it" $PICGEN/api/erase
```

`prompt` (what is behind the object), `feather` (seam softening in px, default 6) and `seed` are optional. Draw the mask over the WHOLE object including its tip, cap or shadow: anything outside the mask stays, by design. If the object sits on a person (a pen in a hand, a hat), say what should be there instead in `prompt` ("her empty hand"); the fill then completes the person rather than painting background. Keep the mask tight to the object: the model redraws the picture a hair off-register, so a wide mask shows double edges at the seam.

**GIMP plug-in.** `tools/gimp/faraway-erase.py` (installed at `~/.config/GIMP/3.2/plug-ins/faraway-erase/` on the machine that runs GIMP) adds *Filters → Faraway → Magic Erase*: make a selection around the thing, run it, and the result arrives as a new layer named "Magic Erase" holding only the selected pixels, so the original stays underneath and the edit can be toggled or deleted. Needs GIMP 3 with Python plug-ins and Pillow on the system Python.

### Draw with a trained character LoRA (Z-Image Turbo)

For high volume, a character can be trained into a small LoRA (ai-toolkit on Z-Image De-Turbo, see [Operations → Character LoRAs](07-operations.md#character-loras)) and used on the fast model: about 10 s a picture and no reference images. Pass `loras` (up to three) and put each LoRA's trigger word in the prompt.

```bash
curl -s -X POST $PICGEN/api/generate -H 'Content-Type: application/json' -d '{
  "prompt": "faraway_death. She writes a parking ticket on a dusty pickup truck. loteria card style, flat colors, thick black outlines, mustard yellow background.",
  "model": "z-image-turbo",
  "loras": [{"name": "faraway_death_v3.safetensors", "strength": 1.0}],
  "size": "portrait"
}'
```

Files must already be in `COMFY_DIR/models/loras`; unknown names are dropped silently. Text the model letters itself (a banner, a sign) comes out misspelled with a character LoRA loaded, so draw lettering over the picture afterwards.

### Create a character

```bash
curl -s -X POST $PICGEN/api/characters \
  -F name=Maya -F species=woman -F notes="red afro, yellow raincoat" \
  -F image=@maya.png -F build_sheet=1
# -> {"id": "c1791055321482", "sheet_queued": 13}
```

The 13 sheet views take about a minute each. Watch them with `GET /api/characters` (`sheet_pending`), or cancel them with `DELETE /api/queue/sheet/{character_id}`.

### Grade, then fix

```bash
# Thumbs-down with what is wrong
curl -s -X POST $PICGEN/api/feedback -H 'Content-Type: application/json' -d '{
  "image_id": "1791047030-6999", "verdict": "down",
  "issues": ["gaze"], "note": "he should be looking up at the sky"
}'
# -> {"id": 7, "diagnosis": [...], "repair": {"prompt": "...", "model": "flux-kontext-edit", "edit_of": "...",
#     "guidance": 4.5, "steps": 28, "guide": {...}, "feedback_id": 7, "repair_of": "1791047030-6999", "regenerate": {...}}}
```

Send `repair` back to `POST /api/generate` as it is (it is already a valid request body), or send `repair.regenerate` with `"feedback_id": 7` added for a fresh picture instead. When the fix finishes, the automatic check records whether the flaw cleared. That record feeds the choice between a word-only fix and a guided fix next time.

### Batch

The queue runs one job at a time, so submit everything first, then wait on each id:

```python
prompts = ["a fox in snow", "a heron at dawn", "a hare in a meadow"]
ids = [pg.generate(p, size="landscape")["id"] for p in prompts]
for i in ids:
    pg.download(pg.wait(i)["id"], f"{i}.png")
```

## Limits that affect clients

| Limit | Value | Source |
|---|---|---|
| Prompt length | 2,000 characters (longer is cut) | server |
| Steps / guidance | clamped to 1–60 / 0–15 | server |
| Characters per picture | 1 (`max_characters`) | `MAX_CHARACTERS` |
| Garments per picture | 2 (`max_garments`), and only while reference slots remain | `MAX_GARMENTS` |
| References per picture | 4: character, pose, face, garment | `MAX_REFS` |
| Upload size | 40 MB new character, 120 MB views, 200 MB library | server |
| Gallery listing | newest 60 (older pictures stay reachable by id) | server |
| Failed-job memory | last 50, until restart | server |

Read live values from `GET /api/options` instead of hard-coding them.

## Endpoint reference

Generated from `docs/openapi.json`. Field types follow JSON; `null` means "not set for this kind of picture".

{{API_REFERENCE}}
