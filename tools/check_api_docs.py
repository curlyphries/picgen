#!/usr/bin/env python3
"""Fail if the routes in app.py and the paths in tools/openapi_spec.py disagree.

Static check, no server needed. It reads every path literal the HTTP handler in app.py
dispatches on, turns prefix matches into the spec's `{param}` form, and compares the
two sets. Run it after adding or renaming an endpoint, before `build_docs.py`.
"""
import importlib.util
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
APP = (HERE.parent / "app.py").read_text()

# Routes the handler matches as prefixes, and the spec path they correspond to.
PREFIXES = {
    "/api/job/": ["/api/job/{job_id}"],
    "/api/image/": ["/api/image/{image_id}"],
    "/api/queue/": ["/api/queue/{job_id}", "/api/queue/sheet/{character_id}"],
    "/api/lessons/": ["/api/lessons/{lesson_id}"],
    "/api/library/": ["/api/library/{tag}"],
    "/api/characters/": ["/api/characters/{character_id}", "/api/characters/{character_id}/sheet",
                         "/api/characters/{character_id}/sheet/{tag}", "/api/characters/{character_id}/views",
                         "/api/characters/{character_id}/derive"],
    "/img/": ["/img/{file}"],
    "/lib/": ["/lib/{file}"],
    "/char/": ["/char/{file}", "/char/{character_id}/sheet/{file}"],
}
NOT_API = {"/", "/docs", "/docs/", "/api/docs", "/api/"}  # HTML pages and the 404 prefix test, not part of the contract


def app_paths() -> set:
    handler = APP[APP.index("class H("):]
    found = set()
    for m in re.finditer(r'(?:p|path)\s*(?:==|!=|in \(|\.startswith\()\s*"(/[^"]*)"', handler):
        found.add(m.group(1))
    for m in re.finditer(r'(?:p|path)\s+in\s+\(([^)]*)\)', handler):
        found.update(re.findall(r'"(/[^"]*)"', m.group(1)))
    paths = set()
    for f in found:
        if f in NOT_API:
            continue
        paths.update(PREFIXES.get(f, [f]))
    return paths


def spec_paths() -> set:
    spec = importlib.util.spec_from_file_location("openapi_spec", HERE / "openapi_spec.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.build()["paths"])


def main() -> int:
    a, s = app_paths(), spec_paths()
    missing_in_spec, missing_in_app = sorted(a - s), sorted(s - a)
    for p in missing_in_spec:
        print(f"route in app.py but not in openapi_spec.py: {p}")
    for p in missing_in_app:
        print(f"path in openapi_spec.py but no route in app.py: {p}")
    if missing_in_spec or missing_in_app:
        return 1
    print(f"ok: {len(a)} paths agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
