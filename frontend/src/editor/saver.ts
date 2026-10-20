import type { Annotation } from "../api/client";
import { api } from "../api/client";

/**
 * Cola de guardado: por imagen se envía siempre lo ÚLTIMO que hay (varios cambios seguidos se funden en una petición)
 * y las peticiones de una misma imagen nunca se solapan, así no llegan al servidor desordenadas.
 */
export class Saver {
  private pending = new Map<number, Annotation>();
  private running = new Set<number>();
  private inflight = 0;
  constructor(private onDone: (frameId: number, ok: boolean, err?: string) => void) {}

  queue(frameId: number, ann: Annotation) {
    this.pending.set(frameId, ann);
    void this.pump(frameId);
  }
  get busy() { return this.inflight > 0 || this.pending.size > 0; }

  private async pump(frameId: number) {
    if (this.running.has(frameId)) return;
    this.running.add(frameId);
    try {
      while (this.pending.has(frameId)) {
        const ann = this.pending.get(frameId)!;
        this.pending.delete(frameId);
        this.inflight++;
        try {
          await api.save(frameId, ann);
          this.onDone(frameId, true);
        } catch (e) {
          this.onDone(frameId, false, (e as Error).message);
        } finally {
          this.inflight--;
        }
      }
    } finally {
      this.running.delete(frameId);
    }
  }
}
