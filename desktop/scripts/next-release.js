// Picks the version release.yml publishes and gets the build ready for it. Every push to main that changes what
// players get is released: the version is the next patch after the last published release (LATEST), or
// package.json's version when someone raised it by hand (a new minor or major). Writes that version into
// package.json for the build only (nothing is committed back) and, when changelog.json has no entry for it, adds one
// from the commit subjects since the last release (SINCE..HEAD). Prints `version=<v>` for $GITHUB_OUTPUT.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const desk = path.join(__dirname, "..");
const pkgFile = path.join(desk, "package.json");
const clFile = path.join(desk, "changelog.json");

const vnum = (v) => String(v || "0").replace(/^v/, "").split(".").map((x) => parseInt(x, 10) || 0);
const newer = (a, b) => { for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i]; return false; };

const pkg = JSON.parse(fs.readFileSync(pkgFile, "utf8"));
const base = vnum(pkg.version);
const latest = process.env.LATEST ? vnum(process.env.LATEST) : null;
const next = !latest || newer(base, latest) ? base : [latest[0], latest[1], latest[2] + 1];
const version = next.join(".");

pkg.version = version;
fs.writeFileSync(pkgFile, JSON.stringify(pkg, null, 2) + "\n");

const cl = JSON.parse(fs.readFileSync(clFile, "utf8"));
if (!cl.versions.some((v) => v.version === version)) {
  const range = process.env.SINCE ? [`${process.env.SINCE}..HEAD`] : ["-1"];
  const subjects = execFileSync("git", ["log", "--no-merges", "--format=%s", ...range], { cwd: desk, encoding: "utf8" })
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const deps = subjects.filter((s) => /^Build\(deps/.test(s)).length;
  // what players notice: not dependency bumps, refreshes (summed up below) or work on CI, docs and releases
  const items = subjects.filter((s) => !/^(Build\(deps|Knowledge refresh|CI|Docs?|README|Releases?)\b/i.test(s));
  if (subjects.some((s) => /^Knowledge refresh/.test(s))) items.push("Fresh community notes and quest data.");
  if (deps) items.push("Updated the components Glide is built on.");
  cl.versions.unshift({
    version,
    date: new Date().toISOString().slice(0, 10),
    title: "Fixes and improvements",
    items: items.length ? items : ["Small fixes and improvements."],
  });
  fs.writeFileSync(clFile, JSON.stringify(cl, null, 2) + "\n");
}

console.log(`version=${version}`);
