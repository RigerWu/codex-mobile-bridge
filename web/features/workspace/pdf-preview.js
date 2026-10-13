'use strict';

// Only the current page is decoded. The transfer URL is the existing authenticated
// workspace download endpoint, so PDF.js can use its byte-range responses.
const BridgePdfPreview = (() => {
  const MAX_BYTES = 20 * 1024 * 1024;
  const MAX_PIXELS = 4 * 1024 * 1024;
  const base = () => window.BridgeRelayBase || '';
  let libraryPromise;
  const library = () => libraryPromise ||= import(base() + '/vendor/pdfjs/pdf.min.mjs?v=5.6.205');
  const allowed = info => Number.isSafeInteger(info?.size) && info.size >= 0
    && Number.isInteger(info?.clickDownloadMiB) && info.clickDownloadMiB > 0
    && info.size <= Math.min(MAX_BYTES, info.clickDownloadMiB * 1024 * 1024);
  const button = (label, action) => {
    const node = document.createElement('button');
    node.type = 'button'; node.textContent = BridgeI18n.t(label);
    node.dataset.i18n = label; node.setAttribute('aria-label', BridgeI18n.t(label));
    node.dataset.i18nAriaLabel = label; node.onclick = action;
    return node;
  };
  const errorMessage = error => {
    if (error?.name === 'PasswordException') return '此 PDF 已加密，请下载后使用其他应用打开。';
    if (error?.name === 'InvalidPDFException' || error?.name === 'FormatError') return 'PDF 文件已损坏或格式无效，请下载后检查。';
    return 'PDF 预览失败，请下载后使用其他应用打开。';
  };
  const create = ({url, info, loadLibrary = library}) => {
    const element = document.createElement('div'); element.className = 'wb-pdf';
    const toolbar = document.createElement('div'); toolbar.className = 'wb-pdf-toolbar';
    const stage = document.createElement('div'); stage.className = 'wb-pdf-stage';
    const status = document.createElement('p'); status.className = 'wb-status'; status.setAttribute('role', 'status');
    const pageLabel = document.createElement('span'); pageLabel.className = 'wb-pdf-page';
    const zoomLabel = document.createElement('span'); zoomLabel.className = 'wb-pdf-zoom';
    let documentProxy, loadingTask, renderTask, textLayer, currentPage, disposed = false, generation = 0;
    let page = 1, scale = 1;
    const previous = button('上一页', () => { if (page > 1) { page--; render(); } });
    const next = button('下一页', () => { if (documentProxy && page < documentProxy.numPages) { page++; render(); } });
    const smaller = button('缩小', () => { if (scale > .5) { scale = Math.max(.5, +(scale - .25).toFixed(2)); render(); } });
    const larger = button('放大', () => { if (scale < 2) { scale = Math.min(2, +(scale + .25).toFixed(2)); render(); } });
    const refresh = () => {
      pageLabel.textContent = documentProxy ? `${page} / ${documentProxy.numPages}` : '';
      zoomLabel.textContent = `${Math.round(scale * 100)}%`;
      previous.disabled = !documentProxy || page <= 1; next.disabled = !documentProxy || page >= documentProxy.numPages;
      smaller.disabled = !documentProxy || scale <= .5; larger.disabled = !documentProxy || scale >= 2;
    };
    toolbar.append(previous, pageLabel, next, smaller, zoomLabel, larger);
    element.append(toolbar, status, stage); refresh();
    const cancelRender = () => {
      generation++;
      const task = renderTask, oldPage = currentPage;
      task?.cancel(); textLayer?.cancel(); renderTask = textLayer = null; currentPage = null;
      if (oldPage) {
        if (task) task.promise.finally(() => oldPage.cleanup()).catch(() => {});
        else oldPage.cleanup();
      }
    };
    const render = async () => {
      if (!documentProxy || disposed) return;
      cancelRender(); const token = generation; refresh();
      status.classList.remove('error');
      status.textContent = BridgeI18n.t('正在渲染 PDF…'); status.hidden = false;
      stage.replaceChildren();
      try {
        const pdfPage = await documentProxy.getPage(page);
        if (disposed || token !== generation) { pdfPage.cleanup(); return; }
        currentPage = pdfPage;
        const viewport = pdfPage.getViewport({scale});
        const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(MAX_PIXELS / (viewport.width * viewport.height)));
        if (!Number.isFinite(ratio) || ratio <= 0) throw Error('Invalid PDF dimensions');
        const sheet = document.createElement('div'); sheet.className = 'wb-pdf-sheet';
        sheet.style.width = `${viewport.width}px`; sheet.style.height = `${viewport.height}px`;
        sheet.style.setProperty('--total-scale-factor', String(viewport.scale));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.floor(viewport.width * ratio));
        canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
        canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
        sheet.append(canvas); stage.append(sheet);
        const context = canvas.getContext('2d', {alpha:false});
        if (!context) throw Error('Canvas unavailable');
        const task = renderTask = pdfPage.render({canvasContext:context, viewport,
          transform:[ratio, 0, 0, ratio, 0, 0], annotationMode:0});
        await task.promise;
        if (disposed || token !== generation) return;
        renderTask = null;
        const text = await pdfPage.getTextContent();
        if (disposed || token !== generation) return;
        if (text.items.length) {
          const overlay = document.createElement('div'); overlay.className = 'wb-pdf-text textLayer';
          sheet.append(overlay);
          const pdfjs = await loadLibrary();
          if (disposed || token !== generation) return;
          textLayer = new pdfjs.TextLayer({textContentSource:text, container:overlay, viewport});
          await textLayer.render();
        }
        if (disposed || token !== generation) return;
        status.hidden = true;
      } catch (error) {
        if (disposed || token !== generation || error?.name === 'RenderingCancelledException') return;
        stage.replaceChildren(); status.hidden = false; status.classList.add('error');
        status.textContent = BridgeI18n.t(errorMessage(error));
      }
    };
    const load = async () => {
      if (!allowed(info)) {
        status.classList.add('error');
        status.textContent = BridgeI18n.t('PDF 超过在线预览或当前下载限额，请下载后打开。');
        return;
      }
      status.textContent = BridgeI18n.t('正在打开 PDF…');
      try {
        const pdfjs = await loadLibrary(); if (disposed) return;
        pdfjs.GlobalWorkerOptions.workerSrc = base() + '/vendor/pdfjs/pdf.worker.min.mjs?v=5.6.205';
        loadingTask = pdfjs.getDocument({url:url + '&preview=1', rangeChunkSize:65536, disableStream:true, disableAutoFetch:true,
          cMapUrl:base() + '/vendor/pdfjs/cmaps/', cMapPacked:true,
          standardFontDataUrl:base() + '/vendor/pdfjs/standard_fonts/',
          isEvalSupported:false, enableXfa:false, useWasm:false});
        documentProxy = await loadingTask.promise; if (disposed) { await loadingTask.destroy(); return; }
        await render();
      } catch (error) {
        if (disposed) return;
        status.classList.add('error'); status.textContent = BridgeI18n.t(errorMessage(error));
      }
    };
    const dispose = () => {
      if (disposed) return; disposed = true; cancelRender();
      loadingTask?.destroy(); documentProxy = null; stage.replaceChildren();
    };
    return {element, load, dispose};
  };
  return {create, allowed, MAX_BYTES};
})();
if (typeof module !== 'undefined') module.exports = BridgePdfPreview;
