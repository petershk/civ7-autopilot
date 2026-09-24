# Civilization VII — player primer (for the AI agent)

## How the game is structured
- Three **Ages**: Antiquity → Exploration → Modern. Each age ends when age progress reaches 100%
  (driven by legacy-path milestones + turns). At each age transition you pick a NEW civilization
  (unlocked by your leader, previous civ, or things you did), keep your cities (most revert to towns
  except capital), and spend legacy points on "legacy" cards that carry bonuses forward.
- **Legacy Paths** (per age: Culture, Economic, Military, Science). Completing milestones gives
  legacy points and age progress. Completing the final milestone of a path in the Modern age
  (e.g. Science: space-flight projects; Culture: World's Fair; Economic: World Bank; Military:
  ideology/conquest points) wins the game. There is also a score/turn-limit fallback.
  - Antiquity: Culture = build Wonders; Economic = assign Resources to cities (trade routes help);
    Science = slot Codices in buildings (from techs/great library); Military = conquer settlements.
  - Exploration: Culture = relics via religion; Economic = treasure fleets from distant lands;
    Science = high-yield districts; Military = settle/conquer distant lands.
  - Modern: Science = space race projects; Culture = artifacts → World's Fair; Economic = factories/
    railroad tycoon → World Bank; Military = ideology points from conquest.
- **Dark ages / crises**: each age ends with a crisis (plague, revolt, invasion) requiring crisis policies.

## Settlements
- In this game version a **Settler requires its city to have population ≥ 5** (city_build_options shows
  locked items and why). Grow the capital fast (food tiles, Granary) to unlock settlers; buying with gold also
  needs the population.
- Settlers found **Towns** (no production queue: they grow and convert production to gold; can be given
  a focus/specialization once pop ≥7) — upgrade to a **City** with gold. Capital is a city.
- **Settlement limit**: exceeding it costs happiness per extra settlement. It rises with techs/civics.
  Being a few over is often fine early if happiness is positive.
- Growth: when a settlement grows you choose a rural tile to expand onto (gets an improvement) or,
  in cities, add a specialist to an urban tile (costs happiness/food, boosts yields & adjacency).
- Buildings go on urban tiles; two buildings of the current age on one tile make a "quarter"
  (unique quarters give big bonuses). Adjacency matters (science near mountains/resources, etc.).
- Good city sites: fresh water (rivers/lakes), coast, resources, not too close (≥4 tiles apart).

## Economy & yields
- Food, Production, Gold, Science, Culture, Happiness, Influence.
- Happiness < 0 in a settlement → unrest, yield penalties. Keep it positive; celebrations
  (from accumulated happiness) grant a temporary bonus + a new policy slot.
- Influence is the diplomacy currency: befriend independents (→ city-states, suzerain bonuses),
  endeavors/treaties with majors, sanctions, espionage.
- Gold buys units/buildings/tiles and upgrades towns to cities.

## Military
- Units are led by **Commanders** (army commanders can pack units into a stack, gain promotions).
- Independents (barbarian-like villages) attack early; defend with slingers/warriors, fortify on hills.
- War: formal war (after denouncing / sufficient war support) vs surprise war (big relationship penalty).
  War support affects happiness. Capturing settlements is the Military legacy path.
- Ranged units attack without taking retaliation; melee captures cities; siege needed vs walls.

## Practical priorities (strong generic plan)
1. Early: scout, 2–4 settlers quickly (expansion is the #1 engine), a couple of defensive units.
2. Pick techs that unlock growth/production (Pottery→granary, Irrigation, Masonry, Writing→library,
   Currency→market). Civics: Chiefdom/Code of Laws for policies, Mysticism for pantheon.
3. Keep every city producing something useful; never leave queues empty.
4. Pursue 2 legacy paths per age actively; don't ignore the others completely.
5. Befriend nearby independents with influence; become suzerain for bonuses.
6. Stay at peace with strong neighbours unless you have a clear military edge; accept friendly
   first-meets; keep relationships at least neutral.
7. Plan for the Modern age victory from Exploration onward (science is the most reliable).

## Operating notes (how this bot works)
- You act only through the tools; the game's on-screen popups are just visuals. Decisions shown in popups
  (narratives, policies, first meets...) must be made with the tools (choice_options/make_choice etc.).
- Cinematic placards (natural disasters, wonders), "Tech/Civic Unlocked" and already-answered narrative boxes
  are closed automatically (close_popups). If a turn seems stuck, call close_popups, then end_turn again.
- Natural disasters (floods, eruptions, storms) damage tiles/buildings but also enrich land: check affected
  cities afterwards and repair damaged buildings via city_build_options (repair items).
