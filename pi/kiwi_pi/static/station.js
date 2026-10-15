'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let state, mode = 'live', loading = false, loadedKey = '', pending = false;
  const labels = {observing:'Observando',stable:'Objeto estable',classifying:'Identificando',provisional:'Verificando',accepted:'Aceptado',rejected:'No reconocido',failed:'Reintentando'};
  async function api(url, options) {
    const response = await fetch(url, {cache:'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'No se pudo completar la acción.');
    return data;
  }
  function drawBoxes(webcam) {
    const svg = $('boxes'); svg.replaceChildren();
    if (mode !== 'analysis' || !webcam.analysis_size) return;
    const [width, height] = webcam.analysis_size;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    for (const track of webcam.analysis_tracks || []) {
      if (!track.box) continue;
      const [x1,y1,x2,y2] = track.box;
      const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
      for (const [key,value] of Object.entries({x:x1,y:y1,width:x2-x1,height:y2-y1,fill:'none',stroke:track.state==='accepted'?'#31e083':'#006dff','stroke-width':4})) rect.setAttribute(key,value);
      svg.append(rect);
    }
  }
  function image() {
    if (!state || loading) return;
    const webcam = state.webcam;
    const revision = mode === 'live' ? webcam.revision : webcam.analysis_revision;
    const available = mode === 'live' ? webcam.online : Boolean(webcam.analysis_size);
    if (!available) {
      $('frame').hidden = true; $('empty').hidden = false; $('boxes').replaceChildren();
      $('empty').textContent = mode === 'live' ? 'Esperando la cámara del iPhone' : 'El análisis aparecerá al escanear';
      loadedKey = ''; return;
    }
    const key = `${mode}:${revision}`;
    if (key === loadedKey) return;
    loading = true;
    const candidate = new Image();
    candidate.onload = () => { loading=false; if (key.split(':')[0] !== mode) { image(); return; }
      if (mode === 'analysis' && state.webcam.analysis_revision !== revision) { image(); return; }
      $('frame').src=candidate.src; $('frame').hidden=false; $('empty').hidden=true; loadedKey=key; drawBoxes(webcam); };
    candidate.onerror = () => { loading=false; };
    candidate.src = `/api/station/webcam/${mode}.jpg?v=${revision}`;
  }
  function render(data) {
    state = data; const running = data.scan.capture_state === 'running', online = data.webcam.online;
    $('cameraStatus').textContent = online ? 'Cámara conectada' : 'Cámara desconectada';
    $('cameraStatus').classList.toggle('online', online);
    $('apiStatus').textContent = data.api.online ? (data.api.mode==='mlx_dino_qwen' ? 'Mac en línea' : 'API en línea') : 'API desconectada';
    $('apiStatus').classList.toggle('online', data.api.online);
    $('start').textContent = running ? 'Pausar análisis' : 'Iniciar escaneo';
    $('start').classList.toggle('running', running);
    $('start').disabled = pending || (!running && (!online || !data.api.online));
    $('finish').disabled = pending;
    $('mode').textContent = data.scan.mode === 'single' ? 'Rápido · 1' : 'Varios';
    $('mode').disabled = pending || running;
    $('desktop').hidden = !data.desktop_exit_enabled;
    $('phase').textContent = running ? (data.scan.busy ? 'Analizando objetos…' : 'Escaneo activo') : 'Escaneo en pausa';
    $('slots').replaceChildren(...data.slots.map(slot => {
      const element = document.createElement('div'); element.className='slot'+(slot.led!=='off'?' lit':'');
      const number = document.createElement('b'); number.textContent=slot.id;
      const head = document.createElement('div'); head.className='slot-head'; head.append(number);
      if(slot.led_remaining != null && slot.led!=='off') {
        const count=document.createElement('span');count.className='countdown';count.textContent=`${slot.led_remaining}s`;head.append(count);
      }
      const label = document.createElement('span'); label.textContent=slot.label;
      element.append(head,label); return element;
    }));
    const active = data.guide.targets || [];
    $('result').textContent = active.length ? active.map(slot=>`${slot.label} → posición ${slot.id}`).join(' · ') : 'Sin LEDs activos. Apunta la cámara a un objeto.';
    const tracks = (data.scan.tracks || []).filter(track=>!track.missing_samples);
    const error = data.scan.connection_error || data.webcam.error;
    const progress = tracks.map(track => {
      const slot = data.slots.find(slot=>slot.category===(track.category || track.recognition?.candidate));
      const status = track.state==='observing' ? `Estabilizando ${track.stable_samples}/${data.scan_stable_samples || 2}` : labels[track.state] || track.state;
      return `${slot?.label || 'Objeto '+track.track_id}: ${status}`;
    });
    if (running && !active.length && progress.length) $('result').textContent=progress.join(' · ');
    if (active.length && data.webcam.first_confirmation_seconds != null) $('result').textContent+=` · Confirmado en ${data.webcam.first_confirmation_seconds}s`;
    if(data.continuous_led_seconds && !pending) $('notice').textContent=`Cada LED se apaga automáticamente a los ${data.continuous_led_seconds}s. Finalizar apaga todos.`;
    $('hint').textContent = !online ? 'Abre la cámara en el iPhone y deja Safari visible.' : error ? error : !data.api.online ? 'Reconecta Colab y ejecuta las secciones de modelos y 6b.' : !running ? (data.scan.mode==='single'?'Modo rápido: presenta un objeto y toca Iniciar escaneo.':'Cámara lista. Toca Iniciar escaneo en esta pantalla.') : active.length ? 'Objeto aceptado. El LED se apaga al terminar la cuenta.' : tracks.some(track=>track.state==='classifying') ? 'Identificando en Colab. Mantén visible el objeto completo.' : tracks.some(track=>track.state==='provisional') ? 'Verificando el objeto en otra imagen antes de encender su LED.' : data.scan.mode==='single' ? 'Presenta un solo objeto completo, sin taparlo.' : 'Mantén el teléfono fijo. Evita superponer objetos.';
    image();
  }
  async function refresh() { try { render(await api('/api/status')); } catch (_) { $('hint').textContent='Reconectando con la estación…'; } }
  async function control(action) {
    if (pending) return; pending=true; $('start').disabled=true; $('finish').disabled=true;
    try { await api('/api/station/scan/'+action,{method:'POST'}); if(action==='finish') { loadedKey=''; $('notice').textContent='Prueba finalizada. LEDs apagados; puedes repetir con el mismo objeto.'; } }
    catch(error) { $('notice').textContent=error.message; }
    finally { pending=false; await refresh(); }
  }
  $('start').addEventListener('click',()=>control(state?.scan.capture_state==='running'?'pause':'start'));
  $('finish').addEventListener('click',()=>control('finish'));
  $('mode').addEventListener('click',async()=>{
    if(pending || state?.scan.capture_state==='running') return;
    pending=true;
    try { await api('/api/station/scan/mode',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:state.scan.mode==='single'?'tracked':'single'})}); }
    catch(error){$('notice').textContent=error.message;}
    finally{pending=false;await refresh();}
  });
  $('view').addEventListener('click',()=>{mode=mode==='live'?'analysis':'live';loadedKey='';$('view').textContent=mode==='live'?'Ver análisis':'Ver cámara';$('boxes').replaceChildren();image();});
  $('help').addEventListener('click',async()=>{
    $('connection').showModal();
    $('phoneQr').hidden=true;
    try { const info=await api('/api/station/connection'); $('phoneUrl').textContent=info.url; $('pairCode').textContent=info.code; $('phoneQr').src='/static/phone-qr.png?v='+Date.now(); $('phoneQr').hidden=false; }
    catch (_) { $('phoneUrl').textContent='No se pudo obtener la dirección. Cierra y reintenta.'; }
  });
  $('closeHelp').addEventListener('click',()=>$('connection').close());
  $('system').addEventListener('click',()=>$('systemPanel').showModal());
  $('closeSystem').addEventListener('click',()=>$('systemPanel').close());
  $('desktop').addEventListener('click',async()=>{
    $('desktop').disabled=true;$('systemMessage').textContent='Finalizando escaneo y abriendo escritorio…';
    try{await api('/api/station/desktop',{method:'POST'});}
    catch(error){$('systemMessage').textContent=error.message;$('desktop').disabled=false;}
  });
  const events = new EventSource('/api/events');
  events.onmessage = event => render(JSON.parse(event.data));
  events.onerror = () => { $('hint').textContent='Reconectando con la estación…'; };
  refresh(); setInterval(refresh,1000);
})();
