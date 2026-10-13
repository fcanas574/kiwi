'use strict';

// ToolLighthouse · flujo de la pantalla táctil: Captura → Revisión → Resultado.
// La Raspberry Pi es la fuente de verdad del estado de LEDs, sensores y API (eventos SSE).

const $ = (selector) => document.querySelector(selector);
const app = $('#app');
const fmt = new Intl.NumberFormat('es', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const state = {
  status: null,       // última instantánea del servidor
  photo: null,        // { photo_id, url, source }
  result: null,       // respuesta de /api/recognize
  screen: 'capture',
  mediaStream: null,
  recognitionVersion: 0,
  recognizing: false,
};

// ---------- utilidades ----------
async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch { /* sin cuerpo */ }
  if (!response.ok) throw new Error((data && data.detail) || `Error ${response.status}`);
  return data;
}
const postJSON = (path, body) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
});

let toastTimer;
function toast(message, ms = 3200) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

function setLoading(button, loading) {
  button.classList.toggle('loading', loading);
  button.disabled = loading;
}

function go(screen) {
  state.screen = screen;
  app.dataset.screen = screen;
  if (screen === 'capture') startBrowserCamera();
  else stopBrowserCamera();
}

// ---------- estado del servidor ----------
function applyStatus(snapshot) {
  state.status = { ...(state.status || {}), ...snapshot };
  const s = state.status;

  const pill = $('#apiPill');
  const simulated = s.drivers && s.drivers.recognizer === 'mock';
  pill.dataset.online = String(Boolean(s.api && s.api.online));
  $('#apiText').textContent = simulated ? 'API simulada' : (s.api && s.api.online ? 'API en línea' : 'API sin conexión');
  pill.title = (s.api && s.api.detail) || '';

  const input = $('#endpointInput');
  if (document.activeElement !== input) {
    input.value = simulated ? 'Simulación local (sin Colab)' : ((s.api && s.api.url) || '');
  }
  input.readOnly = !s.admin || simulated;

  if (state.screen === 'result') renderGuide();
  if (!$('#diag').hidden) renderDiag();
}

async function loadStatus() {
  try {
    applyStatus(await api('/api/status'));
  } catch {
    toast('No se pudo conectar con la estación.');
  }
}

function connectEvents() {
  const source = new EventSource('/api/events');
  source.onmessage = (event) => applyStatus(JSON.parse(event.data));
  source.onerror = () => { $('#apiText').textContent = 'Reconectando…'; };
  source.onopen = () => loadStatus();
}

// ---------- cámara ----------
function setupViewer() {
  const viewer = $('#viewer');
  const img = $('#streamImg');
  if (state.status && state.status.camera_available) {
    img.src = '/api/camera/stream';
    img.hidden = false;
    img.onload = () => viewer.classList.add('live');
    img.onerror = () => { viewer.classList.remove('live'); img.hidden = true; };
    return;
  }
  startBrowserCamera();
}

async function startBrowserCamera() {
  if (state.status && state.status.camera_available) return;
  if (state.mediaStream) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    // Teléfono por http://: el navegador no permite vista previa, pero «Tomar foto» abre la cámara nativa.
    $('#viewerCaption').textContent = 'Toca «Tomar foto» para abrir la cámara';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false,
    });
    if (state.screen !== 'capture') { stream.getTracks().forEach((t) => t.stop()); return; }
    state.mediaStream = stream;
    const video = $('#video');
    video.srcObject = stream;
    video.hidden = false;
    $('#viewer').classList.add('live');
  } catch {
    $('#viewerCaption').textContent = 'Toca «Tomar foto» para abrir la cámara';
  }
}

function stopBrowserCamera() {
  if (!state.mediaStream) return;
  state.mediaStream.getTracks().forEach((t) => t.stop());
  state.mediaStream = null;
  $('#video').hidden = true;
  $('#viewer').classList.remove('live');
}

function flash() {
  const viewer = $('#viewer');
  viewer.classList.remove('flash');
  void viewer.offsetWidth;
  viewer.classList.add('flash');
}

async function blobFromVideo(video) {
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
}

async function shrink(file, maxSide = 1600) {
  // Las fotos de celular pesan varios MB: se reducen antes de enviarlas a la Pi.
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 2_000_000) return file;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
  } catch {
    return file;
  }
}

async function uploadBlob(blob, source) {
  const form = new FormData();
  form.append('file', blob, 'foto.jpg');
  return api(`/api/upload?source=${encodeURIComponent(source)}`, { method: 'POST', body: form });
}

async function withCaptureBusy(task) {
  const busy = $('#captureBusy');
  const buttons = [$('#takeBtn'), $('#uploadBtn')];
  busy.hidden = false;
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const photo = await task();
    if (photo) await afterPhoto(photo);
  } catch (error) {
    toast(error.message);
  } finally {
    busy.hidden = true;
    buttons.forEach((b) => { b.disabled = false; });
  }
}

function takePhoto() {
  const s = state.status || {};
  if (s.camera_available) {
    flash();
    return withCaptureBusy(() => postJSON('/api/capture'));
  }
  const video = $('#video');
  if (state.mediaStream && video.videoWidth) {
    flash();
    return withCaptureBusy(async () => uploadBlob(await blobFromVideo(video), 'browser-camera'));
  }
  $('#cameraInput').click();   // celular sin HTTPS: abre la cámara nativa
  return null;
}

function onFilePicked(input, source) {
  const file = input.files && input.files[0];
  input.value = '';
  if (!file) return;
  withCaptureBusy(async () => uploadBlob(await shrink(file), source));
}

async function afterPhoto(photo) {
  state.recognitionVersion += 1;
  state.photo = photo;
  state.result = null;
  $('#photoImg').src = photo.url;
  $('#photoSource').textContent = photo.source === 'upload' ? 'Desde tus archivos' : 'Desde la cámara';
  if (state.status && state.status.auto_send) {
    go('review');
    await send();
  } else {
    go('review');
  }
}

// ---------- reconocimiento ----------
async function send() {
  if (!state.photo) return;
  const photoId = state.photo.photo_id;
  const request = ++state.recognitionVersion;
  state.recognizing = true;
  const button = $('#sendBtn');
  setLoading(button, true);
  button.querySelector('.label').textContent = 'Identificando…';
  try {
    const result = await postJSON('/api/recognize', { photo_id: photoId });
    if (request !== state.recognitionVersion || state.photo?.photo_id !== photoId) return;
    state.result = result;
    renderResult();
    go('result');
  } catch (error) {
    if (request === state.recognitionVersion) toast(error.message);
  } finally {
    if (request === state.recognitionVersion) {
      state.recognizing = false;
      setLoading(button, false);
      button.querySelector('.label').textContent = 'Identificar herramientas';
    }
  }
}

function renderResult() {
  const r = state.result;
  const kind = r.status === 'accepted' ? 'accepted' : r.status === 'error' ? 'error' : 'rejected';
  $('#screen-result').dataset.kind = kind;
  $('#testChip').hidden = !r.test_mode;
  const accepted = (r.objects || []).filter(o => o.status === 'accepted').length;
  $('#cardLabel').textContent = kind === 'error' ? 'Sin respuesta' : 'Objetos identificados';
  $('#cardTitle').textContent = kind === 'error' ? 'Revisa la conexión' : `${accepted} herramientas aceptadas`;
  $('#cardTitle').classList.add('long');
  $('#cardNote').textContent = kind === 'error' ? r.reason : `${(r.objects || []).length - accepted} para revisar. Los sensores verifican presencia por ubicación.`;
  $('#cardNote').hidden = false;
  $('#nextLabel').textContent = kind === 'error' ? 'Reintentar' : 'Nueva foto';
  $('#wrongBtn').textContent = 'Repetir foto';
  const showPhoto = !!r.image_size && !!state.photo;
  $('#detectionPhoto').hidden = !showPhoto;
  $('#objectBoxes').replaceChildren();
  $('#objectList').replaceChildren();
  if (showPhoto) {
    $('#resultPhoto').src = state.photo.url;
    $('#detectionPhoto').style.aspectRatio = r.image_size.join(' / ');
  }
  for (const object of r.objects || []) {
    if (showPhoto) {
      const box = document.createElement('button');
      box.type = 'button';
      box.className = `object-box ${object.status}`;
      Object.assign(box.style, KiwiMultiobject.boxStyle(object.box, r.image_size));
      box.setAttribute('aria-label', `Corregir objeto ${object.id}: ${object.label}`);
      const number = document.createElement('span');
      number.textContent = object.id;
      box.append(number);
      box.onclick = () => openLabelSheet(object);
      $('#objectBoxes').append(box);
    }
    const row = document.createElement('li');
    row.className = `object-row ${object.status}`;
    const text = document.createElement('div');
    const label = document.createElement('strong');
    label.textContent = `${object.id}. ${object.label}`;
    const detail = document.createElement('p');
    detail.textContent = object.status === 'accepted' ? object.slot.location : `Revisar: ${object.reason}`;
    text.append(label, detail);
    const correct = document.createElement('button');
    correct.type = 'button';
    correct.textContent = 'Corregir';
    correct.setAttribute('aria-label', `Corregir objeto ${object.id}: ${object.label}`);
    correct.onclick = () => openLabelSheet(object);
    row.append(text, correct);
    $('#objectList').append(row);
  }
  renderGuide();
}

function renderGuide() {
  const r = state.result, s = state.status;
  if (!r || !s) return;
  const guide = s.guide || {};
  const progress = KiwiMultiobject.guideProgress(r, s);
  const map = $('#map');
  map.style.setProperty('--cols', s.columns || 3);
  map.style.setProperty('--rows', Math.ceil(s.slots.length / (s.columns || 3)));
  const targets = new Set((r.slots || []).map(slot => slot.id));
  const confirmed = new Set(progress ? guide.confirmed : []);
  const wrong = new Set(progress ? guide.wrong_slots : []);
  map.replaceChildren(...s.slots.map(slot => {
    const cell = document.createElement('div');
    cell.className = 'cell'; cell.textContent = slot.id;
    if (progress && targets.has(slot.id)) {
      cell.classList.add(confirmed.has(slot.id) ? 'ok' : 'target');
      if (!confirmed.has(slot.id) && guide.phase !== 'timeout') cell.classList.add('pulse');
    }
    if (wrong.has(slot.id)) cell.classList.add('wrong');
    const occ = document.createElement('span');
    occ.className = 'occ'; occ.dataset.occupied = String(slot.occupied);
    cell.append(occ);
    cell.setAttribute('aria-label', `Posición ${slot.id}: ${slot.label}, ${slot.led}, ${slot.occupied ? 'ocupada' : 'vacía'}`);
    return cell;
  }));
  const status = $('#statusText');
  status.dataset.tone = '';
  if (r.status !== 'accepted') {
    $('#locationText').textContent = '';
    status.textContent = r.reason || 'Repite la foto.';
    return;
  }
  if (!progress) {
    if (!guide.operation_id && guide.phase === 'idle' && state._wasGuiding) resetToCapture();
    else status.textContent = 'Esta foto ya no tiene una guía activa.';
    return;
  }
  state._wasGuiding = true;
  $('#locationText').textContent = `${progress.confirmed} de ${progress.total} ubicaciones confirmadas`;
  if (progress.complete) {
    status.dataset.tone = 'ok';
    status.textContent = '¡Todas devueltas! Volviendo al inicio…';
  } else if (progress.phase === 'timeout') {
    status.textContent = `Se apagaron las luces. Quedan pendientes las posiciones ${progress.pending.join(', ')}.`;
  } else if (progress.wrong_slots.length) {
    status.dataset.tone = 'warn';
    status.textContent = `Retira los objetos de las posiciones ${progress.wrong_slots.join(', ')} y usa las luces azules.`;
  } else if (progress.blocked.length) {
    status.textContent = `Las posiciones ${progress.blocked.join(', ')} ya estaban ocupadas. Libéralas antes de devolver las herramientas.`;
  } else {
    status.textContent = guide.verify ? 'Devuelve las herramientas en las luces azules. Cada ubicación pasará a verde.' : 'Sin sensores: las luces indican los lugares, pero la devolución no está verificada.';
  }
}

async function primaryResultAction() {
  const r = state.result;
  if (r && r.status === 'error') return retrySend();
  const button = $('#nextBtn');
  setLoading(button, true);
  try {
    await postJSON('/api/next', {photo_id: state.photo?.photo_id});
  } catch (error) {
    toast(error.message);
  } finally {
    setLoading(button, false);
    resetToCapture();
  }
}

async function retrySend() {
  if (!state.photo) return;
  const photoId = state.photo.photo_id;
  const request = ++state.recognitionVersion;
  state.recognizing = true;
  const button = $('#nextBtn');
  setLoading(button, true);
  try {
    const result = await postJSON('/api/recognize', { photo_id: photoId });
    if (request !== state.recognitionVersion || state.photo?.photo_id !== photoId) return;
    state.result = result;
    renderResult();
  } catch (error) {
    if (request === state.recognitionVersion) toast(error.message);
  } finally {
    if (request === state.recognitionVersion) {
      state.recognizing = false;
      setLoading(button, false);
    }
  }
}

async function secondaryResultAction() {
  try { await postJSON('/api/next', {photo_id: state.photo?.photo_id}); }
  catch (error) { toast(error.message); return; }
  resetToCapture();
}

function openLabelSheet(object) {
  state.correctionObjectId = object.id;
  state.correctionFocus = document.activeElement;
  $('#labelSheetTitle').textContent = `¿Qué era el objeto ${object.id}?`;
  const options = $('#labelOptions');
  const slots = (state.status && state.status.slots) || [];
  const buttons = slots
    .filter((slot) => !state.result || slot.category !== object.category)
    .map((slot) => [slot.label, slot.category, '']);
  buttons.push(['No es una herramienta del taller', 'otros', 'other']);
  options.replaceChildren(...buttons.map(([label, category, cls]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    if (cls) button.className = cls;
    button.onclick = () => submitCorrection(category);
    return button;
  }));
  $('#labelSheet').hidden = false;
  $('#labelOptions').querySelector('button')?.focus();
}

async function submitCorrection(actual) {
  $('#labelSheet').hidden = true;
  try {
    await postJSON('/api/reject', { photo_id: state.photo.photo_id, object_id: state.correctionObjectId, actual });
    toast('Gracias. Guardamos el recorte para mejorar el reconocimiento.');
    resetToCapture();
  } catch (error) {
    toast(error.message);
    $('#labelSheet').hidden = false;
  }
}

function closeLabelSheet() {
  $('#labelSheet').hidden = true;
  state.correctionFocus?.focus();
}

function resetToCapture() {
  const wasRecognizing = state.recognizing;
  const canceledPhotoId = state.photo?.photo_id;
  state.recognitionVersion += 1;
  state.recognizing = false;
  setLoading($('#sendBtn'), false);
  $('#sendBtn').querySelector('.label').textContent = 'Identificar herramientas';
  setLoading($('#nextBtn'), false);
  if (wasRecognizing) postJSON('/api/next', {photo_id: canceledPhotoId}).catch((error) => toast(error.message));
  state.result = null;
  state.photo = null;
  state._wasGuiding = false;
  go('capture');
}

// ---------- endpoint ----------
async function saveEndpoint() {
  const input = $('#endpointInput');
  if (input.readOnly) return;
  const url = input.value.trim();
  if (!url || url === (state.status.api && state.status.api.url)) return;
  try {
    await api('/api/endpoint', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }),
    });
    toast('Endpoint actualizado.');
  } catch (error) {
    toast(error.message);
    input.value = state.status.api.url;
  }
}

// ---------- diagnóstico ----------
function renderDiag() {
  const s = state.status;
  if (!s) return;
  const sensorText = (v) => (v === null || v === undefined ? '—' : v ? 'Ocupada' : 'Vacía');
  const drivers = [
    ['API', `${s.api.online ? 'En línea' : 'Sin conexión'} · ${s.api.detail || ''}`],
    ['Modo del modelo', `${s.api.mode || '—'}${s.api.calibrated === false ? ' · sin calibrar' : ''}`],
    ['Reconocimiento', s.drivers.recognizer],
    ['Cámara', s.drivers.camera === 'none' ? 'del navegador' : s.drivers.camera],
    ['LEDs', s.drivers.leds],
    ['Sensores', s.drivers.sensors],
    ['Guía', `${s.guide.phase} · ${(s.guide.confirmed || []).length} ubicaciones confirmadas`],
    ['Modo prueba', s.test_mode ? 'activado' : 'no'],
  ];
  $('#diagDrivers').replaceChildren(...drivers.map(([k, v]) => {
    const wrap = document.createElement('div');
    const dt = document.createElement('dt');
    const dd = document.createElement('dd');
    dt.textContent = k;
    dd.textContent = v;
    wrap.append(dt, dd);
    return wrap;
  }));

  const canSim = s.admin && s.drivers.sensors === 'mock';
  $('#diagSlots').replaceChildren(...s.slots.map((slot) => {
    const tr = document.createElement('tr');
    const cells = [slot.id, slot.label, sensorText(slot.occupied)];
    cells.forEach((value) => {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    });
    const led = document.createElement('td');
    const dot = document.createElement('span');
    dot.className = 'led';
    dot.dataset.mode = slot.led;
    led.append(dot, slot.led);
    tr.append(led);
    const action = document.createElement('td');
    if (canSim) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = slot.occupied ? 'Retirar' : 'Colocar';
      button.onclick = () => postJSON(`/api/sim/slots/${slot.id}`, { occupied: !slot.occupied }).catch((e) => toast(e.message));
      action.append(button);
    }
    tr.append(action);
    return tr;
  }));

  $('#ledTestBtn').disabled = !s.admin;
  $('#ledOffBtn').disabled = !s.admin;
  const force = $('#forceSelect');
  $('#forceWrap').hidden = !(s.admin && s.drivers.recognizer === 'mock');
  if (force.options.length === 1) {
    s.slots.forEach((slot) => force.add(new Option(slot.label, slot.category)));
    force.add(new Option('Objeto ajeno a la estación', 'capacitors'));
  }
}

function openDiag() {
  renderDiag();
  $('#diag').hidden = false;
}

function bindLongPress(element, callback, ms = 800) {
  let timer;
  const start = () => { timer = setTimeout(callback, ms); };
  const cancel = () => clearTimeout(timer);
  element.addEventListener('pointerdown', start);
  ['pointerup', 'pointerleave', 'pointercancel'].forEach((e) => element.addEventListener(e, cancel));
}

// ---------- eventos de la interfaz ----------
$('#takeBtn').addEventListener('click', takePhoto);
$('#uploadBtn').addEventListener('click', () => $('#fileInput').click());
$('#fileInput').addEventListener('change', (e) => onFilePicked(e.target, 'upload'));
$('#cameraInput').addEventListener('change', (e) => onFilePicked(e.target, 'browser-camera'));
$('#sendBtn').addEventListener('click', send);
$('#retakeBtn').addEventListener('click', resetToCapture);
$('#nextBtn').addEventListener('click', primaryResultAction);
$('#wrongBtn').addEventListener('click', secondaryResultAction);
$('#endpointInput').addEventListener('change', saveEndpoint);
$('#endpointInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
$('#diagClose').addEventListener('click', () => { $('#diag').hidden = true; });
$('#labelCancel').addEventListener('click', closeLabelSheet);
document.addEventListener('keydown', (event) => {
  if ($('#labelSheet').hidden) return;
  if (event.key === 'Escape') { event.preventDefault(); closeLabelSheet(); }
  if (event.key === 'Tab') {
    const buttons = [...$('#labelSheet').querySelectorAll('button')];
    const index = buttons.indexOf(document.activeElement);
    if (event.shiftKey && index <= 0) { event.preventDefault(); buttons.at(-1).focus(); }
    else if (!event.shiftKey && index === buttons.length - 1) { event.preventDefault(); buttons[0].focus(); }
  }
});
$('#ledTestBtn').addEventListener('click', () => postJSON('/api/leds/test').catch((e) => toast(e.message)));
$('#ledOffBtn').addEventListener('click', () => postJSON('/api/leds/off').catch((e) => toast(e.message)));
$('#forceSelect').addEventListener('change', (e) => {
  postJSON(`/api/sim/recognizer?category=${encodeURIComponent(e.target.value)}`).catch((err) => toast(err.message));
});
$('#brand').addEventListener('click', openDiag);
document.addEventListener('contextmenu', (e) => e.preventDefault());
if (new URLSearchParams(location.search).has('diag')) setTimeout(openDiag, 300);

(async function init() {
  await loadStatus();
  setupViewer();
  connectEvents();
})();
