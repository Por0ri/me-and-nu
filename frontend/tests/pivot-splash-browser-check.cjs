// Uses existing Playwright/pngjs installations; never installs packages or contacts an API.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PIVOT_PLAYWRIGHT_MODULE || 'playwright');
const { PNG } = require(process.env.PIVOT_PNG_MODULE || 'pngjs');
const base = process.argv[2] || 'http://127.0.0.1:3100';
const out = process.argv[3] || path.join(require('node:os').tmpdir(), 'pivot-splash');
fs.mkdirSync(out, { recursive: true });
const results = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function poll(check, message, attempts = 100) {
  for (let i = 0; i < attempts; i++) { if (await check()) return; await wait(40); }
  throw new Error(message);
}
async function instrumentation(context, failure = '') {
  await context.addInitScript(failure => {
    const stats = window.__splashGpu = { adapters: 0, devices: 0, destroyedDevices: 0,
      buffers: 0, destroyedBuffers: 0, textures: 0, destroyedTextures: 0, submits: 0, times: [], starts: [] };
    const timeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay, ...args) => {
      if (delay === 3000) stats.starts.push(performance.now());
      return timeout(callback, delay, ...args);
    };
    if (failure === 'unsupported') { Object.defineProperty(navigator, 'gpu', { value: undefined }); return; }
    const gpu = navigator.gpu;
    if (!gpu) return;
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (name, ...args) {
      if (name === 'webgpu' && failure === 'no-context') return null;
      return getContext.call(this, name, ...args);
    };
    const adapter = gpu.requestAdapter.bind(gpu);
    gpu.requestAdapter = async (...args) => {
      stats.adapters++;
      if (failure === 'no-adapter') return null;
      const found = await adapter(...args);
      if (!found) return found;
      const requestDevice = found.requestDevice.bind(found);
      found.requestDevice = async (...args) => {
        if (failure === 'device-rejection') throw new Error('Injected initialization rejection');
        const device = await requestDevice(...args); stats.devices++;
        const destroy = device.destroy.bind(device);
        device.destroy = () => { stats.destroyedDevices++; destroy(); };
        window.__loseSplashDevice = destroy;
        window.__errorSplashDevice = () => device.dispatchEvent(new GPUUncapturedErrorEvent('uncapturederror', { error: new GPUValidationError('Injected device error') }));
        for (const [method, created, destroyed] of [['createBuffer', 'buffers', 'destroyedBuffers'], ['createTexture', 'textures', 'destroyedTextures']]) {
          const create = device[method].bind(device);
          device[method] = (...args) => {
            const resource = create(...args); stats[created]++;
            const release = resource.destroy.bind(resource);
            resource.destroy = () => { stats[destroyed]++; release(); };
            return resource;
          };
        }
        const createModule = device.createShaderModule.bind(device);
        device.createShaderModule = (...args) => {
          const module = createModule(...args);
          if (failure === 'compile-rejection') module.getCompilationInfo = async () => ({ messages: [{ type: 'error' }] });
          if (failure === 'watchdog') module.getCompilationInfo = () => new Promise(() => {});
          return module;
        };
        const write = device.queue.writeBuffer.bind(device.queue), submit = device.queue.submit.bind(device.queue);
        device.queue.writeBuffer = (...args) => { stats.times.push(args[2][1]); return write(...args); };
        device.queue.submit = (...args) => { stats.submits++; return submit(...args); };
        if (failure === 'late-device') await new Promise(resolve => { window.__releaseSplashDevice = resolve; });
        return device;
      };
      return found;
    };
  }, failure);
}
async function status(page) { return page.evaluate(() => document.querySelector('[data-splash-background]')?.dataset.splashBackground); }
async function stats(page) { return page.evaluate(() => window.__splashGpu); }
async function geometry(locator) { return locator.evaluate(e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; }); }
async function loaded(page) {
  await poll(() => page.evaluate(() => Object.keys(document.querySelector('main button') || {}).some(key => key.startsWith('__reactProps'))), 'Splash hydration');
  await wait(80);
  // Clock is paused throughout hydration, so all effects start at this same virtual time.
  // Playwright can replace the init-script timer wrapper when installing its clock.
  await page.evaluate(() => { if (!window.__splashGpu.starts.length) window.__splashGpu.starts.push(performance.now()); });
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.decode().catch(() => {}))); });
}
async function animated(page) {
  for (let i = 0; i < 100; i++) {
    await page.clock.runFor(16); await wait(50);
    const value = await status(page);
    if (value === 'animated') return;
    assert.notEqual(value, 'fallback', 'WebGPU must render on this browser');
  }
  throw new Error('GPU never presented a frame');
}
async function released(page) {
  await poll(async () => {
    const s = await stats(page);
    return s.devices === s.destroyedDevices && s.buffers === s.destroyedBuffers && s.textures === s.destroyedTextures;
  }, 'All owned GPU resources must be released');
}
async function staticVisible(page) {
  assert.equal(await page.locator('[data-splash-background] > img').evaluate(e => getComputedStyle(e).visibility), 'visible');
  assert.equal(await page.locator('canvas').evaluate(e => getComputedStyle(e).visibility), 'hidden');
}
function difference(a, b, circle, logo) {
  const first = PNG.sync.read(a), second = PNG.sync.read(b);
  let changed = 0, sum = 0, pixels = 0;
  for (let y = Math.ceil(circle.y); y < Math.floor(circle.y + circle.height); y++) {
    for (let x = Math.ceil(circle.x); x < Math.floor(circle.x + circle.width); x++) {
      if (Math.hypot(x - circle.x - 187.5, y - circle.y - 187.5) > 180) continue;
      if (x > logo.x - 5 && x < logo.x + logo.width + 5 && y > logo.y - 5 && y < logo.y + logo.height + 5) continue;
      const at = (y * first.width + x) * 4;
      const delta = Math.max(...[0, 1, 2].map(c => Math.abs(first.data[at + c] - second.data[at + c])));
      pixels++; sum += delta; if (delta > 2) changed++;
    }
  }
  return { pixels, changed, ratio: changed / pixels, meanMaxChannelDelta: sum / pixels };
}
async function capture(page, name) { const buffer = await page.screenshot({ path: path.join(out, name + '.png') }); return buffer; }
async function skip(page) {
  // Existing skip is visually revealed on keyboard focus.
  await page.getByRole('button', { name: '로그인 화면으로 건너뛰기' }).focus();
  await page.keyboard.press('Enter');
  await page.waitForURL('**/pivot/login');
  await page.locator('[data-splash-background]').waitFor({ state: 'detached' });
}
async function at(page, time) {
  const target = await page.evaluate(time => window.__splashGpu.starts[0] + time - performance.now(), time);
  if (target > 0) await page.clock.runFor(target);
  await wait(50);
}
async function checkPose(page, reduced) {
  const poses = page.locator('[data-splash-character-state]');
  assert.equal(await poses.count(), 2);
  for (const time of [0, 799, 800, 1599, 1600]) {
    await poses.evaluateAll((items, time) => { for (const item of items) for (const animation of item.getAnimations()) { animation.pause(); animation.currentTime = time; } }, time);
    assert.deepEqual(await poses.evaluateAll(items => items.map(e => Number(getComputedStyle(e).opacity))), reduced || time < 800 || time >= 1600 ? [1, 0] : [0, 1]);
  }
}
async function testCase(browser, name, options, run) {
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true, ...options });
  const errors = [], api = [], external = [];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/favicon.ico') { await route.fulfill({ status: 204 }); return; }
    if (url.pathname.startsWith('/api/')) { api.push(url.href); await route.abort(); return; }
    if (url.origin !== new URL(base).origin) { external.push(url.href); await route.abort(); return; }
    await route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(5000);
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  try {
    const evidence = await run(context, page);
    assert.deepEqual(errors, []); assert.deepEqual(api, []); assert.deepEqual(external, []);
    results.push({ name, status: 'pass', browser: browser.version(), ...evidence, errors, api, external });
    console.log('PASS Splash ' + name);
  } catch (error) {
    await capture(page, name + '-failure').catch(() => {});
    results.push({ name, status: 'fail', error: error.stack, errors, api, external });
    console.error('FAIL Splash ' + name + ': ' + error.stack);
  } finally { await context.close(); }
}
(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PIVOT_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const viewport of [{ width: 375, height: 812 }, { width: 390, height: 844 }]) for (const motion of ['no-preference', 'reduce']) {
      const name = `${viewport.width}-${motion}`;
      await testCase(browser, name, { viewport, reducedMotion: motion }, async (context, page) => {
        await instrumentation(context); await page.clock.install(); await page.clock.pauseAt(new Date());
        await page.goto(base + '/pivot'); await loaded(page);
        if (motion === 'no-preference') await animated(page); else { assert.equal(await status(page), 'static'); await staticVisible(page); }
        const circle = await geometry(page.locator('[data-splash-background]'));
        const logo = await geometry(page.getByRole('img', { name: 'me;nu', exact: true }));
        assert.equal(circle.width, 375); assert.equal(circle.height, 375); assert.equal(logo.width, 140);
        assert.equal(logo.height, 27.96875);
        await checkPose(page, motion === 'reduce');
        const first = await capture(page, name + '-a');
        const before = await stats(page);
        await page.clock.runFor(1200); await wait(100); await page.clock.runFor(50); await wait(100);
        const second = await capture(page, name + '-b');
        const delta = difference(first, second, circle, logo);
        if (motion === 'no-preference') {
          assert.ok(delta.ratio > .05, 'Time-separated circle pixels must change');
          assert.ok((await stats(page)).times.at(-1) - before.times.at(-1) > 1, 'Source receives advancing seconds after its ms conversion');
          assert.equal(await page.locator('canvas').evaluate(e => getComputedStyle(e).opacity), '0.2');
        } else { assert.equal(delta.changed, 0); assert.equal((await stats(page)).adapters, 0); }
        assert.deepEqual(await geometry(page.getByRole('img', { name: 'me;nu', exact: true })), logo);
        assert.equal(await page.locator('main').evaluate(e => e.scrollWidth > innerWidth), false);
        await at(page, 2999); assert.ok(await status(page));
        await at(page, 3000); await released(page);
        if (motion === 'no-preference') {
          assert.equal(await status(page), 'static'); await at(page, 3299); assert.equal(new URL(page.url()).pathname, '/pivot'); await at(page, 3300);
        }
        await page.waitForURL('**/pivot/login');
        const final = await stats(page); await page.clock.runFor(1500); await wait(50);
        assert.equal((await stats(page)).submits, final.submits, 'No frame submissions after Splash ends');
        return { viewport, motion, circle, logo, delta, gpu: final, timing: motion === 'reduce' ? '3000ms' : '3000ms + 300ms' };
      });
    }
    for (const failure of ['unsupported', 'no-adapter', 'device-rejection', 'no-context', 'compile-rejection', 'watchdog', 'late-device', 'device-lost', 'uncapturederror']) {
      await testCase(browser, failure, {}, async (context, page) => {
        await instrumentation(context, failure); await page.clock.install(); await page.clock.pauseAt(new Date());
        await page.goto(base + '/pivot'); await loaded(page);
        if (failure === 'watchdog') { await poll(async () => (await stats(page)).buffers === 3, 'Setup must allocate buffers'); await page.clock.runFor(2501); }
        if (failure === 'late-device') {
          await poll(() => page.evaluate(() => !!window.__releaseSplashDevice), 'Device acquisition must be pending');
          await skip(page);
          await page.evaluate(() => window.__releaseSplashDevice()); await released(page);
          assert.equal((await stats(page)).buffers, 0, 'A device arriving after unmount must not allocate shader resources');
        } else {
          if (['device-lost', 'uncapturederror'].includes(failure)) {
            await animated(page);
            await page.evaluate(failure => failure === 'device-lost' ? window.__loseSplashDevice() : window.__errorSplashDevice(), failure);
          }
          await poll(async () => ['static', 'fallback'].includes(await status(page)), 'Failure must show static artwork');
          await staticVisible(page); await released(page); await capture(page, failure);
          await skip(page);
        }
        const final = await stats(page); await page.clock.runFor(5000); await wait(50);
        assert.equal((await stats(page)).submits, final.submits);
        return { gpu: final, fallback: true };
      });
    }
    await testCase(browser, 'live-motion-preference-and-skip', {}, async (context, page) => {
      await instrumentation(context); await page.clock.install(); await page.clock.pauseAt(new Date());
      await page.goto(base + '/pivot'); await loaded(page); await animated(page);
      const cdp = await context.newCDPSession(page), heaps = [];
      for (let cycle = 0; cycle < 5; cycle++) {
        await page.emulateMedia({ reducedMotion: 'reduce' }); await poll(async () => await status(page) === 'static', 'Preference change stops shader');
        await staticVisible(page); await released(page);
        await cdp.send('HeapProfiler.collectGarbage');
        heaps.push(await cdp.send('Runtime.getHeapUsage'));
        if (cycle < 4) { await page.emulateMedia({ reducedMotion: 'no-preference' }); await animated(page); }
      }
      assert.ok(heaps.at(-1).usedSize - heaps[0].usedSize < 5 * 1024 * 1024, 'No multi-megabyte heap growth after five GPU start/stop cycles');
      await page.emulateMedia({ reducedMotion: 'no-preference' }); await animated(page);
      await page.getByRole('button', { name: '로그인 화면으로 건너뛰기' }).focus(); await page.keyboard.press('Enter');
      await page.waitForURL('**/pivot/login'); await released(page);
      return { gpu: await stats(page), heaps, note: 'Owned-resource counts plus GC heap samples; does not prove absence of all driver/device leaks.' };
    });
  } finally { await browser.close(); fs.writeFileSync(path.join(out, 'splash-results.json'), JSON.stringify(results, null, 2)); }
  if (results.some(r => r.status !== 'pass')) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
