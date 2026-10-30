# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

World of Warcraft Forever players leveling a character to 60 who follow (or want to beat) the RestedXP routes. The primary user plays with WoW fullscreen on one monitor and keeps Glide open on a second monitor, glancing over between pulls, at arm's length. The app will be distributed to other players, so first run, setup and settings must make sense to a stranger, not only to its author.

## Product Purpose

Glide is a live AI leveling coach. It reads the game state from the Glide addon (a pixel strip the companion captures), knows the RestedXP route and a knowledge base built from collected quest data, and tells the player what to do next: by voice, in a click-through overlay over the game, and in the desktop app. Success is the player leveling faster with fewer mistakes, without having to stop and read.

## Positioning

The coach reasons from the player's live game state plus a knowledge base built from every quest, NPC and route recorded in Forever (with provenance and RestedXP step analysis), not from a static guide. It speaks, answers push-to-talk questions, and can use any model provider the player plugs in.

## Operating Context

- Used during play sessions of hours; glanced at, not read. Spoken callouts carry most of the information; the screen confirms it.
- The desktop app (Electron) hosts: the coach console (the engine's dashboard, served by the Python companion), Settings, Addon & game (WoW folder, addon install), and Logs. A tray icon and a click-through overlay run alongside.
- Setup moments: first install, choosing the WoW folder, installing the addon, adding API keys, picking models and voices.

## Capabilities and Constraints

- Glance priorities while playing: the current RestedXP step and where to go (waypoint distance and direction, map); XP pace (level, XP/hour, time to level, session); gear upgrades, buff timers and open reminders. The coach feed matters too but is mostly heard.
- The dashboard is served by the Python engine at 127.0.0.1 inside an iframe; it and the Electron shell are separate documents and must look like one product.
- Runs offline except for the AI and online voice providers; fonts and assets must be bundled, nothing loaded from the web.
- Windows only for now. Users zoom the window with Ctrl + wheel.
- Terminology from the game and RestedXP: step, waypoint, turn-in, rested XP, hearth, quest log, flight path.

## Brand Commitments

- Name: Glide to 60 ("Glide" for short).
- App icon: the drawn icon, desktop/assets/icon.svg (a red-orange winged boot in neon line work, speeding up and to the right on black), rendered to icon.png by scripts/render-icon.js. It replaced the game's Sprint icon on 2026-10-30 when the repository went public: no Blizzard art in the repository or the installer.

## Evidence on Hand

No testimonials, user counts or benchmarks exist; none may be invented.

## Product Principles

1. Glanceable first: the answer to "what do I do now" must be readable from arm's length in under a second.
2. Speak, then show: the screen confirms what the voice said; it doesn't compete with the game.
3. Honest about what it knows: unknown, stale or offline states are shown plainly, never dressed up.
4. Setup a stranger can finish: every setting says what it does in plain words.

## Accessibility & Inclusion

Legibility at distance is a hard requirement: large type for the glance data, strong contrast, color never the only signal. Zoom must keep layouts intact.
