const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../kiwi_pi/static/app.js'), 'utf8');

function functionSource(name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.ok(start >= 0);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function \w+\(/m);
  return next < 0 ? rest : rest.slice(0, next + 1);
}

for (const method of ['send', 'retrySend']) {
  test(`${method}: discards a result after retaking the photo`, async () => {
    let release;
    const response = new Promise(resolve => { release = resolve; });
    const button = { querySelector: () => ({ textContent: '' }) };
    const state = { photo: { photo_id: 'old' }, result: null, screen: 'review', recognitionVersion: 0 };
    const context = vm.createContext({ state, $: () => button, setLoading() {},
      postJSON: (url) => url === '/api/recognize' ? response : Promise.resolve({ ok: true }),
      renderResult() {}, toast() {}, go(screen) { state.screen = screen; } });
    vm.runInContext(functionSource(method) + functionSource('resetToCapture'), context);
    const pending = context[method]();
    context.resetToCapture();
    state.photo = { photo_id: 'new' };
    release({ status: 'accepted', photo_id: 'old' });
    await pending;
    assert.equal(state.result, null);
    assert.equal(state.screen, 'capture');
  });
}
