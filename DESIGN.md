# ToolLighthouse

## Product and audience
Electronics workshop return station, used from a Spanish-speaking phone or Raspberry Pi kiosk. Capture → review → all-object result and concurrent location progress. Approved behavioral authority: `docs/multiobject-integration-design.md`.

## Visual direction
Extend the existing Figma-derived interface. White surfaces, black header, purple camera action, green upload action, blue guidance and yellow result card. The shelf map and numbered photo boxes are the signature; avoid a dashboard redesign.

## Runtime token ownership
Canonical tokens live in `pi/kiwi_pi/static/styles.css` (`:root`). This document records their use; it does not generate CSS. `--blue: #006DFF`, `--green: #31E083`, `--purple: #9746FF`, `--yellow: #F5F500`, `--red: #FF3B1F`, `--ink: #333333`, `--grey: #E3E7E8`, white and black. Keep rejection labels textual rather than relying on color alone.

## Typography
Albert Sans and the established system fallback stack; 700 for headings, 400 for explanations. Keep list text at 16px and details at 14px. One category can wrap; never truncate the correction action.

## Layout and controls
Maintain capture/review screens. Result screen scrolls with up to 12 objects. Photo container uses the image's own aspect ratio so boxes align at any width. Map remains visible alongside the list on landscape screens. Pill buttons and native button semantics; every detected object also has a list correction action.

## Accessibility and motion
Visible focus, keyboard-accessible correction dialog, Escape/cancel restoration, Spanish accessible names, readable text status for errors and confirmations. Permit zoom. Reduce animation when requested. Existing CSS owns light theme; no dark theme introduced.
