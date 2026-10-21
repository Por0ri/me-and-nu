const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const folder = path.join(__dirname, '../lib/pivot/moving-gradient');
function load(file, dependencies = {}, globals = {}) {
  const mod = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(folder, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, ...globals,
    require(name) { if (dependencies[name]) return dependencies[name]; throw new Error(name); },
  });
  return mod.exports;
}
const parameters = load('parameters.ts');
const turn = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function harness(options = {}) {
  const statuses = [], resources = [], timers = new Map(), frames = new Map(), loss = deferred();
  let id = 0, submits = 0, destroys = 0, unconfigures = 0, setups = 0, renders = 0;
  const resource = () => { const r = { destroyed: 0, destroy() { this.destroyed++; }, createView() { return {}; } }; resources.push(r); return r; };
  const device = new EventTarget();
  Object.assign(device, {
    lost: loss.promise, queue: { submit() { submits++; }, onSubmittedWorkDone() { return options.work?.promise || Promise.resolve(); } },
    createTexture: resource, createShaderModule: () => ({}),
    createRenderPipeline: () => ({ getBindGroupLayout: () => ({}) }), createBindGroup: () => ({}),
    createCommandEncoder: () => ({ beginRenderPass: () => ({ setPipeline() {}, setBindGroup() {}, draw() {}, end() {} }), finish() { return {}; } }),
    pushErrorScope() {}, popErrorScope: async () => options.validationError || null,
    destroy() { destroys++; },
  });
  const context = { configure() { if (options.configureFails) throw new Error('configure'); },
    unconfigure() { unconfigures++; }, getCurrentTexture() { return { createView() { return {}; } }; } };
  const canvas = { width: 0, height: 0, getContext() { return options.noContext ? null : context; } };
  const gpu = options.noGpu ? undefined : {
    requestAdapter: () => options.adapter?.promise || Promise.resolve(options.noAdapter ? null : {
      requestDevice: () => options.device?.promise || (options.deviceFails ? Promise.reject(new Error('device')) : Promise.resolve(device)),
    }), getPreferredCanvasFormat: () => 'bgra8unorm',
  };
  const runtime = load('runtime.ts', { './parameters': parameters, './figma-source': {
    setup(_, frame) {
      setups++;
      for (const key of ['vertexBuffer', 'indexBuffer', 'uniformBuf']) frame.state[key] = resource();
      if (options.setupFails) throw new Error('partial setup');
      frame.state.shaderModule = { getCompilationInfo: () => options.compilation?.promise || Promise.resolve({ messages: options.shaderFails ? [{ type: 'error' }] : [] }) };
    },
    render(_, frame) {
      if (options.renderFails) throw new Error('render');
      renders++;
      for (const key of ['msaaTexture', 'depthTexture']) frame.state[key] ||= resource();
    },
  } });
  const dispose = runtime.startMovingGradient(canvas, status => statuses.push(status), {
    gpu, pixelRatio: 3, now: () => 0,
    requestFrame(callback) { const handle = ++id; frames.set(handle, callback); return handle; },
    cancelFrame(handle) { frames.delete(handle); },
    setTimer(callback) { const handle = ++id; timers.set(handle, callback); return handle; },
    clearTimer(handle) { timers.delete(handle); },
  });
  return { statuses, resources, frames, timers, loss, device, canvas, dispose,
    async frame(time = 100) { const first = frames.entries().next().value; assert.ok(first, 'A frame is scheduled'); frames.delete(first[0]); first[1](time); await turn(); },
    watchdog() { [...timers.values()][0](); },
    get counts() { return { submits, destroys, unconfigures, setups, renders }; },
  };
}
test('Missing WebGPU remains static without requesting frames or resources', () => {
  const h = harness({ noGpu: true }); assert.deepEqual(h.statuses, ['static']); h.dispose();
  assert.equal(h.frames.size, 0); assert.equal(h.resources.length, 0);
});
for (const failure of ['noAdapter', 'deviceFails', 'noContext', 'configureFails', 'setupFails', 'shaderFails', 'validationError', 'renderFails']) {
  test(`WebGPU ${failure} safely falls back and releases every allocated resource`, async () => {
    const h = harness({ [failure]: true }); await turn();
    if (h.frames.size) await h.frame();
    assert.equal(h.statuses.at(-1), 'fallback');
    assert.equal(h.frames.size, 0); assert.equal(h.timers.size, 0);
    assert.ok(h.resources.every(r => r.destroyed === 1));
    assert.equal(h.counts.destroys, ['noAdapter', 'deviceFails'].includes(failure) ? 0 : 1);
    h.dispose(); assert.ok(h.resources.every(r => r.destroyed === 1));
  });
}
test('First completed frame reveals the shader; backing size is capped and rendering stops after disposal', async () => {
  const h = harness(); await turn(); assert.equal(h.canvas.width, 563); assert.equal(h.canvas.height, 563);
  assert.equal(h.statuses.at(-1), 'initializing'); await h.frame(100); assert.equal(h.statuses.at(-1), 'animated');
  await h.frame(110); assert.equal(h.counts.renders, 1, '30fps ceiling'); await h.frame(150); assert.equal(h.counts.renders, 2);
  assert.equal(h.timers.size, 0); h.dispose(); h.dispose();
  assert.equal(h.frames.size, 0); assert.equal(h.counts.destroys, 1); assert.equal(h.counts.unconfigures, 1);
  assert.ok(h.resources.every(r => r.destroyed === 1));
});
test('GPU backpressure permits only one in-flight frame and late work cannot restart after unmount', async () => {
  const work = deferred(), h = harness({ work }); await turn(); await h.frame();
  assert.equal(h.frames.size, 0); assert.equal(h.counts.renders, 1);
  h.dispose(); work.resolve(); await turn();
  assert.equal(h.frames.size, 0); assert.equal(h.statuses.includes('animated'), false);
});
test('Rejected GPU queue work falls back without an unhandled promise or another scheduled frame', async () => {
  const work = deferred(), h = harness({ work }); await turn(); await h.frame();
  work.reject(new Error('queue failed')); await turn();
  assert.equal(h.statuses.at(-1), 'fallback'); assert.equal(h.frames.size, 0);
  assert.ok(h.resources.every(r => r.destroyed === 1));
});
test('Unmount during pending device acquisition destroys a late device without allocating', async () => {
  const device = deferred(), h = harness({ device }); await turn(); h.dispose(); device.resolve(h.device); await turn();
  assert.equal(h.counts.destroys, 1); assert.equal(h.counts.setups, 0); assert.equal(h.resources.length, 0);
});
test('Unmount during adapter acquisition prevents device creation', async () => {
  const adapter = deferred(), h = harness({ adapter }); h.dispose(); let requests = 0;
  adapter.resolve({ requestDevice() { requests++; } }); await turn(); assert.equal(requests, 0);
});
test('Compilation watchdog releases resources and ignores late compilation success', async () => {
  const compilation = deferred(), h = harness({ compilation }); await turn(); h.watchdog();
  compilation.resolve({ messages: [] }); await turn();
  assert.equal(h.statuses.at(-1), 'fallback'); assert.equal(h.frames.size, 0);
  assert.ok(h.resources.every(r => r.destroyed === 1));
});
for (const cause of ['lost', 'uncapturederror']) test(`Device ${cause} returns to static fallback without restarting`, async () => {
  const h = harness(); await turn(); await h.frame();
  if (cause === 'lost') h.loss.resolve({ reason: 'unknown' }); else h.device.dispatchEvent(new Event('uncapturederror', { cancelable: true }));
  await turn(); assert.equal(h.statuses.at(-1), 'fallback'); assert.equal(h.frames.size, 0);
  assert.equal(h.counts.destroys, 1); assert.ok(h.resources.every(r => r.destroyed === 1));
});
test('Original setup/render and WGSL are preserved independently of the Figma editor registration', () => {
  const source = fs.readFileSync(path.join(folder, 'figma-source.js'), 'utf8').replace(/\r\n/g, '\n');
  const original = source.slice(source.indexOf('export function setup'), source.indexOf('defineProperties(Effect'));
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'source.json')));
  assert.equal(crypto.createHash('sha256').update(original).digest('hex'), manifest.setupRenderSha256);
  assert.equal(JSON.parse(fs.readFileSync(path.join(folder, 'features.json'))).isAnimated, true);
});
test('Original renderer receives milliseconds and exact Figma settings instead of its editor defaults', () => {
  const writes = [], buffers = [], pass = { setPipeline() {}, setBindGroup() {}, setVertexBuffer() {}, setIndexBuffer() {}, draw() {}, drawIndexed() {}, end() {} };
  const pipeline = { getBindGroupLayout() { return {}; } };
  const device = {
    createShaderModule: () => ({}), createBuffer({ size }) { const buffer = new ArrayBuffer(size); buffers.push(buffer); return { getMappedRange: () => buffer, unmap() {} }; },
    createRenderPipeline: () => pipeline, createTexture: () => ({ createView: () => ({}), destroy() {} }), createBindGroup: () => ({}),
    createCommandEncoder: () => ({ beginRenderPass: () => pass, finish: () => ({}) }),
    queue: { writeBuffer(...args) { writes.push(args); }, submit() {} },
  };
  const source = load('figma-source.js', {}, { GPUBufferUsage: { VERTEX: 32, INDEX: 16, UNIFORM: 64, COPY_DST: 8 }, GPUTextureUsage: { RENDER_ATTACHMENT: 16 } });
  const frame = { state: {}, output: { width: 375, height: 375, format: 'rgba8unorm', createView: () => ({}) }, time: 1800, params: parameters.MOVING_GRADIENT_PARAMETERS };
  source.setup(device, frame); source.render(device, frame);
  assert.equal(buffers[0].byteLength, 76614 * 3 * 4); assert.equal(frame.state.indexCount, 150528 * 3);
  const u = frame.state.uniformData;
  for (const [index, value] of [[0, 1.78], [1, 1.8], [45, 3.74], [46, 4], [50, .26], [51, 4.29], [52, .04]]) assert.ok(Math.abs(u[index] - value) < 1e-5);
  assert.equal(writes.length, 1); assert.equal(u[3], 3); assert.equal(u[36], 0); assert.equal(u[37], .5); assert.equal(u[38], 1);
});
