const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(userAgent = 'Mobile Safari') {
  const listeners = new Map();
  const on = (target, type, fn) => listeners.set(target + ':' + type, fn);
  const classes = new Set(), properties = new Map();
  const body = {
    classList: {toggle(name, enabled) {if (enabled) classes.add(name); else classes.delete(name);}},
    style: {setProperty: (name, value) => properties.set(name, value), removeProperty: name => properties.delete(name)},
  };
  const visualViewport = {height: 740, offsetTop: 0, scale: 1,
    addEventListener: (type, fn) => on('viewport', type, fn)};
  const document = {body, activeElement: null,
    addEventListener: (type, fn) => on('document', type, fn)};
  const window = {visualViewport, innerHeight: 780,
    addEventListener: (type, fn) => on('window', type, fn)};
  const context = vm.createContext({window, document, navigator: {userAgent}, queueMicrotask});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/hosts/environment.js'), 'utf8'), context);
  return {classes, properties, document, visualViewport,
    fire: (target, type) => listeners.get(target + ':' + type)()};
}

test('browser terminal follows keyboard resize and offset, then restores the page', async () => {
  const f = fixture();
  f.document.activeElement = {matches: selector => selector.includes('.wb-terminal-input textarea')};
  f.fire('document', 'focusin');
  assert.equal(f.properties.size, 0);
  assert.equal(f.classes.has('browser-terminal-keyboard'), false);

  f.visualViewport.height = 400;
  f.visualViewport.offsetTop = 18;
  f.fire('viewport', 'resize');
  assert.equal(f.properties.get('--terminal-viewport-height'), '400px');
  assert.equal(f.properties.get('--terminal-viewport-top'), '18px');
  assert.equal(f.classes.has('browser-terminal-keyboard'), true);

  f.visualViewport.height = 740;
  f.visualViewport.offsetTop = 0;
  f.fire('viewport', 'resize');
  assert.equal(f.classes.has('browser-terminal-keyboard'), false);
  assert.equal(f.classes.has('browser-terminal-viewport'), false);
  assert.equal(f.properties.size, 0);
  f.document.activeElement = null;
  f.fire('document', 'focusout');
  await Promise.resolve();
  assert.equal(f.classes.has('browser-terminal-viewport'), false);
  assert.equal(f.properties.size, 0);
});

test('native host and browser pinch zoom never apply keyboard positioning', () => {
  for (const userAgent of ['BridgeMobile/1-iOS', 'BridgeMobile/1-Android', 'Mobile Safari']) {
    const f = fixture(userAgent);
    f.document.activeElement = {matches: () => true};
    f.visualViewport.height = 400;
    f.visualViewport.scale = userAgent === 'Mobile Safari' ? 1.5 : 1;
    f.fire('document', 'focusin');
    assert.equal(f.classes.size, 0);
    assert.equal(f.properties.size, 0);
  }
});

test('leaving the terminal clears the browser override while the viewport is still small', async () => {
  const f = fixture();
  f.visualViewport.height = 400;
  f.document.activeElement = {matches: selector => selector.includes('.wb-terminal-form textarea')};
  f.fire('document', 'focusin');
  assert.equal(f.classes.has('browser-terminal-viewport'), true);

  f.document.activeElement = {matches: () => false};
  f.fire('document', 'click');
  assert.equal(f.classes.size, 0);
  assert.equal(f.properties.size, 0);

  f.document.activeElement = {matches: () => true};
  f.fire('document', 'focusin');
  f.document.activeElement = null;
  f.fire('document', 'focusout');
  await Promise.resolve();
  assert.equal(f.classes.size, 0);
});
