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

// The software keyboard shrinks the visual viewport without shrinking 100dvh
// in mobile browsers. Keep the terminal's flex layout inside the visible area.
if(typeof window!=='undefined'&&window.visualViewport&&typeof document!=='undefined'){
  const viewport=window.visualViewport;
  const updateTerminalViewport=()=>{
    const body=document.body;
    if(!body)return;
    const terminalInput=document.activeElement?.matches?.('.wb-terminal-input textarea, .wb-terminal-form textarea');
    const active=BridgeHost.kind()==='browser'&&terminalInput&&viewport.scale===1&&viewport.height<window.innerHeight-100;
    body.classList.toggle('browser-terminal-viewport',!!active);
    body.classList.toggle('browser-terminal-keyboard',!!active);
    if(active){
      body.style.setProperty('--terminal-viewport-height',viewport.height+'px');
      body.style.setProperty('--terminal-viewport-top',viewport.offsetTop+'px');
    }else{
      body.style.removeProperty('--terminal-viewport-height');
      body.style.removeProperty('--terminal-viewport-top');
    }
  };
  document.addEventListener('focusin',updateTerminalViewport);
  document.addEventListener('focusout',()=>queueMicrotask(updateTerminalViewport));
  document.addEventListener('click',updateTerminalViewport);
  viewport.addEventListener('resize',updateTerminalViewport);
  viewport.addEventListener('scroll',updateTerminalViewport);
  window.addEventListener('resize',updateTerminalViewport);
}
