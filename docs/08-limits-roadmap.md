# Limits and roadmap

What v1.0 has been tested to do, what it cannot do yet, and what should come next.

## What v1.0 has been tested to do

These were run on the reference hardware during development:

| Behaviour | Evidence |
|---|---|
| Text to image in seconds | Z-Image Turbo: about 10 s a picture warm, about 50 s from cold |
| A saved character keeps its likeness across scenes | One cartoon character held across bicycle, lab and campfire scenes; another kept face, glasses and beard through beach, desk and tuxedo scenes |
| The planner turns one sentence into pose, face and outfit | *"Gets scared and runs away"* produced a running pose and a scared face from the character's own views. *"Sprinting, terrified, in jeans"* used three guides and kept the identity. |
| Garments from the library are worn | White tee with cargo shorts both worn exactly; a chef's toque with an apron; cowboy hat with boots; a bicycle helmet; red stilettos |
| Camera turns work | *"Looks back over his shoulder at the dog"* picked the over-the-shoulder view and the pose followed |
| Edits change only what is asked | *"Make it night"*; *"he needs to run, scared"* turned a beach picture into a sprint with scene and clothes kept |
| The matcher picks the right references | A 40-prompt probe table, 46 must-match / must-not-match probes and a 277-prompt accessory suite all passed |
| Safety guards hold | Path-traversal and wildcard ids are rejected on every destructive route; concurrent library writers do not lose entries |

## Known limits

### Pictures

- **One character per picture.** Two references side by side duplicated one character and lost the other. v1.0 enforces one.
- **Four references at most:** the character, a pose, a face and one garment. With all four in use, the last garment can drop out (cowboy boots came back as plain shoes). Name the most important garment first.
- **Borrowed guides transfer weakly.** A face or pose from another character's picture gives a milder expression, and a side-view pose may come out frontal. Rendering the character's own versions (Characters → *Make this character's own versions*) fixes both, at about 2 minutes per item.
- **The reference picture matters.** A black-and-white or side-on reference lets colours and details drift. Use a colour, front-facing, clearly lit picture.
- **Character pictures are slow:** about 90 s with one reference, plus about 55 s for each extra one. The heaviest pictures took 3–5 minutes.
- **The planner reads English keywords.** It is predictable but literal. Unusual wording can miss; the plan line shows this before any GPU time is spent, and **Rewrite for the generator** usually fixes it.

### Product

- **No accounts.** No logins, per-user galleries or roles. Everyone who can reach the server shares one gallery and one queue, and *cancel all* cancels everyone's waiting jobs.
- **One GPU, one job at a time.** Chat and pictures take turns on the card.
- **Gallery** shows the newest 60 pictures, with no search or paging. Older pictures remain reachable by id.
- **Failed jobs** are remembered only until the server restarts.
- **Accessibility:** no focus trapping in dialogs, mouse-only queue cancel, small badge text, and a dark theme only.

### Licensing and privacy

- **FLUX.1-dev and FLUX.1 Kontext [dev] are non-commercial.** The photoreal model and the FLUX character/edit models use them. Selling or publishing their output commercially needs a commercial license from Black Forest Labs. Z-Image Turbo and Qwen-Image-Edit 2511 (both Apache 2.0) are cleared for commercial use, so a fully commercial pipeline is Z-Image for new pictures and `qwen-image` / `qwen-image-edit` for characters and edits (added 2026-10-04).
- **Review runs in the cloud by default.** Picture checks, feature reading, repair planning and lesson proposals use an Ollama Cloud vision model because it cannot share the GPU with the image models. They need an internet connection. See [Operations → Privacy](07-operations.md#privacy) to run them locally instead.

### Technical

- ComfyUI must listen on port 8188 on the same host.
- ToonYou always draws 768 × 768. Edits keep the source picture's size.
- Character and library images are cached by browsers for an hour without cache-busting.
- `comfy.log` is not rotated automatically.

## Roadmap

In order of priority.

### v1.1: sharing and hardening

| Item | Why |
|---|---|
| **Authentication**: API keys for scripts, and a studio login (or single sign-on at the proxy), with an owner for each picture and character | Required anywhere beyond a trusted private network |
| **A commercial-safe model set**: Z-Image Turbo for text to image, plus Apache-licensed options for characters and edits (candidates already in the catalog: Chroma, FLUX.2 klein 4B), or a Black Forest Labs license | Makes every picture safe to publish commercially |
| **A local review option that fits the GPU**: a vision model small enough to share the card with the image models | Removes the cloud dependency on a single 24 GB machine |
| **Gallery search and paging**, and failed jobs kept on disk | Usable beyond a few dozen pictures |
| **Accessibility pass** and a light theme | Public-facing quality |
| **Push updates** (server-sent events) instead of polling `/api/job` | Fewer requests, instant status |

### v1.2: stronger characters

| Item | Why |
|---|---|
| **Per-character LoRA training** from the sheet and own views | A stable likeness without spending reference slots on identity |
| **Two characters in one picture** (regional prompting or LoRAs) | The most requested scene type |
| **Batch endpoint** (one request, many prompts or seeds) | Newsletter and social-media workflows |
| **Webhooks** when a picture finishes | Plugs into automation tools |

### Later

- Several GPUs or remote worker machines behind one queue.
- Short animations from a character picture.
