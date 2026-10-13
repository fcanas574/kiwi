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
      button.querySelector('.label').textContent = 'Enviar a la API';
    }
  }
}

function renderResult() {
  const r = state.result;
  const screen = $('#screen-result');
  const kind = r.status === 'accepted' ? 'accepted' : r.status === 'error' ? 'error' : 'rejected';
  screen.dataset.kind = kind;
  $('#testChip').hidden = !r.test_mode;

  const note = $('#cardNote');
  note.hidden = true;
  if (kind === 'accepted') {
    $('#cardLabel').textContent = 'Herramienta reconocida';
    $('#cardTitle').textContent = r.label;
    $('#locationText').innerHTML = '';
    r.slot.location.split(/\s*[·—-]\s*/).forEach((line, i) => {
      if (i) $('#locationText').append(document.createElement('br'));
      $('#locationText').append(line);
    });
    $('#nextLabel').textContent = 'Siguiente herramienta';
    $('#wrongBtn').textContent = 'No es correcta';
  } else if (kind === 'rejected') {
    $('#cardLabel').textContent = 'Herramienta no reconocida';
    $('#cardTitle').textContent = 'Prueba otra foto';
    const best = r.ranking && r.ranking[0];
    note.textContent = best ? `Mejor candidato: ${best.label} · similitud ${fmt.format(best.similarity)}` : '';
    note.hidden = !best;
    $('#nextLabel').textContent = 'Repetir foto';
    $('#wrongBtn').textContent = 'Volver al inicio';
  } else {
    $('#cardLabel').textContent = 'Sin respuesta de la API';
    $('#cardTitle').textContent = 'Revisa la conexión';
    $('#nextLabel').textContent = 'Reintentar';
    $('#wrongBtn').textContent = 'Repetir foto';
  }

  // La similitud de EmbeddingGemma no es un porcentaje de confianza: se muestra tal cual.
  const similarity = typeof r.similarity === 'number' ? r.similarity : null;
  $('#meterFill').style.width = `${Math.max(0, Math.min(1, similarity || 0)) * 100}%`;
  $('#meterValue').textContent = similarity === null ? '—' : fmt.format(similarity);
  $('#meter').title = 'Similitud entre la foto y las referencias (no es un porcentaje de confianza)';

  $('#cardTitle').classList.toggle('long', $('#cardTitle').textContent.length > 14);
  renderGuide();
}

function renderGuide() {
  const r = state.result;
  const s = state.status;
  if (!r || !s) return;
  const map = $('#map');
  const guide = s.guide || {};
  const accepted = r.status === 'accepted';
  const targetId = accepted ? r.slot.id : null;
  const ours = accepted && guide.target && guide.target.id === targetId;

  const cols = s.columns || 3;
  map.style.setProperty('--cols', cols);
  map.style.setProperty('--rows', Math.ceil(s.slots.length / cols));
  map.replaceChildren(...s.slots.map((slot) => {
    const cell = document.createElement('div');
    cell.className = 'cell';
    cell.textContent = slot.id;
    if (ours && slot.id === targetId) {
      cell.classList.add(guide.phase === 'returned' ? 'ok' : 'target');
      if (guide.phase === 'guiding') cell.classList.add('pulse');
    }
    if (ours && guide.phase === 'wrong_slot' && slot.id === guide.wrong) cell.classList.add('wrong');
    const occ = document.createElement('span');
    occ.className = 'occ';
    occ.dataset.occupied = String(slot.occupied);
    occ.title = slot.occupied === null ? '' : slot.occupied ? 'Ocupada' : 'Vacía';
    cell.append(occ);
    cell.setAttribute('aria-label', `Posición ${slot.id}: ${slot.label}${slot.occupied ? ', ocupada' : ''}`);
    return cell;
  }));

  const status = $('#statusText');
  status.dataset.tone = '';
  if (!accepted) {
    status.textContent = r.reason || '';
    return;
  }
  const n = targetId;
  let text = `El LED ${n} ya está encendido.`;
  let detail = '';
  if (!ours && state._wasGuiding) {
    resetToCapture();   // la guía terminó (devuelta y reinicio automático) o se canceló
    return;
  }
  if (ours) state._wasGuiding = true;
  switch (ours ? guide.phase : 'idle') {
    case 'guiding':
      detail = guide.verify ? 'Colócala en su lugar: los sensores confirmarán la devolución.' : '';
      if (guide.verify && guide.target_was_occupied) detail = `La posición ${n} aparece ocupada. ¿Hay otro objeto?`;
      break;
    case 'wrong_slot':
      status.dataset.tone = 'warn';
      text = `Esa es la posición ${guide.wrong}. Va en la ${n}.`;
      detail = 'Retírala y colócala donde está la luz azul.';
      break;
    case 'returned':
      status.dataset.tone = 'ok';
      text = '¡Devuelta en su lugar!';
      detail = 'Volviendo al inicio…';
      break;
    case 'timeout':
      text = `No se detectó la devolución. Se apagó el LED ${n}.`;
      break;
    default:
      break;
  }
  status.textContent = text;
  if (detail) {
    const small = document.createElement('small');
    small.textContent = detail;
    status.append(small);
  }
}

async function primaryResultAction() {
  const r = state.result;
  if (r && r.status === 'error') return retrySend();
  const button = $('#nextBtn');
  setLoading(button, true);
  try {
    await postJSON('/api/next');
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
  const r = state.result;
  if (r && r.status === 'accepted') {
    openLabelSheet();   // guarda la foto con su etiqueta real antes de volver al inicio
    return;
  }
  resetToCapture();
}

function openLabelSheet() {
  const options = $('#labelOptions');
  const slots = (state.status && state.status.slots) || [];
  const buttons = slots
    .filter((slot) => !state.result || slot.category !== state.result.category)
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
}

async function submitCorrection(actual) {
  $('#labelSheet').hidden = true;
  try {
    await postJSON('/api/reject', { photo_id: state.photo.photo_id, actual });
    toast('Gracias. Guardamos la foto para mejorar el reconocimiento.');
  } catch (error) {
    toast(error.message);
  }
  resetToCapture();
}

function resetToCapture() {
  const wasRecognizing = state.recognizing;
  state.recognitionVersion += 1;
  state.recognizing = false;
  setLoading($('#sendBtn'), false);
  $('#sendBtn').querySelector('.label').textContent = 'Enviar a la API';
  setLoading($('#nextBtn'), false);
  if (wasRecognizing) postJSON('/api/next').catch((error) => toast(error.message));
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
    ['Guía', `${s.guide.phase}${s.guide.target ? ` · posición ${s.guide.target.id}` : ''}`],
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
$('#labelCancel').addEventListener('click', () => { $('#labelSheet').hidden = true; });
$('#ledTestBtn').addEventListener('click', () => postJSON('/api/leds/test').catch((e) => toast(e.message)));
$('#ledOffBtn').addEventListener('click', () => postJSON('/api/leds/off').catch((e) => toast(e.message)));
$('#forceSelect').addEventListener('change', (e) => {
  postJSON(`/api/sim/recognizer?category=${encodeURIComponent(e.target.value)}`).catch((err) => toast(err.message));
});
bindLongPress($('#brand'), openDiag);
document.addEventListener('contextmenu', (e) => e.preventDefault());
if (new URLSearchParams(location.search).has('diag')) setTimeout(openDiag, 300);

(async function init() {
  await loadStatus();
  setupViewer();
  connectEvents();
})();
