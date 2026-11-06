const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const dashboard = fs.readFileSync(path.join(__dirname, '../src/web/static/js/evaluation.js'), 'utf8');

function browserMic() {
  const events = [];
  const handlers = {};
  let recognition;
  class Recognition {
    constructor() { recognition = this; }
    start() {}
    abort() {}
  }
  const button = {addEventListener() {}, setAttribute() {}, classList: {toggle() {}}};
  const context = vm.createContext({
    document: {getElementById: () => button, querySelectorAll: () => []},
    window: {addEventListener() {}, isSecureContext: true, SpeechRecognition: Recognition},
    io: () => ({connected: true, on: (event, handler) => { handlers[event] = handler; }, emit: (...args) => events.push(args)}),
    setTimeout: () => 1,
    clearTimeout() {},
  });
  vm.runInContext(dashboard, context);
  context.toggleBrowserMic();
  recognition.onaudiostart();
  return {
    events,
    handlers,
    talkNow: context.talkNow,
    speak: (...transcripts) => recognition.onresult({resultIndex: 0, results: [
      Object.assign(transcripts.map(transcript => ({transcript})), {isFinal: true}),
    ]}),
  };
}

test('wake and command in one utterance forwards the original command', () => {
  const mic = browserMic();
  mic.speak('Hey AURUS, introduce yourself!');
  assert.equal(mic.events.length, 1);
  assert.equal(mic.events[0][0], 'send_text');
  assert.equal(mic.events[0][1].text, 'introduce yourself!');
  assert.equal(mic.events[0][1].source, 'browser-mic');
});

test('wake-only aliases can be recognized from alternatives and arm the next command', () => {
  const mic = browserMic();
  mic.speak('Someone else', 'Hey, or us.');
  assert.equal(mic.events[0]?.[0], 'browser_wake_detected');
  mic.handlers.browser_listen_ready({seconds: 10});
  mic.speak('Introduce yourself.');
  assert.equal(mic.events[1]?.[0], 'send_text');
  assert.equal(mic.events[1][1].text, 'Introduce yourself.');
});

test('mentions and unrelated speech are ignored', () => {
  const mic = browserMic();
  mic.speak('I was talking to AURUS yesterday.');
  mic.speak('Aries is a constellation.');
  assert.equal(mic.events.length, 0);
});

test('small mic sends one laptop message without a wake phrase', () => {
  const mic = browserMic();
  mic.talkNow();
  mic.speak('Tell me a Robot joke.');
  mic.speak('Background conversation.');
  assert.equal(mic.events.length, 1);
  assert.equal(mic.events[0][0], 'send_text');
  assert.equal(mic.events[0][1].text, 'Tell me a Robot joke.');
  assert.equal(mic.events[0][1].source, 'browser-mic');
});
