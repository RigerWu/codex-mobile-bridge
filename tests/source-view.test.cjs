'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');

const root = path.resolve(__dirname, '..');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.className = ''; this.value = ''; this.classList = {add:name=>{this.className += ' ' + name;}}; }
  get textContent() { return this.value + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this.value = value; this.children = []; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.value = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const context = {window:{}, AbortController, document:{createElement:tag=>new Element(tag), createTextNode:text=>({textContent:text})}};
vm.createContext(context);
for (const file of ['web/vendor/prism/source-view.min.js', 'web/features/workspace/source-view.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context, {filename:file});
}
const sourceView = vm.runInContext('BridgeSourceView', context);
const tokens = node => [node, ...(node.children || []).flatMap(tokens)];

test('bundled grammars cover the supported source types', () => {
  const names = ['x.py','x.js','x.ts','x.jsx','x.tsx','x.json','x.yaml','x.toml','x.html','x.css','x.sh','x.ps1','x.cmd'];
  for (const name of names) {
    const language = sourceView.languageFor(name);
    assert.ok(language, name);
    assert.ok(context.window.Prism.languages[language], name);
    assert.match(context.window.Prism.highlight('const value = "中文"', context.window.Prism.languages[language], language), /token|const/);
  }
  assert.equal(sourceView.languageFor('README.unknown'), null);
});

test('highlighted code preserves every source character and keeps line numbers separate', () => {
  const original = 'const 中文 = "<&>\u00a0";\r\n\tconsole.log(中文);\r\n';
  const view = sourceView.create({name:'sample.js', text:original});
  const [gutter, pre] = view.children, code = pre.children[0];
  assert.equal(gutter.textContent, '1\n2\n3');
  assert.equal(gutter.attributes['aria-hidden'], 'true');
  assert.equal(code.textContent, original);
  assert.ok(tokens(code).some(node => node.className?.includes('token')));
  assert.ok(tokens(code).some(node => node.textContent?.includes('\u00a0')));
  assert.equal(tokens(code).filter(node => node.tagName === 'img' || node.tagName === 'script').length, 0);
});

test('all bundled grammars preserve CRLF, lone CR, NBSP, tabs and hostile markup as text', () => {
  const original = '<script>\u00a0中文\t</script>\r\nnext\rline';
  for (const name of ['x.py','x.js','x.ts','x.jsx','x.tsx','x.json','x.yaml','x.toml','x.html','x.css','x.sh','x.ps1','x.cmd']) {
    const code = sourceView.create({name, text:original}).children[1].children[0];
    assert.equal(code.textContent, original, name);
    assert.equal(tokens(code).filter(node => node.tagName === 'script').length, 0, name);
  }
});

test('unknown or expensive source falls back to original text', () => {
  const unknown = sourceView.create({name:'opaque.xyz', text:'\t中文\n\nend'});
  assert.equal(unknown.children[1].children[0].textContent, '\t中文\n\nend');
  assert.equal(unknown.children[0].textContent, '1\n2\n3');
  const big = 'print("x")\n'.repeat(7000);
  const view = sourceView.create({name:'big.py', text:big});
  assert.equal(view.dataset.plain, 'true');
  assert.equal(view.children[1].children[0].textContent, big);
  const manyLines = sourceView.create({name:'many.py', text:'\n'.repeat(10001)});
  assert.equal(manyLines.children.length, 1);
  assert.equal(manyLines.children[0].children[0].textContent, '\n'.repeat(10001));
});

test('line numbers follow LF, CRLF and CR without normalizing source', () => {
  const original = 'a\r\nb\rc\nd';
  const view = sourceView.create({name:'a.txt', text:original});
  assert.equal(view.children[0].textContent, '1\n2\n3\n4');
  assert.equal(view.children[1].children[0].textContent, original);
});

test('file preview copies the original source rather than rendered tokens or line numbers', async () => {
  context.BridgeI18n = {t:text=>text};
  const copied = [];
  context.window.BridgeClipboard = {copy:async getText=>copied.push(await getText())};
  vm.runInContext(fs.readFileSync(path.join(root, 'web/features/workspace/workbench.js'), 'utf8'), context);
  const Workbench = vm.runInContext('Workbench', context);
  const workbench = Object.create(Workbench.prototype);
  const session = {key:'test', files:[]};
  const original = 'const x = "\u00a0";\r\n';
  Object.assign(workbench, {
    current:session, sessions:new Map([[session.key, session]]),
    node:(tag, cls='', text)=>{const element=new Element(tag);element.className=cls;if(text!==undefined)element.textContent=text;return element;},
    button:(text, action)=>{const button=new Element('button');button.textContent=text;button.onclick=action;return button;},
    raw:element=>element, select:()=>{}, url:()=>'/fixture',
    request:async()=>({kind:'text', name:'file.js', size:original.length, text:original})
  });
  await workbench.preview(session, {name:'file.js', path:'file.js'});
  const [head, content] = session.files[0].body.children;
  assert.equal(content.children[0].children[1].children[0].textContent, original);
  const copy = head.children.find(child=>child.className==='wb-source-copy');
  assert.ok(copy);
  await copy.onclick();
  assert.deepEqual(copied, [original]);
});
