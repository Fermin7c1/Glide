// The window's only door to the main process (preload.ts): invoke a channel, listen to an event. The channels, their
// arguments and answers are shared/ipc.ts's InvokeMap and EventMap.
import type { EventChannel, EventMap, InvokeArgs, InvokeChannel, InvokeResult } from "../shared/ipc.js";
import { toast } from "./dom.js";

export interface GlideApi {
  invoke<K extends InvokeChannel>(channel: K, ...args: InvokeArgs<K>): Promise<InvokeResult<K>>;
  on<K extends EventChannel>(channel: K, fn: (payload: EventMap[K]) => void): () => void;
}
declare global { interface Window { glide: GlideApi } }
export const glide = window.glide;

/** An IPC failure as the user should read it, without Electron's "Error invoking remote method" prefix. */
export const ipcError = (e: unknown) => String((e as Error).message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, "");

/** Invoke a channel; a failure shows as an error toast and answers undefined. */
export async function call<K extends InvokeChannel>(channel: K, ...args: InvokeArgs<K>): Promise<InvokeResult<K> | undefined> {
  try {
    return await glide.invoke(channel, ...args);
  } catch (e) {
    toast(ipcError(e), true, 8000);
    return undefined;
  }
}

/** Invoke a channel for a background check: a failure answers undefined, silently. */
export async function quiet<K extends InvokeChannel>(channel: K, ...args: InvokeArgs<K>): Promise<InvokeResult<K> | undefined> {
  try { return await glide.invoke(channel, ...args); } catch { return undefined; }
}
