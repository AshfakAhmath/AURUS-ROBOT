const display = document.getElementById('face-display');
const caption = document.getElementById('expression-caption');
const connection = document.getElementById('display-link');
const transcript = document.getElementById('speech-transcript');
const cameraPreview = document.getElementById('face-camera-preview');
const camera = document.getElementById('face-camera');
let cameraGeneration = null;
let cameraRetryAt = 0;
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
  if (!Number.isInteger(generation)) {
    cameraPreview.hidden = true;
    camera.removeAttribute('src');
    cameraGeneration = null;
    cameraRetryAt = 0;
    return;
  }
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
