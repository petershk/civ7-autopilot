---
name: civ7-settling
description: How to choose settlement sites and use Settlers in Civilization VII (placement rules, site scoring, settlement limit, age-specific advice). Compiled from Civ VII guides and forums, 2026-09.
---
# Skill: settling (where and when to found settlements)

## Hard rules (the game refuses otherwise)
- Settlement centres must be **at least 4 tiles apart** (3 empty tiles between). No exception across water.
- Can't found on: mountains, water, navigable-river tiles, occupied tiles, other players' territory; likely not on
  resource tiles either (check with get_plot if unsure).
- Borders never grow past **3 tiles** from a centre. Centres 4-6 apart keep territory joined; 7+ leaves gaps rivals can fill.
- Tiles can't be traded between your settlements later, so don't settle so close that you steal a neighbour's 3rd ring.

## Scoring a candidate site (settle_spots gives candidates; judge them yourself)
1. **Fresh water** (on a river, next to a lake/oasis, or next to a navigable river): about +5 happiness; without it about -5.
   It can never be added later. Strongly prefer it.
2. **Food and production** in the 3-tile ring, then gold. Several resources nearby are good (you claim them by working them).
3. **Coast or navigable river**: needed for ships, fishing quays, treasure fleets and sea trade; coast gives food/gold
   building adjacency.
4. **Defence**: hills, cliffs and rivers help. Escort settlers near independents or enemies, which kill them.
5. **Connection**: within about 8-10 tiles of your network, roads connect automatically; farther needs a merchant road.
The game's own recommendations over-weight fresh water and ignore adjacency, coast, border gaps and threats: treat
them as a starting list. When settle_spots says "fallback scan", the game had no suggestion and you're seeing the
remaining legal tiles; weigh them with get_plot / get_map.

## Settlement limit
- Base limit: Antiquity about 3-4, Exploration 8, Modern 16. Techs and civics raise it.
- Each settlement over the limit costs about -5 happiness **in every settlement** (capped around -35). Going 1-2 over
  is fine only if happiness income clearly covers it; never early in an age.
- Under the limit, another settlement (even a mediocre one) is almost always worth more than an idle settler.

## By age
- **Antiquity**: expand fast to the limit; good sites first. Every city except the capital reverts to a town at the age
  change, and **settlers and other civilians left on the map disappear**, so spend them before the age ends.
- **Exploration (Distant Lands)**: needs Cartography to cross deep ocean (Shipbuilding stops ocean damage).
  - Economic path: treasure fleets need Distant-Lands settlements with treasure resources, improved, plus a fishing
    quay. Prefer coastal sites near home that have several treasure resources.
  - Military path: each new Distant-Lands settlement = 1 point (conquest 2).
  - Hover/get_plot shows whether a tile counts as Distant Lands.
- **Modern**: settlements still pay off up to the limit (resource and factory towns). A settler with no good site:
  settle the best legal site that keeps you under the limit rather than letting it idle; if nothing legal is
  reachable, stop spending turns on it (it vanishes at the end anyway) and stop building more settlers.

## Don'ts
- Don't walk a settler back and forth for turns: pick a site, move, found. Re-evaluate only if the site becomes invalid.
- Don't build settlers when no legal site exists (settle_spots empty) or you're at the limit with low happiness.
