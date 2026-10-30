// Global key state (user32 GetAsyncKeyState), for the overlay's hold-Alt-to-drag. The game has the focus, so the
// overlay never gets key events itself; the old Python overlay polled the key the same way.
import koffi from "koffi";

export const VK: Record<string, number> = { ALT: 0x12, SHIFT: 0x10, CTRL: 0x11, LBUTTON: 0x01 };

let getAsyncKeyState: ((vk: number) => number) | null = null;
try {
  if (process.platform === "win32") {
    getAsyncKeyState = koffi.load("user32.dll").func("short __stdcall GetAsyncKeyState(int vKey)");
  }
} catch {
  getAsyncKeyState = null;          // no key polling: the overlay still moves with Ctrl+Alt+O
}

export const keysAvailable = () => getAsyncKeyState !== null;

export function keyDown(vk: number): boolean {
  return getAsyncKeyState ? (getAsyncKeyState(vk) & 0x8000) !== 0 : false;
}

/** "ALT" / "SHIFT" / "CTRL" (config companion.overlay.drag_key) -> virtual key, ALT when unknown. */
export function dragVk(name: unknown): number {
  return VK[String(name || "ALT").toUpperCase()] ?? VK.ALT;
}

