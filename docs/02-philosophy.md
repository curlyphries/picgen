# Philosophy

Why picgen exists, the ideas it is built on, what each one means in the code, and the cost accepted in exchange. When a new feature fits these ideas it belongs in picgen. When it fights them, it needs a strong reason.

## Why picgen exists

picgen was built for one goal: **consistent characters, generated locally.** The same character should look like itself picture after picture, and the drawing should happen on hardware you own, not on a service that charges per picture and keeps your uploads.

A single workstation cannot do everything at once, though. The image models fill most of a 24 GB graphics card, so the large vision model that reviews finished pictures cannot sit beside them. That work is offloaded to a cloud service, and everything else stays on the machine. The principles below follow from that split.

## 1. Draw locally; offload only what the machine cannot hold

Every picture is drawn by models on the local GPU. Prompts, uploads, characters and results are plain files on the host's disk. No picture is drawn by a cloud service, and no credits are spent.

**What runs in the cloud, and why.** Five jobs use a large vision model: checking finished character and edit pictures, reading a character's permanent features from its reference, describing what a repair must keep, working out the action a guided repair should show, and proposing lessons. A model good enough for that does not fit on the card next to the image models, and swapping models in and out for every check would add minutes to each picture. So by default these jobs go through the local Ollama to an Ollama Cloud model (`CRITIC_MODEL=gemma4:31b-cloud`), which receives the picture, the character's reference and the request.

**In the code.** The image engine is ComfyUI on `127.0.0.1:8188`, and the chat model runs locally in Ollama on `127.0.0.1:11434`, which also relays requests for cloud models. The picgen server binds to `127.0.0.1` by default and is published through your own reverse proxy.

**Choosing fully local.** With a bigger GPU or a second machine, or when nothing may leave the host, set `CRITIC_MODEL` and `LEARNER_MODEL` to a local vision model, or switch the review off with `CRITIC_AUTO=0`. See [Operations](07-operations.md#privacy).

**Cost accepted.** You need your own GPU (24 GB was the reference), and picture speed is bounded by it. In the default setup, the review steps need an internet connection.

## 2. One GPU, shared politely

A workstation GPU usually has other tenants: a chat model, a video analytics detector, a desktop. picgen treats GPU memory as shared.

- ComfyUI starts only when a job arrives and stops after `IDLE_MINUTES` (5) with nothing to do.
- Before drawing, picgen unloads every resident Ollama model, because a chat model, an image model and anything else on the card do not fit in 24 GB together.
- Chat goes the other way: an idle ComfyUI is stopped so the chat reply stays fast.
- ComfyUI runs with `--reserve-vram 3`, keeping 3 GB free for the desktop and browser, and frees its models after the last job in the queue.

**Cost accepted.** The first picture after a quiet period waits roughly 40 seconds for the model to load. Character pictures re-load the text encoder more often than they would on a dedicated card.

## 3. Describe a moment; the studio picks the references

Consistent characters with open models depend on choosing the right reference pictures: who the character is, how they stand, what their face does, what they wear. Doing that by hand for every picture is the tedious part, so picgen does it.

**In the code.** The planner (`plan_refs`) reads the prompt, scores the character's own views and the shared library by keyword, and fills four slots: identity, pose or activity, expression, and garments. It prefers the character's own pictures over borrowed guides, never uses a face-only crop as the identity, and raises guidance and steps when a pose or face guide is in play. Every slot can be overridden by hand, and **Auto** fills the slots you leave alone.

**Cost accepted.** Keyword matching is predictable but literal. Words that mean several things ("cap", "flats", "watch") need hand-written exceptions, and those exceptions live in the code.

## 4. Show your work before spending GPU time

A two-minute picture that comes back wrong wastes time and trust. So picgen says what it will do first, and records what it did.

- While you type, the **Auto will use →** line shows the references the planner chose. It comes from a dry run (`GET /api/plan`) that costs no GPU time.
- Every finished picture stores the exact prompt sent to the model, the references and their roles, the lessons applied, the guidance and steps actually used, the seed and the time taken.

**Cost accepted.** More metadata per picture, and more for a user to read if they want to.

## 5. Every thumbs-down teaches something

A bad picture is information. picgen asks what went wrong (anatomy, gaze, pose, expression, garment, identity, scene, style, artifact), explains the likely cause, and offers a targeted repair: a changed prompt, a pose guide, different settings or a fresh seed. Fixes that keep working become lessons, which are small rules that adjust future prompts and settings when their keywords appear. Lessons can be reviewed, edited, switched off and deleted.

**Cost accepted.** The loop needs a vision model and some patience. A lesson learned from a few pictures can be wrong, so lessons stay visible and editable rather than hidden.

## 6. Boring, durable technology

picgen is meant to keep working through operating-system upgrades and without a developer on call.

- The server is Python standard library only. There is no `pip install`, no framework and no virtual environment to break.
- The studio is a single HTML file with plain JavaScript and CSS, with no build step and no `node_modules`.
- Storage is files you can open: one PNG and one JSON per picture, a JSON index for the library, and SQLite for feedback. A backup is a copy of the folder.
- Shared files are written under a file lock, by writing a temporary file and renaming it, so two writers cannot corrupt each other's changes.

**Cost accepted.** The server is one large file rather than a package of modules, and there is no ORM, no migrations and no dependency injection. That suits one maintainer and one host. It would need restructuring for a team or a fleet.

## 7. API first: the studio is just one client

Everything the studio page does is an HTTP call that a script can make, with the same JSON. No feature is only reachable through the page. The OpenAPI spec, the interactive explorer and the Python client exist so that automation, such as a weekly newsletter illustration or a batch of reaction pictures, is a first-class use.

**Cost accepted.** Endpoints are shaped by what the studio needs, so a few return more than a script needs.

## 8. Honest limits

Every limit in picgen came from a test, not a guess. Two stitched character references produced one duplicated character and lost the other, so v1.0 allows one character per picture. With four references the last garment could already drop out, so four is the ceiling. Each limit is written in the code next to the setting, and listed in [Limits and roadmap](08-limits-roadmap.md).
