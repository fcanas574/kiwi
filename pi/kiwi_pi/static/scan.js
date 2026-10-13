(function(root) {
  'use strict';

  const FRAME_INTERVAL_MS = 1000;
  const CLIENT_BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];

  function messageFor(error) {
    if (error && typeof error.message === 'string' && error.message) return error.message;
    return 'No se pudo completar la solicitud de escaneo.';
  }

  function responseState(result) {
    return result && (result.state || result.snapshot || result);
  }

  function makeClock(clock) {
    const source = clock || {};
    return {
      now: typeof source.now === 'function' ? () => source.now() : () => Date.now(),
      setTimeout: typeof source.setTimeout === 'function'
        ? (callback, delay) => source.setTimeout(callback, delay)
        : (callback, delay) => root.setTimeout(callback, delay),
      clearTimeout: typeof source.clearTimeout === 'function'
        ? (id) => source.clearTimeout(id)
        : (id) => root.clearTimeout(id),
    };
  }

  function createController({ transport, camera, clock, onState } = {}) {
    if (!transport || typeof transport.sendFrame !== 'function') {
      throw new TypeError('transport.sendFrame es obligatorio.');
    }
    if (!camera || typeof camera.capture !== 'function') {
      throw new TypeError('camera.capture es obligatorio.');
    }

    const timer = makeClock(clock);
    let current = {
      session_id: null,
      generation: 0,
      capture_state: 'paused',
      last_sequence: 0,
      busy: false,
      tracks: [],
      connection_error: null,
      retry_after_seconds: 0,
      detection_seconds: 0,
      classification_seconds: 0,
      guide: null,
      last_analyzed_frame: null,
      error: null,
    };
    let disposed = false;
    let running = false;
    let starting = false;
    let startPromise = null;
    let epoch = 0;
    let sequence = 0;
    let frameBusy = false;
    let lastFrameStartedAt = null;
    let retryIndex = 0;
    let scheduled = null;
    let unsubscribe = null;
    let guideVersion = 0;

    function snapshotForCallback() {
      return {
        ...current,
        tracks: Array.isArray(current.tracks) ? current.tracks.map(track => ({ ...track })) : [],
        guide: current.guide && typeof current.guide === 'object' ? { ...current.guide } : current.guide,
      };
    }

    function emit(patch) {
      current = { ...current, ...patch };
      if (typeof onState === 'function') {
        try { onState(snapshotForCallback()); } catch { /* UI listeners must not stop capture. */ }
      }
    }

    function clearScheduled() {
      if (scheduled !== null) timer.clearTimeout(scheduled);
      scheduled = null;
    }

    function releaseFrame(frame) {
      if (!frame) return;
      try {
        if (typeof frame.release === 'function') frame.release();
        else if (camera && typeof camera.releaseFrame === 'function') camera.releaseFrame(frame);
      } catch { /* A stale preview is safe to leave for the browser to collect. */ }
    }

    function mergeServerState(snapshot, { keepLocalFrame = true, keepGuide = false, allowSession = false } = {}) {
      const incoming = responseState(snapshot);
      if (!incoming || typeof incoming !== 'object') return current;
      const changedSession = current.session_id && incoming.session_id
        && incoming.session_id !== current.session_id;
      if (changedSession && !allowSession) return current;
      if (!changedSession && Number.isInteger(incoming.generation)
          && incoming.generation < current.generation) return current;
      const changedGeneration = Number.isInteger(incoming.generation)
        && incoming.generation !== current.generation;
      if (running && changedGeneration) {
        running = false;
        epoch += 1;
        clearScheduled();
      }
      const patch = {};
      for (const key of ['session_id', 'generation', 'capture_state', 'last_sequence', 'busy',
        'tracks', 'connection_error', 'retry_after_seconds', 'detection_seconds',
        'classification_seconds', 'guide']) {
        if (key === 'guide' && keepGuide) continue;
        if (Object.prototype.hasOwnProperty.call(incoming, key)) patch[key] = incoming[key];
      }
      if (incoming.capture_state === 'paused' && running) {
        running = false;
        epoch += 1;
        clearScheduled();
        patch.busy = false;
      }
      if (!keepLocalFrame && Object.prototype.hasOwnProperty.call(incoming, 'last_analyzed_frame')) {
        patch.last_analyzed_frame = incoming.last_analyzed_frame;
      }
      // A server snapshot can stop local capture, but only explicit Start can
      // resume it (including after a hidden-page startup was invalidated).
      if (!running && !starting && patch.capture_state === 'running') {
        patch.capture_state = 'paused';
        patch.busy = false;
      }
      emit(patch);
      return current;
    }

    function scheduleNext(runEpoch, extraDelay = 0) {
      if (!running || disposed || runEpoch !== epoch || scheduled !== null || frameBusy) return;
      const elapsed = lastFrameStartedAt === null ? FRAME_INTERVAL_MS
        : Math.max(0, timer.now() - lastFrameStartedAt);
      const cadenceDelay = Math.max(0, FRAME_INTERVAL_MS - elapsed);
      const delay = Math.max(cadenceDelay, extraDelay);
      scheduled = timer.setTimeout(() => {
        scheduled = null;
        void captureAndSend(runEpoch);
      }, delay);
    }

    function retryDelay(error) {
      const serverDelay = Number(current.retry_after_seconds || 0) * 1000;
      if (serverDelay > 0) return Math.max(FRAME_INTERVAL_MS, serverDelay);
      const delay = CLIENT_BACKOFF_MS[Math.min(retryIndex, CLIENT_BACKOFF_MS.length - 1)];
      retryIndex += 1;
      return delay;
    }

    function matchesRequest(result, request) {
      const response = responseState(result);
      const correlation = result && result.correlation;
      if (!response || typeof response !== 'object') return false;
      if (correlation && ((correlation.session_id && correlation.session_id !== request.session_id)
          || (Number.isInteger(correlation.generation) && correlation.generation !== request.generation)
          || (Number.isInteger(correlation.sequence) && correlation.sequence !== request.sequence))) {
        return false;
      }
      return response.session_id === request.session_id
        && response.generation === request.generation
        && response.last_sequence === request.sequence;
    }

    function stopAfterAuthorization(error) {
      running = false;
      epoch += 1;
      clearScheduled();
      emit({
        capture_state: 'paused',
        busy: false,
        error: messageFor(error),
        connection_error: messageFor(error),
      });
      if (camera && typeof camera.stop === 'function') Promise.resolve(camera.stop()).catch(() => {});
    }

    async function captureAndSend(runEpoch) {
      if (!running || disposed || runEpoch !== epoch || frameBusy) return;
      const startedAt = timer.now();
      lastFrameStartedAt = startedAt;
      frameBusy = true;
      let frame = null;
      let retryMs = 0;
      emit({ busy: true, error: null });
      try {
        frame = await camera.capture({ maxSide: 960 });
        if (!running || disposed || runEpoch !== epoch) return;
        if (!frame || frame.jpeg == null) throw new Error('La cámara no entregó un cuadro JPEG.');
        const request = {
          session_id: current.session_id,
          generation: current.generation,
          sequence: sequence + 1,
          jpeg: frame.jpeg,
        };
        if (!request.session_id || !Number.isInteger(request.generation)) {
          throw new Error('La sesión de escaneo no está lista.');
        }
        sequence = request.sequence;
        const guideAtRequest = guideVersion;
        const result = await transport.sendFrame(request);
        if (!running || disposed || runEpoch !== epoch) return;
        if (!matchesRequest(result, request)) {
          const response = responseState(result);
          if (response && response.generation !== current.generation) {
            mergeServerState(response);
          }
          return;
        }

        const response = responseState(result);
        const previousFrame = current.last_analyzed_frame;
        const analyzedFrame = {
          ...frame,
          sequence: request.sequence,
          generation: request.generation,
          session_id: request.session_id,
        };
        const serverError = response.connection_error || null;
        // SSE owns sensor progress delivered while this HTTP result was in
        // transit. The response still owns the analysis of this exact JPEG.
        mergeServerState(response, { keepGuide: guideVersion !== guideAtRequest });
        emit({
          capture_state: 'running',
          last_analyzed_frame: analyzedFrame,
          error: serverError,
        });
        if (previousFrame && previousFrame !== analyzedFrame) releaseFrame(previousFrame);
        if (serverError) {
          retryMs = Math.max(FRAME_INTERVAL_MS, Number(response.retry_after_seconds || 0) * 1000);
        } else {
          retryIndex = 0;
          retryMs = 0;
        }
        frame = null;
      } catch (error) {
        if (running && !disposed && runEpoch === epoch) {
          const status = Number(error && error.status);
          if (status === 401 || status === 409) {
            stopAfterAuthorization(error);
          } else {
            const detail = messageFor(error);
            emit({ connection_error: detail, error: detail });
            retryMs = retryDelay(error);
          }
        }
      } finally {
        if (frame) releaseFrame(frame);
        frameBusy = false;
        if (running && !disposed) {
          emit({ busy: false });
          scheduleNext(epoch, retryMs);
        }
      }
    }

    async function start(options = {}) {
      if (disposed) throw new Error('El controlador de escaneo ya se cerró.');
      if (running) return snapshotForCallback();
      if (startPromise) return startPromise;

      const runEpoch = ++epoch;
      clearScheduled();
      starting = true;
      emit({ capture_state: 'starting', busy: false, error: null });
      startPromise = (async () => {
        let cameraStarted = false;
        try {
          if (typeof camera.start === 'function') {
            await camera.start();
            cameraStarted = true;
          }
          if (disposed || runEpoch !== epoch) return snapshotForCallback();

          const known = typeof transport.getSnapshot === 'function'
            ? responseState(transport.getSnapshot()) : current;
          const sessionId = current.session_id || (known && known.session_id) || null;
          if (options.advanceGeneration || (known && known.capture_state === 'running')) {
            if (!sessionId) throw new Error('No se encontró la sesión que debe recuperarse.');
            const paused = await transport.pause(sessionId);
            if (disposed || runEpoch !== epoch) return snapshotForCallback();
            mergeServerState(paused);
          }
          const started = await transport.start(sessionId || undefined);
          if (disposed || runEpoch !== epoch) {
            const abandoned = responseState(started);
            if (abandoned && abandoned.session_id && typeof transport.pause === 'function') {
              const paused = await transport.pause(abandoned.session_id);
              if (!disposed) mergeServerState(paused, { allowSession: true });
            }
            return snapshotForCallback();
          }
          const server = responseState(started);
          if (!server || server.capture_state !== 'running' || !server.session_id) {
            throw new Error('La estación no inició el escaneo.');
          }
          sequence = 0;
          if (!frameBusy) lastFrameStartedAt = null;
          retryIndex = 0;
          mergeServerState(server, { allowSession: true });
          if (current.session_id !== server.session_id || current.generation !== server.generation) {
            throw new Error('La sesión cambió mientras iniciaba. Vuelve a iniciar el escaneo.');
          }
          running = true;
          emit({ capture_state: 'running', busy: false, error: null, connection_error: null });
          void captureAndSend(runEpoch);
          return snapshotForCallback();
        } catch (error) {
          if (runEpoch === epoch && !disposed) {
            running = false;
            emit({ capture_state: 'paused', busy: false, error: messageFor(error) });
          }
          throw error;
        } finally {
          if (cameraStarted && (!running || disposed || runEpoch !== epoch)
              && typeof camera.stop === 'function') {
            try { await camera.stop(); } catch { /* Invalidated startup must not retain the camera. */ }
          }
          starting = false;
          startPromise = null;
        }
      })();
      return startPromise;
    }

    async function pause() {
      if (disposed) return snapshotForCallback();
      const sessionId = current.session_id;
      const wasActive = running || starting || current.capture_state === 'running';
      running = false;
      starting = false;
      epoch += 1;
      clearScheduled();
      emit({ capture_state: 'paused', busy: false, error: null });
      if (!sessionId || !wasActive || typeof transport.pause !== 'function') return snapshotForCallback();
      try {
        const paused = await transport.pause(sessionId);
        mergeServerState(paused);
        emit({ capture_state: 'paused', busy: false });
        return snapshotForCallback();
      } catch (error) {
        emit({ error: messageFor(error), connection_error: messageFor(error) });
        throw error;
      }
    }

    async function cancel() {
      if (disposed) return snapshotForCallback();
      const sessionId = current.session_id;
      running = false;
      starting = false;
      epoch += 1;
      clearScheduled();
      emit({ capture_state: 'paused', busy: false, error: null });
      if (!sessionId || typeof transport.cancel !== 'function') return snapshotForCallback();
      try {
        const canceled = await transport.cancel(sessionId);
        mergeServerState(canceled);
        emit({ capture_state: 'paused', busy: false });
        return snapshotForCallback();
      } catch (error) {
        emit({ error: messageFor(error), connection_error: messageFor(error) });
        throw error;
      }
    }

    function suspend() {
      if (disposed) return snapshotForCallback();
      running = false;
      starting = false;
      epoch += 1;
      clearScheduled();
      emit({ capture_state: 'paused', busy: false, error: null });
      return snapshotForCallback();
    }

    function syncFromServer(snapshot) {
      if (disposed) return snapshotForCallback();
      const incoming = responseState(snapshot);
      const accepted = incoming && (!current.session_id || !incoming.session_id || incoming.session_id === current.session_id)
        && (!Number.isInteger(incoming.generation) || incoming.generation >= current.generation);
      if (accepted && Object.prototype.hasOwnProperty.call(incoming, 'guide')) guideVersion += 1;
      return mergeServerState(incoming);
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      running = false;
      starting = false;
      epoch += 1;
      clearScheduled();
      if (typeof unsubscribe === 'function') unsubscribe();
      const oldFrame = current.last_analyzed_frame;
      current = { ...current, capture_state: 'paused', busy: false };
      releaseFrame(oldFrame);
      if (camera && typeof camera.stop === 'function') Promise.resolve(camera.stop()).catch(() => {});
    }

    if (typeof transport.subscribe === 'function') {
      unsubscribe = transport.subscribe(syncFromServer);
    }

    return {
      start,
      pause,
      cancel,
      suspend,
      syncFromServer,
      dispose,
      getState: () => snapshotForCallback(),
    };
  }

  function createBrowserTransport({ fetchImpl } = {}) {
    const fetcher = fetchImpl || root.fetch.bind(root);
    let latestScan = null;
    const listeners = new Set();

    async function request(url, options = {}, asBlob = false) {
      const response = await fetcher(url, { credentials: 'same-origin', ...options });
      if (asBlob && response.ok) return response.blob();
      let data = null;
      try { data = await response.json(); } catch { /* respuestas vacías */ }
      if (!response.ok) {
        const error = new Error((data && data.detail) || `Error ${response.status}`);
        error.status = response.status;
        throw error;
      }
      return data;
    }

    const post = (url, body) => request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });

    return {
      getSnapshot: () => latestScan,
      subscribe(listener) {
        listeners.add(listener);
        if (latestScan) listener(latestScan);
        return () => listeners.delete(listener);
      },
      publishSnapshot(snapshot) {
        latestScan = snapshot && snapshot.scan ? snapshot.scan : snapshot;
        for (const listener of listeners) listener(latestScan);
      },
      pair(code) { return post('/api/scan/pair', { code }); },
      start(session_id) { return post('/api/scan/start', session_id ? { session_id } : {}); },
      pause(session_id) { return post('/api/scan/pause', { session_id }); },
      cancel(session_id) { return post('/api/scan/cancel', { session_id }); },
      async sendFrame({ session_id, generation, sequence, jpeg }) {
        const form = new root.FormData();
        form.append('session_id', String(session_id));
        form.append('generation', String(generation));
        form.append('sequence', String(sequence));
        form.append('file', jpeg, 'cuadro.jpg');
        const state = await request('/api/scan/frame', { method: 'POST', body: form });
        // The API echoes `last_sequence` with the scan state rather than a
        // separate correlation object; attach the request values locally.
        return { state, correlation: { session_id, generation, sequence } };
      },
      prepareCorrection({ session_id, track_id, revision }) {
        return post('/api/scan/correction/prepare', { session_id, track_id, revision });
      },
      correctionCrop(correction_id) {
        return request(`/api/scan/correction/${encodeURIComponent(correction_id)}/crop`, {}, true);
      },
      submitCorrection({ correction_id, actual }) {
        return post('/api/scan/correction/submit', { correction_id, actual });
      },
      cancelCorrection(correction_id) {
        return post('/api/scan/correction/cancel', { correction_id });
      },
    };
  }

  function createBrowserCamera(video, { onMessage } = {}) {
    let stream = null;
    let wakeLock = null;

    function report(message) {
      if (typeof onMessage === 'function') onMessage(message);
    }

    function secureContextMessage() {
      return 'Para usar la cámara del teléfono, abre Kiwi mediante HTTPS con un certificado confiable.';
    }

    async function waitForVideo() {
      if (video.readyState >= 2 && video.videoWidth > 0) return;
      await new Promise((resolve, reject) => {
        const timeout = root.setTimeout(() => {
          cleanup();
          reject(new Error('La cámara no entregó imagen. Revisa el permiso y vuelve a iniciar.'));
        }, 5000);
        const ready = () => { cleanup(); resolve(); };
        const failed = () => { cleanup(); reject(new Error('No se pudo iniciar la vista de la cámara.')); };
        const cleanup = () => {
          root.clearTimeout(timeout);
          video.removeEventListener('loadeddata', ready);
          video.removeEventListener('error', failed);
        };
        video.addEventListener('loadeddata', ready, { once: true });
        video.addEventListener('error', failed, { once: true });
      });
    }

    async function start() {
      if (stream && stream.active) return;
      if (!root.isSecureContext) throw new Error(secureContextMessage());
      if (!root.navigator || !root.navigator.mediaDevices
          || typeof root.navigator.mediaDevices.getUserMedia !== 'function') {
        throw new Error('Este navegador no permite abrir la cámara. Usa un navegador actualizado y HTTPS.');
      }
      try {
        stream = await root.navigator.mediaDevices.getUserMedia({
          audio: false,
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1280 },
            height: { ideal: 960 },
            frameRate: { ideal: 24, min: 15, max: 30 },
          },
        });
        video.srcObject = stream;
        video.hidden = false;
        await video.play();
        await waitForVideo();
        report('Cámara trasera activa.');
        if (root.navigator.wakeLock && typeof root.navigator.wakeLock.request === 'function') {
          try { wakeLock = await root.navigator.wakeLock.request('screen'); } catch { wakeLock = null; }
        }
      } catch (error) {
        if (stream) stream.getTracks().forEach(track => track.stop());
        stream = null;
        video.srcObject = null;
        video.hidden = true;
        const name = error && error.name;
        if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
          throw new Error('Permite el acceso a la cámara trasera en el navegador y vuelve a iniciar.');
        }
        if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
          throw new Error('No se encontró una cámara disponible en este dispositivo.');
        }
        if (name === 'NotReadableError' || name === 'TrackStartError') {
          throw new Error('La cámara está ocupada por otra aplicación. Ciérrala y vuelve a iniciar.');
        }
        if (name === 'SecurityError') throw new Error(secureContextMessage());
        throw error;
      }
    }

    async function stop() {
      if (stream) stream.getTracks().forEach(track => track.stop());
      stream = null;
      video.srcObject = null;
      video.hidden = true;
      if (wakeLock) {
        try { await wakeLock.release(); } catch { /* already released */ }
        wakeLock = null;
      }
    }

    async function capture({ maxSide = 960 } = {}) {
      if (!stream || !stream.active || !video.videoWidth || !video.videoHeight) {
        throw new Error('La cámara todavía no está lista.');
      }
      const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
      const width = Math.max(1, Math.round(video.videoWidth * scale));
      const height = Math.max(1, Math.round(video.videoHeight * scale));
      const canvas = root.document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) throw new Error('No se pudo preparar el cuadro de la cámara.');
      context.drawImage(video, 0, 0, width, height);
      const jpeg = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.88));
      if (!jpeg) throw new Error('No se pudo convertir el cuadro a JPEG.');
      const previewUrl = root.URL.createObjectURL(jpeg);
      return {
        jpeg,
        preview: { src: previewUrl, width, height },
        release: () => root.URL.revokeObjectURL(previewUrl),
      };
    }

    return { start, stop, capture, releaseFrame(frame) { if (frame && frame.release) frame.release(); } };
  }

  const api = { createController, createBrowserTransport, createBrowserCamera };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.KiwiScan = api;
})(typeof window !== 'undefined' ? window : globalThis);
