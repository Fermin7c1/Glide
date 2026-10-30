// The Memory page: what the coach learned about the player (notes it keeps and uses), and the open reminders.
// Both live in the engine (/memory, /reminders); the reminders also arrive with every state on the stream.
import { $, esc, icon, toast } from "./dom.js";
import { engineGet, enginePost, live } from "./state.js";
import { Obj } from "./types.js";

const MEM_SOURCE: Record<string, [string, string]> = { player: ["user", "You told me"], coach: ["bot", "The coach noted"],
  reflection: ["lightbulb", "Learned from your sessions"], mark: ["flag", "From your feedback mark"] };
const MEM_TITLE: Record<string, string> = { preference: "How you like to be coached", request: "Standing requests",
  goal: "Your goals", feedback: "Corrections the coach won't repeat", fact: "About you" };
let memory: Obj | null = null;
let editing: number | null = null;

/** Read the memory (no body) or change it ({op: ...}); the new memory, or null with a toast when it failed. */
async function memoryCall(body?: Obj): Promise<Obj | null> {
  if (!live.engineUrl) return null;
  try {
    const r = await (body ? enginePost("/memory", body) : engineGet("/memory"));
    const d = await r.json();
    if (d.ok === false) throw new Error(d.error);
    return d.memory || d;
  } catch (e) {
    toast(`Memory: ${(e as Error).message}`, true);
    return null;
  }
}

export async function refreshMemory(): Promise<void> {
  memory = await memoryCall();
  renderMemory();
  renderReminders();
}

function renderMemory(): void {
  const host = $("mem-notes");
  $<HTMLInputElement>("mem-learning").disabled = !memory;
  $<HTMLButtonElement>("mem-reflect").disabled = !memory;
  if (!live.engineUrl || !memory) {
    host.innerHTML = `<div class="mem-empty">Start the coach to see and edit what it remembers.</div>`;
    return;
  }
  $<HTMLInputElement>("mem-learning").checked = !!memory.learning;
  const notes: Obj[] = memory.notes || [];
  if (!notes.length) {
    host.innerHTML = `<div class="mem-empty">Nothing yet. Tell the coach how you like to be coached, your goals, or when it gets something wrong; say "remember that…" while playing, or add a note above. ${memory.persistent ? "" : "(Simulation mode: notes here are not saved.)"}</div>`;
    return;
  }
  host.innerHTML = (memory.kinds as Obj[]).map((k) => {
    const list = notes.filter((n) => n.kind === k.id);
    if (!list.length) return "";
    return `<div class="mem-group"><h2>${esc(MEM_TITLE[k.id] || k.label)}<span>${list.length}</span></h2><div class="mem-list">${list.map(noteHtml).join("")}</div></div>`;
  }).join("");
  bindNotes(host, notes);
}

function noteHtml(n: Obj): string {
  const when = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const [ic, src] = MEM_SOURCE[n.source] || ["user", n.source];
  const body = editing === n.id
    ? `<input data-edit="${n.id}" value="${esc(n.text)}" aria-label="Edit note">`
    : esc(n.text);
  return `<div class="note${n.pinned ? " pinned" : ""}"><div><div class="txt">${body}</div>
        <div class="meta"><span>${icon(ic)}${esc(src)}</span><span>${n.updated !== n.created ? "Updated" : "Added"} ${when(n.updated)}</span>${n.used ? `<span>Used ${n.used}×</span>` : ""}</div></div>
        <div class="acts">
          <button class="btn ghost icon-only${n.pinned ? " on" : ""}" data-pin="${n.id}" title="${n.pinned ? "Unpin" : "Pin: always include it"}" aria-label="${n.pinned ? "Unpin" : "Pin"}">${icon(n.pinned ? "pin-off" : "pin")}</button>
          <button class="btn ghost icon-only" data-ed="${n.id}" title="${editing === n.id ? "Save" : "Edit"}" aria-label="Edit">${icon(editing === n.id ? "check" : "pencil")}</button>
          <button class="btn ghost icon-only danger" data-del="${n.id}" title="Forget it" aria-label="Forget">${icon("trash-2")}</button>
        </div></div>`;
}

/** Pin, edit (Enter saves, Esc cancels) and forget. */
function bindNotes(host: HTMLElement, notes: Obj[]): void {
  const save = async (id: number) => {
    const inp = host.querySelector<HTMLInputElement>(`[data-edit="${id}"]`);
    editing = null;
    if (inp) memory = (await memoryCall({ op: "update", id, text: inp.value })) || memory;
    renderMemory();
  };
  host.querySelectorAll<HTMLButtonElement>("[data-pin]").forEach((b) => (b.onclick = async () => {
    const n = notes.find((x) => x.id === Number(b.dataset.pin));
    memory = (await memoryCall({ op: "update", id: Number(b.dataset.pin), pinned: !n?.pinned })) || memory;
    renderMemory();
  }));
  host.querySelectorAll<HTMLButtonElement>("[data-ed]").forEach((b) => (b.onclick = () => {
    const id = Number(b.dataset.ed);
    if (editing === id) return save(id);
    editing = id;
    renderMemory();
    host.querySelector<HTMLInputElement>(`[data-edit="${id}"]`)?.focus();
  }));
  host.querySelectorAll<HTMLInputElement>("[data-edit]").forEach((inp) => (inp.onkeydown = (e) => {
    if (e.key === "Enter") save(Number(inp.dataset.edit));
    if (e.key === "Escape") { editing = null; renderMemory(); }
  }));
  host.querySelectorAll<HTMLButtonElement>("[data-del]").forEach((b) => (b.onclick = async () => {
    memory = (await memoryCall({ op: "remove", id: Number(b.dataset.del) })) || memory;
    toast("Forgotten.");
    renderMemory();
  }));
}

export function renderReminders(): void {
  const host = $("rem-list");
  if (!live.engineUrl) { host.innerHTML = `<div class="rem-empty">Start the coach to see your reminders.</div>`; return; }
  const reminders = live.reminders;
  host.innerHTML = reminders.length ? reminders.map((r) => `<div class="rem-row"><button class="btn icon-only" data-done="${r.id}" title="Mark done" aria-label="Mark done">${icon("check")}</button>
    <div class="grow">${esc(r.what)}<small>${esc([r.when, r.every_min ? `nudges every ${r.every_min} min` : ""].filter(Boolean).join(", ") || "whenever")}</small></div></div>`).join("")
    : `<div class="rem-empty">No open reminders.</div>`;
  host.querySelectorAll<HTMLButtonElement>("[data-done]").forEach((b) => (b.onclick = () =>
    enginePost("/reminders", { done: Number(b.dataset.done) }).catch(() => undefined)));
}

export function wireMemory(): void {
  $("mem-add").onsubmit = async (e) => {
    e.preventDefault();
    const text = $<HTMLInputElement>("mem-text").value.trim();
    if (!text) return;
    const r = await memoryCall({ op: "add", text, kind: $<HTMLSelectElement>("mem-kind").value || undefined });
    if (r) { memory = r; $<HTMLInputElement>("mem-text").value = ""; toast("Remembered."); renderMemory(); }
  };
  $("mem-learning").onchange = async () => {
    memory = (await memoryCall({ op: "learning", on: $<HTMLInputElement>("mem-learning").checked })) || memory;
    toast($<HTMLInputElement>("mem-learning").checked ? "The coach will learn from your sessions." : "Learning is off; the coach only remembers what you tell it.");
  };
  $("mem-reflect").onclick = async () => {
    if (await memoryCall({ op: "reflect" })) toast("The coach is reviewing your recent sessions. New notes appear here and in the coach feed.", false, 7000);
    window.setTimeout(refreshMemory, 15000);
  };
  $("rem-add").onsubmit = (e) => {
    e.preventDefault();
    let q = $<HTMLInputElement>("rem-text").value.trim();
    if (!q || !live.engineUrl) return;
    if (!/remind me/i.test(q)) q = `remind me to ${q}`;
    enginePost("/ask", { q }).catch(() => undefined);
    $<HTMLInputElement>("rem-text").value = "";
    toast("Reminder sent to the coach.");
  };
}
