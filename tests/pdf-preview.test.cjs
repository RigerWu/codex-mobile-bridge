'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const pdf = require('../web/features/workspace/pdf-preview.js');

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={}; this.style={setProperty(){}}; this.className=''; this.classList={add:name=>this.className+=' '+name,remove:name=>this.className=this.className.replace(' '+name,'')}; this.value=''; }
  get textContent() { return this.value+this.children.map(child=>child.textContent).join(''); }
  set textContent(value) { this.value=value; this.children=[]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.value=''; this.children=children; }
  setAttribute(name,value) { this.attributes[name]=value; }
  getContext() { return {}; }
}
const element = tag => new Element(tag);

test('PDF preview enforces both local memory and current click-download limits before loading', async () => {
  global.document={createElement:element}; global.BridgeI18n={t:text=>text};
  assert.equal(pdf.allowed({size:1024*1024,clickDownloadMiB:1}),true);
  assert.equal(pdf.allowed({size:1024*1024+1,clickDownloadMiB:1}),false);
  assert.equal(pdf.allowed({size:20*1024*1024,clickDownloadMiB:100}),true);
  assert.equal(pdf.allowed({size:20*1024*1024+1,clickDownloadMiB:100}),false);
  assert.equal(pdf.allowed({size:null,clickDownloadMiB:20}),false);
  const reader=pdf.create({url:'/api/workspace/download',info:{size:21*1024*1024,clickDownloadMiB:100}});
  await reader.load();
  assert.match(reader.element.textContent,/PDF 超过在线预览/);
  reader.dispose(); reader.dispose();
  delete global.document; delete global.BridgeI18n;
});

test('rapid page changes cancel old rendering and closing destroys the document', async () => {
  global.document={createElement:element}; global.BridgeI18n={t:text=>text};
  global.window={BridgeRelayBase:'',devicePixelRatio:1};
  const tasks=[], pages=[], loading={destroy(){tasks.push('destroy');}};
  let documentUrl;
  const doc={numPages:2,async getPage(number){
    pages.push(number);
    return {getViewport:({scale})=>({width:100*scale,height:100*scale,scale}),
      cleanup(){tasks.push('cleanup '+number);},
      render(){let reject;const promise=new Promise((_,fail)=>{reject=fail;});
        const task={promise,cancel(){tasks.push('cancel '+number);reject({name:'RenderingCancelledException'});}};
        tasks.push(task); return task;},
      async getTextContent(){return {items:[]};}};
  }};
  loading.promise=Promise.resolve(doc);
  const reader=pdf.create({url:'/api/workspace/download?path=file.pdf',info:{size:1000,clickDownloadMiB:1},
    loadLibrary:async()=>({GlobalWorkerOptions:{},getDocument:options=>{documentUrl=options.url;return loading;}})});
  const pending=reader.load();
  await new Promise(setImmediate);
  assert.deepEqual(pages,[1]);
  assert.equal(documentUrl,'/api/workspace/download?path=file.pdf&preview=1');
  reader.element.children[0].children[2].onclick();
  await new Promise(setImmediate);
  assert.deepEqual(pages,[1,2]);
  assert.ok(tasks.includes('cancel 1'));
  assert.ok(tasks.includes('cleanup 1'));
  reader.dispose(); await pending;
  assert.ok(tasks.includes('cancel 2'));
  assert.ok(tasks.includes('destroy'));
  delete global.document; delete global.BridgeI18n; delete global.window;
});

test('workbench uses download info and disposes PDF when the tab closes', async () => {
  const calls=[], events=[];
  const context={window:{}, AbortController, BridgeI18n:{t:text=>text},
    BridgePdfPreview:{create:options=>{events.push(['create',options]);return {element:element('reader'),load:()=>events.push(['load']),dispose:()=>events.push(['dispose'])};}},
    document:{createElement:element}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root,'web/features/workspace/workbench.js'),'utf8'),context);
  const Workbench=vm.runInContext('Workbench',context);
  const workbench=Object.create(Workbench.prototype), session={key:'fixture',files:[],active:'chat'};
  Object.assign(workbench,{current:session,sessions:new Map([[session.key,session]]),
    node:(tag,cls='',text)=>{const node=element(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;},
    button:(text,action)=>{const node=element('button');node.textContent=text;node.onclick=action;return node;},
    raw:node=>node,select:()=>{},paint:()=>{},url:(_session,operation)=>'/api/workspace/'+operation+'?path=file.pdf',
    request:async url=>{calls.push(url);return {name:'file.pdf',size:5,clickDownloadMiB:20};}});
  await workbench.preview(session,{name:'file.pdf',path:'file.pdf'});
  assert.deepEqual(calls,['/api/workspace/download?path=file.pdf&info=1']);
  assert.deepEqual(events.map(event=>event[0]),['create','load']);
  assert.equal(session.files[0].body.children[0].children[1].href,'/api/workspace/download?path=file.pdf');
  await workbench.close('file:file.pdf');
  assert.deepEqual(events.map(event=>event[0]),['create','load','dispose']);
});

test('HTML preview switches to the original source and disposes its script frame', async () => {
  const lifecycle=[], original='<button>中文</button>\r\n<script>go()</script>';
  const context={window:{},AbortController,BridgeI18n:{t:text=>text},
    BridgeHtmlPreview:{create:({text})=>{assert.equal(text,original);lifecycle.push('create');return {element:element('frame'),dispose:()=>lifecycle.push('dispose')};}},
    BridgeSourceView:{create:({text})=>{const view=element('source');view.textContent=text;return view;}},
    document:{createElement:element}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root,'web/features/workspace/workbench.js'),'utf8'),context);
  const Workbench=vm.runInContext('Workbench',context), workbench=Object.create(Workbench.prototype);
  const session={key:'fixture',files:[],active:'chat'};
  Object.assign(workbench,{current:session,sessions:new Map([[session.key,session]]),
    node:(tag,cls='',text)=>{const node=element(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;},
    button:(text,action)=>{const node=element('button');node.textContent=text;node.onclick=action;return node;},
    raw:node=>node,select:()=>{},paint:()=>{},url:()=>'/api/workspace',
    request:async()=>({kind:'text',name:'page.html',size:original.length,text:original})});
  await workbench.preview(session,{name:'page.html',path:'page.html'});
  const [head,content]=session.files[0].body.children;
  assert.equal(content.children[0].tagName,'frame');
  head.children[3].onclick();
  assert.equal(content.children[0].textContent,original);
  assert.deepEqual(lifecycle,['create','dispose']);
  head.children[2].onclick();
  await workbench.close('file:page.html');
  assert.deepEqual(lifecycle,['create','dispose','create','dispose']);
});
