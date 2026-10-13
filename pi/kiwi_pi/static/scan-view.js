(function(root) {
  'use strict';

  const $ = selector => root.document.querySelector(selector);

  function create({ controller, getStatus, getCameraMessage, getBusy, onCorrect } = {}) {
    if (!controller || typeof controller.getState !== 'function') {
      throw new TypeError('controller.getState es obligatorio.');
    }

    function trackDescription(track, slots) {
      const location = track.category
        ? (slots.find(slot => slot.category === track.category) || {}).label
        : null;
      const candidate = (track.recognition && track.recognition.candidate) || track.category;
      const candidateLabel = slots.find(slot => slot.category === candidate)?.label || candidate;
      if (track.state === 'accepted') return `Reconocido · ${location || candidateLabel || 'herramienta'}`;
      if (track.state === 'rejected') return 'No reconocido';
      if (track.state === 'classifying') return 'Clasificando…';
      if (track.state === 'provisional') return `Esperando otra imagen · ${candidateLabel || 'clasificación provisional'}`;
      if (track.state === 'failed') return 'No se pudo clasificar; se espera otro cuadro';
      if (track.state === 'stable') return 'Estable · esperando clasificación';
      return `Esperando estabilidad · ${track.stable_samples || 1} cuadros`;
    }

    function renderFrame(scan, slots) {
      const image = $('#scanAnalyzedImage');
      const empty = $('#scanEmptyFrame');
      const wrap = $('#scanAnalyzedImageWrap');
      const boxes = $('#scanAnalyzedBoxes');
      const frame = scan.last_analyzed_frame;
      if (!frame || !frame.preview || !frame.preview.src) {
        image.hidden = true;
        empty.hidden = false;
        boxes.replaceChildren();
        return;
      }
      if (image.src !== frame.preview.src) image.src = frame.preview.src;
      image.hidden = false;
      empty.hidden = true;
      wrap.style.aspectRatio = `${frame.preview.width} / ${frame.preview.height}`;
      const frameTracks = (scan.tracks || []).filter(track => track.frame_id === frame.sequence);
      boxes.replaceChildren(...frameTracks.map(track => {
        const button = root.document.createElement('button');
        button.type = 'button';
        button.className = `scan-box ${track.state === 'accepted' ? 'accepted' : track.state === 'rejected' ? 'rejected' : ''}`;
        Object.assign(button.style, root.KiwiMultiobject.boxStyle(track.box, [frame.preview.width, frame.preview.height]));
        button.setAttribute('aria-label', `Corregir seguimiento ${track.track_id}: ${trackDescription(track, slots)}`);
        const number = root.document.createElement('span');
        number.textContent = String(track.track_id);
        button.append(number);
        button.addEventListener('click', () => { if (onCorrect) onCorrect(track); });
        return button;
      }));
      $('#scanFrameCaption').textContent = `Cuadro ${frame.sequence} · imagen conservada durante su análisis.`;
    }

    function renderTracks(scan, slots) {
      const frame = scan.last_analyzed_frame;
      const tracks = frame ? (scan.tracks || []).filter(track => track.frame_id === frame.sequence) : [];
      $('#scanTrackCount').textContent = String(tracks.length);
      if (!tracks.length) {
        $('#scanTrackList').replaceChildren(Object.assign(root.document.createElement('li'), {
          className: 'scan-empty-track',
          textContent: scan.capture_state === 'running' ? 'Esperando una herramienta en el encuadre.' : 'Presenta una herramienta dentro del encuadre.',
        }));
        return;
      }
      $('#scanTrackList').replaceChildren(...tracks.map(track => {
        const row = root.document.createElement('li');
        row.className = 'scan-track-row';
        const copy = root.document.createElement('div');
        copy.className = 'scan-track-copy';
        const title = root.document.createElement('strong');
        title.textContent = `Seguimiento ${track.track_id}`;
        const detail = root.document.createElement('span');
        detail.textContent = trackDescription(track, slots);
        copy.append(title, detail);
        const correct = root.document.createElement('button');
        correct.type = 'button';
        correct.textContent = 'Corregir';
        correct.setAttribute('aria-label', `Corregir seguimiento ${track.track_id}`);
        correct.addEventListener('click', () => { if (onCorrect) onCorrect(track); });
        row.append(copy, correct);
        return row;
      }));
    }

    function renderMap(scan, status) {
      const slots = status.slots || [];
      const guide = scan.guide || status.guide || {};
      const targets = new Set((guide.targets || []).map(slot => slot.id));
      const confirmed = new Set(guide.confirmed || []);
      const wrong = new Set(guide.wrong_slots || []);
      const blocked = new Set(guide.blocked || []);
      const map = $('#scanMap');
      const columns = Number(status.columns || 3);
      map.style.setProperty('--cols', columns);
      map.style.setProperty('--rows', Math.ceil(slots.length / columns));
      map.replaceChildren(...slots.map(slot => {
        const cell = root.document.createElement('div');
        cell.className = 'cell';
        cell.textContent = slot.id;
        if (targets.has(slot.id)) {
          cell.classList.add(confirmed.has(slot.id) ? 'ok' : 'target');
          if (!confirmed.has(slot.id) && guide.phase !== 'returned') cell.classList.add('pulse');
        }
        if (wrong.has(slot.id)) cell.classList.add('wrong');
        const occupied = root.document.createElement('span');
        occupied.className = 'occ';
        occupied.dataset.occupied = String(slot.occupied);
        cell.append(occupied);
        const stateText = confirmed.has(slot.id) ? 'confirmada'
          : blocked.has(slot.id) ? 'bloqueada, libera primero'
            : wrong.has(slot.id) ? 'ubicación equivocada'
              : targets.has(slot.id) ? 'pendiente' : 'sin objetivo';
        const occupancy = slot.occupied === true ? 'ocupada' : slot.occupied === false ? 'vacía' : 'sin lectura';
        cell.setAttribute('aria-label', `Posición ${slot.id}: ${slot.label}, ${stateText}, ${occupancy}`);
        return cell;
      }));
      const total = targets.size;
      const done = confirmed.size;
      $('#scanGuideProgress').textContent = guide.operation_id
        ? `${done} de ${total} ubicaciones confirmadas${guide.wrong_slots?.length ? ` · ${guide.wrong_slots.length} error` : ''}`
        : 'Sin devoluciones activas';
      $('#scanGuideNote').textContent = guide.operation_id
        ? guide.verify ? 'Las ubicaciones continúan activas hasta que los sensores confirmen la devolución.'
          : 'Sin sensores: las ubicaciones se muestran, pero la devolución no se verifica.'
        : 'El mapa y las guías se conservan al pausar el escaneo.';
    }

    function render(scan = controller.getState()) {
      if (!scan || !$('#scanStatus')) return;
      const statusSnapshot = (typeof getStatus === 'function' && getStatus()) || {};
      const guide = statusSnapshot.guide || scan.guide || {};
      const status = $('#scanStatus');
      const busy = typeof getBusy === 'function' && getBusy();
      const start = $('#scanStartBtn');
      const pause = $('#scanPauseBtn');
      const cancel = $('#scanCancelBtn');
      const manualGuide = Boolean(guide.operation_id && !guide.continuous);
      start.disabled = busy || scan.capture_state === 'running' || manualGuide;
      start.setAttribute('aria-disabled', String(start.disabled));
      pause.disabled = busy || scan.capture_state !== 'running';
      pause.setAttribute('aria-disabled', String(pause.disabled));
      cancel.disabled = busy || !scan.session_id || !guide.operation_id || !guide.continuous;
      cancel.setAttribute('aria-disabled', String(cancel.disabled));

      status.dataset.tone = scan.error ? 'error' : '';
      if (manualGuide) {
        status.textContent = 'Termina la guía manual antes de iniciar el escaneo continuo.';
      } else if (scan.capture_state === 'starting') {
        status.textContent = 'Preparando la cámara trasera…';
      } else if (scan.capture_state === 'running') {
        if (scan.connection_error) {
          const seconds = Math.ceil(Math.max(1, Number(scan.retry_after_seconds || 1)));
          status.textContent = `Colab no respondió. Se conserva el progreso y se volverá a intentar en ${seconds} s.`;
          status.dataset.tone = 'error';
        } else if (statusSnapshot.api?.online === false) {
          status.textContent = 'Colab no está disponible. Las devoluciones siguen activas y no se acumulan cuadros.';
          status.dataset.tone = 'error';
        } else {
          status.textContent = scan.busy ? 'Escaneo activo · analizando el cuadro actual…'
            : 'Escaneo activo · la estación analiza como máximo un cuadro por segundo.';
        }
      } else if (scan.error) {
        status.textContent = scan.error;
      } else if (guide.operation_id && guide.continuous) {
        status.textContent = 'Escaneo pausado · las ubicaciones y su progreso siguen activos.';
      } else {
        status.textContent = 'Escaneo pausado. Empareja el teléfono y toca «Iniciar escaneo».';
      }

      $('#scanCameraPrompt').hidden = Boolean($('#scanVideo').srcObject);
      const cameraMessage = typeof getCameraMessage === 'function' && getCameraMessage();
      if (cameraMessage && scan.capture_state !== 'running') $('#scanFrameCaption').textContent = cameraMessage;
      const slots = statusSnapshot.slots || [];
      renderFrame(scan, slots);
      renderTracks(scan, slots);
      renderMap(scan, statusSnapshot);
    }

    return { render };
  }

  root.KiwiScanView = { create };
})(typeof window !== 'undefined' ? window : globalThis);
