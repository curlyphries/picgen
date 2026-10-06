#!/usr/bin/env python3
"""picgen_client — a small client for the picgen HTTP API (stdlib only).

As a command:
    picgen_client.py status
    picgen_client.py generate "a red fox asleep in fresh snow" --wait --out fox.png
    picgen_client.py generate "she waves from a bicycle" --character c1791012478462 --wait
    picgen_client.py edit 1791049868-8818 "make it night, keep everything else the same" --wait
    picgen_client.py plan c1791012478462 "runs away scared in a hoodie"
    picgen_client.py characters | gallery | queue | models | library [--kind footwear]
    picgen_client.py download <image id> [--out file.png]
    picgen_client.py delete <image id>
    picgen_client.py cancel [<job id>]

As a module:
    from picgen_client import Picgen
    pg = Picgen()                                   # PICGEN_URL or http://127.0.0.1:8070
    job = pg.generate("a lighthouse at dusk", size="wide", wait=True)
    pg.download(job["id"], "lighthouse.png")

Every call returns the decoded JSON the server sent. Errors raise PicgenError
with the server's {"error": "..."} message.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = os.environ.get("PICGEN_URL", "http://127.0.0.1:8070")
DONE = ("done", "failed")


class PicgenError(RuntimeError):
    pass


class Picgen:
    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 60):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    # ---------------------------------------------------------------- plumbing
    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None, raw: bool = False):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                msg = json.loads(payload).get("error") or payload.decode()[:200]
            except Exception:
                msg = payload.decode(errors="replace")[:200] or e.reason
            raise PicgenError(f"{method} {path}: HTTP {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise PicgenError(f"cannot reach picgen at {self.base}: {e.reason}") from None
        return payload if raw else json.loads(payload or b"null")

    # ---------------------------------------------------------------- read
    def status(self):
        return self._call("GET", "/api/status")

    def options(self):
        return self._call("GET", "/api/options")

    def image_models(self):
        return self._call("GET", "/api/image_models")

    def characters(self):
        return self._call("GET", "/api/characters")

    def library(self, kind: str | None = None):
        lib = self._call("GET", "/api/library")
        if kind:
            lib["items"] = [i for i in lib["items"] if i.get("kind") == kind]
        return lib

    def gallery(self):
        return self._call("GET", "/api/gallery")

    def queue(self):
        return self._call("GET", "/api/queue")

    def job(self, job_id: str):
        try:
            return self._call("GET", f"/api/job/{job_id}")
        except PicgenError as e:
            if "HTTP 404" in str(e):
                return {"id": job_id, "status": "unknown"}
            raise

    def plan(self, character: str, prompt: str, model: str = "flux-kontext", **extra):
        return self._call("GET", "/api/plan", query={"prompt": prompt, "character": character, "model": model, **extra})

    # ---------------------------------------------------------------- write
    def generate(self, prompt: str, *, model: str | None = None, size: str = "square", style: str = "none",
                 steps: int | None = None, guidance: float | None = None, seed: int | None = None,
                 character: str | None = None, reference: str = "auto", garments="auto",
                 edit_of: str | None = None, wait: bool = False, poll: float = 2.0, max_wait: float = 1800, on_status=None):
        """Queue a picture. With wait=True, block until it is done (or failed) and return the finished job."""
        if character and not model:
            model = "flux-kontext"
        if edit_of and not model:
            model = "flux-kontext-edit"
        body = {"prompt": prompt, "model": model, "size": size, "style": style, "steps": steps, "guidance": guidance,
                "seed": seed, "reference": reference, "garments": garments, "edit_of": edit_of,
                "characters": [character] if character else []}
        job = self._call("POST", "/api/generate", {k: v for k, v in body.items() if v is not None})
        return self.wait(job["id"], poll, max_wait, on_status) if wait else job

    def wait(self, job_id: str, poll: float = 2.0, max_wait: float = 1800, on_status=None):
        t0, last = time.time(), None
        while True:
            j = self.job(job_id)
            if on_status and j.get("status") != last:
                on_status(j)
            last = j.get("status")
            if last in DONE:
                if last == "failed":
                    raise PicgenError(f"job {job_id} failed: {j.get('error') or 'no error given'}")
                return j
            if time.time() - t0 > max_wait:
                raise PicgenError(f"job {job_id} still '{last}' after {max_wait:.0f} s")
            time.sleep(poll)

    def download(self, image_id: str, path: str | None = None) -> str:
        path = path or f"{image_id}.png"
        with open(path, "wb") as f:
            f.write(self._call("GET", f"/img/{image_id}.png", raw=True))
        return path

    def delete(self, image_id: str):
        return self._call("DELETE", f"/api/image/{image_id}")

    def cancel(self, job_id: str | None = None):
        return self._call("DELETE", f"/api/queue/{job_id}" if job_id else "/api/queue")


# -------------------------------------------------------------------- command line
def _print(obj):
    print(json.dumps(obj, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Talk to a picgen server.", epilog=f"Server: --url or PICGEN_URL (now {DEFAULT_URL})")
    ap.add_argument("--url", default=DEFAULT_URL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "options", "models", "characters", "gallery", "queue"):
        sub.add_parser(name)
    lib = sub.add_parser("library"); lib.add_argument("--kind")
    j = sub.add_parser("job"); j.add_argument("job_id")
    pl = sub.add_parser("plan", help="dry run: which references Auto would use (no GPU)")
    pl.add_argument("character"); pl.add_argument("prompt")

    def gen_args(p):
        p.add_argument("--model"); p.add_argument("--size", default="square"); p.add_argument("--style", default="none")
        p.add_argument("--steps", type=int); p.add_argument("--guidance", type=float); p.add_argument("--seed", type=int)
        p.add_argument("--wait", action="store_true", help="block until the picture is done")
        p.add_argument("--out", help="with --wait: save the PNG here")

    g = sub.add_parser("generate", help="queue a picture"); g.add_argument("prompt")
    g.add_argument("--character", help="character id (uses FLUX Kontext)")
    g.add_argument("--reference", default="auto", help="auto | original | <view tag> | lib:<tag>")
    g.add_argument("--garments", default="auto", help="auto | none | comma-separated library tags")
    gen_args(g)
    e = sub.add_parser("edit", help="change an existing picture"); e.add_argument("image_id"); e.add_argument("prompt")
    gen_args(e)
    d = sub.add_parser("download"); d.add_argument("image_id"); d.add_argument("--out")
    x = sub.add_parser("delete"); x.add_argument("image_id")
    c = sub.add_parser("cancel"); c.add_argument("job_id", nargs="?")
    a = ap.parse_args(argv)
    pg = Picgen(a.url)
    say = lambda j: print(f"  {j.get('id')}: {j.get('status')}", file=sys.stderr)
    try:
        if a.cmd == "models":
            _print(pg.image_models())
        elif a.cmd in ("status", "options", "characters", "gallery", "queue"):
            _print(getattr(pg, a.cmd)())
        elif a.cmd == "library":
            _print(pg.library(a.kind))
        elif a.cmd == "job":
            _print(pg.job(a.job_id))
        elif a.cmd == "plan":
            _print(pg.plan(a.character, a.prompt))
        elif a.cmd in ("generate", "edit"):
            garments = a.garments if a.cmd == "generate" else "auto"
            if isinstance(garments, str) and garments not in ("auto", "none"):
                garments = [t.strip() for t in garments.split(",") if t.strip()]
            elif garments == "none":
                garments = []
            job = pg.generate(a.prompt, model=a.model, size=a.size, style=a.style, steps=a.steps, guidance=a.guidance,
                              seed=a.seed, character=getattr(a, "character", None), reference=getattr(a, "reference", "auto"),
                              garments=garments, edit_of=getattr(a, "image_id", None), wait=a.wait, on_status=say)
            if a.wait and a.out:
                job["saved_to"] = pg.download(job["id"], a.out)
            _print(job)
        elif a.cmd == "download":
            print(pg.download(a.image_id, a.out))
        elif a.cmd == "delete":
            _print(pg.delete(a.image_id))
        elif a.cmd == "cancel":
            _print(pg.cancel(a.job_id))
    except PicgenError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
