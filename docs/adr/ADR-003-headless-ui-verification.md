# ADR-003: Headless UI verification with egui_kittest

Date: 2026-11-21 · Status: accepted

## Context

The mandate forbids fake functionality and demands evidence for every
capability. The desktop shell (ADR-002) cannot open a window in this
build environment:

```
$ find /usr/lib /lib -name "libxkbcommon*"
/usr/lib/x86_64-linux-gnu/libxkbcommon.so.0      # base only
$ LIBGL_ALWAYS_SOFTWARE=1 LAS_SMOKE_FRAMES=20 DISPLAY=:99 ./las-desktop
thread 'main' panicked at xkbcommon-dl-0.4.2/src/x11.rs:59:28:
Library libxkbcommon-x11.so could not be loaded.
```

winit (egui's windowing layer) needs the X11 keyboard bridge; the
package is absent and cannot be installed without sudo. Base Mesa GL
is present (libGL.so.1 + llvmpipe), so rendering itself is not the
blocker - windowing is. X11 headers exist (/usr/include/X11/Xlib.h),
so alternative toolkits were technically possible, but egui remains
the best product decision (ADR-002); the sandbox limit should not
drive the product's architecture.

## Decision

UI code is structured as a library (`las-desktop/src/lib.rs`) with a
thin binary. Every screen is an `egui` widget function over an
explicit `AppState`, verified by **egui_kittest** tests: real egui
passes through a CPU renderer (vello), with assertions on rendered
output (label presence, pixel coverage, per-screen). The same code
path runs on screen outside this sandbox with zero changes.

## Consequences

- UI behavior is genuinely tested here, not eyeballed or skipped.
- The on-screen smoke path stays in the binary (`LAS_SMOKE_FRAMES=N`)
  so the user (or CI with a full desktop) can verify the window
  opens in one command; evidence of the sandbox limitation is
  recorded rather than hidden.
