# ToolLighthouse UX contract

Approved business behavior: [multiobject design](docs/multiobject-integration-design.md). Visual context: [DESIGN.md](DESIGN.md). Spanish workshop interface for phone and Pi kiosk.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Button | Native button with `.btn` or object correction button | `pi/kiwi_pi/static/app.js` event bindings | Primary, secondary, small | Node tests and browser activation |
| Dialog | `labelSheet` and `openLabelSheet` | Per-object correction contract | Category correction | Keyboard Escape, Tab and focus restoration |
| File upload | `fileInput`, `cameraInput`, `onFilePicked` | Server validates normalized image | Gallery or device photo | Browser upload and API invalid-image handling |
| Status feedback | `renderGuide` and `toast` | Station snapshot and operation ID | Pending, confirmed, wrong, timeout, offline | Node operation tests and browser sensor simulation |
| Scrollbar | CSS root scrollbar tokens | `pi/kiwi_pi/static/styles.css` | Mobile result scroll | 390x844 browser inspection |
| Toast | `toast` | API error messages | Informational or error | Browser correction and network error |
| Select/Listbox | Native `forceSelect` | Diagnostic simulator categories | Local diagnostic only | Native keyboard and selected option |

## State and recovery
Capture → review → multiobject result. A new capture or correction cancels the guide for the corresponding photo. Ignore outdated prediction responses and events belonging to a different operation. No confirmation is shown for partial completion. Timeout turns off lights but retains visible pending locations. When every location is confirmed and errors cleared, show completion then return to capture.

## Correction and accessibility
Each list row and box opens correction for that object ID. Persist only its crop. A failed correction displays an error and permits retry. Dialog has Escape, cyclic Tab focus and returns focus to its initiating control. Photo boxes always have equivalent list actions. All owned copy uses Spanish, including accessible names; statuses convey meaning through text as well as color.

## Audit boundary
HTML buttons have external `addEventListener` bindings in `app.js`, including a long-press diagnostic binding. The static premium auditor detects only inline handlers and consequently reports false `actionless-button` findings. Verify those actions in a real browser rather than adding duplicate inline handlers to satisfy that parser.
