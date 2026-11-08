// Fire drawn from engine/state `fire_grid` (arrival time per 10 m cell), redrawn every animation frame at the
// interpolated scenario time: the front creeps smoothly between the 1 s engine updates and between cells
// (arrival times are upsampled bilinearly, so edges follow sub-cell contours instead of 10 m steps).
import type { CanvasSource, Map } from "maplibre-gl";
import type { FireGrid } from "./types";

const UP = 2;                 // canvas pixels per grid cell
const FPS = 20;
// colours (rgb + alpha 0-255) match the legend
const BLACK = [11, 11, 12, 150], HOT = [240, 120, 52, 205], EMBER = [150, 50, 42, 165];
const FRONT5 = [207, 122, 82, 92], FRONT10 = [217, 169, 90, 58];

export class FireLayer {
  private canvas = document.createElement("canvas");
  private ctx = this.canvas.getContext("2d")!;
  private img?: ImageData;
  private arr?: Float32Array;          // upsampled arrival, s after t0
  private burnout = 300;
  private stateRel = 0;                 // scenario seconds (ts - t0) of the latest message
  private rxAt = 0;                     // performance.now() when it arrived
  private key = "";
  private seq = 0;
  private timer = 0;
  private map: Map;

  constructor(map: Map, beforeId: string) {
    this.map = map;
    this.canvas.width = this.canvas.height = 1;
    // placeholder corners until the first grid; replaced in update()
    map.addSource("fire", { type: "canvas", canvas: this.canvas, animate: true,
      coordinates: [[-112.79, 34.24], [-112.74, 34.24], [-112.74, 34.2], [-112.79, 34.2]] });
    map.addLayer({ id: "fire", type: "raster", source: "fire", paint: { "raster-resampling": "linear", "raster-fade-duration": 0 } }, beforeId);
    this.timer = window.setInterval(() => this.draw(), 1000 / FPS);
  }

  destroy() { clearInterval(this.timer); }

  async update(grid: FireGrid | null | undefined, ts: number | undefined) {
    const seq = ++this.seq;
    if (!grid || ts == null) { this.arr = undefined; this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); return; }
    const bytes = Uint8Array.from(atob(grid.data), c => c.charCodeAt(0));
    const raw = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"))).arrayBuffer();
    if (seq !== this.seq) return;                       // a newer message arrived meanwhile
    const q = new Uint16Array(raw), { cols, rows, unit_s, t0 } = grid;
    const rel = ts - t0, far = rel + 3600;
    // cell values in s after t0: the black at Start well past burnout, unreached cells far in the future
    const v = new Float32Array(cols * rows);
    for (let i = 0; i < v.length; i++) v[i] = q[i] === 0 ? -grid.burnout_s - 120 : q[i] === 65535 ? far : (q[i] - 1) * unit_s;
    const W = cols * UP, H = rows * UP, key = `${cols}x${rows}:${grid.corners.join()}`;
    if (key !== this.key) {
      this.canvas.width = W; this.canvas.height = H;
      this.img = this.ctx.createImageData(W, H);
      (this.map.getSource("fire") as CanvasSource | undefined)?.setCoordinates(grid.corners);
      this.key = key;
    }
    const a = this.arr?.length === W * H ? this.arr : new Float32Array(W * H);
    for (let y = 0; y < H; y++) {
      const sy = Math.min(Math.max((y + .5) / UP - .5, 0), rows - 1), r0 = Math.floor(sy), r1 = Math.min(r0 + 1, rows - 1), fy = sy - r0;
      for (let x = 0; x < W; x++) {
        const sx = Math.min(Math.max((x + .5) / UP - .5, 0), cols - 1), c0 = Math.floor(sx), c1 = Math.min(c0 + 1, cols - 1), fx = sx - c0;
        const top = v[r0 * cols + c0] * (1 - fx) + v[r0 * cols + c1] * fx;
        const bot = v[r1 * cols + c0] * (1 - fx) + v[r1 * cols + c1] * fx;
        a[y * W + x] = top * (1 - fy) + bot * fy;
      }
    }
    this.arr = a; this.burnout = grid.burnout_s; this.stateRel = rel; this.rxAt = performance.now();
    this.draw();
  }

  private draw() {
    const a = this.arr, img = this.img;
    if (!a || !img) return;
    // scenario time now: latest message + time since it arrived (capped, so a stalled engine doesn't run ahead)
    const now = this.stateRel + Math.min((performance.now() - this.rxAt) / 1000, 1.5);
    const d = img.data, burn = this.burnout;
    for (let i = 0, p = 0; i < a.length; i++, p += 4) {
      const age = now - a[i];
      let c: number[] | null;
      if (age >= burn) c = BLACK;
      else if (age >= 0) {                                  // burning: bright at the front, fading to embers
        const k = age / burn;
        d[p] = HOT[0] + (EMBER[0] - HOT[0]) * k; d[p + 1] = HOT[1] + (EMBER[1] - HOT[1]) * k;
        d[p + 2] = HOT[2] + (EMBER[2] - HOT[2]) * k; d[p + 3] = HOT[3] + (EMBER[3] - HOT[3]) * k;
        continue;
      } else c = age >= -300 ? FRONT5 : age >= -600 ? FRONT10 : null;
      if (c) { d[p] = c[0]; d[p + 1] = c[1]; d[p + 2] = c[2]; d[p + 3] = c[3]; } else d[p + 3] = 0;
    }
    this.ctx.putImageData(img, 0, 0);
  }
}
