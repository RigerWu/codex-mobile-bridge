'use strict';
const BridgeHtmlPreview=(()=>{
  const maxBytes=512*1024;
  function create({text,onEscape}){
    if(typeof text!=='string'||new TextEncoder().encode(text).length>maxBytes)throw Error('HTML 预览内容超出文本大小限制');
    const element=document.createElement('div');element.className='html-preview-frame';
    const frame=document.createElement('iframe');frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');
    frame.setAttribute('title',typeof BridgeI18n==='undefined'?'HTML 预览':BridgeI18n.t('HTML 预览'));frame.setAttribute('data-i18n-title','HTML 预览');
    frame.src=(window.BridgeRelayBase||'')+'/html-preview-shell';element.append(frame);
    let disposed=false,ready=false;
    const cleanup=()=>{window.removeEventListener('message',receive);clearTimeout(timer);};
    const fail=()=>{cleanup();if(disposed)return;frame.remove();const notice=document.createElement('p');notice.textContent=typeof BridgeI18n==='undefined'?'HTML 预览无法加载，请查看源代码或下载文件':BridgeI18n.t('HTML 预览无法加载，请查看源代码或下载文件');notice.setAttribute('data-i18n','HTML 预览无法加载，请查看源代码或下载文件');element.append(notice);};
    const receive=event=>{
      if(disposed||event.source!==frame.contentWindow||event.origin!=='null')return;
      if(ready&&event.data?.type==='bridge-html-preview-escape'){onEscape?.();return;}
      if(ready||event.data?.type!=='bridge-html-preview-ready')return;
      ready=true;clearTimeout(timer);
      if(typeof onEscape!=='function')window.removeEventListener('message',receive);
      try{frame.contentWindow.postMessage({type:'bridge-html-preview-content',text},'*');}catch{fail();}
    };
    window.addEventListener('message',receive);
    const timer=setTimeout(fail,10000);
    return {element,dispose(){if(disposed)return;disposed=true;cleanup();frame.remove();}};
  }
  return {create};
})();
if(typeof module!=='undefined')module.exports=BridgeHtmlPreview;
