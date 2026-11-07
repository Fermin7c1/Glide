// What's new for a version not installed yet: the release's notes (release.yml writes "Glide <v>: <title>", the
// items as a list, then "source: <sha>"), as electron-updater hands them over. Pure (no electron), so tests run it
// under Node (tests/test_update_notes.py).

/** A release's notes (electron-updater's releaseNotes: GitHub's HTML of the release body, markdown, or a list per
 * version) as What's new: the title of the "Glide <v>: <title>" line and the items (list entries), without the
 * "source: <sha>" line release.yml adds. */
export function releaseNotes(raw: unknown, version = ""): { title: string; notes: string[] } {
  let text = "";
  if (Array.isArray(raw)) {
    const mine = raw.find((r) => r && r.version === version) || raw[0];
    text = String(mine?.note ?? "");
  } else if (typeof raw === "string") text = raw;
  const plain = (s: string) => s.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;|&apos;|&#x27;/g, "'").replace(/\s+/g, " ").trim();
  let items = [...text.matchAll(/<li[^>]*>([\s\S]*?)<\/li>/gi)].map((m) => plain(m[1]));
  if (!items.length) items = text.split(/\r?\n/).filter((l) => /^\s*[-*]\s+/.test(l)).map((l) => plain(l.replace(/^\s*[-*]\s+/, "")));
  const head = plain(text.split(/<\/(?:p|h\d)>|\r?\n/)[0] || "");
  const title = (/^Glide\s+[\d.]+:\s*(.+)$/.exec(head) || [])[1] || "";
  return { title: title.slice(0, 120), notes: items.filter((x) => x && !/^source:/i.test(x)).slice(0, 12) };
}
