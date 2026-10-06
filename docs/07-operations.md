# Operations

How to install, configure, run, back up and troubleshoot picgen.

## Reference deployment

v1.0 was built and tested on one workstation:

| Part | Reference |
|---|---|
| GPU | NVIDIA RTX 3090, 24 GB |
| Host | Ubuntu 26.04, 20 cores, 62 GB RAM |
| Python | 3.12 (standard library only) |
| Image engine | ComfyUI 0.38, in its own virtual environment with Pillow |
| Language models | Ollama 0.34 |
| Service | systemd user unit, listening on `127.0.0.1:8070` |
| Access | A reverse proxy (Caddy) terminates TLS for the studio's hostname, reachable only over a private VPN mesh |

## Requirements

- **GPU:** NVIDIA with 24 GB recommended. Kontext (character and edit) needs about 12 GB for the model plus room for up to four references. Qwen-Image-Edit 2511 at Q6_K takes about 17 GB and ran beside a 9 GB neighbour (Frigate) by letting ComfyUI spill layers to system RAM. Z-Image Turbo and ToonYou run with less.
- **Disk:** about 80 GB for the seven installed models, plus your pictures. The repository ships an empty library; a full one of about 560 reference views is roughly 380 MB. Each picture is 1–2 MB.
- **ComfyUI** recent enough to include the Z-Image loaders (0.38 or later), installed with a virtual environment at `COMFY_DIR/venv` that includes Pillow.
- **Ollama** with a chat model for the prompt helper, and a vision model for the automatic check (see [Privacy](#privacy)).
- **Python 3.12 or later.** Nothing to `pip install`.

### Model files

All paths are relative to `COMFY_DIR/models`.

| Model | Files | Size |
|---|---|---|
| Z-Image Turbo | `diffusion_models/z_image_turbo_bf16.safetensors`, `text_encoders/qwen_3_4b_fp8_mixed.safetensors`, `vae/ae.safetensors` | 17.6 GB |
| FLUX.1 Kontext (character and edit) | `diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors`, `text_encoders/clip_l.safetensors`, `text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors`, `vae/ae.safetensors` | 17.5 GB |
| Qwen-Image-Edit 2511 (character and edit) | `unet/qwen-image-edit-2511-Q6_K.gguf` (unsloth/Qwen-Image-Edit-2511-GGUF), `loras/Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors` (lightx2v), `text_encoders/qwen/qwen_2.5_vl_7b_fp8_scaled.safetensors`, `vae/qwen_image_vae.safetensors` (Comfy-Org/Qwen-Image_ComfyUI). Needs the custom node `city96/ComfyUI-GGUF` in `COMFY_DIR/custom_nodes` and `pip install gguf` in the ComfyUI venv. | 27.5 GB |
| FLUX.1-dev | `checkpoints/flux1-dev-fp8.safetensors` | 17 GB |
| ToonYou | `checkpoints/toonyou_beta6.safetensors` | 2.2 GB |

`GET /api/image_models` reports each model's `install` hint and whether it is `installed`. A missing model shows as "not installed" in the studio, and the API refuses jobs for it with a clear error.

### Character LoRAs

A recurring character can be trained into a LoRA for Z-Image Turbo and then drawn at about 10 s a picture with no reference images (`loras` on `POST /api/generate`). The recipe that passed its audit on 2026-10-05 (City of Faraway, "Death"):

1. **Reference set:** one approved master picture, then 24 views of it through `qwen-image` (new poses, props, scenes, a back view, a close-up), keeping only the ones that are true to the design. Add the master itself.
2. **Captions:** one `.txt` per picture: the trigger word, then only what *varies* (scene, pose, prop, background colour, banner text). Do not describe the character's fixed features: naming them binds them to those words instead of to the trigger, and a prompt without them then draws none of them (that cost two aborted runs).
3. **Trainer:** [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) in its own venv (`pip install torch torchvision torchaudio` first; `torchaudio` is needed but not listed). Model arch **`zimage:deturbo`** (`ostris/Z-Image-De-Turbo` with `Tongyi-MAI/Z-Image-Turbo` as `extras_name_or_path`). Training Turbo directly (`arch: zimage`) learned nothing. Rank 16, lr 1e-4, 1500 steps, `cache_text_embeddings: false` (trigger words need live captions), `quantize`/`low_vram` on. About 1 h 50 min on a 3090 that also runs Frigate (~22 GB used); stop picgen jobs while it runs.
4. **Judge late:** samples at steps 250 and 500 look like the base model; the identity arrives between 500 and 1000. Judge at 750+.
5. **Audit:** 10 scenes that are not in the training set × 3 seeds on Turbo at 8 steps; pass when ≥ 90 % read as the character without a reroll. Copy the final `.safetensors` into `COMFY_DIR/models/loras`.

Lettering the model draws itself comes out misspelled with a character LoRA loaded (the trigger word leaks into rendered text); overlay titles and banners afterwards.

## Install

1. **Install ComfyUI** and its virtual environment, and download the model files above.
2. **Install Ollama**, then pull a chat model (`ollama pull <model>`) and, for a fully local install, a vision model for the automatic check.
3. **Copy the picgen folder** to the host, for example `~/picgen`.
4. **Build the docs** (optional; needs `pip install markdown` on the build machine only): `python3 tools/build_docs.py`.
5. **Create the service.** A user unit at `~/.config/systemd/user/picgen.service`:

   ```ini
   [Unit]
   Description=picgen studio (image models on the local GPU)
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=%h/picgen
   Environment=PICGEN_HOST=127.0.0.1 PICGEN_PORT=8070 IDLE_MINUTES=5
   Environment=CHAT_MODEL=qwen2.5:7b CRITIC_MODEL=<your vision model>
   ExecStart=/usr/bin/python3 %h/picgen/app.py
   Restart=on-failure
   RestartSec=3

   [Install]
   WantedBy=default.target
   ```

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now picgen
   loginctl enable-linger $USER      # keep it running when nobody is logged in
   ```

6. **Publish it through a reverse proxy.** Caddy, for example:

   ```
   pics.example.com {
       reverse_proxy 127.0.0.1:8070
   }
   ```

   Keep the hostname on a private network (VPN, mesh or LAN-only DNS), or add authentication at the proxy. See [Security](#security).

7. **Check it:** `curl -s http://127.0.0.1:8070/api/status`, then open the studio, `/docs` and `/api/docs`.

## Configuration

All settings are environment variables on the service.

| Variable | Default | What it does |
|---|---|---|
| `PICGEN_HOST` | `127.0.0.1` | Address to listen on. Keep it local and use a proxy. |
| `PICGEN_PORT` | `8070` | Port to listen on |
| `COMFY_DIR` | `~/ComfyUI` | ComfyUI install (models, venv, input, output) |
| `COMFY_URL` | `http://127.0.0.1:8188` | ComfyUI API. Start and stop always use port 8188 on this host. |
| `IDLE_MINUTES` | `5` | Stop ComfyUI after this many minutes without a job |
| `RESERVE_VRAM` | `3` | GB of GPU memory ComfyUI leaves free for the desktop and other software |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API |
| `CHAT_MODEL` | `qwen35-9b-uncensored:Q4_K_M` | Default model for the prompt helper and Rewrite |
| `CHAT_KEEP` | `10m` | How long Ollama keeps the chat model loaded |
| `DEFAULT_IMAGE_MODEL` | `z-image-turbo` | Default image model, if installed |
| `MAX_CHARACTERS` | `1` | Characters per picture. Two was tested and failed; leave at 1. |
| `MAX_GARMENTS` | `2` | Garments per picture |
| `MAX_REFS` | `4` (2–4) | Reference pictures per character picture. Each extra one adds about 55 s. |
| `CRITIC_AUTO` | `1` | `0` turns off the automatic check |
| `CRITIC_MODEL` | `gemma4:31b-cloud` | Vision model for the automatic check |
| `LEARNER_MODEL` | same as `CRITIC_MODEL` | Model that proposes lessons |
| `OUTPUT_DIR` | `./output` | Gallery pictures and records |
| `CHAR_DIR` | `./characters` | Characters |
| `LIBRARY_DIR` | `./library` | Shared library |

The default `CHAT_MODEL` is an uncensored community model. Set it to a model that fits your content policy.

## Running it

| Task | Command |
|---|---|
| Status | `systemctl --user status picgen` |
| Restart | `systemctl --user restart picgen` (see below) |
| Server log | `journalctl --user -u picgen -f` |
| Image engine log | `tail -f ~/picgen/comfy.log` |
| Health | `curl -s http://127.0.0.1:8070/api/status` |
| What is queued | `curl -s http://127.0.0.1:8070/api/queue` |
| GPU use | `nvidia-smi` |

**Restarting safely.** The queue is saved to disk, so a restart never loses waiting work. A picture that is *drawing* during a restart starts again from the beginning. To avoid wasting that time, restart when `/api/status` shows `"current": null`. If several people or scripts use the server, tell them first.

**Updating.** Replace `app.py`, `feedback.py` and `static/`, then restart. Changes to `static/index.html` alone need no restart; the page is read fresh on every load. After changing an endpoint, update `tools/openapi_spec.py` and run `python3 tools/build_docs.py`.

## Backup and restore

Back up the picgen folder. These parts matter:

| Path | Contents | If lost |
|---|---|---|
| `characters/` | Characters, their sheets and views | Characters must be recreated and their sheets rebuilt |
| `library/` | The shared library and its index | Library must be re-ingested |
| `output/` | Gallery pictures and their records | Pictures are gone |
| `data/feedback.db` | Grades, critiques, outcomes, lessons | Learning starts over |
| `data/queue.json` | Waiting jobs | Waiting jobs are lost |

Use a tool that copies SQLite safely (stop the service first, or use `sqlite3 data/feedback.db ".backup ..."`). Files under `COMFY_DIR/input/picgen-*` are rebuilt automatically and need no backup. The models are re-downloadable and are usually excluded.

**Restore:** stop the service, copy the folders back, start the service.

## Security

- **No logins in v1.0.** Anyone who can reach the port can use every endpoint, including deletes. That is acceptable only when access is limited by the network.
- **Bind to localhost** (`PICGEN_HOST=127.0.0.1`, the default) and publish through a proxy.
- **Limit who can reach the proxy:** a VPN or mesh network, LAN-only DNS, or authentication at the proxy (basic auth, single sign-on through a forward-auth service, or client certificates).
- **Uploads** are size-limited (40, 120 and 200 MB) and converted to PNG. Identifiers from clients are stripped to bare names, and destructive routes check ids against strict patterns.
- **Deletes are permanent.** There is no trash. Back up regularly.

### Privacy

Pictures are always drawn on the host. By design, the review work goes to the cloud, because a vision model large enough to judge pictures does not fit on a 24 GB card beside the image models.

- **Review jobs.** With the default `CRITIC_MODEL=gemma4:31b-cloud`, five jobs are sent through the local Ollama to Ollama Cloud: the automatic check of character and edit pictures, reading a character's permanent features, describing what a repair must keep, working out the action a guided repair should show, and proposing lessons. They send the picture, the character's reference and the request. For a fully offline install, set `CRITIC_MODEL` and `LEARNER_MODEL` to a local vision model (on a bigger GPU or a second machine), or set `CRITIC_AUTO=0` to turn the automatic check off.
- **Chat model.** If `CHAT_MODEL` names an Ollama cloud model, prompts go to that service. Local models keep them on the host.

`GET /api/status` shows the critic model in use.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Status pill says **offline** | The service is down | `systemctl --user status picgen`; read the journal |
| First picture takes about a minute | ComfyUI was asleep and is loading | Normal after `IDLE_MINUTES` without work |
| Job fails: *ComfyUI did not start; see comfy.log* | Another process holds port 8188, or there is not enough GPU memory | `ss -ltnp \| grep 8188`; `nvidia-smi`; read `comfy.log` |
| Job fails: *… is not installed* | Model files missing | Compare with `install` in `/api/image_models` |
| Job fails: *ComfyUI rejected the workflow* | ComfyUI too old, or a custom node is missing | Update ComfyUI; the error text names the node |
| Chat replies are slow | The image engine was still loaded, or the chat model spilled onto the CPU | picgen stops an idle engine before chat. If a job is queued, chat waits for the GPU. |
| Check strip says *automatic check unavailable* | The critic model cannot be reached (a cloud model needs internet and an Ollama sign-in) | Point `CRITIC_MODEL` at a reachable model, or set `CRITIC_AUTO=0` |
| A replaced view still shows the old picture | Browser cache (character and library images are cached for an hour) | Hard refresh |
| `/docs` answers 404 | Docs not built | `python3 tools/build_docs.py` |
| Disk keeps growing | Pictures, ComfyUI's own copies, `comfy.log` | Delete pictures through the API (it removes both copies); rotate `comfy.log` |
