const socket = io({ transports: ['websocket', 'polling'], reconnection: true });
let driveSequence = 0;
let driveTimer = null;
let latestSensors = null;
let robotSpeaking = false;
let browserAudioPlaying = false;
let browserAudioCount = 0;
let sensorControlAuthorized = false;
let browserMicActive = false;
let demoActive = false;
let pendingObjectScan = null;
const demoAudio = new Set();
let cameraConnected = false;
let cameraControl = null;
let cameraRequested = null;
let cameraFeedGeneration = null;

const $ = (id) => document.getElementById(id);
const fmtDistance = (value) => value != null && Number.isFinite(Number(value)) ? `${Number(value).toFixed(0)} cm` : 'N/A';

function renderCamera(data = {}) {
  const previousGeneration = cameraControl?.generation;
  if (cameraControl && data.generation != null && cameraControl.generation != null && data.generation < cameraControl.generation) data = {};
  cameraControl = {...(cameraControl || {}), ...data};
  const enabled = cameraControl.enabled !== false;
  const state = cameraControl.state || 'starting';
  const known = typeof cameraControl.enabled === 'boolean';
  const ready = cameraConnected && enabled && state === 'on';
  if (cameraRequested === cameraControl.enabled) cameraRequested = null;
  const button = $('camera-toggle');
  button.disabled = !cameraConnected || !known || cameraRequested !== null;
  button.textContent = !known ? 'Camera loading' : cameraRequested !== null ? 'Updating camera...' : (enabled ? 'Turn camera off' : 'Turn camera on');
  button.setAttribute('aria-pressed', String(enabled));
  const image = $('camera');
  image.hidden = !ready;
  $('camera-overlay').hidden = ready;
  if (ready) {
    if (!image.getAttribute('src') || cameraFeedGeneration !== cameraControl.generation) {
      image.src = `${image.dataset.streamUrl}?session=${cameraControl.generation || 0}`;
      cameraFeedGeneration = cameraControl.generation;
    }
  } else {
    image.removeAttribute('src');
    cameraFeedGeneration = null;
    const labels = !cameraConnected ? ['Dashboard disconnected', 'Reconnect to control the camera.']
      : state === 'error' ? ['Camera unavailable', cameraControl.error || 'Check the camera connection.']
      : !enabled ? [state === 'stopping' ? 'Stopping camera...' : 'Camera switched off', 'Video, face detection and recognition are paused.']
      : ['Camera starting...', 'Waiting for a fresh camera image.'];
    $('camera-state').textContent = labels[0];
    $('camera-help').textContent = labels[1];
    $('identity-status').textContent = 'NO FACE';
    $('identity-name').textContent = 'No target';
    $('identity-confidence').textContent = 'Confidence 0.00';
  }
  const feedLabel = ready ? 'Live feed' : state === 'error' ? 'Camera error' : state === 'stopping' ? 'Stopping' : !enabled ? 'Camera off' : 'Waiting';
  $('camera-feed-status').innerHTML = `<i></i> ${feedLabel}`;
  $('camera-feed-status').classList.toggle('inactive', !ready);
  $('enroll').disabled = !ready || demoActive;
  $('enroll-name').disabled = !ready || demoActive;
  $('remember').disabled = !ready || demoActive;
  document.querySelectorAll('[data-mode="exploring"]').forEach(button => { button.disabled = !known || !enabled; });
  if (!enabled) $('vision-backend').textContent = 'Disabled';
  if (!ready || previousGeneration !== cameraControl.generation) {
    pendingObjectScan = null;
    $('detected-objects').replaceChildren();
    $('detected-objects').hidden = true;
    $('object-status').textContent = !enabled ? 'Camera is switched off. Turn it on to scan objects.'
      : ready ? 'Hold an object in front of the camera to scan it.' : 'Waiting for a fresh camera image.';
  }
  updateScanButton();
}

socket.on('connect', () => {
  cameraConnected = true;
  // A restarted robot can reset its generation counter. Trust only fresh
  // server state after reconnecting, rather than retaining the previous epoch.
  cameraControl = null;
  cameraRequested = null;
  cameraFeedGeneration = null;
  renderCamera({state: 'starting'});
  $('connection').innerHTML = '<i></i> Connected';
  $('connection').className = 'pill good';
  updateScanButton();
});
socket.on('disconnect', () => {
  cameraConnected = false;
  cameraRequested = null;
  renderCamera();
  stopDemoAudio();
  sensorControlAuthorized = false;
  document.querySelectorAll('[data-sensor]').forEach(input => { input.disabled = true; });
  $('sensor-defaults').disabled = true;
  endDrive();
  $('connection').innerHTML = '<i></i> Offline';
  $('connection').className = 'pill bad';
  if (pendingObjectScan) $('object-status').textContent = 'Connection lost during the scan. Reconnect to retry.';
  pendingObjectScan = null;
  updateScanButton();
});
socket.on('command_error', ({message}) => {
  $('error').textContent = message;
  cameraRequested = null;
  if (cameraControl) renderCamera();
});
socket.on('camera_configuration', renderCamera);
socket.on('system_error', ({component, error}) => { $('error').textContent = `${component}: ${error}`; });
socket.on('system_health', renderHealth);
socket.on('enrollment_progress', renderEnrollment);
socket.on('conversation', renderConversation);
socket.on('telemetry', renderTelemetry);
socket.on('mission_update', renderMission);
socket.on('play_audio', handleBrowserAudio);
socket.on('demo_status', renderDemo);
socket.on('demo_stop_audio', stopDemoAudio);
function stopDemoAudio() {
  demoAudio.forEach(audio => { audio.pause(); audio.dispatchEvent(new Event('error')); });
}

function renderDemo(data = {}) {
  demoActive = Boolean(data.active);
  $('showcase').textContent = demoActive ? 'Stop Demo' : 'Start FIT Expo Demo';
  $('demo-status').textContent = `${data.stage || 'Ready'}${data.active && !data.motion_enabled ? ' · Speech only' : ''}${data.reason ? ' · ' + data.reason : ''}`;
  document.querySelectorAll('[data-audio-mode]').forEach(button => { button.disabled = demoActive; });
  if (cameraControl) renderCamera();
  updateScanButton();
}

function updateScanButton() {
  $('detect-objects').disabled = !socket.connected || !cameraControl?.enabled || cameraControl.state !== 'on'
    || demoActive || pendingObjectScan !== null;
}

function scanObjects() {
  if ($('detect-objects').disabled) return;
  const scan = Symbol();
  pendingObjectScan = scan;
  updateScanButton();
  const list = $('detected-objects');
  list.replaceChildren();
  list.hidden = true;
  $('object-status').textContent = 'Scanning the current camera frame…';
  socket.timeout(40000).emit('detect_objects', {}, (error, result) => {
    if (pendingObjectScan !== scan) return;
    pendingObjectScan = null;
    updateScanButton();
    if (error || !result) {
      $('object-status').textContent = 'No scan result received. Check the connection and try again.';
      return;
    }
    if (result.status !== 'ok' || !result.objects.length) {
      $('object-status').textContent = result.message + (result.speech_queued === false ? ' Speech was not queued. Please try again.' : '');
      return;
    }
    for (const label of result.objects) {
      const item = document.createElement('li');
      item.textContent = label;
      list.append(item);
    }
    list.hidden = false;
    const countLabel = result.objects.length === 1 ? '1 object identified' : `${result.objects.length} objects identified`;
    $('object-status').textContent = countLabel + (result.speech_queued === false ? ' · Speech was not queued. Please try again.' : '');
  });
}
socket.on('sensor_configuration', renderSensorControl);
socket.on('sensor_control_authorized', ({authorized}) => {
  sensorControlAuthorized = Boolean(authorized);
  document.querySelectorAll('[data-sensor]').forEach(input => { input.disabled = !sensorControlAuthorized; });
  $('sensor-defaults').disabled = !sensorControlAuthorized;
});

function renderSensorControl(sensors) {
  for (const [key, state] of Object.entries(sensors.states || {})) {
    $(`sensor-toggle-${key}`).checked = state.enabled;
    $(`sensor-health-${key}`).textContent = `${state.enabled ? 'ON' : 'OFF'} · ${fmtDistance(state.distance_cm)} · ${state.status} · ${state.age_ms == null ? 'N/A' : Math.round(state.age_ms) + ' ms'}`;
    $(`sensor-error-${key}`).textContent = state.error || '';
  }
  const restricted = Object.entries(sensors.coverage || {}).filter(([, valid]) => !valid).map(([direction]) => direction);
  const hasHealthySensor = Object.values(sensors.states || {}).some(state => state.status === 'Healthy');
  $('sensor-coverage').textContent = sensors.reduced_coverage
    ? (sensors.coverage_required === false
      ? (hasHealthySensor ? 'Reduced coverage: unavailable sensors ignored; remaining healthy sensors are active.' : 'No usable sensor readings: movement stopped.')
      : `Reduced coverage. Restricted: ${restricted.join(', ') || 'front-center degraded mode (45/75 cm margins)'}.`)
    : 'All five sensors healthy; full configured coverage.';
}

let missionDiscoverySignature = '';

function renderTelemetry(data) {
  renderDemo(data.demo);
  $('mode').textContent = data.mode;
  document.querySelectorAll('[data-mode]').forEach(btn => btn.classList.toggle('active', btn.dataset.mode === data.mode));
  latestSensors = data.sensors;
  robotSpeaking = browserAudioPlaying || Boolean(data.health && data.health.audio_playback);
  renderSensorControl(data.sensors);
  for (const key of ['fl','f','fr','rl','rr']) $(`s-${key}`).textContent = fmtDistance(data.sensors[key]);
  $('sensor-age').textContent = data.sensors.age_ms == null ? 'stale' : `${data.sensors.age_ms.toFixed(0)} ms`;
  const decision = data.motion;
  if (decision.movement_cancelled && driveTimer) endDrive();
  $('decision').textContent = decision.reason;
  $('decision').style.color = decision.allowed ? 'var(--green)' : 'var(--red)';
  $('motion-vector').textContent = `Final · ${decision.final.vx.toFixed(2)} / ${decision.final.vy.toFixed(2)} / ${decision.final.omega.toFixed(2)}`;
  const identity = data.vision.identity;
  const faceCount = Number(data.vision.face_count || 0);
  if (!cameraControl || (data.vision.generation || 0) >= (cameraControl.generation || 0)) {
    $('identity-status').textContent = faceCount ? identity.status.toUpperCase() : 'NO FACE';
    $('identity-name').textContent = faceCount ? (identity.name || 'Not enrolled') : 'No target';
    $('identity-confidence').textContent = `Confidence ${Number(identity.confidence || 0).toFixed(2)}`;
    $('vision-backend').textContent = `${data.vision.backend} · ${faceCount} face${faceCount === 1 ? '' : 's'}`;
  }
  renderEnrollment(data.enrollment);
  renderMission(data.mission);
  renderHealth(data.health);
  renderCamera({enabled: data.health.camera_enabled ?? (data.vision.enabled !== false), generation: data.health.camera_generation ?? data.vision.generation,
    state: data.health.camera_state || (data.vision.healthy ? 'on' : data.vision.enabled === false ? 'off' : 'starting'),
    error: data.vision.error});
  drawRadar(data.sensors);
}

let audioContextUnlocked = false;
function unlockAudioContext() {
  if (audioContextUnlocked) return;
  audioContextUnlocked = true;
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (AudioContext) {
      const ctx = new AudioContext();
      ctx.resume().catch(() => {});
    }
  } catch (e) {}
}
window.addEventListener('click', unlockAudioContext, { once: true });
window.addEventListener('keydown', unlockAudioContext, { once: true });

function handleBrowserAudio({data, format, playback_id, audio_id}) {
  if (!data) return;
  let finishAudio = () => {};
  try {
    const mime = format === 'wav' ? 'audio/wav' : 'audio/mpeg';
    const audio = new Audio(`data:${mime};base64,${data}`);
    if (playback_id) demoAudio.add(audio);
    robotSpeaking = true;
    browserAudioCount += 1;
    browserAudioPlaying = true;
    socket.emit('browser_audio_state', {playing: true});
    let finished = false;
    finishAudio = (success = false) => {
      if (finished) return;
      finished = true;
      browserAudioCount = Math.max(0, browserAudioCount - 1);
      browserAudioPlaying = browserAudioCount > 0;
      robotSpeaking = browserAudioPlaying;
      socket.emit('browser_audio_state', {playing: browserAudioPlaying, audio_id, finished: true});
      if (playback_id) {
        demoAudio.delete(audio);
        socket.emit('demo_audio_finished', {playback_id, success});
      }
    };
    audio.onplaying = () => socket.emit('browser_audio_state', {playing: true, audio_id});
    audio.onended = () => finishAudio(true);
    audio.onerror = () => finishAudio(false);
    const playPromise = audio.play();
    if (playPromise !== undefined) {
      playPromise.catch(err => {
        finishAudio();
        console.warn('[Browser Audio] Playback prevented:', err);
        $('error').textContent = 'Click on the page to allow PC speaker audio playback';
      });
    }
  } catch (err) {
    finishAudio();
    console.error('[Browser Audio Error]', err);
  }
}

function updateAudioOutputUI(configuredMode, activeOutput) {
  const mode = configuredMode || 'auto';
  document.querySelectorAll('[data-audio-mode]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.audioMode === mode);
  });
  const tag = $('audio-active-tag');
  if (tag) {
    const activeLabel = activeOutput === 'browser' ? 'PC' : 'Pi';
    tag.textContent = `${mode.toUpperCase()} · ${activeLabel}`;
  }
}

function renderHealth(health) {
  if (typeof health.camera_enabled === 'boolean') {
    renderCamera({enabled: health.camera_enabled, state: health.camera_state, generation: health.camera_generation, error: health.camera_error});
  }
  const keys = ['runtime','motors','sensors','camera','microphone','tts','llm','groq_vision','database','mcp_agent','wake_phrase','estop'];
  $('health').innerHTML = keys.map(key => {
    const browserVoice = browserMicActive && (key === 'microphone' || key === 'wake_phrase');
    const value = Boolean(health[key]) || browserVoice;
    const expectedBad = key === 'estop';
    const good = expectedBad ? !value : value;
    let label = value ? 'READY' : 'OFF';
    const disabledCamera = key === 'camera' && (cameraControl ? cameraControl.enabled : health.camera_enabled) === false;
    if (key === 'tts') label = health.tts_backend || label;
    if (key === 'camera') label = health.vision_backend || label;
    if (key === 'microphone') label = health.stt_backend || label;
    if (key === 'wake_phrase') label = health.wake_backend || label;
    if (key === 'llm') label = health.llm_backend || label;
    if (key === 'groq_vision') label = health.groq_vision_model || label;
    if (browserVoice) label = 'browser';
    if (disabledCamera) label = 'DISABLED';
    return `<div class="health-item ${disabledCamera ? 'disabled' : good ? '' : 'bad'}"><span class="health-name"><i></i>${key.replaceAll('_',' ')}</span><strong>${label}</strong></div>`;
  }).join('');
  if (health && health.tts_audio_output) {
    updateAudioOutputUI(health.tts_audio_output, health.tts_active_output);
  }
}

function renderEnrollment(status) {
  if (!status) return;
  const required = Math.max(1, Number(status.required || 20));
  const accepted = Number(status.accepted || 0);
  $('enroll-progress').style.width = `${Math.min(100, accepted / required * 100)}%`;
  $('enroll-label').textContent = status.complete
    ? `${status.name || 'Person'} enrolled${status.error ? ` (${status.error})` : ''}`
    : status.active ? `Capturing ${accepted}/${required} good face samples…` : (status.error || 'Enrollment idle');
}

function renderConversation(data) {
  if (data.transcript) appendMessage(data.transcript, 'user', data.source);
  if (data.response) appendMessage(data.response, '', `${data.provider}${data.fallback_reason ? ' · fallback' : ''}`);
  const provider = String(data.provider || 'local');
  $('provider').replaceChildren();
  const dot = document.createElement('i');
  $('provider').append(dot, document.createTextNode(` ${provider}`));
}

function appendMessage(text, className, meta) {
  const emptyState = $('chat').querySelector('.chat-empty');
  if (emptyState) emptyState.remove();
  const p = document.createElement('p');
  p.className = className;
  p.textContent = text;
  const small = document.createElement('small');
  small.textContent = meta || '';
  p.appendChild(small);
  $('chat').appendChild(p);
  $('chat').scrollTop = $('chat').scrollHeight;
}

function drawRadar(s) {
  const canvas = $('radar');
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height, cx = w / 2, cy = h * .66;
  ctx.clearRect(0, 0, w, h);
  const maxRadius = Math.min(w * 0.42, h * 0.64);
  ctx.strokeStyle = 'rgba(113, 227, 211, 0.12)';
  ctx.lineWidth = 1;
  [0.33, 0.66, 1].forEach(scale => {
    ctx.beginPath();
    ctx.arc(cx, cy, maxRadius * scale, Math.PI, 2 * Math.PI);
    ctx.stroke();
  });
  [-150, -120, -90, -60, -30].forEach(deg => {
    const angle = deg * Math.PI / 180;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(angle) * maxRadius, cy + Math.sin(angle) * maxRadius);
    ctx.stroke();
  });

  ctx.save();
  ctx.translate(cx, cy);
  ctx.fillStyle = '#182c2b';
  ctx.strokeStyle = '#71e3d3';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.roundRect(-24, -17, 48, 34, 8);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#a5f5e9';
  ctx.beginPath();
  ctx.arc(0, -4, 3, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  const readings = [
    ['fl', -135], ['f', -90], ['fr', -45], ['rl', 145], ['rr', 35]
  ];
  readings.forEach(([key, deg]) => {
    if (s[key] == null || !Number.isFinite(Number(s[key]))) return;
    const distance = Math.min(200, Number(s[key]));
    const radius = distance / 200 * maxRadius;
    const angle = deg * Math.PI / 180;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius;
    ctx.strokeStyle = distance < 20 ? '#ff6b74' : distance < 35 ? '#eec77b' : '#68dfa6';
    ctx.globalAlpha = 0.5;
    ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.shadowColor = ctx.strokeStyle;
    ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
  });
}

function sendText() {
  const text = $('message').value.trim();
  if (!text) return;
  socket.emit('send_text', {text});
  $('message').value = '';
}
// Browser Wake Word Logic
let recognition = null;
let wakeArmedUntil = 0;
let wakeTimeout = null;
const wakeTargets = ['aurus', 'auris', 'oris', 'aries'];

socket.on('browser_listen_ready', ({seconds = 10}) => {
  const duration = Math.max(1, Number(seconds) || 10) * 1000;
  wakeArmedUntil = Date.now() + duration;
  setBrowserMicLabel(`Listening · ${Math.round(duration / 1000)}s`);
  clearTimeout(wakeTimeout);
  wakeTimeout = setTimeout(() => {
    if (Date.now() >= wakeArmedUntil) {
      wakeArmedUntil = 0;
      setBrowserMicLabel('Listening');
      socket.emit('browser_listen_timeout', {});
    }
  }, duration + 50);
});

function editDistance(a, b) {
  const row = Array.from({length: b.length + 1}, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let diagonal = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const above = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1));
      diagonal = above;
    }
  }
  return row[b.length];
}

function renderMission(mission) {
  if (!mission) return;
  const elapsed = Math.max(0, Number(mission.elapsed_seconds || 0));
  const minutes = Math.floor(elapsed / 60);
  const seconds = String(Math.floor(elapsed % 60)).padStart(2, '0');
  $('mission-elapsed').textContent = `${minutes}:${seconds}`;
  $('mission-observations').textContent = Number(mission.observations || 0);
  $('mission-people').textContent = Number(mission.people || 0);
  $('mission-routes').textContent = Number(mission.route_changes || 0);
  $('mission-status').textContent = mission.active ? `Mission ${mission.mission_id} · live` : 'Mission idle';
  $('mission-status').classList.toggle('safe', Boolean(mission.active));

  const discoveries = Array.isArray(mission.discoveries) ? mission.discoveries : [];
  const signature = discoveries.map(item => item.id).join('|');
  if (signature === missionDiscoverySignature) return;
  missionDiscoverySignature = signature;
  const strip = $('discoveries');
  strip.replaceChildren();
  if (!discoveries.length) {
    const empty = document.createElement('div');
    empty.className = 'discovery-empty';
    empty.innerHTML = '<span>✦</span><div><strong>No mission discoveries yet</strong><p>Start Explore mode to build a live visual mission log.</p></div>';
    strip.appendChild(empty);
    return;
  }
  discoveries.forEach(item => {
    const card = document.createElement('article');
    card.className = `discovery-card category-${String(item.category || 'scene')}`;
    if (item.image) {
      const image = document.createElement('img');
      image.src = item.image;
      image.alt = 'Camera frame captured for this discovery';
      card.appendChild(image);
    } else {
      const visual = document.createElement('div');
      visual.className = 'discovery-symbol';
      visual.textContent = item.category === 'route' ? '↗' : '✦';
      card.appendChild(visual);
    }
    const body = document.createElement('div');
    body.className = 'discovery-body';
    const meta = document.createElement('span');
    const stamp = item.timestamp ? new Date(item.timestamp * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : 'Now';
    meta.textContent = `${item.category || 'scene'} · ${stamp}`;
    const title = document.createElement('strong');
    title.textContent = item.title || 'Discovery';
    const description = document.createElement('p');
    description.textContent = item.text || '';
    const confidence = document.createElement('small');
    confidence.textContent = `Confidence ${Number(item.confidence || 0).toFixed(2)}`;
    body.append(meta, title, description, confidence);
    card.appendChild(body);
    strip.appendChild(card);
  });
}

function wakeMatch(transcript) {
  const words = transcript.toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(Boolean);
  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    if (word.length < 4 || word.length > 8) continue;
    const similarity = Math.max(...wakeTargets.map(target => (
      1 - editDistance(word, target) / Math.max(word.length, target.length)
    )));
    if (similarity >= 0.78) {
      return {matched: true, command: words.slice(i + 1).join(' ')};
    }
  }
  return {matched: false, command: ''};
}

function setBrowserMicLabel(label, active = browserMicActive) {
  const btn = $('browser-mic');
  if (!btn) return;
  btn.classList.toggle('active', active);
  btn.innerHTML = `<span>${active ? '🔴' : '🎤'}</span> ${label}`;
}

function initBrowserMic() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert("Your browser doesn't support Web Speech API. Try Chrome.");
    return;
  }
  if (!window.isSecureContext && !['localhost', '127.0.0.1'].includes(location.hostname)) {
    $('error').textContent = 'Browser microphone permission may require HTTPS for a remote dashboard.';
  }
  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = 'en-US';

  recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        const transcript = event.results[i][0].transcript.toLowerCase().trim();
        if (!transcript || robotSpeaking || demoActive) continue;

        if (Date.now() < wakeArmedUntil) {
          wakeArmedUntil = 0;
          clearTimeout(wakeTimeout);
          setBrowserMicLabel('Listening');
          socket.emit('send_text', {text: transcript, source: 'browser-mic'});
          continue;
        }

        const match = wakeMatch(transcript);
        if (match.matched) {
          wakeArmedUntil = 0;
          setBrowserMicLabel('Wake heard');
          socket.emit('browser_wake_detected', {});
        }
      }
    }
  };

  recognition.onend = () => {
    if (browserMicActive) {
      try { recognition.start(); } catch(e) {}
    }
  };

  recognition.onerror = (e) => {
    console.warn("[Browser Mic Error]", e.error);
    if (['not-allowed', 'service-not-allowed', 'audio-capture', 'network'].includes(e.error)) {
      browserMicActive = false;
      $('error').textContent = `Browser microphone stopped: ${e.error}. Check permission, HTTPS, and network access.`;
      setBrowserMicLabel('Browser Mic', false);
    }
  };
}

function toggleBrowserMic() {
  if (!recognition) initBrowserMic();
  if (!recognition) return;

  browserMicActive = !browserMicActive;
  const btn = $('browser-mic');
  if (browserMicActive) {
    setBrowserMicLabel('Listening');
    try { recognition.start(); } catch(e) {}
  } else {
    wakeArmedUntil = 0;
    clearTimeout(wakeTimeout);
    setBrowserMicLabel('Browser Mic', false);
    recognition.stop();
  }
}

$('send').onclick = sendText;
$('sensor-authorize').onclick = () => {
  socket.emit('authorize_sensor_control', {token: $('sensor-operator-token').value});
  $('sensor-operator-token').value = '';
};
$('sensor-defaults').onclick = () => socket.emit('set_sensor_enabled', {restore_defaults: true});
document.querySelectorAll('[data-sensor]').forEach(input => {
  input.onchange = () => socket.emit('set_sensor_enabled', {sensor: input.dataset.sensor, enabled: input.checked});
});
if ($('browser-mic')) $('browser-mic').onclick = toggleBrowserMic;
$('message').addEventListener('keydown', e => { if (e.key === 'Enter') sendText(); });
$('listen').onclick = () => socket.emit('start_listening', {});
$('estop').onclick = () => socket.emit('stop', {});
$('clear-estop').onclick = () => socket.emit('clear_estop', {});
$('showcase').onclick = () => {
  endDrive();
  socket.emit(demoActive ? 'stop_showcase' : 'perform_showcase', {});
};
$('enroll').onclick = () => socket.emit('enroll_person', {name: $('enroll-name').value.trim()});
$('detect-objects').onclick = scanObjects;
$('remember').onclick = () => socket.emit('remember_fact', {text: $('fact').value.trim()});
document.querySelectorAll('[data-mode]').forEach(btn => btn.onclick = () => socket.emit('set_mode', {mode: btn.dataset.mode}));
document.querySelectorAll('[data-audio-mode]').forEach(btn => {
  btn.onclick = () => {
    const mode = btn.dataset.audioMode;
    socket.emit('set_audio_output', {mode});
  };
});

let currentSpeed = 1.0;
const speedSlider = $('speed-slider');
const speedValue = $('speed-value');

if (speedSlider && speedValue) {
  speedSlider.addEventListener('input', (e) => {
    currentSpeed = parseFloat(e.target.value);
    speedValue.textContent = currentSpeed.toFixed(2);
    const percentage = ((currentSpeed - 0.2) / 0.8) * 100;
    speedSlider.style.background = `linear-gradient(90deg, var(--accent) 0 ${percentage}%, rgba(255,255,255,.08) ${percentage}% 100%)`;
  });
}

function getDriveVector(name) {
  if (name === 'stop') return [0, 0, 0];
  if (name === 'forward') return [currentSpeed, 0, 0];
  if (name === 'backward') return [-currentSpeed, 0, 0];
  if (name === 'left') return [0, -currentSpeed, 0];
  if (name === 'right') return [0, currentSpeed, 0];
  if (name === 'spin-left') return [0, 0, -currentSpeed];
  if (name === 'spin-right') return [0, 0, currentSpeed];
  return [0, 0, 0];
}

function emitDrive(vector) {
  driveSequence += 1;
  socket.emit('manual_drive', {vx: vector[0], vy: vector[1], omega: vector[2], sequence: driveSequence});
}

function beginDrive(name) {
  endDrive();
  
  // If the robot is in IDLE mode, automatically switch to MANUAL mode
  const currentMode = $('mode').textContent.toLowerCase();
  if (currentMode === 'idle' && name !== 'stop') {
    socket.emit('set_mode', {mode: 'manual'});
  }

  const vector = getDriveVector(name);
  emitDrive(vector);
  if (name !== 'stop') {
    driveTimer = setInterval(() => {
      emitDrive(getDriveVector(name));
    }, 100);
  }
}

function endDrive() {
  if (driveTimer) clearInterval(driveTimer);
  driveTimer = null;
  emitDrive([0, 0, 0]);
}

$('camera-toggle').addEventListener('click', () => {
  if (!cameraConnected || !cameraControl || cameraRequested !== null) return;
  cameraRequested = !cameraControl.enabled;
  renderCamera();
  socket.emit('set_camera_enabled', {enabled: cameraRequested});
});

document.querySelectorAll('[data-drive]').forEach(btn => {
  btn.addEventListener('pointerdown', e => { e.preventDefault(); beginDrive(btn.dataset.drive); });
  btn.addEventListener('pointerup', endDrive);
  btn.addEventListener('pointerleave', endDrive);
  btn.addEventListener('pointercancel', endDrive);
});
