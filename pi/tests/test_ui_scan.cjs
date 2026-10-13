'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const KiwiScan = require('../kiwi_pi/static/scan.js');

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
