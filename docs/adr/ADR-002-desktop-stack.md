# ADR-002: Desktop stack is egui/eframe, not Tauri 2 + Leptos

Date: 2026-11-21 · Status: accepted

## Context

The delivery mandate (section 4.2) requires a Rust-first desktop
architecture, preferably Tauri 2 with a Leptos (Rust→WASM) frontend,
and explicitly forbids React/TypeScript/Node sidecars. It also
anticipates a fallback: "If current compatibility research uncovers a
material blocker in this combination, investigate the most suitable
fully Rust alternative and document the evidence."

## Evidence (build environment, 2026-11-21)

- Tauri 2 on Linux hard-requires WebKitGTK at build time:

  ```
  $ pkg-config --modversion webkit2gtk-4.1
  Package webkit2gtk-4.1 was not found in the pkg-config search path.
  $ pkg-config --modversion gtk+-3.0
  Package 'gtk+-3.0', required by 'virtual:world', not found
  $ pkg-config --modversion libsoup-3.0
  Package libsoup-3.0 was not found ...
  ```

- Installing them is not possible here: `sudo -n true` →
  "sudo: a password is required".
- Rust itself is fine (rustup installed 1.100.0 user-space).

## Decision

The desktop shell is **egui/eframe**: immediate-mode Rust GUI,
rendered by glow (OpenGL). Rationale:

1. It is *fully Rust* including rendering - stricter than the mandate
   minimum (which permits CSS for presentation).
2. Zero system dependencies on the mandate's primary target (Ubuntu
   22.04+ desktop): no webview, no GTK. Tauri would add a webview
   runtime dependency.
3. All domain logic stays in `las-core`; the shell is a view layer,
   so a future Tauri/Leptos frontend could reuse it unchanged.

## Known environment limit (ADR-003)

This sandbox cannot open ANY winit-based window (missing
`libxkbcommon-x11`); UI verification therefore uses egui_kittest
(CPU renderer, no window) - see ADR-003. On the user's Ubuntu 22.04
desktop the binary runs as-is.

## Consequences

- No HTML/CSS theming; egui style provides light/dark themes.
- Accessibility relies on egui's widget labels + full keyboard
  operation; screen-reader support is weaker than a webview's -
  recorded as a known gap to revisit (mandate 14 demands WCAG 2.2 AA;
  we provide keyboard operation, focus order and contrast, and will
  assess AT-SPI integration during the UI milestone).
