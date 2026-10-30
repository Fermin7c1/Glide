# Third-party notices

Glide's own code is licensed under the GNU General Public License v3.0 or later (see [LICENSE](LICENSE)). The
material below is not Glide's own: each part keeps its own license, listed here with its source and what Glide
does with it. World of Warcraft and Blizzard Entertainment are trademarks of Blizzard Entertainment, Inc.; Glide is
not affiliated with or endorsed by Blizzard. No Blizzard game files ship with Glide: the game data it uses (maps,
terrain, buff icons, the level-up sound, the game's tables) is read at runtime from the player's own installation.

## Game data from community projects

| Where in Glide | Source | License | Notes |
|---|---|---|---|
| `data/seed/seed.json.gz` (source `fg`) | [prezus/ForeverGuide](https://github.com/prezus/ForeverGuide) `Data/*.lua` | ForeverGuide code: MIT. Its `Data/` is derived from [Questie](https://github.com/Questie/Questie), which allows reuse under GPL-3.0 | Quests, NPCs and objects of WoW Forever |
| `data/seed/seed.json.gz` (source `fqm`) | [Swafox/ForeverQuestMarker](https://github.com/Swafox/ForeverQuestMarker) | GPL-3.0 | Which quest IDs are new or changed in Forever |
| `data/seed/seed.json.gz` (source `rxp`), `companion/data/stat_weights.json` | [RestedXP/RXPGuides](https://github.com/RestedXP/RXPGuides) | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) | The free Forever guides and the stat weights; Glide is free and non-commercial, and these files stay under CC BY-NC-SA 4.0 |
| `addon/Glide/Data/VendorTreasures.lua` | [Cpt. Stadics' Map of Vendor Treasures](https://www.curseforge.com/wow/addons/cpt-stadics-map-of-vendor-treasures), via RestedXP | [CC BY-NC 3.0](https://creativecommons.org/licenses/by-nc/3.0/) | Vendors with limited-stock gear and potions |
| `tools/dbd/*.dbd` | [wowdev/WoWDBDefs](https://github.com/wowdev/WoWDBDefs) | Data: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | Layouts of the game's table files, to read them from the player's client |
| `knowledge/community/` | The community scan's own notes; each note cites its sources | Glide's notes: GPL-3.0; quoted facts belong to their sources | Short factual claims with links to the original posts and articles |

Glide used data from [alcaras/forever-ref](https://github.com/alcaras/forever-ref) (a QuestieDB copy, in-game
observations and Wowhead harvests) until 2026-10-30. That repository publishes no license, and Wowhead's terms don't
allow redistributing its data, so none of it is in Glide any more.

## Fonts

All under the [SIL Open Font License 1.1](https://openfontlicense.org); the full texts are in `companion/static/fonts/`.

- **Tenor Sans**, copyright (c) 2011 Denis Masharov.
- **Geist**, copyright 2024 The Geist Project Authors.
- **Source Serif 4**, copyright 2014-2021 Adobe, with Reserved Font Name Source.

## Icons

`companion/static/icons.svg` holds icons from [Lucide](https://lucide.dev) (lucide-static 1.59.0):

> ISC License
>
> Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright
> (c) for Lucide are held by Lucide Contributors 2022.
>
> Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
> granted, provided that the above copyright notice and this permission notice appear in all copies.
>
> THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL
> IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
> INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
> AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
> PERFORMANCE OF THIS SOFTWARE.

## In the installer

The Windows installer also bundles software under its own licenses, each shipped with its license text: Electron
and Chromium (MIT and the Chromium licenses, `LICENSES.chromium.html`), the embeddable CPython runtime (PSF License)
and NumPy (BSD-3-Clause). Optional parts the app installs on request (faster-whisper and its dependencies, Whisper
models from Hugging Face) come with their own licenses.
