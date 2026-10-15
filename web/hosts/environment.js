'use strict';
// Host identity and native layout readiness are separate from viewport size.
// These hints choose UI behavior only; native bridges still verify token/origin/frame.
const BridgeHost = (() => {
  function kind(userAgent = globalThis.navigator?.userAgent || '') {
    const match = /BridgeMobile\/[\w.-]+-(iOS|Android)(?:\s|$)/.exec(userAgent);
    return match ? (match[1] === 'iOS' ? 'ios' : 'android') : 'browser';
  }
  function isNativeApp(userAgent) { return kind(userAgent) !== 'browser'; }
  function hasNativeLayout() {
    // Native injection can finish after deferred page scripts have executed.
    return !!globalThis.document?.documentElement?.classList.contains('bridge-mobile');
  }
  return Object.freeze({kind, isNativeApp, hasNativeLayout});
})();
if (typeof module === 'object' && module.exports) module.exports = BridgeHost;
if(typeof document!=='undefined'&&BridgeHost.kind()==='browser')document.documentElement?.classList?.add?.('bridge-browser');

// Mobile browsers can pan or zoom the visual viewport around a focused editor
// while the 100dvh shell remains tied to the layout viewport.
if(typeof window!=='undefined'&&window.visualViewport&&typeof document!=='undefined'){
  const viewport=window.visualViewport;
  let restingHeight=0;
  let restingWidth=0;
  let appliedHeight=null;
  const updateEditorViewport=()=>{
    const body=document.body;
    if(!body)return;
    const input=document.activeElement;
    const editor=input?.matches?.('.composer textarea, .wb-terminal-input textarea, .wb-terminal-form textarea');
    const active=BridgeHost.kind()==='browser'&&editor;
    const output=active&&(input.closest?.('.wb-terminal')?.querySelector('.xterm-viewport,.wb-terminal-output')
      ||input.closest?.('.wb-side-content')?.querySelector('.wb-side-messages')
      ||input.closest?.('.chat')?.querySelector('.timeline'));
    const scrollTop=output?.scrollTop;
    const atEnd=output&&output.scrollHeight-output.clientHeight-scrollTop<2;
    if(active&&(!restingHeight||restingWidth!==document.documentElement.clientWidth)){
      restingHeight=restingWidth?Math.max(window.innerHeight,viewport.height*viewport.scale):Math.max(body.getBoundingClientRect().height,window.innerHeight,viewport.height*viewport.scale);
      restingWidth=document.documentElement.clientWidth;
    }
    const keyboard=active&&viewport.height*viewport.scale<restingHeight-100;
    body.classList.toggle('browser-editing-viewport',!!active);
    body.classList.toggle('browser-editing-keyboard',!!keyboard);
    body.classList.toggle('browser-terminal-keyboard',!!keyboard&&document.activeElement.matches('.wb-terminal-input textarea, .wb-terminal-form textarea'));
    if(active){
      if(appliedHeight!==viewport.height){
        appliedHeight=viewport.height;
        body.style.setProperty('--editing-viewport-height',viewport.height+'px');
        body.style.setProperty('--editing-viewport-top',viewport.offsetTop+'px');
      }
    }else{
      restingHeight=0;
      restingWidth=0;
      if(appliedHeight!==null){
        appliedHeight=null;
        body.style.removeProperty('--editing-viewport-height');
        body.style.removeProperty('--editing-viewport-top');
      }
    }
    if(output&&output.scrollTop!==scrollTop)output.scrollTop=atEnd?output.scrollHeight-output.clientHeight:scrollTop;
  };
  document.addEventListener('focusin',updateEditorViewport);
  document.addEventListener('focusout',()=>queueMicrotask(updateEditorViewport));
  document.addEventListener('click',updateEditorViewport);
  // Moving the focused shell on visual-viewport scroll can trigger another
  // Safari caret scroll, so only resize changes its position.
  viewport.addEventListener('resize',updateEditorViewport);
  window.addEventListener('resize',updateEditorViewport);
}
