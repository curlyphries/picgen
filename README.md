# picgen studio

A private image studio that runs on your own GPU: text to image in seconds, recurring characters that keep their likeness, a reference planner that picks poses, faces and outfits from one sentence, editing, and a feedback loop that learns. One web page and one HTTP API, Python standard library only.

## Requirements

- Linux host with an NVIDIA GPU (24 GB recommended; see [Operations](docs/07-operations.md) for what each model needs)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) 0.38 or later with the model files listed in [Operations](docs/07-operations.md#model-files)
- [Ollama](https://ollama.com) with a chat model, plus a vision model if you want the automatic picture check to run locally
- Python 3.12 or later. The app has no third-party dependencies.

## Install

```bash
git clone https://github.com/curlyphries/picgen.git ~/picgen
cd ~/picgen
PICGEN_PORT=8070 COMFY_DIR=~/ComfyUI python3 app.py      # try it in the foreground first
```

For a service that starts at login, copy [picgen.service.example](picgen.service.example) to `~/.config/systemd/user/picgen.service`, edit the paths and model names, then `systemctl --user enable --now picgen`. Every setting is an environment variable; `app.py` lists them near the top and [Operations](docs/07-operations.md) explains each one.

The repository ships an empty `library/`, `characters/`, `output/` and `data/`. Your reference views, characters, pictures and feedback database are created there as you use the studio and are ignored by git.

## Start here

| | |
|---|---|
| Studio | `http://127.0.0.1:8070/` (or your proxy address) |
| Documentation | `/docs`, or `docs/dist/picgen-docs.html`, built from `docs/*.md` |
| API explorer | `/api/docs` |
| API spec | `/api/openapi.json` (OpenAPI 3.1) |
| API client | `tools/picgen_client.py --help` |

```bash
tools/picgen_client.py status
tools/picgen_client.py generate "A red fox asleep in fresh snow at dawn" --size landscape --wait --out fox.png
```

## Documentation

1. [Overview](docs/01-overview.md): what it is, what version 1.0 includes, models and licenses
2. [Philosophy](docs/02-philosophy.md): why it exists, what runs locally and what runs in the cloud, and the trade-offs
3. [User guide](docs/03-user-guide.md)
4. [API guide and reference](docs/04-api.md)
5. [Backend architecture](docs/05-backend.md)
6. [Frontend architecture](docs/06-frontend.md)
7. [Operations](docs/07-operations.md): install, configure, run, back up, troubleshoot
8. [Limits and roadmap](docs/08-limits-roadmap.md)

## Rebuilding the docs

```bash
python3 tools/build_docs.py      # regenerates docs/openapi.json from tools/openapi_spec.py, then docs/dist/
```

This needs the `markdown` package on the build machine only. When you add or change an API route in `app.py`, update `tools/openapi_spec.py` in the same change.

## License

MIT, see [LICENSE](LICENSE). The image models have their own licenses: Qwen-Image-Edit is Apache 2.0, while FLUX.1 Kontext [dev] and FLUX.1-dev are non-commercial without a license from Black Forest Labs. The studio shows each model's license next to its name.
