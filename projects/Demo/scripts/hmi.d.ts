// Types for display scripts (VS Code IntelliSense via JSDoc: @param {import("./hmi").DisplayContext} ctx)
export interface DisplayContext {
  /** current value of a tag reference ("{{...}}" not needed: "Tank1.Level") */
  read(ref: string): any;
  write(ref: string, value: any): Promise<void>;
  /** subscribe to an expression/tag; callback(value, quality) */
  subscribe(ref: string, fn: (value: any, quality: number) => void): void;
  open(path: string, opts?: { target?: "main" | "popup" | "window"; aliases?: Record<string, string>; title?: string }): Promise<any>;
  close(): void;
  alias(name: string): string;
  /** call a server Python function decorated with @hmi.function */
  py(name: string, args?: any): Promise<any>;
  toast(msg: string, level?: "info" | "warn" | "error"): void;
  /** SVG element of an object by id */
  get(id: string): SVGGElement | undefined;
  /** change visual properties of an object at runtime */
  set(id: string, props: { fill?: string; stroke?: string; text?: string; visible?: boolean; opacity?: number }): void;
  every(ms: number, fn: () => void): number;
  obj: any;
}
