'use strict';

const BridgePdfPreview = (() => {
  const MAX_BYTES = 20 * 1024 * 1024, MAX_PIXELS = 4 * 1024 * 1024;
  const MIN_ZOOM = .5, MAX_ZOOM = 2, GAP = 16, TOTAL_PIXELS = 12 * 1024 * 1024;
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
    const pages = document.createElement('div'); pages.className = 'wb-pdf-pages';
    const status = document.createElement('p'); status.className = 'wb-status'; status.setAttribute('role', 'status');
    const pageLabel = document.createElement('span'); pageLabel.className = 'wb-pdf-page';
    const zoomLabel = document.createElement('span'); zoomLabel.className = 'wb-pdf-zoom';
    const touch = !!window.matchMedia?.('(pointer: coarse)').matches;
    if (touch) element.classList.add('wb-pdf-touch');
    let documentProxy, loadingTask, resizeObserver, started = false, disposed = false;
    let slots = [], fit = 1, zoom = 1, firstWidth = 0, firstHeight = 0, current = 1, measuredWidth = 0;
    let scheduled = null, gesture = null;
    const availableWidth = () => Math.max(1, stage.clientWidth - 32);
    const wrapperWidth = () => slots.reduce((max, slot) => Math.max(max, slot.width), availableWidth());
    const topOf = index => slots.slice(0, index).reduce((sum, slot) => sum + slot.height + GAP, 0);
    const locate = y => {
      let top = 0;
      for (const slot of slots) {
        if (y < top + slot.height + GAP)
          return {slot, top, fraction:Math.max(0, Math.min(1, (y - top) / slot.height))};
        top += slot.height + GAP;
      }
      const slot = slots[slots.length - 1];
      return slot && {slot, top:top - slot.height - GAP, fraction:1};
    };
    const anchorAt = (viewportY, viewportX = stage.clientWidth / 2) => {
      const found = locate((stage.scrollTop || 0) + viewportY);
      return found && {number:found.slot.number, fraction:found.fraction, viewportY,
        viewportX, fractionX:Math.max(0, Math.min(1,
          ((stage.scrollLeft || 0) + viewportX - 16 - (wrapperWidth() - found.slot.width) / 2) / found.slot.width))};
    };
    const refresh = () => {
      if (slots.length)
        current = locate((stage.scrollTop || 0) + Math.min(80, (stage.clientHeight || 800) / 4))?.slot.number || 1;
      pageLabel.textContent = documentProxy ? current + ' / ' + documentProxy.numPages : '';
      zoomLabel.textContent = Math.round(zoom * 100) + '%';
      previous.disabled = !documentProxy || current <= 1;
      next.disabled = !documentProxy || current >= documentProxy.numPages;
      smaller.disabled = !documentProxy || zoom <= MIN_ZOOM;
      larger.disabled = !documentProxy || zoom >= MAX_ZOOM;
    };
    const go = delta => {
      if (!documentProxy) return;
      const number = Math.max(1, Math.min(documentProxy.numPages, current + delta));
      stage.scrollTop = topOf(number - 1); current = number; refresh(); schedule();
    };
    const previous = button('上一页', () => go(-1));
    const next = button('下一页', () => go(1));
    const smaller = button('缩小', () => changeZoom(Math.max(MIN_ZOOM, +(zoom - .25).toFixed(2))));
    const larger = button('放大', () => changeZoom(Math.min(MAX_ZOOM, +(zoom + .25).toFixed(2))));
    toolbar.append(previous, pageLabel, next, smaller, zoomLabel, larger);
    stage.tabIndex = 0; stage.append(pages); element.append(toolbar, status, stage); refresh();

    const retire = slot => {
      slot.token++;
      const task = slot.renderTask, proxy = slot.proxy;
      task?.cancel(); slot.textLayer?.cancel();
      slot.renderTask = slot.textLayer = slot.proxy = null;
      slot.loading = slot.ready = false;
      slot.budget = slot.textEnabled = null;
      if (slot.canvas) { slot.canvas.width = slot.canvas.height = 0; slot.canvas = null; }
      slot.element.replaceChildren();
      if (proxy) {
        const cleanup = () => { if (slot.proxy !== proxy) proxy.cleanup(); };
        if (task) task.promise.finally(cleanup).catch(() => {});
        else cleanup();
      }
    };
    const sizeSlot = (slot, preserveScroll = true) => {
      const old = slot.height;
      const anchor = preserveScroll && old ? anchorAt(0) : null;
      slot.width = slot.naturalWidth * fit * zoom;
      slot.height = slot.naturalHeight * fit * zoom;
      if (!Number.isFinite(slot.width) || !Number.isFinite(slot.height)
          || slot.width <= 0 || slot.height <= 0) throw Error('Invalid PDF dimensions');
      slot.element.style.width = slot.width + 'px';
      slot.element.style.height = slot.height + 'px';
      slot.element.style.setProperty('--total-scale-factor', String(fit * zoom));
      if (anchor) {
        const anchored = slots[anchor.number - 1];
        stage.scrollTop = Math.max(0, topOf(anchor.number - 1) + anchored.height * anchor.fraction);
        stage.scrollLeft = Math.max(0, 16 + (wrapperWidth() - anchored.width) / 2
          + anchored.width * anchor.fractionX - anchor.viewportX);
      }
    };
    const hydrate = async (slot, budget, textEnabled) => {
      if (slot.loading || slot.ready || slot.failed || disposed) return;
      slot.loading = true;
      slot.budget = budget; slot.textEnabled = textEnabled;
      const token = ++slot.token;
      try {
        const proxy = await documentProxy.getPage(slot.number);
        if (disposed || token !== slot.token) { proxy.cleanup(); return; }
        slot.proxy = proxy;
        const natural = proxy.getViewport({scale:1});
        if (!Number.isFinite(natural.width) || !Number.isFinite(natural.height)
            || natural.width <= 0 || natural.height <= 0) throw Error('Invalid PDF dimensions');
        slot.naturalWidth = natural.width; slot.naturalHeight = natural.height;
        sizeSlot(slot); schedule();
        const viewport = proxy.getViewport({scale:fit * zoom});
        const ratio = Math.min(window.devicePixelRatio || 1, 2,
          Math.sqrt(budget / (viewport.width * viewport.height)),
          16384 / viewport.width, 16384 / viewport.height);
        if (!Number.isFinite(ratio) || ratio <= 0) throw Error('Invalid PDF dimensions');
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.floor(viewport.width * ratio));
        canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
        canvas.style.width = viewport.width + 'px'; canvas.style.height = viewport.height + 'px';
        slot.canvas = canvas; slot.element.append(canvas);
        const context = canvas.getContext('2d', {alpha:false});
        if (!context) throw Error('Canvas unavailable');
        const task = slot.renderTask = proxy.render({canvasContext:context, viewport,
          transform:[ratio, 0, 0, ratio, 0, 0], annotationMode:0});
        await task.promise;
        if (disposed || token !== slot.token) return;
        slot.renderTask = null;
        if (textEnabled) {
          const text = await proxy.getTextContent();
          if (disposed || token !== slot.token) return;
          if (text.items.length) {
            const overlay = document.createElement('div'); overlay.className = 'wb-pdf-text textLayer';
            slot.element.append(overlay);
            const pdfjs = await loadLibrary();
            if (disposed || token !== slot.token) return;
            slot.textLayer = new pdfjs.TextLayer({textContentSource:text, container:overlay, viewport});
            await slot.textLayer.render();
          }
        }
        if (disposed || token !== slot.token) return;
        slot.loading = false; slot.ready = true; status.hidden = true; schedule();
      } catch (error) {
        if (disposed || token !== slot.token || error?.name === 'RenderingCancelledException') return;
        retire(slot); slot.failed = true;
        status.hidden = false; status.classList.add('error');
        status.textContent = BridgeI18n.t(errorMessage(error));
      }
    };
    const reconcile = () => {
      scheduled = null;
      if (disposed || !documentProxy || gesture || !slots.length) return;
      const viewTop = stage.scrollTop || 0, viewBottom = viewTop + stage.clientHeight;
      const centerY = (viewTop + viewBottom) / 2;
      const wanted = new Set();
      let top = 0;
      for (const slot of slots) {
        if (top < viewBottom && top + slot.height > viewTop) wanted.add(slot.number);
        top += slot.height + GAP;
      }
      if (!wanted.size) wanted.add(locate(centerY)?.slot.number || 1);
      if (wanted.size < 3) {
        const center = locate(centerY)?.slot.number || 1;
        for (const number of [center - 1, center, center + 1])
          if (number >= 1 && number <= slots.length && wanted.size < 3) wanted.add(number);
      }
      const budget = Math.min(MAX_PIXELS, Math.floor(TOTAL_PIXELS / wanted.size));
      const textPages = new Set([...wanted].sort((a,b) => {
        const middle = number => topOf(number - 1) + slots[number - 1].height / 2;
        return Math.abs(middle(a) - centerY) - Math.abs(middle(b) - centerY);
      }).slice(0, 3));
      for (const slot of slots) {
        if ((!wanted.has(slot.number) || slot.budget !== budget
            || slot.textEnabled !== textPages.has(slot.number))
            && (slot.loading || slot.ready || slot.proxy)) retire(slot);
      }
      for (const number of wanted) hydrate(slots[number - 1], budget, textPages.has(number));
      refresh();
    };
    const schedule = () => {
      if (scheduled !== null || disposed) return;
      if (typeof requestAnimationFrame === 'function') scheduled = requestAnimationFrame(reconcile);
      else { scheduled = true; queueMicrotask(reconcile); }
    };
    const relayout = anchor => {
      for (const slot of slots) { retire(slot); slot.failed = false; sizeSlot(slot, false); }
      if (anchor) {
        stage.scrollTop = Math.max(0, topOf(anchor.number - 1)
          + slots[anchor.number - 1].height * anchor.fraction - anchor.viewportY);
        const slot = slots[anchor.number - 1];
        stage.scrollLeft = Math.max(0, 16 + (wrapperWidth() - slot.width) / 2
          + anchor.fractionX * slot.width - anchor.viewportX);
      }
      refresh(); schedule();
    };
    const changeZoom = (value, anchor = anchorAt((stage.clientHeight || 800) / 2)) => {
      if (!documentProxy || value === zoom) return;
      zoom = value; relayout(anchor);
    };
    const initialize = () => {
      if (slots.length || !documentProxy || !firstWidth || !firstHeight
          || stage.clientWidth <= 0 || stage.clientHeight <= 0) return;
      measuredWidth = availableWidth(); fit = measuredWidth / firstWidth;
      if (!Number.isFinite(fit) || fit <= 0) throw Error('Invalid PDF dimensions');
      slots = Array.from({length:documentProxy.numPages}, (_, index) => {
        const sheet = document.createElement('div'); sheet.className = 'wb-pdf-sheet';
        const slot = {number:index + 1, element:sheet, naturalWidth:firstWidth, naturalHeight:firstHeight,
          width:0, height:0, token:0, loading:false, ready:false, failed:false};
        sizeSlot(slot, false); pages.append(sheet); return slot;
      });
      refresh(); schedule();
    };
    const onResize = () => {
      if (!documentProxy || !firstWidth || disposed || stage.clientWidth <= 0 || stage.clientHeight <= 0) return;
      try {
        if (!slots.length) { initialize(); return; }
        const nextWidth = availableWidth();
        if (nextWidth !== measuredWidth) {
          const anchor = anchorAt((stage.clientHeight || 800) / 2);
          measuredWidth = nextWidth; fit = measuredWidth / firstWidth;
          relayout(anchor);
        } else schedule();
      } catch (error) {
        status.hidden = false; status.classList.add('error');
        status.textContent = BridgeI18n.t(errorMessage(error));
      }
    };
    const distance = touches => Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
    const onTouchStart = event => {
      if (!touch || !documentProxy || event.touches.length !== 2) return;
      const rect = stage.getBoundingClientRect();
      const centerY = (event.touches[0].clientY + event.touches[1].clientY) / 2 - rect.top;
      const centerX = (event.touches[0].clientX + event.touches[1].clientX) / 2 - rect.left;
      gesture = {distance:Math.max(1, distance(event.touches)), startZoom:zoom,
        centerY, centerX, anchor:anchorAt(centerY, centerX), nextZoom:zoom};
    };
    const onTouchMove = event => {
      if (!gesture || event.touches.length !== 2) return;
      event.preventDefault();
      gesture.nextZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM,
        gesture.startZoom * distance(event.touches) / gesture.distance));
      const ratio = gesture.nextZoom / gesture.startZoom;
      pages.style.transformOrigin = (stage.scrollLeft + gesture.centerX) + 'px ' + (stage.scrollTop + gesture.centerY) + 'px';
      pages.style.transform = 'scale(' + ratio + ')';
      zoomLabel.textContent = Math.round(gesture.nextZoom * 100) + '%';
    };
    const onTouchEnd = event => {
      if (!gesture || event.touches.length >= 2) return;
      const ended = gesture; gesture = null; pages.style.transform = '';
      changeZoom(ended.nextZoom, ended.anchor); schedule();
    };
    const onKeyDown = event => {
      if (event.key === 'PageDown' || event.key === 'ArrowRight') { event.preventDefault(); go(1); }
      else if (event.key === 'PageUp' || event.key === 'ArrowLeft') { event.preventDefault(); go(-1); }
      else if (event.key === '+' || event.key === '=') { event.preventDefault(); larger.click(); }
      else if (event.key === '-') { event.preventDefault(); smaller.click(); }
    };
    stage.addEventListener('scroll', schedule);
    stage.addEventListener('keydown', onKeyDown);
    if (touch) {
      stage.addEventListener('touchstart', onTouchStart, {passive:true});
      stage.addEventListener('touchmove', onTouchMove, {passive:false});
      stage.addEventListener('touchend', onTouchEnd);
      stage.addEventListener('touchcancel', onTouchEnd);
    }
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(onResize); resizeObserver.observe(stage);
    }
    const load = async () => {
      if (started || disposed) return;
      started = true;
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
        const first = await documentProxy.getPage(1);
        if (disposed) { first.cleanup(); return; }
        const natural = first.getViewport({scale:1});
        first.cleanup();
        if (!Number.isFinite(natural.width) || !Number.isFinite(natural.height)
            || natural.width <= 0 || natural.height <= 0) throw Error('Invalid PDF dimensions');
        firstWidth = natural.width; firstHeight = natural.height;
        initialize();
      } catch (error) {
        if (disposed) return;
        status.classList.add('error'); status.textContent = BridgeI18n.t(errorMessage(error));
      }
    };
    const dispose = () => {
      if (disposed) return;
      disposed = true; gesture = null;
      resizeObserver?.disconnect();
      stage.removeEventListener('scroll', schedule);
      stage.removeEventListener('keydown', onKeyDown);
      if (touch) {
        stage.removeEventListener('touchstart', onTouchStart);
        stage.removeEventListener('touchmove', onTouchMove);
        stage.removeEventListener('touchend', onTouchEnd);
        stage.removeEventListener('touchcancel', onTouchEnd);
      }
      if (scheduled !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(scheduled);
      slots.forEach(retire); pages.replaceChildren();
      loadingTask?.destroy(); documentProxy = null;
    };
    return {element, load, dispose};
  };
  return {create, allowed, MAX_BYTES};
})();
if (typeof module !== 'undefined') module.exports = BridgePdfPreview;
