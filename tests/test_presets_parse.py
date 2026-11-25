#!/usr/bin/env python3
"""WoWSims' talent builds read from a spec's presets.ts (companion/simbuild.py presets, parse_presets) and an .apl.siml
rotation converted by WoWSims' runner (companion/wowsims.py WoWSims.siml_parse). It pins:
- the narrow pattern on a presets.ts snippet: each makePresetBuild whose talents are a makePresetTalents string and
  whose rotation a makePresetAPLRotation of an imported .apl.json or .apl.siml, by the build's name (one taken: its
  talent preset's); then a talent preset no build took, paired with the APL preset of its name; a build on a simple
  rotation, a talent preset with no rotation, an empty talents string, a commented-out build or field, an import
  outside the repository's specs and a .siml build (makePresetBuildFromSiml) left out; "//" inside quotes isn't a
  comment; rotation paths resolved in the repository;
- presets() reads the class's presets.ts at the release through the runner's files, and refuses a class WoWSims
  doesn't sim here;
- siml_parse runs "siml parse -json <file>" with the release's runner on the file kept at the release (a name that
  says its kind), without the player's API keys, and gives the JSON it prints; the runner's own reason when the file
  doesn't load; not a .siml, or no WoWSims: SimError;
- with a local WoWSims runner (GLIDE_WOWSIMS_HOME, else %TEMP%/glide-wowsims-home) the real runner converts a small
  mage .apl.siml to the APL a request carries, and says why a broken one doesn't load; WoWSims' own mage presets.ts,
  when kept there, gives its Arcane Fire build with its own rotation. Without one that part is skipped (the rest runs).

Run: python3 tests/test_presets_parse.py   (prints OK)
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "companion"))
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
           "TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ[_k] = ""
os.environ["GLIDE_PRESETS_TEST_API_KEY"] = "not-a-real-key"     # (made up: the runner never gets *_API_KEY)
import simbuild  # noqa: E402
import wowsims  # noqa: E402
from simbuild import SimError  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + str(what))


TA, TF = "050205223101311531-5500003-003", "050205203100311531-525000012001-003"
TFR = "-5-053500011121331251"
SNIPPET = """﻿import * as PresetUtils from '@app/preset_utils';
import { SavedTalents } from '@generated/proto/ui';
import arcane from './apls/arcane.apl.json';
import fireFill from "./apls/arcaneFireFill.apl.json";
import frostSiml from './apls/frost.apl.siml';
import escape from '../../../../evil.apl.json';
import PreRaidJson from './gear_sets/preraid.gear.json';
import SimlBuildJson from './builds/siml.siml';

export const ArcaneApl = PresetUtils.makePresetAPLRotation('Arcane', arcane);
export const FireFillApl = PresetUtils.makePresetAPLRotation('Arcane Fire', fireFill, { group: 'Arcane' });
export const FrostApl = PresetUtils.makePresetAPLRotation('Frost', frostSiml);
export const EvilApl = PresetUtils.makePresetAPLRotation('Evil', escape);
export const SimpleRot = PresetUtils.makePresetSimpleRotation('Simple', Spec.SpecMage, {});

export const ArcaneTalents = PresetUtils.makePresetTalents('Arcane', SavedTalents.create({ talentsString: '%s' }));
export const ArcaneFireTalents = PresetUtils.makePresetTalents(
\t'Arcane Fire',
\tSavedTalents.create({ talentsString: "%s" }),
);
export const FrostTalents = PresetUtils.makePresetTalents('Frost', SavedTalents.create({ talentsString: '%s' }));
export const LonelyTalents = PresetUtils.makePresetTalents('Lonely', SavedTalents.create({ talentsString: '5-5-5' }));
export const EvilTalents = PresetUtils.makePresetTalents('Evil', SavedTalents.create({ talentsString: '1' }));
export const EmptyTalents = PresetUtils.makePresetTalents('Simple', SavedTalents.create({ talentsString: '' }));

/* export const OldBuild = PresetUtils.makePresetBuild('Old', {
\ttalents: LonelyTalents,
\trotation: ArcaneApl,
}); */

export const ArcaneBuild = PresetUtils.makePresetBuild('Arcane', {
\tgroup: 'Arcane',
\tgear: PreRaid,
\ttalents: ArcaneTalents,
\t// rotation: FireFillApl,
\trotation: ArcaneApl,
\tsettings: { tooltip: 'See https://wowsims.com/forever/ // not a comment', nested: { a: 1 } },
});
// the same name again: under its talent preset's
export const ArcaneFireBuild = PresetUtils.makePresetBuild('Arcane', { talents: ArcaneFireTalents, rotation: FireFillApl });
export const SimpleBuild = PresetUtils.makePresetBuild('Simple', { talents: LonelyTalents, rotation: SimpleRot });
export const EvilBuild = PresetUtils.makePresetBuild('Evil', { talents: EvilTalents, rotation: EvilApl });
const build = (name: string, json: any) => PresetUtils.makePresetBuildFromSiml(name, Spec.SpecMage, json, {});
export const Builds = [build('Siml', SimlBuildJson)];
""" % (TA, TF, TFR)
BASE = "ui/specs/mage/dps"

# ---- the narrow pattern --------------------------------------------------------------------------------------------
got = simbuild.parse_presets(SNIPPET, BASE)
check(list(got) == ["Arcane", "Arcane Fire", "Frost"], got)
check(got["Arcane"] == (TA, BASE + "/apls/arcane.apl.json"), got["Arcane"])
check(got["Arcane Fire"] == (TF, BASE + "/apls/arcaneFireFill.apl.json"), "a taken name: the talent preset's")
check(got["Frost"] == (TFR, BASE + "/apls/frost.apl.siml"), "paired by name, an .apl.siml rotation")
check(simbuild.parse_presets("", BASE) == {} and simbuild.parse_presets("export const x = 1;", BASE) == {}, "none")
commented = simbuild.parse_presets(SNIPPET.replace("\trotation: ArcaneApl,\n", ""), BASE)
check(commented.get("Arcane") == got["Arcane Fire"] and TA not in [t for t, _ in commented.values()],
      "a commented-out rotation isn't the build's (its name goes to the next build): %s" % commented)
check(simbuild.parse_presets(SNIPPET.replace("\r\n", "\n").replace("\n", "\r\n"), BASE) == got, "CRLF")


# ---- presets(): the class's presets.ts at the release, through the runner's files ----------------------------------
class FakeWS:
    def __init__(self, files):
        self.files, self.asked = files, []

    def file(self, path, tag=None):
        self.asked.append((path, tag))
        if path not in self.files:
            raise wowsims.FetchError("WoWSims %s has no %s" % (tag, path))
        return self.files[path]


fake = FakeWS({simbuild.CLASSES["MAGE"]["presets"]: SNIPPET.encode("utf-8")})
check(simbuild.CLASSES["MAGE"]["presets"] == BASE + "/presets.ts", simbuild.CLASSES["MAGE"]["presets"])
check(simbuild.presets("mage", fake, "v1.0.4") == got and fake.asked == [(BASE + "/presets.ts", "v1.0.4")],
      fake.asked)
try:
    simbuild.presets("PALADIN", fake, "v1.0.4")
    check(False, "no presets for a class WoWSims doesn't sim here")
except SimError as e:
    check("Paladin" in str(e), e)
try:
    simbuild.presets("MAGE", FakeWS({}), "v1.0.4")
    check(False, "a presets.ts that can't be had")
except wowsims.FetchError:
    pass

# ---- siml_parse: the release's runner converts the kept file -------------------------------------------------------
FAKE_RUNNER = r'''
import json, os, sys
args = sys.argv[1:]
if args[:3] != ["siml", "parse", "-json"] or len(args) != 4:
    sys.stderr.write("usage: wowsimcli siml parse [-json] <file>\n"); sys.exit(2)
path = args[3]
text = open(path, encoding="utf-8").read()
if "BROKEN" in text:
    sys.stderr.write(path + ":4:8 unknown-name no spell is named broken\n" + path + " does not load\n"); sys.exit(1)
if "GARBLED" in text:
    print("not json"); sys.exit(0)
keys = sorted(k for k in os.environ if k.upper().endswith("_API_KEY"))
print(json.dumps({"type": "TypeAPL", "priorityList": [], "first": text.splitlines()[0], "name": os.path.basename(path),
                  "keys": keys}))
'''
tmp = tempfile.mkdtemp(prefix="glide-presets-")
try:
    runner = os.path.join(tmp, "fake_wowsimcli.py")
    with open(runner, "w", encoding="utf-8") as fh:
        fh.write(FAKE_RUNNER)
    home = os.path.join(tmp, "home")

    def keep(tag, path, text):
        p = os.path.join(home, "data", "wowsims", tag, "files", *path.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def no_network(url, limit):
        raise OSError("no network in this test: %s" % url)

    ws = wowsims.WoWSims(home, get=no_network, command=lambda tag: [sys.executable, runner])
    good, broken, garbled = (BASE + "/apls/frost.apl.siml", BASE + "/apls/broken.apl.siml",
                             BASE + "/apls/garbled.apl.siml")
    keep("v9.9.9", good, "siml 1\nspec mage\n\npriority:\n  cast frostbolt\n")
    keep("v9.9.9", broken, "siml 1\nspec mage\n\npriority:\n  cast BROKEN\n")
    keep("v9.9.9", garbled, "GARBLED\n")
    out = ws.siml_parse(good, "v9.9.9")
    check(out["type"] == "TypeAPL" and out["first"] == "siml 1" and out["name"] == "frost.apl.siml", out)
    check(out["keys"] == [], "the runner never sees an API key: %s" % out["keys"])
    try:
        ws.siml_parse(broken, "v9.9.9")
        check(False, "a rotation that doesn't load")
    except SimError as e:
        check("no spell is named broken" in str(e) and "glide-siml-" not in str(e) and "v9.9.9" in str(e), e)
    try:
        ws.siml_parse(garbled, "v9.9.9")
        check(False, "output that isn't JSON")
    except SimError as e:
        check("no JSON" in str(e), e)
    for bad in (BASE + "/apls/arcane.apl.json", "ui/specs/mage/dps/apls/../../x y.siml"):
        try:
            ws.siml_parse(bad, "v9.9.9")
            check(False, "not a .siml: %s" % bad)
        except SimError:
            pass
    try:
        ws.siml_parse(good)
        check(False, "no WoWSims installed")
    except SimError as e:
        check("isn't installed" in str(e), e)
    try:
        ws.siml_parse(BASE + "/apls/missing.apl.siml", "v9.9.9")
        check(False, "a file that can't be had")
    except wowsims.FetchError:
        pass
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---- the real runner, when there is one --------------------------------------------------------------------------------
def local_release():
    """(engine home, tag) of a local WoWSims runner, or None."""
    home = os.environ.get("GLIDE_WOWSIMS_HOME") or os.path.join(tempfile.gettempdir(), "glide-wowsims-home")
    base = os.path.join(home, "data", "wowsims")
    try:
        tags = sorted((t for t in os.listdir(base) if wowsims.TAG.match(t)), reverse=True)
    except OSError:
        return None
    for tag in tags:
        if os.path.isfile(os.path.join(base, tag, wowsims.EXE)):
            return home, tag
    return None


found = local_release()
if not found:
    print("OK (no local WoWSims runner: its part skipped)")
    raise SystemExit(0)
HOME, TAG = found
EXE = os.path.join(HOME, "data", "wowsims", TAG, wowsims.EXE)
tmp = tempfile.mkdtemp(prefix="glide-presets-cli-")
try:
    home = os.path.join(tmp, "home")
    ws = wowsims.WoWSims(home, get=no_network, command=lambda tag: [EXE])
    for path, text in ((BASE + "/apls/tiny.apl.siml", "siml 1\nspec mage\n\npriority:\n  cast arcane_missiles\n"),
                       (BASE + "/apls/bad.apl.siml", "siml 1\nspec mage\n\npriority:\n  cast no_such_spell_xyz\n")):
        p = os.path.join(home, "data", "wowsims", TAG, "files", *path.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    apl = ws.siml_parse(BASE + "/apls/tiny.apl.siml", TAG)
    check(apl.get("type") == "TypeAPL", apl)
    cast = apl["priorityList"][0]["action"]["castSpell"]["spellId"]
    check(cast.get("spellId") == 10212, "Arcane Missiles, as the runner knows it: %s" % json.dumps(apl)[:300])
    try:
        ws.siml_parse(BASE + "/apls/bad.apl.siml", TAG)
        check(False, "the real runner refuses a rotation with a spell it doesn't know")
    except SimError as e:
        check("no_such_spell_xyz" in str(e), e)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
kept = os.path.join(HOME, "data", "wowsims", TAG, "files", *simbuild.CLASSES["MAGE"]["presets"].split("/"))
if os.path.isfile(kept):
    with open(kept, "rb") as fh:
        real = simbuild.parse_presets(fh.read().decode("utf-8-sig"), BASE)
    check(real.get("Arcane Fire", ("", ""))[1].endswith("/arcaneFireFill.apl.json"), real)
    check(real.get("Arcane", ("", ""))[1].endswith("/arcane.apl.json"), real)
print("OK")
