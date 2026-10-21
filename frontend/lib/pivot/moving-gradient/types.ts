// A deliberately small WebGPU boundary: this project does not install global GPU types.
export interface GradientTexture {
  width: number;
  height: number;
  format: string;
  createView(): unknown;
  destroy(): void;
}
export interface GradientPipeline { getBindGroupLayout(index: number): unknown }
export interface GradientPass {
  setPipeline(pipeline: GradientPipeline): void;
  setBindGroup(index: number, group: unknown): void;
  draw(count: number): void;
  end(): void;
}
export interface GradientDevice extends EventTarget {
  lost: Promise<unknown>;
  queue: { submit(commands: unknown[]): void; onSubmittedWorkDone(): Promise<void> };
  createTexture(descriptor: unknown): GradientTexture;
  createShaderModule(descriptor: { code: string }): { getCompilationInfo(): Promise<{ messages: { type: string }[] }> };
  createRenderPipeline(descriptor: unknown): GradientPipeline;
  createBindGroup(descriptor: unknown): unknown;
  createCommandEncoder(): { beginRenderPass(descriptor: unknown): GradientPass; finish(): unknown };
  pushErrorScope(filter: string): void;
  popErrorScope(): Promise<unknown>;
  destroy(): void;
}
export interface GradientGpu {
  requestAdapter(options: { powerPreference: string }): Promise<{ requestDevice(): Promise<GradientDevice> } | null>;
  getPreferredCanvasFormat(): string;
}
export interface GradientContext {
  configure(options: { device: GradientDevice; format: string; alphaMode: string }): void;
  getCurrentTexture(): GradientTexture;
  unconfigure(): void;
}
export interface GradientFrame {
  state: Record<string, unknown>;
  output: GradientTexture;
  time: number;
  params: typeof import('./parameters').MOVING_GRADIENT_PARAMETERS;
}
