// The Memory page's search: every word typed must appear in the note, its kind or where it came from; the words found
// are marked. Pure (the tests run it under Node).

/** The search's words: lower case, split on spaces. */
export const searchWords = (q: string): string[] => q.toLowerCase().split(/\s+/).filter(Boolean);

/** Whether every word appears in one of the texts (a note's own, its kind's title, where it came from). */
export function noteMatches(words: string[], ...texts: string[]): boolean {
  const hay = texts.join(" ").toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** The text escaped (esc), with the words found in it marked (<mark>); the longer word wins where two overlap. Found
 * in the plain text and escaped piece by piece, so a word never matches inside an escape ("amp" in "&amp;"). */
export function markWords(text: string, words: string[], esc: (s: string) => string): string {
  if (!words.length) return esc(text);
  const alt = [...words].sort((a, b) => b.length - a.length).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return text.split(new RegExp(`(${alt.join("|")})`, "gi"))
    .map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part))).join("");
}
