# Frontend architecture

The studio is one file: `static/index.html`, about 2,500 lines of HTML, CSS and plain JavaScript. It has no framework, no bundler, no npm packages and no CDN dependencies. The server reads the file fresh on every request (`Cache-Control: no-store`), so editing it and reloading the browser is the whole deploy.

## Why one file and no build step

- **Nothing to install, nothing to break.** No `node_modules`, no build pipeline, and no framework upgrade is ever forced.
- **The page is a thin client.** All decisions (planning, matching, queueing, feedback) live on the server, behind the same API scripts use. The page only gathers input and shows results.
- **It suits the scale.** One maintainer and five tabs. A component framework would add more machinery than it removes.

The trade-off is a long file that is navigated by section banners rather than by module files. If the studio grows past a handful more screens, splitting it into ES modules served from `static/` (still with no build step) is the natural next step.

## File layout

| Block | Lines (approx.) | Contents |
|---|---|---|
| `<head>` | 1–7 | charset, viewport, `color-scheme: dark`, title |
| `<style>` | 8–383 | Tokens, base, top bar, layout, inputs, buttons, chat, chips, grids, character sheet, pickers, library, overlays, toasts, responsive rules, feedback and learning |
| Markup | 385–716 | Top bar, five `<section class="tab">` blocks, lightbox, edit modal, save-as-character modal, grade modal, toast container |
| `<script>` | 718–2483 | One `'use strict'` IIFE in banner-marked sections: helpers, state, model helpers, tabs, status pill, loaders, chips, job polling, plan line and generate, gallery, characters, library, chat, feedback loop, learning tab, overlays, edit flow, save-as-character, `bind()`, `init()` |

To find a feature, search for its banner, for example `/* ---- library ---- */`.

## State

All state lives in one object, `S`, private to the IIFE:

- **Catalogs** loaded from the server: image models, chat models, characters, gallery, library, options.
- **Selections**: selected characters, `reference` (`auto`, `original`, a view tag, or `lib:<tag>`), `garments` (`auto`, `[]` for none, or a list of tags), filters.
- **Jobs**: whether a Create job is running, and its last status and result.
- **Modals**: edit, save-as-character and grade state.
- **Chat**: the message history.

Nothing is stored in the browser: no localStorage, cookies or service worker. A reload keeps only the current tab, which lives in the URL hash (`#create`, `#characters`, `#gallery`, `#library`, `#learning`).

`S.options` holds fallbacks used only if `/api/options` fails. Its lists are older than the server's (8 library kinds instead of 13), so the server's answer always wins.

## Talking to the server

Every request goes through one helper, `api(path, opts)`:

- A network failure becomes the error *cannot reach the server*.
- JSON responses are parsed. A non-2xx status, or a 2xx body that contains `error`, throws with the server's message.
- `postJSON()` sends JSON; `softList()` returns `[]` on any error (used for lists that may be empty).
- Images load as plain `<img src>` from `/img/`, `/char/` and `/lib/`.

### Polling

| What | How often | Notes |
|---|---|---|
| Status pill (`GET /api/status`) | every 5 s | Always on |
| A running job (`GET /api/job/<id>`) | 1.5 s after each reply | Stops at `done` or `failed`; a 0.5 s local ticker updates the seconds shown |
| Characters while sheets render (`GET /api/characters`) | every 10 s | Only while any character has `sheet_pending`; open forms and scroll positions are kept across the re-render |
| Plan line (`GET /api/plan`) | 350 ms after typing stops | Late answers are discarded with a sequence counter |
| Fix diagnosis (`GET /api/feedback/diagnose`) | 300 ms after typing stops | Same |

The gallery does not poll. It reloads after a picture is made and when you press Refresh.

### Calls by screen

| Screen | Calls |
|---|---|
| Startup | `GET /api/options`, `/api/models`, `/api/image_models`, `/api/characters`, `/api/gallery`, `/api/library`, `/api/status` |
| Create | `GET /api/plan`, `POST /api/compile`, `POST /api/generate`, `GET /api/job/<id>`, `POST /api/chat` |
| Pictures (result and gallery cards) | `DELETE /api/image/<id>`, `POST /api/characters/from_image`, edit via `POST /api/generate` with `edit_of` |
| Characters | `POST /api/characters` (multipart), `DELETE /api/characters/<id>`, `POST /api/characters/<id>/sheet`, `DELETE /api/characters/<id>/sheet/<tag>`, `POST /api/characters/<id>/views` (multipart), `POST /api/characters/<id>/derive` |
| Library | `POST /api/library/views` (multipart), `DELETE /api/library/<tag>` |
| Feedback | `POST /api/feedback`, `GET /api/feedback/diagnose`, `POST /api/feedback/critique`, then a follow-up `POST /api/generate` with `repair_of` or `retry_of`; **Teach pose** uses `GET` and `POST /api/feedback/teach` |
| Learning | `GET /api/feedback/stats`, `PATCH /api/lessons/<id>`, `DELETE /api/lessons/<id>`, `POST /api/lessons`, `POST /api/lessons/learn` |
| Status pill | `DELETE /api/queue` (cancel everything waiting) |

The page does not use `GET /api/queue`, `DELETE /api/queue/<id>`, `DELETE /api/queue/sheet/<cid>`, `GET /api/feedback` or `GET /api/lessons`. They exist for scripts. Field-level detail for every call is in the [API reference](04-api.md).

## Screens

### Create

- **Talk it through** (chat): sends the whole history to `/api/chat` and renders the reply. **Use as prompt** takes the first ```` ```prompt ```` block, else the first fenced block, else the whole reply.
- **Prompt** with **Rewrite for the generator** (`/api/compile`).
- **Plan line**, *Auto will use →*: shown when a character is selected. It renders the pose, face and garment slots, reference count, guidance, steps, an estimated time, notes, lessons and a collapsible *prompt that will be sent*.
- **Character chips**: radio buttons while `max_characters` is 1. Selecting one switches the model to the installed character-mode model and disables text-only models, with a note explaining why.
- **Reference picker**: Auto and Original are pinned first, then the character's own views (generated, uploaded with an amber dot, derived with a green dot), then library *guides* with a dashed border. A filter appears at nine or more tiles.
- **Wearing picker**: Auto, None, then one tile per garment, capped at `max_garments`.
- **Model, style, size, steps, guidance, seed.** Changing the model refills steps and guidance from its defaults and shows its speed, best use and tip.
- **Result panel**: the picture, an optional before/after comparison for fixes, the automatic-check strip, a metadata line and the actions.

### Characters

The add form, then one card per character with its view grid. Generated views come first in sheet order, then uploaded and derived views, then pulsing placeholders for views still rendering. Cards also hold **Build sheet**, **Add views** (multipart upload with optional auto-split) and **Make this character's own versions** (choose kinds, see a time estimate, render).

### Gallery

The newest 60 pictures, each with the same metadata, check strip and actions as the Create result. It includes a lightbox and an edit modal with its own plan line, **Apply**, **Keep** and **Edit again**.

### Library

An upload form (kind, layout, caption and top crop, group size, drawn-with character, labels), kind pills with counts, a filter, and cards. Multi-view composites span two columns.

### Learning

Approval tiles, flaw bar charts (user-reported and automatic), a fix-outcome table, the lessons table with on/off switches, **Learn from my feedback**, **Add my own rule**, recent feedback, and approval by model and by reference setup. It is all drawn from one call to `GET /api/feedback/stats`.

### Grade dialog ("What went wrong?")

An issue checklist pre-ticked from the automatic check, a note, a live diagnosis, an editable proposed fix, and three ways to submit: fix just this, regenerate with lessons, or submit only.

## Design system

The studio is dark only (`color-scheme: dark`). Tokens are on `:root`:

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0f1115` | Page |
| `--card`, `--card2` | `#171a21`, `#1c2029` | Cards, raised controls |
| `--input` | `#0e1117` | Inputs, code boxes |
| `--text`, `--muted` | `#e8e8ea`, `#8b90a0` | Text |
| `--accent`, `--accent2` | `#4f8cff`, `#3d78e6` | Primary actions, focus, selection |
| `--danger`, `--ok`, `--warn` | `#c0392b`, `#2a7a4b`, `#b7791f` | States |
| `--border` | `#262b36` | All 1 px borders |
| `--radius`, `--gutter` | `12px`, `16px` | Cards, spacing |

- **Type:** the system UI font at 15 px / 1.5. Headings are 600 weight.
- **Layout:** content width is capped at 1400 px, under a sticky blurred top bar.
- **Breakpoints:** at 980 px the two-column layouts stack; at 640 px the tabs fill the width and the controls drop to two columns.
- **Components:** cards, chips, pills, kind badges (one colour per library kind), reference tiles, modals, lightbox and toasts (at most four at once; errors stay 6.5 s).

## Accessibility

**In place:**
- `aria-live` regions for chat, plan lines, the diagnosis and toasts.
- Dialogs with `role="dialog"` and `aria-modal`.
- Chips and tiles exposed as radios or checkboxes with `aria-checked`.
- Screen-reader text on emoji buttons.
- Visible focus outlines, and Esc to close overlays.

**Gaps to fix before a public release:**
- No focus trap or focus restore in dialogs.
- Tabs are plain links, not ARIA tabs.
- Radio groups have no arrow-key movement.
- The status pill (queue cancel) is mouse-only.
- Many thumbnails have empty `alt` text.
- Badge text is as small as 7.5 px.
- There is no light theme.

## Known frontend limits

- Steps and guidance inputs accept up to 100 and 30. The server quietly caps them at 60 and 15.
- The gallery shows only the newest 60 pictures and has no paging. Older pictures are still reachable by id.
- Clicking the status pill cancels every waiting job, including sheet jobs.
- Character, sheet and library images are cached for an hour and gallery images for a day, with no cache-busting. A replaced view can show the old picture until a hard refresh.
- Chat history is not saved and replies are not streamed.
- Only the first selected character drives the plan line, reference, wearing, rewrite and chat.
- Some explanatory text predates the newer garment kinds (it names five kinds where the server has ten).

## Changing the studio

1. Edit `static/index.html`.
2. Reload the browser. There is no build step, and the page is served with `no-store`.
3. Keep calls going through `api()` so errors are handled the same way everywhere.
4. Add a server feature as an API endpoint first, then call it from the page. Never put business logic only in the page.
5. Keep the copy in the user's words: say what a control does, and make errors say how to fix the problem.
