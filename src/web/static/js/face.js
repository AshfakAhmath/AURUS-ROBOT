const display = document.getElementById('face-display');
const caption = document.getElementById('expression-caption');
const connection = document.getElementById('display-link');
const transcript = document.getElementById('speech-transcript');
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

async function pollExpression() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2200);
  try {
    const response = await fetch(display.dataset.stateUrl, {cache: 'no-store', signal: controller.signal});
    if (!response.ok) throw new Error('Display state unavailable');
    const state = await response.json();
    showExpression(state.expression, state.transcript);
  } catch {
    showExpression('offline');
  } finally {
    clearTimeout(timeout);
    setTimeout(pollExpression, display.dataset.expression === 'offline' ? 1000 : 300);
  }
}

pollExpression();
