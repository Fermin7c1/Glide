// Small DOM helpers every part of the window uses: find by id, escape for HTML, an icon from the sprite, a toast.

export const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

/** An icon from the sprite inlined into the page (companion/static/icons.svg). */
export const icon = (n: string, cls = "icon") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;

export const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** A short message that goes away after `ms`; an error is marked as one (red). */
export function toast(text: string, err = false, ms = 4500): void {
  const d = document.createElement("div");
  d.className = err ? "err" : "";
  d.textContent = text;
  $("toast").appendChild(d);
  setTimeout(() => d.remove(), ms);
}
