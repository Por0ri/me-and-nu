import { setup, render } from './figma-source';
import { MOVING_GRADIENT_PARAMETERS } from './parameters';
import type { GradientContext, GradientDevice, GradientFrame, GradientGpu, GradientTexture } from './types';

// The source writes straight alpha. Canvas presentation requires premultiplied RGB.
// Keep this conversion outside the original WGSL/setup/render implementation.
const PRESENT = `
@group(0) @binding(0) var image: texture_2d<f32>;
@vertex fn vertex(@builtin(vertex_index) i: u32) -> @builtin(position) vec4f {
  let p = array(vec2f(-1., -3.), vec2f(-1., 1.), vec2f(3., 1.));
  return vec4f(p[i], 0., 1.);
}
@fragment fn fragment(@builtin(position) p: vec4f) -> @location(0) vec4f {
  let c = textureLoad(image, vec2i(p.xy), 0);
  return vec4f(c.rgb * c.a, c.a);
}`;

export type GradientStatus = 'static' | 'initializing' | 'animated' | 'fallback';
type Environment = {
  gpu?: GradientGpu;
  requestFrame(callback: FrameRequestCallback): number;
  cancelFrame(id: number): void;
  setTimer(callback: () => void, delay: number): number;
  clearTimer(id: number): void;
  now(): number;
  pixelRatio: number;
};

export function startMovingGradient(canvas: HTMLCanvasElement, onStatus: (status: GradientStatus) => void,
  environment: Environment = {
    gpu: (navigator as Navigator & { gpu?: GradientGpu }).gpu,
    requestFrame: window.requestAnimationFrame.bind(window), cancelFrame: window.cancelAnimationFrame.bind(window),
    setTimer: window.setTimeout.bind(window), clearTimer: window.clearTimeout.bind(window),
    now: () => performance.now(), pixelRatio: window.devicePixelRatio,
  }): () => void {
  let stopped = false, frameId = 0, watchdog = 0, device: GradientDevice | undefined;
  let context: GradientContext | null = null, output: GradientTexture | undefined;
  let state: Record<string, unknown> = {};
  const cleanup = () => {
    if (stopped) return;
    stopped = true;
    environment.cancelFrame(frameId);
    environment.clearTimer(watchdog);
    device?.removeEventListener('uncapturederror', onGpuError);
    // Pipelines, bind groups and shader modules have no destroy(); dropping state releases them.
    for (const key of ['vertexBuffer', 'indexBuffer', 'uniformBuf', 'msaaTexture', 'depthTexture']) {
      (state[key] as { destroy(): void } | undefined)?.destroy();
    }
    state = {};
    output?.destroy();
    context?.unconfigure();
    device?.destroy();
  };
  const fail = () => { if (!stopped) { cleanup(); onStatus('fallback'); } };
  const onGpuError = (event: Event) => { event.preventDefault(); fail(); };
  if (!environment.gpu) { onStatus('static'); return cleanup; }
  onStatus('initializing');
  // Initialization must never extend or block the existing 3-second Splash transition.
  watchdog = environment.setTimer(fail, 2500);
  void (async () => {
    try {
      const gpu = environment.gpu!;
      const adapter = await gpu.requestAdapter({ powerPreference: 'low-power' });
      if (stopped) return;
      if (!adapter) { fail(); return; }
      const acquired = await adapter.requestDevice();
      if (stopped) { acquired.destroy(); return; }
      device = acquired;
      device.addEventListener('uncapturederror', onGpuError);
      void device.lost.then(fail, fail);
      context = (canvas as unknown as { getContext(name: 'webgpu'): GradientContext | null }).getContext('webgpu');
      if (!context) { fail(); return; }
      const format = gpu.getPreferredCanvasFormat();
      // 375px logical artwork; cap backing resolution and allow at most one submission in flight.
      canvas.width = canvas.height = Math.round(375 * Math.min(1.5, Math.max(1, environment.pixelRatio)));
      context.configure({ device, format, alphaMode: 'premultiplied' });
      // WebGPU's stable bit flags: RENDER_ATTACHMENT=16, TEXTURE_BINDING=4.
      output = device.createTexture({ size: [canvas.width, canvas.height], format: 'rgba8unorm', usage: 16 | 4 });
      const frame: GradientFrame = { state, output, time: 0, params: MOVING_GRADIENT_PARAMETERS };
      device.pushErrorScope('validation');
      setup(device, frame);
      const sourceModule = state.shaderModule as ReturnType<GradientDevice['createShaderModule']>;
      const compilation = await sourceModule.getCompilationInfo();
      const setupError = await device.popErrorScope();
      if (stopped) return;
      if (setupError || compilation.messages.some(message => message.type === 'error')) { fail(); return; }
      const module = device.createShaderModule({ code: PRESENT });
      const pipeline = device.createRenderPipeline({ layout: 'auto',
        vertex: { module, entryPoint: 'vertex' }, fragment: { module, entryPoint: 'fragment', targets: [{ format }] },
        primitive: { topology: 'triangle-list' },
      });
      const group = device.createBindGroup({ layout: pipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: output.createView() }] });
      const origin = environment.now();
      let last = -Infinity, presented = false;
      const tick = async (time: number) => {
        if (stopped) return;
        if (time - last < 1000 / 30) { frameId = environment.requestFrame(tick); return; }
        last = time;
        try {
          const current = device!;
          current.pushErrorScope('validation');
          frame.time = time - origin; // Figma frame.time is milliseconds; source converts to seconds.
          render(current, frame);
          const encoder = current.createCommandEncoder();
          const pass = encoder.beginRenderPass({ colorAttachments: [{ view: context!.getCurrentTexture().createView(),
            clearValue: { r: 0, g: 0, b: 0, a: 0 }, loadOp: 'clear', storeOp: 'store' }] });
          pass.setPipeline(pipeline); pass.setBindGroup(0, group); pass.draw(3); pass.end();
          current.queue.submit([encoder.finish()]);
          const [error] = await Promise.all([current.popErrorScope(), current.queue.onSubmittedWorkDone()]);
          if (stopped) return;
          if (error) { fail(); return; }
          if (!presented) { presented = true; environment.clearTimer(watchdog); onStatus('animated'); }
          frameId = environment.requestFrame(tick);
        } catch { fail(); }
      };
      frameId = environment.requestFrame(tick);
    } catch { fail(); }
  })();
  return cleanup;
}
