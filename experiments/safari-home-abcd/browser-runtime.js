(() => {
  'use strict';
  const disabled = window.__LAB_VARIANT__ === 'B';
  const metrics = window.__labImages = {
    condition: window.__LAB_VARIANT__, disabled, phase: 'normal',
    native: { fetchImage: 0, responseBlob: 0, blobConstructor: 0, objectUrl: 0, decode: 0, bitmap: 0, imageConstructor: 0, imageDecoder: 0 },
    blocked: {}, imagePreloadNodes: 0,
  };
  const block = kind => { metrics.blocked[kind] = (metrics.blocked[kind] || 0) + 1; };
  const imageUrl = value => /(?:\/slides\/\d+\/image|\/_next\/image|\.(?:png|jpe?g|gif|webp|avif|svg|ico|bmp)(?:[?#]|$)|^(?:blob:|data:image\/))/i.test(String(value || ''));
  const originalFetch = window.fetch;
  window.fetch = function(input, options) {
    const href = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
    if (imageUrl(href) || /^image\//i.test(new Headers(options?.headers || (input instanceof Request ? input.headers : undefined)).get('Accept') || '')) {
      if (disabled) { block('fetchImage'); return Promise.reject(new Error('LAB_B_IMAGE_DISABLED')); }
      metrics.native.fetchImage++;
    }
    return originalFetch.apply(this, arguments);
  };
  const wrap = (object, name, count, reject) => {
    const original = object[name]; if (typeof original !== 'function') return;
    object[name] = function(...args) {
      if (disabled) { block(count); if (reject) return Promise.reject(new Error('LAB_B_IMAGE_DISABLED')); throw new Error('LAB_B_IMAGE_DISABLED'); }
      metrics.native[count]++; return original.apply(this, args);
    };
  };
  wrap(Response.prototype, 'blob', 'responseBlob', true);
  wrap(URL, 'createObjectURL', 'objectUrl', false);
  wrap(HTMLImageElement.prototype, 'decode', 'decode', true);
  wrap(window, 'createImageBitmap', 'bitmap', true);
  for (const [name, count] of [['Blob','blobConstructor'],['Image','imageConstructor'],['ImageDecoder','imageDecoder']]) {
    if (typeof window[name] !== 'function') continue;
    window[name] = new Proxy(window[name], {
      construct(target, args, receiver) { if (disabled) { block(count); throw new Error('LAB_B_IMAGE_DISABLED'); } metrics.native[count]++; return Reflect.construct(target,args,receiver); },
      apply(target, receiver, args) { if (disabled) { block(count); throw new Error('LAB_B_IMAGE_DISABLED'); } metrics.native[count]++; return Reflect.apply(target,receiver,args); },
    });
  }
  const nativeOpen = XMLHttpRequest.prototype.open, nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...args) { this.__labImage = imageUrl(url); return nativeOpen.call(this,method,url,...args); };
  XMLHttpRequest.prototype.send = function(...args) { if (disabled && this.__labImage) { block('xhrImage'); throw new Error('LAB_B_IMAGE_DISABLED'); } return nativeSend.apply(this,args); };
  if (disabled) {
    for (const [proto, properties] of [[HTMLImageElement.prototype,['src','srcset']], [HTMLSourceElement.prototype,['src','srcset']]]) {
      for (const property of properties) {
        const descriptor = Object.getOwnPropertyDescriptor(proto,property);
        if (descriptor?.set) Object.defineProperty(proto,property,{...descriptor,set(value) { if(value) block('imageSource'); }});
      }
    }
    const nativeSet = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function(name,value) {
      const lower = name.toLowerCase(), tag = this.tagName;
      if (['IMG','SOURCE','IMAGE'].includes(tag) && ['src','srcset','href','xlink:href'].includes(lower)) { block('imageAttribute'); return; }
      if (tag === 'LINK' && (lower === 'as' && value === 'image' || lower === 'href' && imageUrl(value))) { block('imagePreload'); return; }
      if (lower === 'style' && /url\(/i.test(value)) { block('imageStyle'); return; }
      return nativeSet.call(this,name,value);
    };
  }
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) if (node instanceof Element) {
      const links = [...node.querySelectorAll('link[rel="preload"][as="image"]')];
      if (node.matches('link[rel="preload"][as="image"]')) links.push(node);
      metrics.imagePreloadNodes += links.length;
      if (disabled) for (const link of links) { block('imagePreloadNode'); link.remove(); }
    }
  }).observe(document.documentElement,{subtree:true,childList:true});
})();
