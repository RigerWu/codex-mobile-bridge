const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(userAgent = 'Mobile Safari') {
  const listeners = new Map();
  const on = (target, type, fn) => listeners.set(target + ':' + type, fn);
  const classes = new Set(), htmlClasses = new Set(), properties = new Map(), writes = [];
  let bodyHeight = 780;
  let outputForReset = null;
  const body = {
    getBoundingClientRect: () => ({height: bodyHeight}),
    classList: {toggle(name, enabled) {if (enabled) classes.add(name); else classes.delete(name);if(name==='browser-editing-viewport'&&enabled&&outputForReset)outputForReset.scrollTop=0;}},
    style: {setProperty: (name, value) => {properties.set(name, value);writes.push([name, value]);}, removeProperty: name => {properties.delete(name);writes.push([name, null]);}},
  };
  const visualViewport = {height: 740, offsetTop: 0, scale: 1,
    addEventListener: (type, fn) => on('viewport', type, fn)};
  const document = {body, activeElement: null, documentElement:{clientWidth:390,classList:{add:name=>htmlClasses.add(name)}},
    addEventListener: (type, fn) => on('document', type, fn)};
  const window = {visualViewport, innerHeight: 780,
    addEventListener: (type, fn) => on('window', type, fn)};
  const context = vm.createContext({window, document, navigator: {userAgent}, queueMicrotask});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/hosts/environment.js'), 'utf8'), context);
  return {classes, htmlClasses, properties, writes, document, visualViewport, window,
    setBodyHeight: height => {bodyHeight = height;},
    resetOutputOnLayout: output => {outputForReset = output;},
    focus: kind => {document.activeElement = {matches: selector => selector.includes(kind)};listeners.get('document:focusin')();},
    fire: (target, type) => listeners.get(target + ':' + type)?.()};
}

test('browser terminal follows visual viewport and restores after keyboard dismissal', async () => {
  const f = fixture();
  f.focus('.wb-terminal-input textarea');
  assert.equal(f.properties.get('--editing-viewport-height'), '740px');
  assert.equal(f.classes.has('browser-editing-keyboard'), false);

  f.visualViewport.height = 400;
  f.visualViewport.offsetTop = 18;
  f.window.innerHeight = 400; // Safari may shrink innerHeight too.
  f.fire('viewport', 'resize');
  assert.equal(f.properties.get('--editing-viewport-height'), '400px');
  assert.equal(f.properties.get('--editing-viewport-top'), '18px');
  assert.equal(f.classes.has('browser-terminal-keyboard'), true);

  f.visualViewport.height = 740;
  f.visualViewport.offsetTop = 0;
  f.fire('viewport', 'resize');
  assert.equal(f.classes.has('browser-editing-keyboard'), false);
  f.document.activeElement = null;
  f.fire('document', 'focusout');
  await Promise.resolve();
  assert.equal(f.classes.size, 0);
  assert.equal(f.properties.size, 0);
});

test('main and side chat editors follow focus zoom plus keyboard without changing scale', () => {
  for (const editor of ['.composer textarea', '.wb-terminal-form textarea']) {
    const f = fixture();
    f.focus(editor);
    f.visualViewport.scale = 1.5;
    f.visualViewport.height = 260;
    f.visualViewport.offsetTop = 22;
    f.fire('viewport', 'resize');
    assert.equal(f.properties.get('--editing-viewport-height'), '260px');
    assert.equal(f.properties.get('--editing-viewport-top'), '22px');
    assert.equal(f.classes.has('browser-editing-keyboard'), true);
    assert.equal(f.classes.has('browser-terminal-keyboard'), editor.includes('terminal'));
    assert.equal(f.visualViewport.scale, 1.5);
  }
});

test('pinch zoom without keyboard preserves zoom and avoids keyboard compaction', () => {
  const f = fixture();
  f.focus('.composer textarea');
  f.visualViewport.scale = 1.5;
  f.visualViewport.height = 520;
  f.fire('viewport', 'resize');
  assert.equal(f.classes.has('browser-editing-viewport'), true);
  assert.equal(f.classes.has('browser-editing-keyboard'), false);
  assert.equal(f.properties.get('--editing-viewport-height'), '520px');
});

test('resting height follows browser chrome or orientation before editor focus', () => {
  const f = fixture();
  f.setBodyHeight(550);
  f.window.innerHeight = 550;
  f.visualViewport.height = 550;
  f.fire('window', 'resize');
  f.focus('.composer textarea');
  assert.equal(f.classes.has('browser-editing-keyboard'), false);
  f.visualViewport.height = 310;
  f.fire('viewport', 'resize');
  assert.equal(f.classes.has('browser-editing-keyboard'), true);
});

test('rotating while focused recalculates the resting viewport height', () => {
  const f = fixture();
  f.focus('.composer textarea');
  f.visualViewport.height = 400;
  f.fire('viewport', 'resize');
  assert.equal(f.classes.has('browser-editing-keyboard'), true);
  f.document.documentElement.clientWidth = 844;
  f.window.innerHeight = 390;
  f.visualViewport.height = 390;
  f.fire('window', 'resize');
  assert.equal(f.classes.has('browser-editing-keyboard'), false);
  assert.equal(f.properties.get('--editing-viewport-height'), '390px');
});

test('native hosts and non-editor focus do not move the shell', () => {
  for (const userAgent of ['BridgeMobile/1-iOS', 'BridgeMobile/1-Android']) {
    const f = fixture(userAgent);
    assert.equal(f.htmlClasses.has('bridge-browser'),false);
    f.focus('.composer textarea');
    f.visualViewport.height = 400;
    f.fire('viewport', 'resize');
    assert.equal(f.classes.size, 0);
    assert.equal(f.properties.size, 0);
  }
  const f = fixture();
  assert.equal(f.htmlClasses.has('bridge-browser'),true);
  f.focus('.login input');
  assert.equal(f.classes.size, 0);
});

test('switching tabs clears viewport override even while keyboard remains open', async () => {
  const f = fixture();
  f.visualViewport.height = 400;
  f.focus('.wb-terminal-form textarea');
  assert.equal(f.classes.has('browser-editing-viewport'), true);
  f.document.activeElement = {matches: () => false};
  f.fire('document', 'click');
  assert.equal(f.classes.size, 0);
  assert.equal(f.properties.size, 0);

  f.focus('.composer textarea');
  f.document.activeElement = null;
  f.fire('document', 'focusout');
  await Promise.resolve();
  assert.equal(f.classes.size, 0);
});

test('visual viewport panning and duplicate resize events do not write shell geometry', () => {
  const f = fixture();
  f.focus('.wb-terminal-input textarea');
  f.visualViewport.height = 400;
  f.visualViewport.offsetTop = 18;
  f.fire('viewport', 'resize');
  const writes = f.writes.length;
  const top = f.properties.get('--editing-viewport-top');
  for (const offset of [24, 80, 12]) {
    f.visualViewport.offsetTop = offset;
    f.fire('viewport', 'scroll');
    f.fire('viewport', 'resize');
    f.fire('window', 'resize');
  }
  assert.equal(f.writes.length, writes);
  assert.equal(f.properties.get('--editing-viewport-top'), top);
});

test('focused terminal preserves its inner scroll position when shell layout changes', () => {
  const f = fixture();
  const output = {scrollTop:500, scrollHeight:1500, clientHeight:500};
  f.resetOutputOnLayout(output);
  f.document.activeElement = {
    matches: selector => selector.includes('.wb-terminal-input textarea'),
    closest: selector => selector==='.wb-terminal'?{querySelector:()=>output}:null,
  };
  f.fire('document', 'focusin');
  assert.equal(output.scrollTop,500);
  f.visualViewport.height=400;
  f.fire('viewport', 'resize');
  assert.equal(output.scrollTop,500);
});
