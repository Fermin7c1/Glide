// Picks the version release.yml publishes and gets the build ready for it. Every push to main that changes what
// players get is released: the version is the next patch after the last published release (LATEST), or
// package.json's version when someone raised it by hand (a new minor or major). Writes that version into
// package.json for the build only (nothing is committed back) and, when changelog.json has no entry for it, adds one
// from the commit subjects since the last release (SINCE..HEAD). Prints `version=<v>` for $GITHUB_OUTPUT.
//
// The entries it adds are never committed either, so What's new also fills in every published release newer than
// changelog.json's newest entry (RELEASES: a file of the published releases, as `gh api .../releases --jq` writes
// them; release.yml makes it): each from the commits between its source and the release before it (a release's notes
// end with "source: <sha>"; one without, a release from before that, is where changelog.json got its entry).
// Without that, an installed app's What's new showed its own version and then the last entry committed by hand.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const desk = path.join(__dirname, "..");
const pkgFile = path.join(desk, "package.json");
const clFile = path.join(desk, "changelog.json");

const vnum = (v) => String(v || "0").replace(/^v/, "").split(".").map((x) => parseInt(x, 10) || 0);
const newer = (a, b) => { for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i]; return false; };
const today = () => new Date().toISOString().slice(0, 10);

/** git's output, or null when it fails (an unknown commit, a shallow checkout). */
function git(args) {
  try {
    return execFileSync("git", args, { cwd: desk, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return null;
  }
}

/** What's new items for the commits of `range` (git log arguments; null: none known). */
function itemsFor(range) {
  const out = range ? git(["log", "--no-merges", "--format=%s", ...range]) : null;
  const subjects = (out || "").split("\n").map((s) => s.trim()).filter(Boolean);
  const deps = subjects.filter((s) => /^Build\(deps/.test(s)).length;
  // what players notice: not dependency bumps, refreshes (summed up below) or work on CI, docs and releases
  const items = subjects.filter((s) => !/^(Build\(deps|Knowledge refresh|CI|Docs?|README|Releases?)\b/i.test(s));
  if (subjects.some((s) => /^Knowledge refresh/.test(s))) items.push("Fresh community notes and quest data.");
  if (deps) items.push("Updated the components Glide is built on.");
  return items.length ? items : ["Small fixes and improvements."];
}

/** Every JSON value in a text: one array, JSON lines, or values one after another (pretty-printed or not). */
function jsonValues(text) {
  const out = [];
  let depth = 0, start = -1, inStr = false, esc = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === "{" || c === "[") { if (depth++ === 0) start = i; }
    else if ((c === "}" || c === "]") && depth > 0 && --depth === 0) {
      try { out.push(JSON.parse(text.slice(start, i + 1))); } catch { /* not JSON: skip it */ }
    }
  }
  return out.flatMap((v) => (Array.isArray(v) ? v : [v]));
}

/** The published releases in RELEASES, oldest first: {version, date, source}. */
function published(file) {
  if (!file || !fs.existsSync(file)) return [];
  const rows = jsonValues(fs.readFileSync(file, "utf8").replace(/^﻿/, ""));
  return rows.filter((r) => r && !r.draft && /^v?\d+\.\d+\.\d+$/.test(String(r.tag_name || "")))
    .map((r) => ({
      version: vnum(r.tag_name).join("."),
      date: String(r.published_at || "").slice(0, 10) || today(),
      source: (/source: ([0-9a-f]{40})/.exec(String(r.body || "")) || [])[1] || null,
    }))
    .sort((a, b) => (newer(vnum(a.version), vnum(b.version)) ? 1 : newer(vnum(b.version), vnum(a.version)) ? -1 : 0));
}

/** The commit a published release was built from: its notes' source, else the commit that gave changelog.json its
 * entry (a release from before the notes named one); null when this checkout doesn't have it. */
function sourceOf(rel) {
  const known = (sha) => (sha && git(["cat-file", "-e", `${sha}^{commit}`]) !== null ? sha : null);
  if (rel.source) return known(rel.source);
  const first = (git(["log", "--reverse", "--format=%H", "-S", `"version": "${rel.version}"`, "--", "changelog.json"]) || "")
    .split("\n")[0].trim();
  return known(first);
}

const pkg = JSON.parse(fs.readFileSync(pkgFile, "utf8"));
const base = vnum(pkg.version);
const latest = process.env.LATEST ? vnum(process.env.LATEST) : null;
const next = !latest || newer(base, latest) ? base : [latest[0], latest[1], latest[2] + 1];
const version = next.join(".");

pkg.version = version;
fs.writeFileSync(pkgFile, JSON.stringify(pkg, null, 2) + "\n");

const cl = JSON.parse(fs.readFileSync(clFile, "utf8"));
const known = new Set(cl.versions.map((v) => v.version));
const newestKnown = cl.versions.reduce((m, v) => (newer(vnum(v.version), m) ? vnum(v.version) : m), [0, 0, 0]);
let added = false;

// the releases since changelog.json's newest entry, each from the commits since the release before it
const pub = published(process.env.RELEASES);
pub.forEach((rel, i) => {
  if (known.has(rel.version) || rel.version === version || !newer(vnum(rel.version), newestKnown)) return;
  const src = sourceOf(rel), prev = i > 0 ? sourceOf(pub[i - 1]) : null;
  const range = !src ? null : prev ? [`${prev}..${src}`] : ["-1", src];
  cl.versions.push({ version: rel.version, date: rel.date, title: "Fixes and improvements", items: itemsFor(range) });
  known.add(rel.version);
  added = true;
});

// this build's own entry
if (!known.has(version)) {
  const range = process.env.SINCE ? [`${process.env.SINCE}..HEAD`] : ["-1"];
  cl.versions.push({ version, date: today(), title: "Fixes and improvements", items: itemsFor(range) });
  added = true;
}

if (added) {
  cl.versions.sort((a, b) => (newer(vnum(b.version), vnum(a.version)) ? 1 : newer(vnum(a.version), vnum(b.version)) ? -1 : 0));
  fs.writeFileSync(clFile, JSON.stringify(cl, null, 2) + "\n");
}

console.log(`version=${version}`);
