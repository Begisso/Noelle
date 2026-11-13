// Display script for displays/Main.nll ("script" property). Exported functions can be
// called by actions of type "script". Types: scripts/hmi.d.ts (VS Code IntelliSense).

/** @param {import("./hmi").DisplayContext} ctx */
export function onOpen(ctx) {
  ctx.subscribe("{{Temp1}}", (v) => ctx.set("panel", { stroke: v > 70 ? "#ff5a5a" : "#3a414b" }));
}

let on = false;
/** @param {import("./hmi").DisplayContext} ctx */
export function highlight(ctx) {
  on = !on;
  ctx.set("panel", { fill: on ? "#2d3a4d" : "#22272e" });
  ctx.toast(on ? "panel highlighted (JS)" : "highlight off");
}
