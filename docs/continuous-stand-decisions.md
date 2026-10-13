# Decisiones de ejecución

Decisiones registradas durante la implementación, en orden, con motivo y coste si resultan equivocadas.

Ruling: Implementers use GPT-6 Luna Max; all reviewers and fixers use GPT-6.1 Sol Medium, including final review — explicit user instruction overrides skill default model and original-implementer fix routing — cost if wrong: additional fix/review time on these models.

Ruling: execute tasks sequentially with a fresh implementer and task review before dependents — shared files/contracts and the SDD workflow prohibit concurrent implementers — cost if wrong: longer wall-clock duration.

Ruling: Tracker category_visibility covers accepted categories previously classified during its session, not the whole station catalog; retain known-category names after track expiry and clear on reset; coordinator owns suppression across reconnect — keeps geometry independent of Config and matches known-category semantics — cost if wrong: coordinator integration may need category visibility adaptation.

Ruling: after reconnect/reset, unclassified or uncertain tracks do not establish absence of a suppressed category; clear valid observations are required — prevents an empty known-category map from accidentally rearming a still-visible tool — cost if wrong: conservative rearming may wait longer in cluttered scenes.

Ruling: move test_pause_does_not_disable_sensors from Task 3 to Task 4, where ScanCoordinator.pause actually exists; do not create an unused Station.paused test marker — verifies real integration and avoids a vacuous test — cost if wrong: pause regression coverage arrives one task later.

Ruling: permit scan-view.js as a focused rendering module alongside pure controller/transport scan.js; keep shared pairing/correction adapters in app.js — avoids adding roughly600 scan-rendering lines to existing app.js while preserving manual flow owners — cost if wrong: one extra module and callback boundary to maintain.

Ruling: Insert a separately reviewed Sol Medium mock integration fix before resuming Task5 — browser validation exposed mismatched classification correlation and unstable mock boxes — cost if wrong: extra simulator metadata and implementation time.

Ruling: retain external JavaScript button bindings despite15 static actionless-button errors and reportauditexit1 honestly — independentreview confirms real handlers and simulatedruntimecheckscover scancontrols; staticrule cannot follow bindings — cost if wrong: a missed runtimeaction needs further browserverification.

Ruling: keep certificate-matching IP URL but preserve loopback transport for local kiosk — plan IP navigation otherwise conflicts with unchanged local-only admin boundary — cost if wrong: kiosk browser/HTTPS resolution may need device-specific adjustment.
