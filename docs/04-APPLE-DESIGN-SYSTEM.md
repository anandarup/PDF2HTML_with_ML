# Apple Design System — PDF2HTML Editor

**Primary file:** `python_app/templates/document.html` (document + editor + reader)
**Upload page:** `python_app/web_templates/index.html`
**Date:** 2026-09-09

> This documents the Apple-inspired visual system already implemented in the document template's CSS. It reflects the code as-is (the "Apple-style editor toolbar redesign" per the git history). No styles were changed.

---

## 1. Design Principles (as expressed in the code)

The editor UI follows Apple's macOS/iOS toolbar language:

- **Frosted glass** surfaces (`backdrop-filter: blur() saturate()`) that let content show through.
- **Soft, layered shadows** rather than hard borders.
- **Pill-shaped controls** and rounded rectangles.
- **Spring-like easing** using `cubic-bezier(.22, 1, .36, 1)`.
- **Segmented controls** with a sliding thumb.
- **System fonts** (`-apple-system`) for native feel.
- **Focus-visible rings** for keyboard accessibility.
- **Graceful degradation** via `@supports` fallbacks.

---

## 2. Design Tokens (CSS Custom Properties)

Defined in `:root` and overridden for dark mode under `@media (prefers-color-scheme: dark)`.

### Color

| Token | Light | Dark |
|---|---|---|
| `--color-bg` | `#ffffff` | `#1a1a2e` |
| `--color-text` | `#1a1a2e` | `#e0e0e0` |
| `--color-text-secondary` | `#4a4a6a` | `#a0a0b0` |
| `--color-heading` | `#16213e` | `#bbe1fa` |
| `--color-link` | `#0f4c75` | `#3282b8` |
| `--color-link-hover` | `#3282b8` | `#bbe1fa` |
| `--color-border` | `#e0e0e0` | `#2a2a4a` |
| `--color-code-bg` | `#f5f5f7` | `#16213e` |
| `--color-toc-bg` | `#fafbfc` | `#16213e` |
| `--color-toc-active` | `#0f4c75` | `#bbe1fa` |

### Typography

| Token | Value |
|---|---|
| `--font-body` | `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` |
| `--font-heading` | `"Georgia", "Times New Roman", serif` |
| `--font-mono` | `"SF Mono", "Fira Code", "Fira Mono", Menlo, Consolas, monospace` |

Body text uses `-webkit-font-smoothing: antialiased` and a base `line-height: 1.7`.

### Layout

| Token | Value |
|---|---|
| `--max-width` | `780px` |
| `--sidebar-width` | `260px` |

### Editor "rail" surface tints

Defined on `.edit-toolbar`, with `color-mix` upgrades where supported:

| Token | Fallback | Enhanced (`color-mix`) |
|---|---|---|
| `--rail-hover` | `rgba(125,125,135,0.13)` | `color-mix(in srgb, var(--color-text) 8%, transparent)` |
| `--rail-press` | `rgba(125,125,135,0.22)` | `color-mix(… 15% …)` |
| `--rail-tint` | `rgba(50,130,184,0.18)` | `color-mix(in srgb, var(--color-link) 18%, transparent)` |
| `--rail-line` | `rgba(125,125,135,0.22)` | `color-mix(… 13% …)` |

---

## 3. The Edit Toolbar (`.edit-toolbar`) — Frosted Rail

A fixed top rail that slides in when editing begins.

```css
.edit-toolbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
  display: flex; align-items: center; flex-wrap: wrap; gap: 2px;
  padding: 6px 10px; min-height: 52px;
  background: color-mix(in srgb, var(--color-bg) 82%, transparent);
  -webkit-backdrop-filter: blur(22px) saturate(180%);
  backdrop-filter: blur(22px) saturate(180%);
  border-bottom: 1px solid var(--rail-line);
  box-shadow: 0 1px 3px rgba(0,0,0,.05), 0 10px 30px -18px rgba(0,0,0,.28);
  transform: translateY(-100%);
  transition: transform 0.28s cubic-bezier(.22, 1, .36, 1);
}
.edit-toolbar.visible { transform: translateY(0); }
```

- **Translucency:** 82% background tint + blur/saturate produces the macOS "vibrancy" look.
- **Motion:** slides down with a spring easing when `.visible` is toggled.
- **Replaces** the floating Edit/Export/Publish cluster (`body.edit-mode .top-actions { display: none; }`).

**Fallbacks:**
```css
@supports not ((-webkit-backdrop-filter: blur(1px)) or (backdrop-filter: blur(1px))) {
  .edit-toolbar { background: var(--color-bg); }  /* solid when blur unsupported */
}
```

---

## 4. Rail Controls (`.rail-ctl`)

The individual toolbar buttons — 34px tall, pill-rounded, transparent until interacted with.

```css
.rail-ctl {
  height: 34px; min-width: 34px; padding: 0 8px;
  border: 0; background: transparent; color: var(--color-text);
  border-radius: 8px; cursor: pointer;
  font-size: 13px; font-weight: 500; letter-spacing: -0.01em;
  transition: background 0.12s ease, color 0.12s ease;
}
.rail-ctl:hover  { background: var(--rail-hover); }
.rail-ctl:active { background: var(--rail-press); }
.rail-ctl[aria-pressed="true"],
.rail-ctl[aria-expanded="true"] { background: var(--rail-tint); color: var(--color-link); }
.rail-ctl:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--color-bg), 0 0 0 4px var(--color-link);
}
```

- **State-driven styling** via ARIA attributes (`aria-pressed`, `aria-expanded`).
- **Accessible focus:** a double box-shadow ring (inner bg color, outer accent) — the Apple focus-ring idiom.
- Glyph variants: `.rail-glyph.b` (bold), `.rail-glyph.i` (italic, serif), `.rail-glyph.u` (underline); SVG icons are 19×19.

---

## 5. Segmented Control (alignment)

An iOS-style segmented control with a physically sliding thumb.

```css
.rail-seg { position: relative; display: inline-flex; padding: 2px;
  background: var(--rail-hover); border-radius: 9px; }
.rail-seg button { height: 30px; width: 36px; border: 0; background: transparent;
  color: var(--color-text-secondary); border-radius: 7px; }
.rail-seg button[aria-checked="true"] { color: var(--color-text); }
.rail-seg-thumb {
  position: absolute; z-index: 1; top: 2px; left: 2px; height: 30px; width: 36px;
  background: var(--color-bg); border-radius: 7px;
  box-shadow: 0 1px 3px rgba(0,0,0,.18), 0 0 1px rgba(0,0,0,.12);
  transition: transform 0.22s cubic-bezier(.22, 1, .36, 1);
}
```

The `.rail-seg-thumb` translates under the active option with the same spring easing.

---

## 6. Popovers (`.rail-pop`)

Dropdown menus/popovers use the same frosted material, rounded 13px corners, and a scale+fade entrance.

```css
.rail-pop {
  position: absolute; top: calc(100% + 9px); left: 0;
  min-width: 224px; padding: 6px;
  background: color-mix(in srgb, var(--color-bg) 92%, transparent);
  -webkit-backdrop-filter: blur(26px) saturate(180%);
  backdrop-filter: blur(26px) saturate(180%);
  border: 1px solid var(--color-border); border-radius: 13px;
  box-shadow: 0 12px 44px -10px rgba(0,0,0,.32);
  opacity: 0; transform: translateY(-7px) scale(0.97); transform-origin: top left;
  pointer-events: none;
  transition: opacity 0.15s ease, transform 0.17s cubic-bezier(.22, 1, .36, 1);
}
.rail-pop.open { opacity: 1; transform: none; pointer-events: auto; }
.rail-pop.rail-right { left: auto; right: 0; transform-origin: top right; }
```

**Popover rows** (`.rail-row`) show an icon, label, and a **monospace keyboard shortcut** on the right; the active row shows a `✓` (`\2713`) and highlights with the accent color on hover.

---

## 7. Primary & Secondary Buttons

```css
.edit-toolbar .btn-save {
  height: 34px; padding: 0 17px; border: 0; border-radius: 17px;  /* full pill */
  background: var(--color-link); color: #fff;
  font-size: 13px; font-weight: 600;
  box-shadow: 0 1px 2px rgba(0,0,0,.2);
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.edit-toolbar .btn-cancel {
  height: 34px; padding: 0 11px; border: 0; background: transparent;
  color: var(--color-text-secondary); border-radius: 8px;
}
```

- **Save** is a filled accent pill (radius = half the height → capsule).
- **Cancel** is a quiet, text-only button.
- **Save status** text uses semantic colors: success `#1e874b`, error `#c9382e`.

---

## 8. Insert Panel

A wider popover variant (`.rail-pop--insert`, 340px) with a search field and a two-column grid of insertable blocks.

```css
.rail-insert-search:focus {
  outline: none; border-color: var(--color-link);
  box-shadow: 0 0 0 3px var(--rail-tint);   /* soft accent focus glow */
}
.rail-insert-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px;
  max-height: 322px; overflow-y: auto; }
.rail-insert-item .ico { width: 28px; height: 28px; border-radius: 8px;
  background: var(--rail-hover); display: grid; place-items: center; }
```

Each item is an icon tile + label — the app-grid metaphor.

---

## 9. Reading Surface (document body)

The reading layout is calm and text-first, complementing the chrome:

- **Sticky TOC sidebar** (`.toc-sidebar`, 260px) with hover/active states in the accent color.
- **Serif headings** (`--font-heading`) over a **sans-serif body**, an editorial pairing.
- **Content column** capped for readability, generous padding, `scroll-behavior: smooth`.
- Responsive breakpoints at **900px** and **600px**; a dedicated **`@media print`** block.

---

## 10. Motion & Interaction Language

| Pattern | Value |
|---|---|
| Signature easing | `cubic-bezier(.22, 1, .36, 1)` (spring-like ease-out) |
| Toolbar slide-in | `transform` over `0.28s` |
| Popover entrance | `opacity 0.15s` + `transform 0.17s` (scale 0.97 → 1) |
| Segment thumb | `transform 0.22s` |
| Hover/press feedback | `background 0.12s ease` |

Short, eased, transform-based transitions keep the UI feeling responsive and physical rather than abrupt.

---

## 11. Accessibility Built Into the System

- **Keyboard focus:** `:focus-visible` double-ring on controls; `outline: 2px solid var(--color-link)` on swatches.
- **ARIA state → visual state:** `aria-pressed`, `aria-expanded`, `aria-checked`, `aria-selected` drive styling (toolbar, segmented control, tabs, accordions).
- **Dark mode:** automatic via `prefers-color-scheme`.
- **Reduced motion:** the learner runtime respects `prefers-reduced-motion` for animated headings.
- **Glossary tooltips** use semantic `<dfn>` elements with `role="term"` and keyboard focusability (see `GLOSSARY_CSS`).

---

## 12. The Upload Page (`index.html`)

The entry page carries the same language in a lighter form:

- A **drop zone** (`.drop-zone`) with a distinct `.drag-over` state; the icon lifts (`transform: translateY(-2px)`) and tints on hover/drag.
- Accepts `application/pdf,.pdf`.
- Reads a `refId` from the URL query and posts it as `ref_id` for parent-app integration.
- Shows **real upload progress** (XHR `progress` events) capped at 90%, then switches to an indeterminate bar while server-side conversion runs, polling `/convert-status/<job_id>`.

---

## 13. Summary of the System

The design system is a **token-driven, Apple-inspired** layer: system fonts, an accent built around `#0f4c75`, frosted-glass toolbars and popovers, pill controls, a sliding segmented control, spring easing, and accessible focus rings — all with `@supports` fallbacks so the experience degrades cleanly on browsers without `backdrop-filter` or `color-mix`. It cleanly separates a rich **editor chrome** from a calm, readable **document surface**, and the same visual language extends to the upload page.
