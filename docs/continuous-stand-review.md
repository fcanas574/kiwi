# Revisión del escaneo continuo

Estado final: todos los hallazgos importantes y los cuatro ajustes menores accionables quedaron resueltos en ca5f675 y verificados por revisión independiente. La integración de software está lista; Raspberry, teléfono, QR real y GPU requieren validación física. La advertencia de dependencia y los 15 hallazgos de la auditoría estática siguen documentados como límites.

Los informes siguientes conservan su estado histórico: la revisión inicial pidió correcciones y el informe final confirma su resolución.

## Revisión completa inicial

# Final whole-branch review

Range: immutable `019f22a..2feefab`; branch `codex/continuous-stand`. Reviewed the supplied whole-branch package in bounded passes against the approved specification, plan, global constraints, five Review Focus conditions and ledger rulings. Applied the requesting-code-review reviewer template. This review makes no deployment or physical-performance claim.

## Strengths

- Colab has separately callable DINO/Qwen operations, preserves the manual composition and uses the shared model lock. Pi validates remote correlation, dimensions, boxes and classification data before guide effects.
- The coordinator places blocking image work off the event loop, permits one remote scan operation, checks generation after asynchronous boundaries and applies Qwen only after compatible later DINO evidence. Tracker uncertainty remains conservative for suppression.
- Continuous guides retain independent sensor progress and use cycle-bound timers; browser rendering keeps analysis tied to its exact JPEG. Pairing secrets remain separate from Colab credentials; the local kiosk retains loopback administration while using certificate-matching HTTPS.
- The evidence distinguishes simulated software verification from outstanding physical Pi, phone, QR and GPU validation. The strict static audit's exit 1 is reported honestly.

## Issues

### Critical

None found.

### Important — should fix before merge

1. **Camera loss remains a running scan and enters the network retry loop.** `pi/kiwi_pi/static/scan.js:191`, `:240–249`, `:571–573`.

   A ended/unavailable camera makes `camera.capture()` reject, but the shared catch block handles that error like a transient transport failure: capture remains running, another capture is scheduled and the backend is never paused. The browser camera adapter also has no track-ended handler. An operator cannot recover by pressing Start because the controller returns early while running; the status presents camera failure as a Colab outage. This violates the explicit camera-loss behavior (pause capture and invalidate pending work). Separate camera failures from remote failures, stop/pause the session while keeping guides and sensors, and require explicit restart. A regression should cover an ended camera with zero subsequent frame submissions and a server generation change.

   Focused read-only Node reproduction using injected camera/transport/clock: `camera.capture()` rejects with `Camera track ended` immediately after Start. Result: `{state:"running", captures:1, pauses:0, retryDelays:[1000], error:"Camera track ended"}`. No full UI suite was rerun.

2. **Natural completion of the return cycle invalidates an otherwise valid frozen correction.** `pi/kiwi_pi/scan.py:327`, `:345–352`; integration with `pi/kiwi_pi/station.py:259–261`.

   Prepare a correction while a continuous guide is active. The frozen crop correctly survives the pause generation change, and sensors correctly continue working. If the last sensor confirms while the user chooses a label, the normal green timer closes that cycle and clears `station.operation_id`. Submit then returns 409 solely because the frozen operation ID differs from None, even though the crop is unexpired and owner/session/generation have not changed. The UI cannot save that correction and must abandon it. The earlier protection against canceling a successor guide is useful, but natural closure of the original guide must not be treated as a successor operation. Track enough cycle identity/history to distinguish completed original work from a replacement, preserve rejection for a real successor, and add the sensor-completion-during-correction regression.

   Focused read-only Python reproduction used the actual tracker, coordinator and station with the existing ScriptedRecognizer: four valid frames establish a servo guide; freeze its current track; confirm its actual slot using `on_sensor`; let the configured 10 ms green timer complete. Submit outputs `{operation_id_after_natural_completion:None, generation:2, correction_status:409, detail:"La corrección ya no controla la sesión actual."}`. No feedback file was written. The first reproduction attempt used the existing 64×48 helper with the scripted 50-pixel box and correctly failed remote validation; the corrected 128×128 reproduction above isolates this issue.

### Minor — ledger triage

- **Standalone empty detection coverage:** `colab/test_scan_recognizer.py:14` tests bounded nonempty locate, but lacks a direct empty-list locate assertion. Retain as a small coverage follow-up; `locate` itself returns an empty object list correctly by inspection. Not a product blocker.
- **DINO classification wording:** `docs/superpowers/specs/2026-10-13-continuous-stand-design.md:165` should say Qwen classification confirmed by later DINO detection. Documentation-only correction recommended in the authorized final fix wave.
- **Intermediate absence assertions:** `pi/tests/test_scan_end_to_end.py:230` should assert suppression after each of the first two empty frames. This is a minor integration coverage improvement; the focused coordinator test already checks retention after two frames (`pi/tests/test_scan.py:384–390`). No early-rearm defect found.
- **50 ms green-window flakiness:** `pi/tests/test_scan_end_to_end.py:206`, `:293` can miss the short real-time returned state under a heavily scheduled runner. Use a controlled timer or a longer window for those assertions. Existing passing evidence is valid; repeated blanket reruns would not resolve the structural timing risk.
- **Baseline TestClient warning:** existing Starlette/httpx deprecation remains dependency maintenance, outside this feature's necessary fixes. Do not suppress it; evidence already records it.
- **Static audit:** the 15 actionless-button reports remain an acknowledged static-tool limitation under the explicit ledger ruling. I inspected the source bindings for scan, pairing, correction, cancellation and diagnostic controls. No additional missing-handler defect found; no static-audit pass claimed.

## Five Review Focus results

1. Tool changes while Qwen responds: compatible strictly later DINO evidence and revision/generation checks gate acceptance (`tracking.py:243–285`, `scan.py:259–272`). Changed or uncertain geometry discards provisional recognition; no additional finding.
2. Wi-Fi loss after return: network failures do not call the absence update; reconnect resets geometry but retains coordinator suppression. Unclassified tracks prevent absence evidence and empty valid observations permit rearm (`scan.py:274`, `:282–287`, `:454–479`; browser EventSource recovery). No nonexistent-frame rearm found. Camera loss is separately the Important finding above.
3. New cycle during the old green period: station cycle token protects the old timer, while additions in the same cycle cancel its reset without losing progress (`station.py:153–161`, `:207–231`); the dedicated timer test checks a different operation survives the old deadline. No additional finding.
4. Correction generation: prepare atomically freezes its crop and advances generation; old inference is discarded. Successor owner/generation/operation protection exists. Natural completion during the modal is the Important finding above.
5. Certificate trust/IP: generation validates IP SAN/serverAuth, renewal supports the existing CA, docs distinguish CA installation from full trust and real getUserMedia, and kiosk transport retains loopback authorization. Physical trust and changing-IP operation remain pending; no software claim replaces them.

## Declined to judge

- Actual Pi GPIO response, CPU/RAM load and the ≤100 ms additional sensor latency: no physical Pi attached; software/browser evidence cannot establish those measurements.
- Real Colab GPU DINO/Qwen timing, memory use and recognition quality: real models/GPU unavailable; scripted contracts do not establish model performance.
- Dedicated-phone CA trust, rear-camera selection, effective preview rate and sustained foreground behavior: no phone/browser pairing on real hardware; deployment acceptance must verify the actual device.
- Real QR generation/decoding: qrencode was stubbed in Task 6; no QR image was generated or scanned.
- Identity swaps that preserve compatible geometry and quantity/identity of deposited tools: explicitly documented exclusions; this version deliberately uses geometry and slot presence.
- Existing dependency warning remediation: predates this branch and does not alter the reviewed behavior; tracked openly as maintenance.
- Exhaustive unchanged manual/diagnostic UI runtime traversal: outside the new continuous flow; bindings were inspected, but the reported browser journey does not exercise every legacy control.

## Assessment

**Ready to merge? With fixes.** Two reproduced user-visible integration defects should be resolved in the authorized final fix wave and checked by a scoped review. The architecture and recorded verification otherwise support the requested software behavior; physical deployment acceptance remains explicitly pending.

## Review method and evidence

Read the immutable diff package in bounded production-boundary passes, inspected related test cases, spec/plan, ledger, Task 7 review/report and validation document. Named unchanged integration reads were limited to the existing test configuration/JPEG helper and station sensor/timer behavior needed for the correction reproduction. Did not rerun the reported 147 Python/26 Node suites, 17-cell notebook AST check or full browser journey. Two focused reproductions only; no product/index/HEAD/branch changes and no subagents. This requested ignored report is the sole review artifact written.


## Corrección final y evidencia

# Final authorized fix wave

Baseline: `2feefab`, branch `codex/continuous-stand`, worktree `/Users/felipecanas/.codex/worktrees/continuous-stand/Kiwi`. One authorized wave addresses the two Important findings and four actionable Minor follow-ups in final-review.md. No subagents, hardware, new models, deployment, trust-store edits or broad refactor. Scoped review remains pending; this report does not approve merging.

## Root causes and resulting contracts

1. Camera capture rejection previously reached the same catch/backoff branch as transport rejection, and the adapter did not report track loss. The actual controller now distinguishes its capture stage, handles `subscribeLoss` from the actual browser adapter, pauses locally synchronously (epoch invalidation), clears scheduled captures and asks the backend to pause/advance generation. It preserves guide and sensor state, closes camera resources, and requires explicit Start. Start waits for loss cleanup before reacquiring. No camera retry timer is created. Network failures after capture retain existing backoff. Old in-flight response and local still cannot become the new generation's analysis. A paused SSE snapshot cannot automatically resume capture.

2. Equality of active operation IDs rejected natural closure and also failed to detect a successor that had already closed back to None. `Station.operation_revision` monotonically advances on replacement/explicit cancellation; natural sensor/green timer closure preserves it. `_guide_cycle` and `recognition_version` still advance on every closure, preserving timer and manual recognition protections. Frozen correction captures the operation revision atomically alongside owner and new pause generation. Submit requires unchanged revision plus original operation or naturally cleared None, unchanged session/owner/generation, paused capture and unexpired crop. A successor started and then closed, including a reused operation string, cannot defeat the guard. Explicit admin cancel also invalidates a frozen correction. Crop writing and metadata-failure rollback are untouched.

3. A related camera-loss pause-response boundary could overwrite sensor progress that had already arrived by SSE. A focused RED test reproduced confirmed [1] reverting to []. Pause now applies the existing guide-version rule when merging its delayed response, keeping newer SSE progress while retaining the returned scan generation.

## RED evidence (tests written before production changes)

- `rtk proxy env PYTHONPATH=pi:colab /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests/test_scan.py -k 'natural_sensor_completion or closed_successor' -q`: **4 failed**, 24 deselected, unchanged TestClient warning, exit 1. Natural completion failed with 409; manual/continuous successor then explicit closure and admin cancel wrongly saved instead of raising 409.
- `rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test --test-name-pattern='capture failure|browser track ended' pi/tests/test_ui_scan.cjs`: **2 failed**, exit 1, both running rather than paused.
- `rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test --test-name-pattern='camera loss keeps' pi/tests/test_ui_scan.cjs`: **1 failed**, exit 1, delayed pause erased newer sensor confirmation.

## GREEN evidence

- Initial correction subset: 7 passed, 21 deselected, warning, exit 0. Initial scan Node suite: 22 passed, exit 0.
- After related boundary coverage and final code: `rtk proxy env PYTHONPATH=pi:colab /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests/test_scan.py pi/tests/test_scan_api.py pi/tests/test_scan_end_to_end.py pi/tests/test_station.py colab/test_scan_recognizer.py -q`: **62 passed, 1 warning in 5.01s**, exit 0. Covers previous owner/generation/operation tests, actual coordinator/station sensor closure, HTTP preparation/crop/save through running sensor monitor, byte-exact feedback crop and closed successor protection.
- `rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test pi/tests/test_ui_scan.cjs`: **23 passed**, exit 0. Real controller and real adapter run in a browser-like VM; only camera platform/transport/clock boundaries are controlled. Includes immediate track loss with remote request in flight, stale result discard, backend generation change, zero retries, explicit reacquisition and newer SSE sensor progress.
- Full final Python: `rtk proxy env PYTHONPATH=pi:colab KIWI_TEST_CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' /tmp/kiwi-merge-venv/bin/python -m pytest pi/tests colab -q`: **154 passed, 1 warning in 19.42s**, exit 0; includes opt-in Chrome/network coverage.
- Full final Node: `rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test pi/tests/test_ui_*.cjs`: **29 passed**, 0 failed, exit 0.
- `rtk git diff --check`: exit 0. No notebooks or deployment shell files changed, so no new AST or shell syntax execution claimed.

## Bounded Chrome integration

Own temporary service: `rtk proxy env PYTHONPATH=pi:pi/tests:colab /tmp/kiwi-merge-venv/bin/python /tmp/kiwi-final-camera-server.py`, loopback port 18778, real create_app and mock-only hardware/recognizer, no physical camera. Browser: `rtk proxy /Users/felipecanas/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node /tmp/kiwi-final-camera-browser.cjs`, isolated headless Chrome with fake-device/fake-ui and Y4M flags. Fifth DINO held 3s; video track stopped and synthetic ended event dispatched. Final exit 0:

```json
{"generationBefore":1,"generationPaused":2,"generationRestarted":3,"framesAtLoss":5,"subsequentFramesBeforeStart":0,"sensorConfirmedWhilePaused":[1],"guidePreserved":true,"pageErrors":[]}
```

Initial temporary runner attempts incorrectly advanced async waitForFunction predicates and failed before camera loss/restart assertions; corrected runner explicitly polls real status. No product changes resulted from these harness attempts. Browser/context/service closed after final check. This is a synthetic event integration check, not a physical camera-disconnect test.

## Minor rulings and limitations

- Added standalone empty locate result assertion, without Qwen classification.
- Corrected spec wording to Qwen classification confirmed by later DINO detection; updated stale implementation/review status factually.
- Asserted category suppression after each of the first two valid empty integration frames.
- Replaced 50ms real-time green window in integration with controlled station timer boundary and same event-loop callback; station's actual timer/cycle tests remain.
- Baseline Starlette/httpx TestClient warning remains visible; no suppression/migration.
- Prior 15 static affordance.actionless-button findings remain acknowledged tool limitation, not an audit pass.
- Real Pi GPIO/CPU/RAM and <=100ms sensor latency, actual dedicated phone/rear camera/CA trust/foreground cadence, QR encoding/decoding and Colab GPU recognition/timing remain unverified.
- On simultaneous camera and network loss, local capture stays paused but backend invalidation cannot be confirmed until the pause request reaches the Pi. Connection failure remains exposed; camera errors do not trigger network capture retries.
- One fresh scoped review is still required after this wave; no additional fix wave authorized.


## Revisión enfocada de la corrección

- **Important: Camera loss remains a running scan and enters the network retry loop** — **ADDRESSED**. `pi/kiwi_pi/static/scan.js:184–197` routes loss through synchronous local pause/epoch invalidation, backend pause, resource closure and explicit restart; `:210–213`, `:260–270` distinguish capture errors from transport backoff; `:283–284` waits for loss cleanup before Start. Actual adapter track-ended subscription and capture validation are at `:563–572`, `:614–617`, `:638–641`; controller subscription is at `:429–431`. `:353–377` clears scheduled capture and requests the generation-changing pause without canceling guides. Covering regressions: `pi/tests/test_ui_scan.cjs:484`, `:531` assert paused backend generation, zero subsequent submissions, stale in-flight result rejection and explicit reacquisition. Network errors retain the existing backoff branch.
- **Important: Natural completion of the return cycle invalidates an otherwise valid frozen correction** — **ADDRESSED**. `pi/kiwi_pi/station.py:231–235` distinguishes natural closure from explicit cancellation, while `:265–266` uses natural closure only for the existing returned timer. `_guide_cycle` and `recognition_version` still advance on closure. Replacements through `:162–163`, `:188–189` advance `operation_revision`; revision remains monotonic even after a successor closes or reuses an operation string. `pi/kiwi_pi/scan.py:327–335` freezes revision and new pause generation atomically; `:349–355` requires unchanged revision, original operation or naturally cleared None, session, owner, generation and paused capture. TTL and persistence/rollback remain unchanged. Regressions: `pi/tests/test_scan.py:611`, `:641`, `:663`; actual app preparation/sensor/crop/submit integration: `pi/tests/test_scan_end_to_end.py:318–361`, including byte-exact saved crop and metadata.
- **Minor: Standalone empty detection coverage** — **ADDRESSED**, `colab/test_scan_recognizer.py:84–93` asserts dimensions, empty objects and zero raw detections, with a classifier that fails if called.
- **Minor: DINO classification wording** — **ADDRESSED**, `docs/superpowers/specs/2026-10-13-continuous-stand-design.md:165` names Qwen classification confirmed by later DINO detection.
- **Minor: Intermediate absence assertions** — **ADDRESSED**, `pi/tests/test_scan_end_to_end.py:247–254` asserts both suppressed categories after each of the first two empty frames and empty suppression after the third.
- **Minor: 50 ms green-window flakiness** — **ADDRESSED**, `pi/tests/test_scan_end_to_end.py:117–131`, `:223–237` replace the short real-time deadline with a controlled cycle-aware timer fired on the app event loop after returned-state assertions. The same controlled boundary remains active for the final returned assertion. Existing station real-timer coverage is retained.
- **Minor: Baseline TestClient warning** — **ADDRESSED by prior ledger disposition**, `final-fix-report.md` records the unchanged warning in the final 154-test Python result; no dependency changes or warning suppression appear in this fix diff. Dependency maintenance remains outside this fix scope.
- **Minor: Static audit** — **ADDRESSED by prior ledger disposition**, `docs/continuous-stand-validation.md` continues to report 15 actionless-button findings as the acknowledged static-tool limitation, without claiming an audit pass. No new static audit execution is claimed.
- **Related delayed camera-pause/SSE boundary** — **ADDRESSED**, `pi/kiwi_pi/static/scan.js:364–367` preserves guide progress when accepted SSE updated its version while the pause response was pending; `pi/tests/test_ui_scan.cjs:558–573` specifically asserts that newer confirmed `[1]` survives an older delayed pause guide.
- **New breakage in the fix diff: Critical/Important** — **None found**. Scoped production reads covered camera loss, restart, stale response filtering, pause/SSE merge, frozen correction guards, replacement/cancellation revision advancement and natural timer closure. No second broad review was performed.
- **New breakage in the fix diff: Minor** — **None found**.
- **Out-of-scope observations** — **None newly identified**. Previously documented physical Pi/GPIO/latency, phone camera/CA trust/foreground behavior, QR and Colab GPU acceptance remain unverified and non-blocking for this scoped software review. Simultaneous camera/network loss leaves local capture paused; remote invalidation depends on successful Pi delivery, as recorded in the report.
- **Checks/evidence** — Read supplied immutable `review-2feefab..ca5f675.diff` (base `2feefab`, head `ca5f675`), approved specification, global constraints, original findings and final fix report; inspected the amended production paths and corresponding test assertions. Report names the covering correction/adapter/SSE regressions, records their RED failures and focused GREEN runs (62 Python and 23 scan Node), and shows final full-run output: 154 Python passed with one existing warning; 29 Node passed; bounded fake-camera Chrome generation 1→2→3, zero subsequent frames before Start, retained sensor/guide and no page errors; diff check exit 0. These supplied execution results were checked against the changed tests and contracts, not independently rerun. No specific unanswered code doubt required a reproduction. No suites, subagents, Git mutations, deployment or product edits were performed; this requested ignored report is the sole written artifact.
- **Fix round/readiness** — **All original actionable findings addressed; no new Critical/Important breakage. Ready for controller adjudication and software integration.** Physical deployment acceptance remains pending; this verdict does not assert hardware performance or authorize deployment.
