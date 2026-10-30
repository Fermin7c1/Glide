// The rail on the left and the pages it switches between. A page can ask to be refreshed each time it's shown.

let page = "coach";
const onShown = new Map<string, () => void>();

export const currentPage = () => page;

/** Run `fn` whenever page `p` is shown (it reads fresh data then). */
export function whenShown(p: string, fn: () => void): void {
  onShown.set(p, fn);
}

export function show(p: string): void {
  page = p;
  document.querySelectorAll<HTMLButtonElement>("#rail>button[data-page]").forEach((b) => {
    b.classList.toggle("on", b.dataset.page === p);
    if (b.dataset.page === p) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".page").forEach((s) => s.classList.toggle("on", s.id === `page-${p}`));
  onShown.get(p)?.();
}

export function wireNav(): void {
  document.querySelectorAll<HTMLButtonElement>("#rail>button[data-page]").forEach((b) => (b.onclick = () => show(b.dataset.page!)));
}
