#!/usr/bin/env python3
"""Build the picgen documentation package: docs/*.md + docs/openapi.json -> one HTML page.

    python3 tools/build_docs.py            # writes docs/dist/picgen-docs.html (served at /docs)
                                           # and docs/dist/picgen-docs.artifact.html (body-only copy for hosting)

Build-time only: needs the `markdown` package (pip install markdown). picgen itself
stays stdlib-only at runtime; it just serves the built file.
"""

import html
import json
import re
import sys
import time
from pathlib import Path

try:
    import markdown
except ImportError:
    sys.exit("build_docs needs the markdown package: pip install markdown")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DIST = DOCS / "dist"
VERSION = "1.0"

METHOD_ORDER = {"get": 0, "post": 1, "patch": 2, "delete": 3}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render_md(src: str, prefix: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Markdown -> HTML with prefixed heading ids; returns (html, [(level, id, text)])."""
    src = re.sub(r"```mermaid\n(.*?)```", lambda m: f'<pre class="mermaid">{html.escape(m.group(1))}</pre>', src, flags=re.S)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list", "toc"],
                           extension_configs={"toc": {"slugify": lambda t, sep: f"{prefix}-{slug(t)}", "toc_depth": "2-3"}})
    body = md.convert(src)
    heads = []

    def walk(tokens):
        for t in tokens:
            heads.append((t["level"], t["id"], html.unescape(t["name"])))
            walk(t["children"])
    walk(md.toc_tokens)
    body = re.sub(r"<table>", '<div class="tbl"><table>', body).replace("</table>", "</table></div>")
    # links inside one document, written as (#anchor), get the same prefix as its heading ids
    body = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{prefix}-{m.group(1)}"', body)
    # cross-document links written as (05-backend.md#anchor) or (05-backend.md) become in-page anchors
    body = re.sub(r'href="(\d\d-[a-z-]+)\.md(?:#([^"]*))?"',
                  lambda m: f'href="#{m.group(1)[3:]}-{m.group(2)}"' if m.group(2) else f'href="#{m.group(1)[3:]}"', body)
    return body, heads


def schema_rows(schema: dict, spec: dict, depth: int = 0) -> list[str]:
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    rows = []
    req = set(schema.get("required", []))
    for name, prop in (schema.get("properties") or {}).items():
        if "$ref" in prop:
            prop = spec["components"]["schemas"][prop["$ref"].rsplit("/", 1)[1]] | {"description": prop.get("description", "")}
        typ = prop.get("type", "")
        if isinstance(typ, list):
            typ = " | ".join(typ)
        if typ == "array" and isinstance(prop.get("items"), dict):
            it = prop["items"]
            typ = f"array of {it.get('type') or it.get('$ref', '').rsplit('/', 1)[-1] or 'object'}"
        if "enum" in prop:
            typ += " · " + " / ".join(f"<code>{html.escape(str(e))}</code>" for e in prop["enum"])
        dflt = f' Default <code>{html.escape(json.dumps(prop["default"]))}</code>.' if "default" in prop else ""
        rows.append(f'<tr><td><code>{"&nbsp;" * 4 * depth}{html.escape(name)}</code>{"<span class=req>required</span>" if name in req else ""}</td>'
                    f'<td class="typ">{typ}</td><td>{md_inline(prop.get("description", ""))}{dflt}</td></tr>')
    return rows


def md_inline(text: str) -> str:
    out = markdown.markdown(text or "")
    return re.sub(r"^<p>(.*)</p>$", r"\1", out, flags=re.S)


def render_api(spec: dict) -> tuple[str, list[tuple[int, str, str]]]:
    heads, parts = [], []
    tag_desc = {t["name"]: t.get("description", "") for t in spec.get("tags", [])}
    groups: dict[str, list] = {t: [] for t in tag_desc}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method in METHOD_ORDER:
                groups.setdefault((op.get("tags") or ["Other"])[0], []).append((path, method, op))
    for tag, ops in groups.items():
        if not ops:
            continue
        tid = f"ref-{slug(tag)}"
        heads.append((3, tid, tag))
        parts.append(f'<h3 id="{tid}">{html.escape(tag)}</h3><p class="lede">{md_inline(tag_desc.get(tag, ""))}</p>')
        for path, method, op in ops:
            oid = f"op-{op.get('operationId') or slug(method + path)}"
            block = [f'<section class="op" id="{oid}"><div class="op-head"><span class="verb {method}">{method.upper()}</span>'
                     f'<code class="path">{html.escape(path)}</code><a class="anchor" href="#{oid}" aria-label="Link to this endpoint">#</a></div>'
                     f'<p class="op-sum">{html.escape(op.get("summary", ""))}</p>']
            if op.get("description"):
                block.append(f'<div class="op-desc">{markdown.markdown(op["description"], extensions=["tables", "fenced_code"])}</div>')
            params = op.get("parameters") or []
            if params:
                rows = []
                for p in params:
                    if "$ref" in p:
                        p = spec["components"]["parameters"][p["$ref"].rsplit("/", 1)[1]]
                    s = p.get("schema", {})
                    typ = s.get("type", "") + (" · " + " / ".join(f"<code>{e}</code>" for e in s["enum"]) if "enum" in s else "")
                    rows.append(f'<tr><td><code>{p["name"]}</code>{"<span class=req>required</span>" if p.get("required") else ""}</td>'
                                f'<td class="typ">{p["in"]} · {typ}</td><td>{md_inline(p.get("description", ""))}</td></tr>')
                block.append('<p class="lbl">Parameters</p><div class="tbl"><table><thead><tr><th>Name</th><th>In · type</th><th>Meaning</th></tr></thead><tbody>'
                             + "".join(rows) + "</tbody></table></div>")
            rb = op.get("requestBody")
            if rb:
                for ctype, media in rb.get("content", {}).items():
                    rows = schema_rows(media.get("schema", {}), spec)
                    block.append(f'<p class="lbl">Request body <span class="ctype">{ctype}</span></p>')
                    if rows:
                        block.append('<div class="tbl"><table><thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead><tbody>'
                                     + "".join(rows) + "</tbody></table></div>")
                    if "example" in media:
                        block.append(f'<pre class="ex"><code>{html.escape(json.dumps(media["example"], indent=2))}</code></pre>')
            for code, resp in (op.get("responses") or {}).items():
                if "$ref" in resp:
                    resp = spec["components"]["responses"][resp["$ref"].rsplit("/", 1)[1]]
                block.append(f'<p class="lbl">Response <span class="code c{str(code)[0]}">{code}</span> {md_inline(resp.get("description", ""))}</p>')
                for ctype, media in (resp.get("content") or {}).items():
                    ex = media.get("example")
                    if ex is not None and ctype == "application/json":
                        txt = json.dumps(ex, indent=2)
                        block.append(f'<pre class="ex"><code>{html.escape(txt)}</code></pre>')
            block.append("</section>")
            parts.append("".join(block))
    return "".join(parts), heads


def build():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import openapi_spec                       # the spec source; regenerate docs/openapi.json first
    openapi_spec.write(DOCS / "openapi.json")
    pages = sorted(p for p in DOCS.glob("[0-9][0-9]-*.md"))
    spec = json.loads((DOCS / "openapi.json").read_text())
    sections, nav = [], []
    for p in pages:
        key = p.stem[3:]
        body, heads = render_md(p.read_text(), key)
        title = next((t for lvl, _, t in heads if lvl == 1), None)
        if title is None:
            m = re.search(r"^# (.+)$", p.read_text(), re.M)
            title = m.group(1) if m else key
        if key == "api":
            ref_html, ref_heads = render_api(spec)
            body = body.replace("<p>{{API_REFERENCE}}</p>", f'<div class="api-ref">{ref_html}</div>')
            heads = heads + ref_heads
        body = re.sub(r"<h1[^>]*>", f'<h1 id="{key}">', body, count=1)
        sections.append(f'<section class="doc" id="sec-{key}" data-key="{key}">{body}</section>')
        subs = "".join(f'<li><a href="#{hid}">{html.escape(t)}</a></li>' for lvl, hid, t in heads if lvl == 2)
        nav.append(f'<li><a class="top" href="#{key}">{html.escape(title)}</a><ul>{subs}</ul></li>')
    tpl = (DOCS / "template.html").read_text()
    page = (tpl.replace("{{NAV}}", "".join(nav)).replace("{{CONTENT}}", "".join(sections))
               .replace("{{VERSION}}", VERSION).replace("{{BUILT}}", time.strftime("%Y-%m-%d"))
               .replace("{{ENDPOINTS}}", str(sum(1 for ops in spec["paths"].values() for m in ops if m in METHOD_ORDER))))
    DIST.mkdir(exist_ok=True)
    head, body = page.split("<!--BODY-->", 1)
    served = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
              "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
              + head + "</head><body>" + body
              + '<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>'
              + "<script>mermaid.initialize({startOnLoad:true,theme:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'neutral'});</script>"
              + "</body></html>\n")
    (DIST / "picgen-docs.html").write_text(served)
    (DIST / "picgen-docs.artifact.html").write_text(head + body)   # artifact hosts add the skeleton and render mermaid themselves
    print(f"built {len(pages)} pages + {sum(1 for ops in spec['paths'].values() for m in ops if m in METHOD_ORDER)} endpoints -> {DIST}")


if __name__ == "__main__":
    build()
