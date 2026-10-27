const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const { sourceAssetHash } = require('./helpers/source-asset-hash.cjs');
const crypto = require('node:crypto');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const root = path.join(__dirname, '..');

function load(relative, { react = React, clock } = {}) {
  const mod = { exports: {} };
  const output = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(output, { module: mod, exports: mod.exports,
    setTimeout: clock?.setTimeout || setTimeout, clearTimeout: clock?.clearTimeout || clearTimeout,
    require(name) {
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return require(name);
      if (name === '@/lib/pivot/transitions') return clock ? load('lib/pivot/transitions.ts', { clock }) : transitions;
      if (name === './pivot-ui') return load('components/pivot/pivot-ui.tsx');
      if (name === './splash-background') return load('components/pivot/splash-background.tsx');
      if (name === '@/lib/pivot/moving-gradient/runtime') return { startMovingGradient() { throw new Error('SSR must not initialize WebGPU'); } };
      if (name === 'next/navigation') return { useRouter: () => ({ replace() {} }) };
      if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
      throw new Error('Unexpected transition dependency: ' + name);
    },
  });
  return mod.exports;
}
const transitions = load('lib/pivot/transitions.ts');
function fakeClock() {
  let now = 0, id = 0;
  const timers = new Map(), cancelled = [];
  return {
    setTimeout(callback, delay) { const handle = ++id; timers.set(handle, { at: now + delay, callback }); return handle; },
    clearTimeout(handle) { if (timers.has(handle)) cancelled.push(timers.get(handle).callback); timers.delete(handle); },
    advance(to) {
      for (;;) {
        const next = [...timers.entries()].filter(([, timer]) => timer.at <= to).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at; timers.delete(next[0]); next[1].callback();
      }
      now = to;
    },
    deliverCancelled() { cancelled.forEach(callback => callback()); },
    get pending() { return timers.size; },
  };
}

// Exercise the component's actual effects using the existing VM loader and fake clock.
function loadingHarness({ complete = false, naturalWidth = 0, reducedMotion = false } = {}) {
  const clock = fakeClock(), hooks = [], jobs = [], updates = [], listeners = new Map(), registered = [];
  let cursor = 0, dirty = true, mounted = true, tree, home = 0, decodeCalls = 0, resolve, reject;
  const decoding = new Promise((yes, no) => { resolve = yes; reject = no; });
  const image = {
    complete, naturalWidth,
    decode() { decodeCalls++; return decoding; },
    addEventListener(name, callback) {
      registered.push(name);
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(callback);
    },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
  };
  const react = {
    ...React,
    useState(initial) {
      const index = cursor++;
      hooks[index] ??= { value: initial };
      return [hooks[index].value, next => {
        updates.push({ index, value: next });
        if (!Object.is(hooks[index].value, next)) { hooks[index].value = next; dirty = true; }
      }];
    },
    useRef() { const index = cursor++; return hooks[index] ??= { current: image }; },
    useSyncExternalStore() { cursor++; return reducedMotion; },
    useEffect(create, deps) {
      const index = cursor++, previous = hooks[index];
      if (!previous || deps.some((value, at) => !Object.is(value, previous.deps[at]))) jobs.push({ index, create, deps });
    },
  };
  const { DemoLoadingScreen } = load('components/pivot/demo-loading-screen.tsx', { react, clock });
  const props = { onComplete: () => { home++; }, onRestart() {} };
  function flush() {
    while (mounted && dirty) {
      dirty = false; cursor = 0; tree = DemoLoadingScreen(props);
      for (const { index, create, deps } of jobs.splice(0)) {
        hooks[index]?.cleanup?.();
        hooks[index] = { deps, cleanup: create() };
      }
    }
  }
  flush();
  return {
    clock, image, registered, updates, flush, resolve, reject,
    emit(name) { for (const callback of [...(listeners.get(name) || [])]) callback(); flush(); },
    handlers(name) { return [...(listeners.get(name) || [])]; },
    advance(to) { clock.advance(to); flush(); },
    unmount() { mounted = false; for (const hook of hooks) hook?.cleanup?.(); },
    get state() { return hooks[1].value; },
    get phase() { return tree.props.children[2].props.phase; },
    get home() { return home; },
    get decodeCalls() { return decodeCalls; },
    get listenerCount() { return [...listeners.values()].reduce((count, values) => count + values.size, 0); },
  };
}

for (const reducedMotion of [false, true]) {
  const label = reducedMotion ? 'reduced motion' : 'normal motion';
  test(`Image load/decode settles once and clears the watchdog (${label})`, async () => {
    const screen = loadingHarness({ reducedMotion });
    screen.image.naturalWidth = 456; screen.emit('load'); screen.emit('load');
    assert.equal(screen.decodeCalls, 1);
    screen.resolve(); await Promise.resolve(); screen.flush();
    assert.equal(screen.state, 'ready'); assert.equal(screen.listenerCount, 0);
    assert.equal(screen.clock.pending, 3, 'Only the three existing loading timers remain');
    screen.clock.deliverCancelled(); screen.flush();
    assert.equal(screen.state, 'ready', 'An already queued watchdog cannot overwrite success');
    screen.advance(800); assert.equal(screen.phase, reducedMotion ? 0 : 1);
    screen.advance(2399); assert.equal(screen.home, 0);
    screen.advance(2400); screen.advance(10000);
    assert.equal(screen.home, 1); assert.equal(screen.clock.pending, 0);
  });
  test(`Complete image with zero natural width fails immediately and removes both listeners (${label})`, () => {
    const screen = loadingHarness({ complete: true, naturalWidth: 0, reducedMotion });
    assert.deepEqual(screen.registered, ['load', 'error']);
    assert.equal(screen.state, 'failed'); assert.equal(screen.listenerCount, 0);
    assert.equal(screen.decodeCalls, 0); assert.equal(screen.clock.pending, 3);
    screen.emit('load'); screen.emit('error'); screen.clock.deliverCancelled(); screen.flush();
    screen.advance(2399); assert.equal(screen.home, 0);
    screen.advance(2400); screen.advance(10000); assert.equal(screen.home, 1);
    assert.equal(screen.updates.filter(update => update.index === 1).length, 1);
  });
  test(`Image error wins over a pending decode (${label})`, async () => {
    const screen = loadingHarness({ complete: true, naturalWidth: 456, reducedMotion });
    screen.emit('error'); screen.resolve(); await Promise.resolve(); screen.flush();
    assert.equal(screen.state, 'failed'); assert.equal(screen.listenerCount, 0);
    assert.equal(screen.updates.filter(update => update.index === 1).length, 1);
    screen.advance(2400); screen.advance(10000); assert.equal(screen.home, 1);
  });
  test(`Decode rejection clears the watchdog and continues through fallback (${label})`, async () => {
    const screen = loadingHarness({ complete: true, naturalWidth: 456, reducedMotion });
    screen.reject(new Error('decode failed')); await Promise.resolve(); screen.flush();
    assert.equal(screen.state, 'failed'); assert.equal(screen.listenerCount, 0);
    assert.equal(screen.clock.pending, 3);
    screen.advance(2400); screen.advance(10000); assert.equal(screen.home, 1);
  });
  for (const complete of [false, true]) test(`Watchdog bounds ${complete ? 'never-ending decode' : 'missing image events'} at 3000ms (${label})`, () => {
    const screen = loadingHarness({ complete, naturalWidth: complete ? 456 : 0, reducedMotion });
    const lateLoad = screen.handlers('load')[0], lateError = screen.handlers('error')[0];
    screen.advance(2999); assert.equal(screen.state, 'pending'); assert.equal(screen.home, 0);
    screen.advance(3000); assert.equal(screen.state, 'failed'); assert.equal(screen.listenerCount, 0);
    lateLoad(); lateError(); screen.flush();
    assert.equal(screen.updates.filter(update => update.index === 1).length, 1);
    screen.advance(5399); assert.equal(screen.home, 0);
    screen.advance(5400); screen.advance(20000);
    assert.equal(screen.home, 1); assert.equal(screen.clock.pending, 0);
  });
  test(`Decode success at 2999ms wins and cancels the watchdog (${label})`, async () => {
    const screen = loadingHarness({ complete: true, naturalWidth: 456, reducedMotion });
    screen.advance(2999); screen.resolve(); await Promise.resolve(); screen.flush();
    assert.equal(screen.state, 'ready'); assert.equal(screen.listenerCount, 0);
    screen.advance(3000); screen.clock.deliverCancelled(); screen.flush();
    assert.equal(screen.state, 'ready');
    screen.advance(5398); assert.equal(screen.home, 0);
    screen.advance(5399); screen.advance(20000); assert.equal(screen.home, 1);
  });
  test(`Late decode after timeout cannot restart the loading cycle (${label})`, async () => {
    const screen = loadingHarness({ complete: true, naturalWidth: 456, reducedMotion });
    screen.advance(3000); screen.advance(3500);
    screen.resolve(); await Promise.resolve(); screen.flush();
    assert.equal(screen.state, 'failed'); assert.equal(screen.clock.pending, 3);
    assert.equal(screen.updates.filter(update => update.index === 1).length, 1);
    screen.advance(5399); assert.equal(screen.home, 0);
    screen.advance(5400); screen.advance(20000); assert.equal(screen.home, 1);
  });
  for (const complete of [false, true]) test(`Unmount cancels watchdog and ignores late ${complete ? 'decode' : 'load/error'} callbacks (${label})`, async () => {
    const screen = loadingHarness({ complete, naturalWidth: complete ? 456 : 0, reducedMotion });
    const lateLoad = screen.handlers('load')[0], lateError = screen.handlers('error')[0];
    screen.advance(1000); screen.unmount();
    assert.equal(screen.listenerCount, 0); assert.equal(screen.clock.pending, 0);
    screen.resolve(); await Promise.resolve(); lateLoad(); lateError();
    screen.clock.deliverCancelled(); screen.advance(20000);
    assert.deepEqual(screen.updates, []); assert.equal(screen.home, 0);
  });
  test(`Unmount during the ready cycle cancels every Home timer (${label})`, async () => {
    const screen = loadingHarness({ complete: true, naturalWidth: 456, reducedMotion });
    screen.resolve(); await Promise.resolve(); screen.flush();
    screen.advance(800); screen.unmount(); screen.advance(20000); screen.clock.deliverCancelled();
    assert.equal(screen.home, 0); assert.equal(screen.clock.pending, 0); assert.equal(screen.listenerCount, 0);
  });
}

test('Splash waits 3 seconds, then dissolves for 300ms before continuing', () => {
  const clock = fakeClock(), events = [];
  transitions.startSplashTransition(() => events.push('dissolve'), () => events.push('continue'), false, clock);
  clock.advance(2999); assert.deepEqual(events, []);
  clock.advance(3000); assert.deepEqual(events, ['dissolve']);
  clock.advance(3299); assert.deepEqual(events, ['dissolve']);
  clock.advance(3300); assert.deepEqual(events, ['dissolve', 'continue']);
  clock.advance(10000); assert.equal(events.length, 2); assert.equal(clock.pending, 0);
});
test('Reduced motion keeps the 3 second wait and skips the dissolve', () => {
  const clock = fakeClock(), events = [];
  transitions.startSplashTransition(() => events.push('dissolve'), () => events.push('continue'), true, clock);
  clock.advance(2999); assert.deepEqual(events, []);
  clock.advance(3000); assert.deepEqual(events, ['continue']); assert.equal(clock.pending, 0);
});
for (const time of [1000, 3100]) test('Splash cancellation at ' + time + 'ms prevents stale navigation', () => {
  const clock = fakeClock(); let navigation = 0;
  const cancel = transitions.startSplashTransition(() => {}, () => navigation++, false, clock);
  clock.advance(time); cancel(); clock.advance(10000); clock.deliverCancelled();
  assert.equal(navigation, 0); assert.equal(clock.pending, 0);
});
test('Demo plays precisely one 800ms × 3 cycle before Home', () => {
  const clock = fakeClock(), phases = []; let home = 0;
  transitions.startDemoLoading(phase => phases.push(phase), () => home++, clock);
  clock.advance(799); assert.deepEqual(phases, []);
  clock.advance(800); assert.deepEqual(phases, [1]);
  clock.advance(1600); assert.deepEqual(phases, [1, 2]);
  clock.advance(2399); assert.equal(home, 0);
  clock.advance(2400); assert.equal(home, 1);
  clock.advance(10000); assert.equal(home, 1); assert.equal(clock.pending, 0);
});
test('Restart cancels every loading timer, including callbacks already queued for delivery', () => {
  const clock = fakeClock(), phases = []; let home = 0;
  const cancel = transitions.startDemoLoading(phase => phases.push(phase), () => home++, clock);
  clock.advance(800); cancel(); clock.advance(10000); clock.deliverCancelled();
  assert.deepEqual(phases, [1]); assert.equal(home, 0); assert.equal(clock.pending, 0);
});
test('A remounted loading screen starts a fresh cycle after cancellation', () => {
  const clock = fakeClock(), oldPhases = [], newPhases = []; let home = 0;
  const cancel = transitions.startDemoLoading(phase => oldPhases.push(phase), () => { throw Error('stale'); }, clock);
  clock.advance(800); cancel();
  transitions.startDemoLoading(phase => newPhases.push(phase), () => home++, clock);
  clock.advance(1599); assert.deepEqual(newPhases, []);
  clock.advance(2400); assert.deepEqual(newPhases, [1, 2]); assert.equal(home, 0);
  clock.advance(3200); assert.equal(home, 1); assert.deepEqual(oldPhases, [1]);
});
test('Loading artwork exposes exactly the selected Figma image and dot variant', () => {
  const { LoadingArtwork } = load('components/pivot/demo-loading-screen.tsx');
  for (const phase of [0, 1, 2]) {
    const html = renderToStaticMarkup(React.createElement(LoadingArtwork, { phase }));
    assert.match(html, new RegExp('data-demo-loading-phase="' + (phase + 1) + '"'));
    const images = [...html.matchAll(/<img\b[^>]*>/g)].map(match => match[0]);
    const visible = images.filter(image => !/hidden=""/.test(image));
    assert.equal(visible.length, 2);
    assert.match(visible[0], new RegExp('phase-' + (phase + 1) + '.png'));
    assert.match(visible[1], new RegExp('dots-' + (phase + 1) + '.svg'));
  }
});
test('Loading copy states that no real collection, recommendation or save is running', () => {
  const { DemoLoadingScreen } = load('components/pivot/demo-loading-screen.tsx');
  const html = renderToStaticMarkup(React.createElement(DemoLoadingScreen, { onComplete() {}, onRestart() {} }));
  assert.match(html, /실제 콘텐츠 수집·추천·저장은 진행하지 않아요/);
  assert.doesNotMatch(html, /맞춤 콘텐츠를 수집하고 있어요/);
  assert.match(html, /role="status"/); assert.match(html, /처음부터 시연/);
});
test('Loading displays a placeholder until the first frame is decoded', () => {
  const { LoadingArtwork } = load('components/pivot/demo-loading-screen.tsx');
  const html = renderToStaticMarkup(React.createElement(LoadingArtwork, { phase: 0, imageState: 'pending' }));
  assert.match(html, /data-loading-image-state="pending"/);
  assert.match(html, /시연용 화면 준비 중/);
  assert.match(html, /fetchPriority="high"/);
  assert.match(html, /decoding="async"/);
});
test('Splash heading is a programmatic focus target for demo restart', () => {
  const { SplashScreen } = load('components/pivot/splash-screen.tsx');
  const html = renderToStaticMarkup(React.createElement(SplashScreen, { preview: true }));
  assert.match(html, /<h1[^>]*tabindex="-1"/);
});

test('Splash preserves authored poses and server-renders the static fallback before WebGPU initializes', () => {
  const { SplashScreen } = load('components/pivot/splash-screen.tsx');
  const html = renderToStaticMarkup(React.createElement(SplashScreen, { preview: true }));
  for (const state of [1, 2]) assert.match(html, new RegExp('data-splash-character-state="' + state + '"'));
  assert.match(html, /\/pivot\/home\/chrome\/mascot.png/);
  assert.match(html, /\/pivot\/splash\/state-2.png/);
  assert.match(html, /data-splash-background="static"/);
  assert.match(html, /<canvas[^>]*width="375"[^>]*height="375"/);
  assert.doesNotMatch(html, /9:41|imgWifi|imgBatteryEnd/);
});

test('Splash pose animation uses 800ms states, respects reduced motion, and leaves navigation timing unchanged', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'public/pivot/splash/sources.json'), 'utf8'));
  assert.equal(manifest.stateDurationMs, 800); assert.equal(manifest.periodMs, 1600);
  assert.equal(manifest.shaderImplemented, true);
  const css = fs.readFileSync(path.join(root, 'components/pivot/pivot.module.css'), 'utf8');
  assert.match(css, /1600ms steps\(1, end\) infinite/);
  assert.match(css, /prefers-reduced-motion: reduce\)\s*{\s*\.splashMascotRest, \.splashMascotRaised\s*{\s*animation: none/);
  assert.equal(transitions.SPLASH_WAIT_MS, 3000); assert.equal(transitions.SPLASH_DISSOLVE_MS, 300);
});
test('All scoped loading assets are present and retain their recorded source hashes', () => {
  const folder = path.join(root, 'public/pivot/loading');
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'sources.json'), 'utf8'));
  assert.equal(manifest.assets.length, 7);
  for (const asset of manifest.assets) {
    const content = fs.readFileSync(path.join(folder, asset.file));
    assert.ok(content.length > 0);
    assert.equal(sourceAssetHash(asset.file, content).toUpperCase(), asset.sha256);
    if (asset.file.endsWith('.png')) {
      assert.equal(content.readUInt32BE(16), 512);
      assert.equal(content.readUInt32BE(20), 512);
      assert.equal(asset.sourceSha256, asset.optimization.sourceSha256);
      assert.equal(asset.optimization.sourceWidth, 1254);
      assert.equal(asset.optimization.sourceHeight, 1254);
      assert.equal(asset.optimization.width, 512); assert.equal(asset.optimization.height, 512);
      assert.equal(asset.optimization.palette, false);
      assert.equal(asset.optimization.preserveAlpha, true);
      assert.equal(asset.optimization.preserveAspectRatio, true);
      assert.ok(content.length < asset.optimization.sourceBytes);
    }
  }
});

test('Latest loading assets stay within the previous 606948-byte PNG budget', () => {
  const folder = path.join(root, 'public/pivot/loading');
  const names = ['phase-1.png', 'phase-2.png', 'phase-3.png'];
  const bytes = names.reduce((total, file) => total + fs.statSync(path.join(folder, file)).size, 0);
  assert.ok(bytes <= 606948, 'Loading PNGs exceed the previous optimized budget: ' + bytes);
});
