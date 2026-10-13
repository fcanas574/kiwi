'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const KiwiScan = require('../kiwi_pi/static/scan.js');
const fs = require('node:fs');
const vm = require('node:vm');
const appSource = fs.readFileSync(require('node:path').join(__dirname, '../kiwi_pi/static/app.js'), 'utf8');

class FakeClock {
  constructor() {
    this.time = 0;
    this.nextId = 1;
    this.timers = new Map();
  }

  now() { return this.time; }

  setTimeout(callback, delay = 0) {
    const id = this.nextId++;
    this.timers.set(id, { at: this.time + Math.max(0, delay), callback });
    return id;
  }

  clearTimeout(id) { this.timers.delete(id); }

  async advance(milliseconds) {
    const target = this.time + milliseconds;
    for (;;) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      const [id, timer] = due;
      this.timers.delete(id);
      this.time = timer.at;
      timer.callback();
      await flushPromises();
    }
    this.time = target;
    await flushPromises();
  }
}

async function flushPromises() {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function harness({ frameResults = [] } = {}) {
  const clock = new FakeClock();
  const sentFrames = [];
  const frames = [...frameResults];
  let generation = 0;
  let sessionId = null;
  let captureState = 'paused';
  let lastSequence = 0;
  let cameraFrame = 0;
  const camera = {
    async start() {},
    async stop() {},
    async capture(options) {
      assert.equal(options.maxSide, 960);
      cameraFrame += 1;
      return {
        jpeg: `jpeg-${cameraFrame}`,
        preview: { src: `still-${cameraFrame}`, width: 960, height: 720 },
      };
    },
  };
  const snapshot = (overrides = {}) => ({
    session_id: sessionId,
    generation,
    capture_state: captureState,
    last_sequence: lastSequence,
    busy: false,
    tracks: [],
    connection_error: null,
    retry_after_seconds: 0,
    detection_seconds: 0,
    classification_seconds: 0,
    guide: { operation_id: null, targets: [], pending: [], confirmed: [], wrong_slots: [], blocked: [], phase: 'idle' },
    ...overrides,
  });
  const transport = {
    async start(existingSession) {
      if (!sessionId) sessionId = existingSession || 'session-1';
      generation += 1;
      captureState = 'running';
      lastSequence = 0;
      return snapshot();
    },
    async pause() {
      generation += 1;
      captureState = 'paused';
      return snapshot();
    },
    async cancel() {
      generation += 1;
      captureState = 'paused';
      return snapshot({ guide: { operation_id: null, targets: [], pending: [], confirmed: [], wrong_slots: [], blocked: [], phase: 'idle' } });
    },
    async sendFrame(request) {
      sentFrames.push(request);
      lastSequence = request.sequence;
      const next = frames.shift();
      if (next) return next.promise;
      return {
        state: snapshot({ last_sequence: request.sequence }),
        correlation: {
          session_id: request.session_id,
          generation: request.generation,
          sequence: request.sequence,
        },
      };
    },
  };
  const states = [];
  const controller = KiwiScan.createController({
    transport,
    camera,
    clock,
    onState: (state) => states.push(state),
  });
  return { controller, transport, camera, clock, sentFrames, states, snapshot };
}

test('starts paused and sends no frame until the user starts scanning', async () => {
  const { controller, sentFrames } = harness();
  assert.equal(controller.getState().capture_state, 'paused');
  assert.equal(sentFrames.length, 0);

  await controller.start();
  await flushPromises();

  assert.equal(sentFrames.length, 1);
  controller.dispose();
});

test('allows one in-flight frame, enforces one frame per second, then captures again', async () => {
  const first = deferred();
  const { controller, clock, sentFrames } = harness({ frameResults: [first] });
  await controller.start();
  await flushPromises();
  assert.equal(sentFrames.length, 1);

  await clock.advance(1000);
  assert.equal(sentFrames.length, 1, 'the pending request must not create a frame queue');

  first.resolve({
    state: { session_id: 'session-1', generation: 1, capture_state: 'running', last_sequence: 1, tracks: [], guide: {} },
    correlation: { session_id: 'session-1', generation: 1, sequence: 1 },
  });
  await flushPromises();
  await clock.advance(0);
  assert.equal(sentFrames.length, 2, 'a fresh frame starts after the prior request finishes');
  assert.ok(sentFrames[1].sequence > sentFrames[0].sequence);
  controller.dispose();
});

test('a failed frame waits at least a second before the next attempt', async () => {
  const failure = deferred();
  const { controller, clock, sentFrames } = harness({ frameResults: [failure] });
  await controller.start();
  await flushPromises();
  failure.reject(new Error('Colab sin conexión'));
  await flushPromises();

  await clock.advance(999);
  assert.equal(sentFrames.length, 1);
  await clock.advance(1);
  assert.equal(sentFrames.length, 2);
  controller.dispose();
});

test('pause discards a late frame response without replacing the paused generation', async () => {
  const frame = deferred();
  const { controller, sentFrames } = harness({ frameResults: [frame] });
  await controller.start();
  await flushPromises();
  const pendingResponse = frame.promise;

  await controller.pause();
  const paused = controller.getState();
  frame.resolve({
    state: { session_id: 'session-1', generation: 1, capture_state: 'running', last_sequence: 1, tracks: [{ track_id: 7 }], guide: {} },
    correlation: { session_id: 'session-1', generation: 1, sequence: 1 },
  });
  await pendingResponse;
  await flushPromises();

  assert.equal(controller.getState().capture_state, 'paused');
  assert.equal(controller.getState().generation, paused.generation);
  assert.deepEqual(controller.getState().tracks, []);
  assert.equal(sentFrames.length, 1);
  controller.dispose();
});

test('cancel discards a late frame and keeps the controller paused', async () => {
  const frame = deferred();
  const { controller } = harness({ frameResults: [frame] });
  await controller.start();
  await flushPromises();
  const pendingResponse = frame.promise;

  await controller.cancel();
  frame.resolve({
    state: { session_id: 'session-1', generation: 1, capture_state: 'running', last_sequence: 1, tracks: [{ track_id: 4 }], guide: {} },
    correlation: { session_id: 'session-1', generation: 1, sequence: 1 },
  });
  await pendingResponse;
  await flushPromises();

  assert.equal(controller.getState().capture_state, 'paused');
  assert.deepEqual(controller.getState().tracks, []);
  controller.dispose();
});

test('restart uses the newer generation and starts its sequence from one', async () => {
  const { controller, sentFrames } = harness();
  await controller.start();
  await flushPromises();
  await controller.pause();
  const pausedGeneration = controller.getState().generation;
  await controller.start();
  await flushPromises();

  assert.equal(sentFrames.at(-1).generation, pausedGeneration + 1);
  assert.equal(sentFrames.at(-1).sequence, 1);
  controller.dispose();
});

test('restart waits for an old request and sends only the new generation afterward', async () => {
  const oldFrame = deferred();
  const { controller, clock, sentFrames } = harness({ frameResults: [oldFrame] });
  await controller.start();
  await flushPromises();
  assert.equal(sentFrames.length, 1);

  await controller.pause();
  await controller.start();
  await flushPromises();
  assert.equal(sentFrames.length, 1, 'the old request still owns the single in-flight slot');

  oldFrame.resolve({
    state: { session_id: 'session-1', generation: 1, capture_state: 'paused', last_sequence: 1, tracks: [], guide: {} },
    correlation: { session_id: 'session-1', generation: 1, sequence: 1 },
  });
  await flushPromises();
  await clock.advance(1000);

  assert.equal(sentFrames.length, 2);
  assert.equal(sentFrames[1].generation, 3);
  assert.equal(sentFrames[1].sequence, 1);
  controller.dispose();
});

test('the annotated analyzed image is the exact still paired with its response', async () => {
  const response = deferred();
  const { controller, states, sentFrames } = harness({ frameResults: [response] });
  await controller.start();
  await flushPromises();
  const analyzedRequest = sentFrames[0];
  assert.equal(controller.getState().last_analyzed_frame, null);

  response.resolve({
    state: {
      session_id: 'session-1', generation: 1, capture_state: 'running', last_sequence: 1,
      tracks: [{ track_id: 12, revision: 2, frame_id: 1, box: [10, 20, 120, 160], state: 'stable', category: null }],
      guide: {},
    },
    correlation: { session_id: 'session-1', generation: 1, sequence: 1 },
  });
  await flushPromises();

  assert.equal(analyzedRequest.jpeg, 'jpeg-1');
  assert.equal(controller.getState().last_analyzed_frame.jpeg, analyzedRequest.jpeg);
  assert.equal(controller.getState().last_analyzed_frame.preview.src, 'still-1');
  assert.equal(controller.getState().tracks[0].frame_id, 1);
  assert.equal(states.at(-1).last_analyzed_frame, controller.getState().last_analyzed_frame);
  controller.dispose();
});


test('delayed frame preserves newer SSE sensor progress and matching analyzed still', async () => {
  const frame = deferred();
  const { controller, snapshot } = harness({ frameResults: [frame] });
  await controller.start();
  await flushPromises();
  controller.syncFromServer(snapshot({ last_sequence: 1, guide: { operation_id: 'return-1', confirmed: [1], pending: [6] } }));
  frame.resolve({ state: snapshot({ last_sequence: 1, tracks: [{ track_id: 8, frame_id: 1 }],
    guide: { operation_id: 'return-1', confirmed: [], pending: [1, 6] } }) });
  await flushPromises();
  assert.deepEqual(controller.getState().guide.confirmed, [1]);
  assert.equal(controller.getState().tracks[0].track_id, 8);
  assert.equal(controller.getState().last_analyzed_frame.preview.src, 'still-1');
  controller.dispose();
});

test('older generations and foreign session SSE cannot replace paused state', async () => {
  const { controller, snapshot } = harness();
  await controller.start();
  await controller.pause();
  const paused = controller.getState();
  controller.syncFromServer(snapshot({ generation: 1, capture_state: 'running', guide: { confirmed: [] } }));
  assert.equal(controller.getState().generation, paused.generation);
  assert.equal(controller.getState().capture_state, 'paused');
  controller.syncFromServer(snapshot({ session_id: 'old-session', generation: 99, capture_state: 'running' }));
  assert.equal(controller.getState().session_id, paused.session_id);
  assert.equal(controller.getState().generation, paused.generation);
  controller.dispose();
});

// Run the real app functions and visibility listener against just their DOM
// surface; camera permission and network timing are supplied by the harness.
function appSurface(controller, transport) {
  const elements = new Map();
  let context;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      id, hidden: true, disabled: false, inert: false, open: false, isConnected: true,
      dataset: {}, buttons: [], textContent: '',
      focus() { context.document.activeElement = this; },
      removeAttribute(name) { delete this[name]; },
      replaceChildren(...children) { this.buttons = children; },
      addEventListener() {},
      querySelectorAll() { return id === '#labelSheet' ? [...element('#labelOptions').buttons, element('#labelCancel')] : this.buttons; },
      querySelector() { return this.buttons.find(button => !button.disabled); },
    });
    return elements.get(id);
  }
  const state = { scanController: controller, status: { slots: [{ category: 'solder', label: 'Soldador' }] }, scanCorrectionBusy: false };
  let stopCamera = () => {};
  context = {
    state, scanTransport: transport, scanCamera: { stop() { return stopCamera(); } },
    $: element, URL: { createObjectURL() { return 'blob:crop'; }, revokeObjectURL() {} },
    toast() {}, renderScan() {}, async loadStatus() {},
    document: { hidden: true, activeElement: element('#scanStartBtn'), createElement() { return { dataset: {}, addEventListener() {}, focus() {} }; } },
  };
  vm.createContext(context);
  for (const name of ['fillCorrectionOptions', 'openLabelSheet', 'closeScanCorrectionSurface', 'openScanCorrection', 'cancelScanCorrection', 'submitScanCorrection', 'submitCorrection']) {
    const start = appSource.search(new RegExp('(?:async )?function ' + name + '\\('));
    assert.ok(start >= 0, `real app function ${name} must exist`);
    const tail = appSource.slice(start);
    const next = tail.slice(1).search(/\n(?:async )?function /);
    vm.runInContext(next < 0 ? tail : tail.slice(0, next + 1), context);
  }
  const visibilityStart = appSource.indexOf("document.addEventListener('visibilitychange',");
  const visibilityEnd = appSource.indexOf("window.addEventListener('pagehide'", visibilityStart);
  context.document.addEventListener = (_, handler) => { context.onVisibility = handler; };
  vm.runInContext(appSource.slice(visibilityStart, visibilityEnd), context);
  return { context, element, setCameraStop(callback) { stopCamera = callback; } };
}

for (const pending of ['camera', 'start request']) {
  test(`hiding during ${pending} startup invalidates capture and releases late camera`, async () => {
    const { controller, camera, transport, sentFrames, snapshot } = harness();
    const wait = deferred();
    let stops = 0;
    camera.stop = async () => { stops += 1; };
    if (pending === 'camera') camera.start = () => wait.promise;
    else {
      const originalStart = transport.start;
      transport.start = async (...args) => { await wait.promise; return originalStart(...args); };
    }
    const surface = appSurface(controller, transport);
    surface.setCameraStop(() => camera.stop());
    const startup = controller.start();
    await flushPromises();
    assert.equal(controller.getState().capture_state, 'starting');
    surface.context.onVisibility();
    wait.resolve();
    await startup;
    await flushPromises();
    assert.equal(controller.getState().capture_state, 'paused');
    assert.equal(sentFrames.length, 0);
    assert.equal(snapshot().capture_state, 'paused', 'late successful server Start is paused too');
    assert.ok(stops >= 1, 'camera acquired by invalidated startup is released');
    await controller.start();
    await flushPromises();
    assert.equal(sentFrames.length, 1, 'explicit Start is required to resume');
    controller.dispose();
  });
}

function correctionSurface(error) {
  const controller = { getState: () => ({ session_id: 's', generation: 2, capture_state: 'paused', last_analyzed_frame: { sequence: 1 } }), suspend() {}, syncFromServer() {} };
  const transport = {
    async prepareCorrection() { return { correction_id: 'c', generation: 3 }; },
    async correctionCrop() { return {}; },
    async cancelCorrection() { if (error) throw error; },
    async submitCorrection() { if (error) throw error; },
  };
  return appSurface(controller, transport);
}
for (const action of ['cancel', 'submit']) {
  test(`correction ${action} restores Cancel on a later scan and manual sheet`, async () => {
    const { context, element } = correctionSurface();
    const track = { track_id: 1, revision: 1, frame_id: 1 };
    await context.openScanCorrection(track);
    if (action === 'cancel') await context.cancelScanCorrection();
    else await context.submitScanCorrection('otros');
    await context.openScanCorrection(track);
    assert.equal(element('#labelCancel').disabled, false);
    await context.cancelScanCorrection();
    assert.equal(element('#labelSheet').hidden, true);
    context.openLabelSheet({ id: 2, category: 'solder' });
    assert.equal(element('#labelCancel').disabled, false);
  });
  test(`correction ${action} 401 releases modal and focuses accessible pairing`, async () => {
    const error = Object.assign(new Error('Empareja otra vez'), { status: 401 });
    const { context, element } = correctionSurface(error);
    await context.openScanCorrection({ track_id: 1, revision: 1, frame_id: 1 });
    if (action === 'cancel') await context.cancelScanCorrection();
    else await context.submitScanCorrection('otros');
    assert.equal(element('#labelSheet').hidden, true);
    assert.equal(element('main').inert, false);
    assert.equal(element('#pairingPanel').open, true);
    assert.equal(context.document.activeElement, element('#pairCode'));
    assert.equal(context.state.scanCorrection, null);
    assert.equal(context.state.scanController.getState().capture_state, 'paused');
  });
}


test('a delayed Start cannot resume a newer paused generation received from SSE', async () => {
  const { controller, transport, snapshot, sentFrames } = harness();
  const delayed = deferred();
  transport.start = () => delayed.promise;
  const startup = controller.start();
  await flushPromises();
  controller.syncFromServer(snapshot({ session_id: 'session-1', generation: 2, capture_state: 'paused' }));
  delayed.resolve(snapshot({ session_id: 'session-1', generation: 1, capture_state: 'running' }));
  await startup.catch(() => {});
  await flushPromises();
  assert.equal(controller.getState().generation, 2);
  assert.equal(controller.getState().capture_state, 'paused');
  assert.equal(sentFrames.length, 0);
  controller.dispose();
});

test('an explicit Start may establish a fresh server session with its own generation', async () => {
  const { controller, transport, snapshot } = harness();
  await controller.start();
  await controller.pause();
  transport.start = async () => snapshot({ session_id: 'fresh-session', generation: 1, capture_state: 'running' });
  await controller.start();
  assert.equal(controller.getState().session_id, 'fresh-session');
  assert.equal(controller.getState().generation, 1);
  controller.dispose();
});


for (const mismatch of ['generation', 'session_id']) {
  test(`view retains prior still without overlay or correction rows for a different ${mismatch}`, () => {
    const { context, element } = appSurface({}, {});
    for (const node of ['#scanAnalyzedImageWrap', '#scanMap']) element(node).style = { setProperty() {} };
    for (const node of ['#scanStartBtn', '#scanPauseBtn', '#scanCancelBtn']) element(node).setAttribute = () => {};
    context.document.querySelector = element;
    context.KiwiMultiobject = { boxStyle: () => ({}) };
    context.document.createElement = () => ({ style: {}, dataset: {}, append() {}, setAttribute() {}, addEventListener() {} });
    vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../kiwi_pi/static/scan-view.js'), 'utf8'), context);
    const scan = { session_id: 'current', generation: 2, capture_state: 'paused', guide: {}, tracks: [{ track_id: 1, frame_id: 1, box: [0, 0, 10, 10] }],
      last_analyzed_frame: { session_id: 'current', generation: 2, sequence: 1, preview: { src: 'blob:retained', width: 960, height: 720 } } };
    scan.last_analyzed_frame[mismatch] = mismatch === 'generation' ? 1 : 'previous';
    context.KiwiScanView.create({ controller: { getState: () => scan }, getStatus: () => ({ slots: [] }) }).render();
    assert.equal(element('#scanAnalyzedImage').src, 'blob:retained');
    assert.equal(element('#scanAnalyzedImage').hidden, false);
    assert.equal(element('#scanAnalyzedBoxes').buttons.length, 0);
    assert.equal(element('#scanTrackCount').textContent, '0');
  });
}


test('capture failure pauses backend generation, keeps guide and needs explicit Start', async () => {
  const { controller, camera, transport, snapshot, clock, sentFrames } = harness();
  const guide = { operation_id: 'guide-1', pending: [1], confirmed: [] };
  const originalPause = transport.pause;
  transport.pause = async (...args) => ({ ...await originalPause(...args), guide });
  camera.capture = async () => { throw new Error('Camera track ended'); };
  await controller.start();
  await flushPromises();
  assert.equal(controller.getState().capture_state, 'paused');
  assert.equal(snapshot().generation, 2);
  assert.equal(snapshot().capture_state, 'paused');
  assert.deepEqual(controller.getState().guide, guide);
  assert.equal(controller.getState().connection_error, null);
  assert.match(controller.getState().error, /Camera track ended/);
  await clock.advance(30000);
  assert.equal(sentFrames.length, 0);
  camera.capture = async () => ({ jpeg: 'recovered' });
  await controller.start();
  await flushPromises();
  assert.equal(sentFrames.length, 1);
  assert.equal(sentFrames[0].generation, 3);
  controller.dispose();
});

function browserCameraSurface() {
  let currentTrack;
  const tracks = [];
  const video = { readyState: 2, videoWidth: 120, videoHeight: 90,
    async play() {}, addEventListener() {}, removeEventListener() {} };
  const context = {
    isSecureContext: true, setTimeout, clearTimeout,
    navigator: { mediaDevices: { async getUserMedia() {
      const listeners = new Set();
      currentTrack = { readyState: 'live', addEventListener(name, callback) { if (name === 'ended') listeners.add(callback); },
        removeEventListener(name, callback) { listeners.delete(callback); }, stop() { this.readyState = 'ended'; },
        end() { this.readyState = 'ended'; for (const callback of [...listeners]) callback(); } };
      tracks.push(currentTrack);
      return { active: true, getTracks: () => [currentTrack], getVideoTracks: () => [currentTrack] };
    } } },
    document: { createElement() { return { getContext: () => ({ drawImage() {} }), toBlob(callback) { callback({}); } }; } },
    URL: { createObjectURL: () => 'blob:camera', revokeObjectURL() {} },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../kiwi_pi/static/scan.js'), 'utf8'), context);
  return { camera: context.KiwiScan.createBrowserCamera(video), tracks };
}

test('browser track ended immediately invalidates in-flight response and permits explicit restart', async () => {
  const oldFrame = deferred();
  const { transport, clock, sentFrames, snapshot } = harness({ frameResults: [oldFrame] });
  const { camera, tracks } = browserCameraSurface();
  const controller = KiwiScan.createController({ transport, camera, clock });
  await controller.start();
  await flushPromises();
  assert.equal(sentFrames.length, 1);
  tracks[0].end();
  await flushPromises();
  assert.equal(controller.getState().capture_state, 'paused');
  assert.equal(snapshot().generation, 2);
  oldFrame.resolve({ state: snapshot({ generation: 1, capture_state: 'running', last_sequence: 1, tracks: [{ track_id: 9 }] }) });
  await flushPromises();
  await clock.advance(30000);
  assert.equal(sentFrames.length, 1);
  assert.deepEqual(controller.getState().tracks, []);
  assert.equal(controller.getState().last_analyzed_frame, null);
  await controller.start();
  await flushPromises();
  assert.equal(tracks.length, 2);
  assert.equal(sentFrames.length, 2);
  assert.equal(sentFrames[1].generation, 3);
  controller.dispose();
});


test('camera loss keeps sensor SSE progress received while backend pause is pending', async () => {
  const { controller, camera, transport, snapshot } = harness();
  const pendingPause = deferred();
  transport.pause = () => pendingPause.promise;
  camera.capture = async () => { throw new Error('Camera unavailable'); };
  await controller.start();
  await flushPromises();
  controller.syncFromServer(snapshot({ generation: 2, capture_state: 'paused',
    guide: { operation_id: 'return-1', pending: [], confirmed: [1] } }));
  pendingPause.resolve(snapshot({ generation: 2, capture_state: 'paused',
    guide: { operation_id: 'return-1', pending: [1], confirmed: [] } }));
  await flushPromises();
  assert.deepEqual(controller.getState().guide.confirmed, [1]);
  controller.dispose();
});
