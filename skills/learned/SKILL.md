---
name: civ7-learned
description: Lessons the Civ VII agent learned from its own games and research (curated at strategy reviews)
---
# Lessons we learned (from our own games and research)
Apply these; they came from real outcomes. Newer lessons override older ones that conflict.
Tags: [verified] = checked against the game's rules data or confirmed by the user. [strategy] = a judgment call
that data can't settle. [unverified] = from a single observation or guess: treat it as a hint and check it
(lookup_rules) before relying on it.
## Antiquity: Culture Legacy (Wonders)
- Culture legacy path in Antiquity is "Wonders of the Ancient World" — each **completed** wonder = 1 legacy point toward milestones at 4 wonders and 7 wonders. A wonder must be fully finished to count; queuing or half-building gives **zero** credit. Only Antiquity wonders count; later-age wonders do not unlock this path. [unverified] _(T82-T92 Egypt)_
- Wonder racing in late Antiquity (after T80) is tight: to hit 4-wonder milestone by age end (~48 turns), need average ~12t per wonder. Achievable but risky if races are lost. Economic path (Markets + resource assignment) is more reliable fallback. [unverified] _(T92 Egypt)_

## Antiquity: Economic Legacy (Resources)
- Economic path requires Market buildings (+1 resource capacity per building). Markets unlock after Citizenship civic. Assign 7 resources to cities → milestone 1 (7/20). Viable in late Antiquity if markets queued early after Citizenship completes. [verified] _(T92 Egypt)_

## Antiquity: Science Legacy
- Science legacy requires Library (unlocked by Writing tech). Writing is **not always available** in the tech tree — if it's absent (tree shows only Navigation, Engineering, Military Training after Bronze Working chain), Science path is dead for that age. Check tech_options early. [strategy] _(T92 Egypt)_
- Antiquity science milestones require 3/6/10 legacy points. If Writing is unavailable, focus on Economic/Culture instead. [verified] _(T92 Egypt)_


## Diplomacy
- Research Collaboration: Support option costs 60 influence for +6 sci/turn (both players) vs Accept (free, +2 sci/turn for you, +4 for them). Support is better if influence reserves allow. [verified] _(T92 Egypt)_
- Espionage Steal Tech/Civic: can only run one at a time across all targets; restart immediately after finishing. Don't let influence pile up when behind in science. [unverified] _(T31/T71 Modern)_

## Policies
- Policy cards can only be swapped on the turn a civic completes or a celebration starts; at other times set_policies fails silently. Plan swaps for those turns. [unverified] _(T92 Egypt)_

## Happiness & Growth
- A capital in unrest (−12 happiness) loses ~60% of yields; fix negative capital happiness first with happiness buildings and fewer specialists. [unverified] _(T41 Modern)_
- Natural disasters damage buildings, which silently lowers happiness and yields. Check city_build_options(purchase=True) for "repair" items (~50–400 gold); buy immediately. [unverified] _(T71 Modern)_

## Settlements
- Settlers require city population ≥ 5 (Antiquity); Antiquity settlers disappear at age transition, so spend them before end-of-age. [unverified] _(Antiquity)_

## Modern Culture (Artifacts & Explorers)
- An Explorer must stand ON the University or Museum **tile** (not city centre) to use Research Artifacts. Reveals dig sites; then move and Excavate (several turns). Find building tile with run_js. [unverified] _(T41 Modern)_
- Research Artifacts works once per continent per era. If only Wake/Delete shown, test with run_js Game.UnitOperations.canStart(id,'UNITOPERATION_RESEARCH_ARTIFACTS'). [unverified] _(T61 Modern)_
- Modern Explorers cost ~800 production (~3200 gold). Queue early in highest-production city; don't let capital happiness collapse (kills production). [unverified] _(T51/T71 Modern)_

## Modern Economic (Railroad Tycoon)
- Factory Resources slotted in settlements with Factory + rail-connection to capital. Milestones 150/300/500; at 500 get Great Banker. Research Industrialization (Rail Station) then Mass Production (Factory). Rail Station in capital first, connect all settlements. [unverified] _(T61 Modern)_

## Strategy
- Falling far behind in science by mid-Modern (5x+) makes space race hopeless; commit to Economic + Culture paths + score instead. [unverified] _(T71 Modern)_
