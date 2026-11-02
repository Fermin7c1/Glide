// Game icons on the Guides page: which image from the player's own client stands for a target, a step's badge or a
// prep item (facts.icons from the engine: wowfiles/target_icons.py through knowledge/api/explorer.py guide_icons),
// shown through glide://icon/<fileDataID>.png (desktop/src/main/game-icons.ts). An icon earns its place by making
// the target recognisable in the game: the boar to look for, the vendor's bag, the item to loot. null keeps the
// page's own glyph, and so does an image that fails to load. DOM-free: tests/test_rxp_icons.py runs it under Node.
import type { RxpFactsPrep, RxpFactsTarget, RxpGuideAction } from "../shared/ipc";

/** facts.icons: "item:<id>" | "spell:<id>" | "npc:<id>" | "role:<name>" -> FileDataID. */
export type IconMap = Record<string, number>;

/** One game icon: its URL and what it shows, for the image's alt text. */
export interface GameIcon { url: string; what: string }

export const HEARTHSTONE = 6948;

export const iconUrl = (fdid: number) => `glide://icon/${fdid}.png`;

const ROLE_WHAT: Record<string, string> = {
  vendor: "vendor", repair: "repair", trainer: "trainer", profession: "profession trainer", flight: "flight master",
  innkeeper: "innkeeper", banker: "banker", stable: "stable master", quest_available: "quest to accept",
  quest_turnin: "quest to turn in", elite: "elite", rare: "rare",
};

function icon(icons: IconMap | null | undefined, key: string, what: string): GameIcon | null {
  const f = icons?.[key];
  return typeof f === "number" && f > 0 ? { url: iconUrl(f), what } : null;
}

/** A role or quest mark (role:vendor, role:quest_turnin, ...). */
export function roleIcon(icons: IconMap | null | undefined, role: string): GameIcon | null {
  return icon(icons, `role:${role}`, ROLE_WHAT[role] || role);
}

/** An item's own icon. */
export function itemIcon(icons: IconMap | null | undefined, id: number | null | undefined, name?: string | null): GameIcon | null {
  return id ? icon(icons, `item:${id}`, name || "item") : null;
}

/** A creature's icon: its beast family's, else its type's (the engine picked it). */
export function npcIcon(icons: IconMap | null | undefined, id: number | null | undefined, name?: string | null): GameIcon | null {
  return id ? icon(icons, `npc:${id}`, name || "creature") : null;
}

/** What a map target shows: a creature its own icon; an object the icon of the item it gives (else the glyph); a quest
 * area stays a ring. */
export function targetIcon(t: RxpFactsTarget, icons: IconMap | null | undefined): GameIcon | null {
  if (t.kind === "npc") return npcIcon(icons, t.id, t.name);
  if (t.kind === "object") return itemIcon(icons, t.item, t.name);
  return null;
}

/** The item a target gives, for its card ("Loot: <item>"); null when it gives none or the icon is unknown. */
export function lootIcon(t: RxpFactsTarget, icons: IconMap | null | undefined): GameIcon | null {
  return t.role === "loot" || t.kind === "object" ? itemIcon(icons, t.item) : null;
}

/** The elite or rare mark for a rank (1 elite, 2 rare elite, 3 boss, 4 rare). */
export function rankIcon(rank: number | null | undefined, icons: IconMap | null | undefined): GameIcon | null {
  if (rank === 1 || rank === 3) return roleIcon(icons, "elite");
  if (rank === 2 || rank === 4) return roleIcon(icons, "rare");
  return null;
}

/** The step's targets of a unit verb's names (.target / .mob name a creature by name; kb found its id). */
function unitByName(a: RxpGuideAction, targets: RxpFactsTarget[] | undefined): RxpFactsTarget | null {
  const names = new Set((a.units || []).map((u) => u.toLowerCase()));
  if (!names.size || !targets) return null;
  return targets.find((t) => t.kind === "npc" && t.name && names.has(t.name.toLowerCase())) || null;
}

/** A step badge's icon (the step list, the cards, the inspector), or null to keep its glyph. */
export function actionIcon(a: RxpGuideAction, icons: IconMap | null | undefined, targets?: RxpFactsTarget[]): GameIcon | null {
  if (!icons) return null;
  const item = a.entity_kind === "item" ? a.entity_id : null;
  switch (a.verb) {
    case "accept": case "acceptmultiple": case "daily":
      return roleIcon(icons, "quest_available");
    case "turnin": case "turninmultiple": case "dailyturnin":
      return roleIcon(icons, "quest_turnin");
    case "collect": case "collectmultiple": case "buy": case "buyAll": case "buyUntilBroke": case "use": case "equip":
    case "openitem":
      return itemIcon(icons, item, a.entity_name);
    case "train":
      return (a.entity_kind === "spell" && a.entity_id ? icon(icons, `spell:${a.entity_id}`, a.entity_name || "spell") : null)
        || roleIcon(icons, "trainer");
    case "trainer":
      return roleIcon(icons, "trainer");
    case "vendor":
      return roleIcon(icons, "vendor");
    case "stable": case "tame":
      return roleIcon(icons, "stable");
    case "fp": case "fly":
      return roleIcon(icons, "flight");
    case "home":
      return roleIcon(icons, "innkeeper");
    case "hs":
      return itemIcon(icons, HEARTHSTONE, "Hearthstone");
    case "bankdeposit": case "bankwithdraw":
      return roleIcon(icons, "banker");
    case "target": case "unitscan": case "mob": case "kill": {
      if (a.entity_kind === "npc" && a.entity_id) return npcIcon(icons, a.entity_id, a.entity_name);
      const t = unitByName(a, targets);
      return t ? npcIcon(icons, t.id, t.name) : null;
    }
    default:
      return null;
  }
}

/** A prep strip item's icon. */
export function prepIcon(p: RxpFactsPrep, icons: IconMap | null | undefined): GameIcon | null {
  return itemIcon(icons, p.item_id, p.name);
}

/** A step pin's icon, by the step's kind (the pin's glyph): the game's quest marks on accept and turn-in pins, the
 * flight master on travel, the Hearthstone or the innkeeper on hearth pins, a fight's first creature, a plain step's
 * vendor, trainer, banker or stable master; a deathskip and anything else keep the glyph. */
export function stepIcon(kind: string, actions: RxpGuideAction[], targets: RxpFactsTarget[] | undefined,
  icons: IconMap | null | undefined): GameIcon | null {
  if (!icons) return null;
  const first = (verbs: string[]) => {
    for (const a of actions) if (verbs.includes(a.verb)) { const gi = actionIcon(a, icons, targets); if (gi) return gi; }
    return null;
  };
  switch (kind) {
    case "accept": return roleIcon(icons, "quest_available");
    case "turnin": return roleIcon(icons, "quest_turnin");
    case "travel": return roleIcon(icons, "flight");
    case "hearth": return first(["hs", "home"]);
    case "combat": {
      const t = (targets || []).find((x) => x.kind === "npc" && (x.role === "kill" || x.role === "loot") && icons[`npc:${x.id}`]);
      return t ? npcIcon(icons, t.id, t.name) : first(["mob", "kill", "target"]);
    }
    case "step": return first(["vendor", "trainer", "train", "bankdeposit", "bankwithdraw", "stable"]);
    default: return null;
  }
}
