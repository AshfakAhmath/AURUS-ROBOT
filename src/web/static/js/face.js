const display = document.getElementById('face-display');
const caption = document.getElementById('expression-caption');
const connection = document.getElementById('display-link');
const transcript = document.getElementById('speech-transcript');
const cameraPreview = document.getElementById('face-camera-preview');
const camera = document.getElementById('face-camera');
const gestureButton = document.getElementById('try-gesture');
const expoButton = document.getElementById('expo-gestures');
const gestureStatus = document.getElementById('gesture-status');
let cameraGeneration = null;
let cameraRetryAt = 0;
let gestureRequest = null;
let gestureTimer;
let expoEnabled = false;
let expoTimer;
let lastGesture = null;
const gestureList = gestureStatus.textContent;
const expoHint = 'Expo mode on. Show a gesture. Lower your hands before repeating it.';
const gestures = {
  wave: 'Wave',
  'thumbs-up': 'Thumbs-up',
  heart: 'Heart',
  'open-palm': 'Open palm',
  peace: 'Peace sign',
};
const captions = {
  idle: 'Hello, I’m AURUS',
  listening: 'I’m listening',
  thinking: 'Thinking…',
  speaking: 'Speaking',
  performing: 'FIT Expo showcase',
  exploring: 'Exploring',
  estop: 'Emergency stop',
  offline: 'Waiting for AURUS',
};

function showExpression(expression, spokenText = '') {
  if (!Object.hasOwn(captions, expression)) expression = 'offline';
  const text = expression === 'speaking' && typeof spokenText === 'string' ? spokenText : '';
  if (transcript.textContent !== text) transcript.textContent = text;
  transcript.hidden = !text;
  display.classList.toggle('has-transcript', Boolean(text));
  if (display.dataset.expression === expression && caption.textContent === captions[expression]) return;
  display.dataset.expression = expression;
  caption.textContent = captions[expression];
  connection.textContent = expression === 'offline' ? 'Reconnecting' : 'Live';
}

function showCamera(generation) {
  const previous = cameraGeneration;
  if (!Number.isInteger(generation)) {
    cameraPreview.hidden = true;
    camera.removeAttribute('src');
    cameraGeneration = null;
    cameraRetryAt = 0;
  } else {
    if (generation !== cameraGeneration) {
      cameraGeneration = generation;
      cameraRetryAt = 0;
      camera.removeAttribute('src');
    }
    if (!camera.hasAttribute('src') && Date.now() >= cameraRetryAt) {
      camera.src = `${camera.dataset.streamUrl}?session=${generation}`;
      cameraPreview.hidden = false;
    }
  }
  if (previous !== cameraGeneration) {
    lastGesture = null;
    clearTimeout(gestureTimer);
    delete display.dataset.gesture;
    refreshExpo();
  }
}

function updateGestureControls() {
  gestureButton.disabled = expoEnabled || gestureRequest !== null || cameraGeneration === null;
  expoButton.disabled = !expoEnabled && (gestureRequest !== null || cameraGeneration === null);
  expoButton.setAttribute('aria-pressed', String(expoEnabled));
}

function queueExpo(delay = 0) {
  clearTimeout(expoTimer);
  if (expoEnabled && !document.hidden && cameraGeneration !== null && !gestureRequest) {
    expoTimer = setTimeout(() => scanGesture(true), delay);
  }
}

function refreshExpo() {
  updateGestureControls();
  if (expoEnabled) {
    if (cameraGeneration === null || document.hidden) {
      gestureRequest?.abort();
      gestureStatus.textContent = 'Expo mode paused. Waiting for the live camera and display.';
    } else {
      gestureStatus.textContent = expoHint;
    }
  }
  queueExpo();
}

function showGesture(gesture) {
  clearTimeout(gestureTimer);
  if (display.dataset.gesture === gesture) {
    delete display.dataset.gesture;
    void display.offsetWidth;
  }
  display.dataset.gesture = gesture;
  gestureStatus.textContent = `I copied your ${gestures[gesture].toLowerCase()}!`;
  gestureTimer = setTimeout(() => {
    delete display.dataset.gesture;
    if (expoEnabled && !document.hidden && cameraGeneration !== null) gestureStatus.textContent = expoHint;
  }, 2300);
}

async function scanGesture(automatic) {
  if (gestureRequest || cameraGeneration === null || automatic !== expoEnabled || (automatic && document.hidden)) return;
  const generation = cameraGeneration;
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 15000);
  let retryDelay = 100;
  gestureRequest = controller;
  updateGestureControls();
  if (!automatic) {
    clearTimeout(gestureTimer);
    delete display.dataset.gesture;
    gestureStatus.textContent = 'Show your gesture now. Wave side to side, or hold the pose.';
  }
  try {
    const response = await fetch(display.dataset.gestureUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(automatic ? {generation, automatic: true, after: lastGesture} : {generation}),
      signal: controller.signal,
    });
    const result = await response.json();
    if (controller.signal.aborted) return;
    if (cameraGeneration !== generation) {
      if (!automatic) gestureStatus.textContent = 'Camera changed. Wait for the live preview and try again.';
    } else if (!response.ok) {
      gestureStatus.textContent = result.message || 'Gesture scan is unavailable.';
      retryDelay = 650;
      if (automatic && response.status !== 409) expoEnabled = false;
    } else if (result.generation === generation && Object.hasOwn(gestures, result.gesture)) {
      if (automatic) lastGesture = result.gesture;
      showGesture(result.gesture);
    } else if (automatic) {
      if (result.generation === generation && result.released) lastGesture = null;
      if (!display.dataset.gesture) gestureStatus.textContent = expoHint;
    } else {
      gestureStatus.textContent = 'I missed that one. Keep your hands in the camera preview and try again.';
    }
  } catch {
    if (!controller.signal.aborted || timedOut) {
      gestureStatus.textContent = 'The scan timed out or lost connection.';
      retryDelay = 1000;
    }
  } finally {
    clearTimeout(timeout);
    gestureRequest = null;
    updateGestureControls();
    if (automatic) queueExpo(retryDelay);
  }
}

gestureButton.addEventListener('click', () => scanGesture(false));
expoButton.addEventListener('click', () => {
  expoEnabled = !expoEnabled;
  lastGesture = null;
  if (!expoEnabled) {
    gestureRequest?.abort();
    clearTimeout(gestureTimer);
    delete display.dataset.gesture;
    gestureStatus.textContent = gestureList;
  }
  refreshExpo();
});
document.addEventListener('visibilitychange', () => {
  if (expoEnabled) refreshExpo();
});

camera.addEventListener('error', () => {
  cameraPreview.hidden = true;
  camera.removeAttribute('src');
  cameraRetryAt = Date.now() + 2000;
});

async function pollExpression() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2200);
  try {
    const response = await fetch(display.dataset.stateUrl, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error('Display state unavailable');
    const state = await response.json();
    showExpression(state.expression, state.transcript);
    showCamera(state.camera_generation);
  } catch {
    showExpression('offline');
    showCamera(null);
  } finally {
    clearTimeout(timeout);
    setTimeout(pollExpression, display.dataset.expression === 'offline' ? 1000 : 300);
  }
}

pollExpression();
