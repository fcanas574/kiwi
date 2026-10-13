# ToolLighthouse UX contract

Approved business behavior: [multiobject design](docs/multiobject-integration-design.md). Visual context: [DESIGN.md](DESIGN.md). Spanish workshop interface for phone and Pi kiosk.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Button | Native button with `.btn` or object correction button | `pi/kiwi_pi/static/app.js` event bindings | Primary, secondary, small | Node tests and browser activation |
| Form | `pairForm` with native labeled password input and app-owned feedback | `POST /api/scan/pair`; pairing code is separate from Colab credentials | Masked code with temporary show/hide | Node transport tests and browser pairing/error flow |
| Dialog | `labelSheet`, `cancelScanSheet`, and their open/close handlers | Per-object correction and continuous-return contracts | Category correction; cancel confirmation | Keyboard Escape, Tab and focus restoration |
| File upload | `fileInput`, `cameraInput`, `onFilePicked` | Server validates normalized image | Gallery or device photo | Browser upload and API invalid-image handling |
| Status feedback | `renderGuide` and `toast` | Station snapshot and operation ID | Pending, confirmed, wrong, timeout, offline | Node operation tests and browser sensor simulation |
| Scrollbar | CSS root scrollbar tokens | `pi/kiwi_pi/static/styles.css` | Mobile result scroll | 390x844 browser inspection |
| Toast | `toast` | API error messages | Informational or error | Browser correction and network error |
| Select/Listbox | Native `forceSelect` | Diagnostic simulator categories | Local diagnostic only | Native keyboard and selected option |
| Continuous scan | `KiwiScan.createController` (`scan.js`) and `KiwiScanView.create` (`scan-view.js`) | Task 5 UI contract; `pi/kiwi_pi/scan_api.py` | Start, pause, cancel returns, reconnect | Injected Node clock/transport tests and simulated-camera browser flow |

## State and recovery
Capture → review → multiobject result. A new capture or correction cancels the guide for the corresponding photo. Ignore outdated prediction responses and events belonging to a different operation. No confirmation is shown for partial completion. Timeout turns off lights but retains visible pending locations. When every location is confirmed and errors cleared, show completion then return to capture.

The continuous scan starts only after an explicit user action, sends at most one JPEG frame per second, and never queues frames. Its live camera preview and last analyzed still are separate surfaces; boxes are drawn only on the still paired with that response. Track rows expose observation, stability, classification, acceptance, rejection, and retry states. The shelf map and guide progress persist while the phone keeps scanning and while capture is paused.

Pause preserves active return guides. Canceling returns uses an app-owned confirmation, clears the active guide, and pauses capture. A page reload that finds an authenticated running session first pauses it; the user explicitly starts again to advance the generation. A visible connection recovery also pauses and starts with a new generation. Hiding the page pauses capture and requires an explicit restart when it becomes visible. A 401 opens pairing recovery and never triggers an automatic retry loop.

Pairing uses only the station pairing code. Mask it by default, clear it after submission, and never put it in a URL or persistent browser storage. Do not request or expose the Colab API key. The camera requires a secure context and the rear-facing phone camera; camera permission and secure-context failures explain the next step.

Continuous correction is opened from a track in the last analyzed still and sends that track ID and revision to the server. Preparing the correction atomically freezes its crop and pauses scanning. Canceling the modal releases the crop and preserves guide progress; selecting a label saves only the frozen crop, cancels that guide cycle, and leaves capture paused. Escape or Cancel closes the correction, restores focus to the initiating control when it remains available (otherwise to Start), and retains the guides. Manual photo controls are disabled whenever a continuous session or guide could be replaced.

## Correction and accessibility
Each list row and box opens correction for that object ID. Persist only its crop. A failed correction displays an error and permits retry. Dialog has Escape, cyclic Tab focus and returns focus to its initiating control. Photo boxes always have equivalent list actions. All owned copy uses Spanish, including accessible names; statuses convey meaning through text as well as color.

The continuous scan correction is scoped to the frozen crop and source track revision. Its modal keeps the image visible with the label choices, reports expired/stale/authorization errors in place, and does not resume capture after submit or cancel. Its opening action pauses the browser loop before the authenticated prepare request so no additional frame is queued.

## Audit boundary
HTML buttons have external `addEventListener` bindings in `app.js`, including the diagnostic trigger. The static premium auditor detects only inline handlers and consequently reports `actionless-button` findings for these source-bound actions. Verify actions in a real browser rather than adding duplicate inline handlers to satisfy that parser.
