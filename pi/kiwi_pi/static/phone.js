'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let stream, transmitting = false, timer, wakeLock;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function request(url, options) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.detail || 'No se pudo conectar.'); error.status = response.status; throw error; }
    return data;
  }
  async function keepAwake() {
    try { if (navigator.wakeLock && !document.hidden) wakeLock = await navigator.wakeLock.request('screen'); } catch (_) {}
  }
  function showPair(message = '') {
    transmitting = false;
    $('connect').hidden = false;
    $('activate').hidden = true;
    $('message').textContent = message;
    $('status').textContent = 'Esperando conexión con la Raspberry';
    $('status').classList.remove('connected');
  }
  async function activate() {
    try {
      if (!window.isSecureContext) throw new Error('Abre Kiwi con HTTPS y el certificado instalado.');
      if (!stream || stream.getVideoTracks().every(track => track.readyState === 'ended')) {
        stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
          facingMode: {ideal: 'environment'}, width: {ideal: 960}, height: {ideal: 720}, frameRate: {ideal: 15, max: 24}}});
        $('camera').srcObject = stream;
      }
      await $('camera').play();
      $('activate').hidden = true;
      await keepAwake();
      if (!transmitting) { transmitting = true; sendFrames(); }
    } catch (error) {
      $('status').textContent = error.name === 'NotAllowedError' ? 'Permite la cámara en Safari para conectar.' : error.message;
      $('status').classList.remove('connected');
      $('activate').hidden = false;
    }
  }
  async function sendFrames() {
    const canvas = $('buffer'), video = $('camera');
    while (transmitting) {
      if (document.hidden || !video.videoWidth || video.readyState < 2) { await sleep(500); continue; }
      const scale = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
      canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.75));
      try {
        if (!blob) throw new Error('Esperando imagen de la cámara.');
        const body = new FormData(); body.append('file', blob, 'camera.jpg');
        const abort = new AbortController(); timer = setTimeout(() => abort.abort(), 5000);
        await request('/api/scan/webcam/frame', {method: 'POST', body, signal: abort.signal});
        $('status').textContent = 'Cámara conectada · usa los controles de la Raspberry';
        $('status').classList.add('connected');
      } catch (error) {
        if (error.status === 401) { showPair('Vuelve a conectar la cámara con el código.'); break; }
        $('status').textContent = error.name === 'AbortError' ? 'Reconectando con la Raspberry…' : error.message;
        $('status').classList.remove('connected');
        await sleep(1000);
      } finally { clearTimeout(timer); }
      await sleep(300);
    }
  }
  $('pair').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      await request('/api/scan/pair', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: $('code').value.trim()})});
      $('code').value = ''; $('connect').hidden = true; await activate();
    } catch (error) { $('message').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('activate').addEventListener('click', activate);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && $('connect').hidden) { keepAwake(); activate(); }
  });
  request('/api/scan/webcam/state').then(activate).catch(error => showPair(error.status === 401 ? '' : 'No se pudo conectar con la Raspberry.'));
})();
