---
name: civ7-learned
description: Lessons the Civ VII agent learned from its own games and research (curated at strategy reviews)
---
# Lessons we learned (from our own games and research)
Apply these; they came from real outcomes. Newer lessons override older ones that conflict.
## Diplomacy
- Espionage Steal Tech (and Steal Civic) can only run one at a time across all targets, so restart it as soon as it finishes. Don't let influence pile up: a big bank (thousands) is wasted when you're behind in science. Steal from hostile civs rather than allies. _(T31/T71 Modern)_

## Policies
- Policy cards can only be swapped on the turn a civic completes or a celebration starts; at other times set_policies fails silently (ok:false with no reasons). Plan swaps for those turns. _(T41 Modern)_

## Happiness
- A capital in unrest loses a huge share of its yields (Capua fell from 71 to 27 production at -12 happiness). Fix negative capital happiness first: happiness buildings, Divine Right, fewer specialists. _(T41 Modern)_
- Natural disasters damage buildings, which silently lowers happiness and yields. When a city's happiness suddenly goes negative, check city_build_options(purchase=True) for items marked "repair". Repairs are very cheap (about 50-400 gold), so buy them right away. _(T71 Modern)_

## Modern Culture (artifacts)
- An Explorer must stand ON the University or Museum TILE, not the city centre, to use Research Artifacts. That action reveals the Exploration-age dig sites; then move to a site and Excavate, which takes several turns. Find the building's tile with run_js (Constructibles location). _(T41 Modern)_
- Research Artifacts works once per continent per era. If the Explorer shows only Wake/Delete, test it with run_js Game.UnitOperations.canStart(id,'UNITOPERATION_RESEARCH_ARTIFACTS') for the reason. Find the revealed dig sites by scanning MapConstructibles for IMPROVEMENT_RUINS, then send the Explorer to them (other continents too). _(T61 Modern)_
- The Hegemony civic (unlocks Antiquity-era dig sites) needs BOTH Nationalism AND Globalism and costs 7500 culture. With low culture it arrives too late, so pick Globalism early if you want the Culture path. Plan for roughly 5-6 Explorers working in parallel, plus Museums and the Palace for display slots. 15 displayed artifacts unlock the World's Fair. Dig sites can be taken by rivals, so start early. _(T41/T71 Modern)_
- Modern Explorers cost about 800 production (a purchase costs about 3200 gold), so queue them early in the highest-production city. Don't let the capital's happiness collapse, which kills the production needed to build them. _(T51/T71 Modern)_

## Modern Economic (Railroad Tycoon)
- Railroad Tycoon points come from Factory Resources slotted in settlements that have a Factory and are rail-connected to the capital. The milestones are 150/300/500, and at 500 you get the Great Banker. Research Industrialization (Rail Station), then Mass Production (Factory, which needs a Rail Station or Port). Put a Rail Station in the capital first, then connect every settlement; turning towns into Factory Towns adds slots. Bank gold before these techs land so you can buy them at once. _(T61 Modern)_

## Strategy
- Falling far behind in science by mid-Modern (5x+) makes the space race hopeless; commit early to the Economic and Culture paths plus score instead of splitting effort. _(T71 Modern)_
