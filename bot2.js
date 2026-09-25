// CivBot part 2: empire management, choices, diplomacy. Extends globalThis.CB (requires bot.js first).
(function () {
  const CB = globalThis.CB;
  const me = () => GameContext.localPlayerID;
  const P = () => Players.get(me());
  const lk = (tbl, h) => { try { return GameInfo[tbl].lookup(h); } catch (e) { return null; } };
  const loc = (s) => { try { return Locale.compose(s); } catch (e) { return s; } };
  const strip = (s) => (s || "").replace(/\[icon:(?:YIELD_)?([A-Z_]+)\]/gi, (m, a) => a.toLowerCase().replace(/_/g, " ")).replace(/\[[^\]]*\]/g, "").replace(/\s+/g, " ").trim();
  const T = (s) => strip(loc(s));
  const cid = (c) => c ? `${c.owner};${c.id};${c.type}` : null;
  const parseCid = (s) => { if (typeof s === "object") return s; const [o, i, t] = String(s).split(";").map(Number); return { owner: o, id: i, type: t }; };
  const xy = (l) => l ? [l.x, l.y] : null;
  const safe = (f, d = null) => { try { const v = f(); return v === undefined ? d : v; } catch (e) { return d; } };
  const PO = (op, args) => {
    const r = safe(() => Game.PlayerOperations.canStart(me(), op, args, false));
    if (!r || !r.Success) return { ok: false, reasons: safe(() => (r.FailureReasons || []).map(T), []) };
    Game.PlayerOperations.sendRequest(me(), op, args); return { ok: true };
  };
  const norm = (s) => String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  // fuzzy match a user-supplied name against a list of {key,name}
  const pick = (list, q) => {
    const n = norm(q);
    return list.find(o => norm(o.key) === n) || list.find(o => norm(o.name) === n) ||
      list.find(o => norm(o.key).endsWith(n)) || list.find(o => norm(o.name).includes(n)) || list.find(o => norm(o.key).includes(n));
  };
  const cityById = (s) => {
    if (s == null) return null;
    const c = safe(() => Cities.get(parseCid(s)));
    if (c) return c;
    return (P().Cities.getCities() || []).find(c => norm(T(c.name)) === norm(s) || norm(T(c.name)).includes(norm(s)));
  };
  const YN = { YIELD_FOOD: "food", YIELD_PRODUCTION: "prod", YIELD_GOLD: "gold", YIELD_SCIENCE: "sci", YIELD_CULTURE: "cult", YIELD_HAPPINESS: "happy", YIELD_DIPLOMACY: "infl" };
  CB.version = 2;

  // ---------- overview ----------
  CB.nodeName = (n) => safe(() => T(lk("ProgressionTreeNodes", n).Name), String(n));
  CB.overview = () => {
    const p = P(); const o = { turn: Game.turn, age: safe(() => lk("Ages", Game.age).AgeType), me: CB.playerName(me()) };
    o.yields = {}; for (const k in YN) o.yields[YN[k]] = Math.round(safe(() => p.Stats.getNetYield(YieldTypes[k]), 0) * 10) / 10;
    o.gold = Math.round(safe(() => p.Treasury.goldBalance, 0));
    o.influence = Math.round(safe(() => p.DiplomacyTreasury.diplomacyBalance, 0));
    o.cities = safe(() => p.Cities.getCities().length); o.settlementLimit = safe(() => p.Stats.settlementCap);  // Cities.getCityLimit() always returns 0
    o.units = safe(() => p.Units.getUnitIds().length);
    const techTree = safe(() => Game.ProgressionTrees.getTree(me(), p.Techs.getTreeType()));
    const at = safe(() => techTree.nodes[techTree.activeNodeIndex].nodeType);
    o.research = at != null ? { node: CB.nodeName(at), turns: safe(() => p.Techs.getTurnsForNode(at)) } : null;
    const cTree = safe(() => Game.ProgressionTrees.getTree(me(), p.Culture.getActiveTree()));
    const ac = safe(() => cTree.nodes[cTree.activeNodeIndex].nodeType);
    o.civic = ac != null ? { node: CB.nodeName(ac), turns: safe(() => p.Culture.getTurnsForNode(ac)) } : null;
    o.government = safe(() => T(lk("Governments", p.Culture.getGovernmentType()).Name));
    o.celebration = safe(() => p.Happiness.isInGoldenAge() ? { turnsLeft: p.Happiness.getGoldenAgeTurnsLeft() } : null);
    const apm = Game.AgeProgressManager;
    o.ageProgress = safe(() => `${apm.getCurrentAgeProgressionPoints()}/${apm.getMaxAgeProgressionPoints()}`);
    if (safe(() => apm.isFinalAge)) o.finalAge = true;
    if (safe(() => apm.isAgeOver)) o.ageOver = true;
    o.legacyPaths = CB.legacyProgress();
    o.atWarWith = safe(() => Players.getAlive().filter(q => q.id != me() && q.isMajor && p.Diplomacy.isAtWarWith(q.id)).map(q => CB.playerName(q.id)), []);
    o.turnState = CB.turnState();
    return o;
  };

  const agePaths = () => {
    const age = safe(() => lk("Ages", Game.age).AgeType.replace("AGE_", ""), "");
    return Array.from(GameInfo.LegacyPaths).filter(d => d.EnabledByDefault !== false && d.LegacyPathType.includes(age));
  };
  const pathMax = (t) => { let m = 0; for (const ms of Array.from(GameInfo.AgeProgressionMilestones)) if (ms.FinalMilestone && ms.LegacyPathType == t) m = ms.RequiredPathPoints; return m; };
  CB.legacyProgress = () => {
    const out = {};
    for (const d of agePaths()) {
      const score = safe(() => P().LegacyPaths.getScore(d.LegacyPathType), 0);
      const ms = Array.from(GameInfo.AgeProgressionMilestones).filter(m => m.LegacyPathType == d.LegacyPathType).map(m => m.RequiredPathPoints);
      out[d.LegacyPathType.replace(/^LEGACY_PATH_[A-Z]+_/, "").toLowerCase()] = `${score}/${pathMax(d.LegacyPathType)} (milestones ${ms.join("/")})`;
    }
    return out;
  };

  // Standings vs. competitors, using only what the game shows a human: the diplomacy ribbon (yields,
  // settlements) and the Age Rankings screen (legacy path scores) - for civs we have met.
  CB.rankings = () => {
    const dip = P().Diplomacy; const paths = agePaths();
    const rows = []; let unmet = 0;
    for (const p of Players.getAlive()) {
      if (!p.isMajor) continue;
      const isMe = p.id == me();
      if (!isMe && !safe(() => dip.hasMet(p.id))) { unmet++; continue; }
      const r = { id: p.id, name: isMe ? CB.playerName(p.id) + " (us)" : CB.playerName(p.id), me: isMe };
      for (const k of ["YIELD_SCIENCE", "YIELD_CULTURE", "YIELD_GOLD", "YIELD_HAPPINESS", "YIELD_DIPLOMACY"]) r[YN[k]] = Math.round(safe(() => p.Stats.getNetYield(YieldTypes[k]), 0) * 10) / 10;
      r.settlements = safe(() => p.Stats.numSettlements, null);
      r.legacy = {};
      for (const d of paths) r.legacy[d.LegacyPathType.replace(/^LEGACY_PATH_[A-Z]+_/, "").toLowerCase()] = safe(() => p.LegacyPaths.getScore(d.LegacyPathType), 0);
      r.legacyTotal = Object.values(r.legacy).reduce((a, b) => a + b, 0);
      if (!isMe) { r.relationship = safe(() => Object.keys(DiplomacyPlayerRelationships).find(k => DiplomacyPlayerRelationships[k] == dip.getRelationshipEnum(p.id)).replace("PLAYER_RELATIONSHIP_", "").toLowerCase()); if (safe(() => dip.isAtWarWith(p.id))) r.war = true; }
      rows.push(r);
    }
    const maxes = {}; for (const d of paths) maxes[d.LegacyPathType.replace(/^LEGACY_PATH_[A-Z]+_/, "").toLowerCase()] = pathMax(d.LegacyPathType);
    // our rank (1 = best) on each metric among known civs
    const mine = rows.find(r => r.me); const ranks = {};
    if (mine) {
      const metric = (k, get) => { ranks[k] = 1 + rows.filter(r => !r.me && get(r) > get(mine)).length; };
      for (const k of ["sci", "cult", "gold", "happy", "infl", "settlements", "legacyTotal"]) metric(k, r => r[k] || 0);
      for (const k of Object.keys(maxes)) metric("legacy_" + k, r => r.legacy[k] || 0);
    }
    return { known: rows.length, unmetMajors: unmet, legacyMax: maxes, ourRank: ranks, players: rows.sort((a, b) => b.legacyTotal - a.legacyTotal) };
  };

  // Intelligence dossier on every civ we have met, limited to what the game shows a human player:
  // diplomacy hub (relationship + its history, government, wars/alliances), diplomacy ribbon (yields),
  // age rankings (legacy scores), and settlements/units that are revealed on our map.
  CB.intel = () => {
    const myDip = P().Diplomacy; const out = [];
    const relName = (enumv) => safe(() => Object.keys(DiplomacyPlayerRelationships).find(k => DiplomacyPlayerRelationships[k] == enumv).replace("PLAYER_RELATIONSHIP_", "").toLowerCase());
    const majorsMet = Players.getAlive().filter(q => q.isMajor && q.id != me() && safe(() => myDip.hasMet(q.id)));
    for (const p of Players.getAlive()) {
      if (p.id == me() || p.isBarbarian) continue;
      if (!safe(() => myDip.hasMet(p.id))) continue;
      const kind = p.isMajor ? "major" : p.isMinor ? "city-state" : p.isIndependent ? "independent" : "other";
      const o = { id: p.id, name: CB.playerName(p.id), kind };
      o.civ = safe(() => T(lk("Civilizations", p.civilizationType).Name));
      if (p.isMajor) o.leader = safe(() => T(lk("Leaders", p.leaderType).Name));
      if (safe(() => myDip.isAtWarWith(p.id))) o.atWarWithUs = true;
      if (safe(() => myDip.hasAllied(p.id))) o.alliedWithUs = true;
      if (p.isMajor) {
        o.relationship = relName(safe(() => myDip.getRelationshipEnum(p.id)));
        o.relationshipScore = safe(() => p.Diplomacy.getRelationshipLevel(me()));
        o.government = safe(() => T(lk("Governments", p.Culture.getGovernmentType()).Name));
        o.yields = {}; for (const k of ["YIELD_SCIENCE", "YIELD_CULTURE", "YIELD_GOLD", "YIELD_HAPPINESS", "YIELD_DIPLOMACY"]) o.yields[YN[k]] = Math.round(safe(() => p.Stats.getNetYield(YieldTypes[k]), 0) * 10) / 10;
        o.settlements = safe(() => p.Stats.numSettlements);
        o.legacy = {}; for (const d of agePaths()) o.legacy[d.LegacyPathType.replace(/^LEGACY_PATH_[A-Z]+_/, "").toLowerCase()] = safe(() => p.LegacyPaths.getScore(d.LegacyPathType), 0);
        // why they feel the way they do: aggregate the favor/grievance history by event type
        const agg = {};
        for (const h of safe(() => p.Diplomacy.getPlayerRelationshipHistory(me()), []) || []) {
          const nm = strip(safe(() => Locale.stylize(p.Diplomacy.getFavorGrievanceEventTypeName(h.eventType, h.triggeredBy)), "")) || "other";
          const a = agg[nm] || (agg[nm] = { reason: nm, total: 0, times: 0, lastTurn: 0 });
          a.total += h.amount || 0; a.times++; a.lastTurn = Math.max(a.lastTurn, h.gameTurn || 0);
        }
        o.relationshipHistory = Object.values(agg).map(a => ({ ...a, total: Math.round(a.total * 10) / 10 })).sort((a, b) => Math.abs(b.total) - Math.abs(a.total)).slice(0, 8);
        // their relations with other civs we know
        o.atWarWith = majorsMet.filter(q => q.id != p.id && safe(() => p.Diplomacy.isAtWarWith(q.id))).map(q => CB.playerName(q.id));
        o.alliedWith = majorsMet.filter(q => q.id != p.id && safe(() => p.Diplomacy.hasAllied(q.id))).map(q => CB.playerName(q.id));
      }
      if (p.isMinor) {
        const sz = safe(() => p.Influence.getSuzerain()); o.suzerain = sz == null || sz < 0 ? null : sz == me() ? "us" : CB.playerName(sz);
        o.cityStateType = safe(() => { const t = p.getCityStateCityStateType(); const row = Array.from(GameInfo.CityStateTypes || []).find(r => r.$hash == t); return row ? row.CityStateType.replace("CITY_STATE_", "").toLowerCase() : null; });
      }
      // settlements we have actually seen
      o.knownSettlements = (safe(() => p.Cities.getCities(), []) || []).filter(c => safe(() => GameplayMap.getRevealedState(me(), c.location.x, c.location.y), 0) != RevealedStates.HIDDEN)
        .map(c => ({ name: T(c.name), at: [c.location.x, c.location.y], pop: GameplayMap.getRevealedState(me(), c.location.x, c.location.y) == RevealedStates.VISIBLE ? c.population : undefined, capital: !!c.isCapital, town: !!c.isTown, distance: safe(() => { const cap = P().Cities.getCapital(); return GameplayMap.getPlotDistance(cap.location.x, cap.location.y, c.location.x, c.location.y); }) }));
      o.totalSettlementsSeen = o.knownSettlements.length;
      // their units currently visible to us
      const vis = (safe(() => p.Units.getUnits(), []) || []).filter(u => safe(() => Visibility.isVisible(me(), u.id), false));
      const byType = {}; for (const u of vis) { const t = safe(() => T(lk("Units", u.type).Name), "?"); byType[t] = (byType[t] || 0) + 1; }
      o.visibleUnits = byType;
      // diplomatic actions between us and them (endeavors, sanctions, befriending...)
      o.sharedActions = (safe(() => Game.Diplomacy.getPlayerEvents(p.id), []) || []).filter(e => e.initialPlayer == me() || e.targetPlayer == me())
        .map(e => ({ action: T(safe(() => lk("DiplomacyActions", e.actionType).Name, e.actionTypeName)) || e.actionTypeName, by: e.initialPlayer == me() ? "us" : "them", progress: `${e.progressScore}/${e.completionScore}` })).slice(0, 8);
      out.push(o);
    }
    const order = { major: 0, "city-state": 1, independent: 2, other: 3 };
    return out.sort((a, b) => order[a.kind] - order[b.kind]);
  };

  // ---------- cities ----------
  const prodName = (h) => {
    if (h == null || h == -1 || h == 0) return null;
    const t = safe(() => GameInfo.Types.lookup(h));
    if (t) { const tbl = t.Kind == "KIND_UNIT" ? "Units" : t.Kind == "KIND_PROJECT" ? "Projects" : "Constructibles"; return safe(() => T(lk(tbl, t.Type).Name), t.Type); }
    return String(h);
  };
  CB.citySummary = (c) => {
    const o = { id: cid(c.id), name: T(c.name), town: !!c.isTown, capital: !!c.isCapital, at: xy(c.location), pop: c.population };
    o.yields = {}; for (const k in YN) { const v = safe(() => c.Yields.getNetYield(YieldTypes[k])); if (v != null) o.yields[YN[k]] = Math.round(v * 10) / 10; }
    const bq = c.BuildQueue;
    if (bq) { o.producing = safe(() => bq.isEmpty ? null : prodName(bq.currentProductionTypeHash)); if (o.producing) o.turnsLeft = safe(() => bq.currentTurnsLeft); o.queueLen = safe(() => bq.getQueue().length, 0); }
    o.growthIn = safe(() => c.Growth.turnsUntilGrowth);
    if (safe(() => c.Growth.isReadyToPlacePopulation)) o.needsPopPlacement = true;
    if (c.isTown) o.focus = safe(() => c.Growth.growthType == GrowthTypes.EXPAND ? "GROWTH" : prodName(c.Growth.projectType) || "project");
    if (safe(() => c.Happiness.hasUnrest)) o.unrest = true;
    if (safe(() => c.isBeingRazed)) o.razing = true;
    const hp = safe(() => { const d = Districts.get(c.id) || null; return null; });
    return o;
  };
  CB.cities = () => (P().Cities.getCities() || []).map(CB.citySummary);

  CB.buildOptions = (cityS, purchase = false) => {
    const c = cityById(cityS); if (!c) return { error: "no such city" };
    const out = { city: T(c.name), gold: Math.round(P().Treasury.goldBalance), units: [], buildings: [], projects: [] };
    const q = (qt) => purchase ? safe(() => Game.CityCommands.canStartQuery(c.id, CityCommandTypes.PURCHASE, qt), []) : safe(() => Game.CityOperations.canStartQuery(c.id, CityOperationTypes.BUILD, qt), []);
    out.locked = [];
    for (const { index, result } of q(CityQueryType.Unit) || []) {
      const d = lk("Units", index); if (!d) continue;
      const rq = result.Requirements || {};
      if (!result.Success) {
        if (!rq.FullFailure && !rq.Obsolete && (rq.NeededPopulation || rq.NeededUnlock > -1 || rq.NeededConstructible != null)) {
          const why = []; if (rq.NeededPopulation) why.push(`needs city population ${rq.NeededPopulation} (now ${c.population})`);
          if (rq.NeededUnlock != null && rq.NeededUnlock > -1) why.push("needs a tech/civic unlock");
          if (rq.NeededConstructible != null) why.push("needs a building");
          if (!purchase) out.locked.push({ key: d.UnitType, name: T(d.Name), why: why.join(", ") });
        }
        continue;
      }
      if (rq.FullFailure || rq.Obsolete) continue;
      const e = { key: d.UnitType, name: T(d.Name) };
      if (purchase) e.gold = safe(() => c.Gold.getUnitPurchaseCost(YieldTypes.YIELD_GOLD, d.UnitType)); else { e.cost = safe(() => c.Production.getUnitProductionCost(d.UnitType)); e.turns = safe(() => c.BuildQueue.getTurnsLeft(d.UnitType)); }
      out.units.push(e);
    }
    for (const { index, result } of q(CityQueryType.Constructible) || []) {
      const d = lk("Constructibles", index); if (!d) continue;
      if (!result.Success && !result.InsufficientFunds) continue;
      if (result.InQueue) continue;
      const plots = [...(result.Plots || []), ...(result.ExpandUrbanPlots || [])];
      if (!plots.length && !result.InProgress) continue;
      const e = { key: d.ConstructibleType, name: T(d.Name), cls: (d.ConstructibleClass || "").toLowerCase() };
      if (purchase) { e.gold = result.Cost != null ? result.Cost : safe(() => c.Gold.getBuildingPurchaseCost(YieldTypes.YIELD_GOLD, d.ConstructibleType)); if (result.InsufficientFunds) e.cantAfford = true; }
      else { e.cost = safe(() => c.Production.getConstructibleProductionCost(d.ConstructibleType)); e.turns = safe(() => c.BuildQueue.getTurnsLeft(d.ConstructibleType)); }
      if (result.InProgress) e.inProgress = true;
      if (result.RepairDamaged) e.repair = true;
      const desc = safe(() => T(d.Description)); if (desc && desc.length < 160) e.desc = desc;
      out.buildings.push(e);
    }
    if (!purchase) for (const pr of GameInfo.Projects) {
      if (pr.CityOnly && c.isTown) continue;
      const r = safe(() => Game.CityOperations.canStart(c.id, CityOperationTypes.BUILD, { ProjectType: pr.$index }, false));
      if (r && r.Success && r.Requirements && !r.Requirements.FullFailure && r.Requirements.MeetsRequirements !== false)
        out.projects.push({ key: pr.ProjectType, name: T(pr.Name), turns: safe(() => c.BuildQueue.getTurnsLeft(pr.ProjectType)) });
    }
    if (c.isTown) out.townUpgradeGold = safe(() => c.Gold.getTownUpgradeCost());
    return out;
  };

  CB.bestPlacement = (c, def) => {
    const all = safe(() => c.Yields.calculateAllBuildingsPlacements());
    const pd = all && all.buildings ? all.buildings.find(b => b.constructibleType == def.$hash) : null;
    if (!pd || !pd.placements || !pd.placements.length) return null;
    let best = null, bv = -1e9;
    for (const pl of pd.placements) {
      let v = (pl.yieldChanges || []).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
      if (pl.overbuiltConstructibleID != null && pl.overbuiltConstructibleID != -1) v -= 0.5;
      if (v > bv) { bv = v; best = pl.plotID; }
    }
    return best;
  };

  CB.build = (cityS, what, purchase = false, x = null, y = null) => {
    const c = cityById(cityS); if (!c) return { ok: false, error: "no such city" };
    let key = String(what).toUpperCase();
    let t = safe(() => GameInfo.Types.lookup(key));
    if (!t) {
      const opts = CB.buildOptions(cityS, purchase);
      const all = [...opts.units, ...opts.buildings, ...(opts.projects || [])];
      const m = pick(all, what); if (!m) return { ok: false, error: `unknown/unavailable item '${what}'` };
      key = m.key; t = GameInfo.Types.lookup(key);
    }
    let args;
    if (t.Kind == "KIND_UNIT") args = { UnitType: t.Hash };
    else if (t.Kind == "KIND_PROJECT") { args = { ProjectType: t.Hash }; if (c.isTown) args.InsertMode = CityOperationsParametersValues.Exclusive; }
    else args = { ConstructibleType: t.Hash };
    const api = purchase && t.Kind != "KIND_PROJECT" ? Game.CityCommands : Game.CityOperations;
    const opT = purchase && t.Kind != "KIND_PROJECT" ? CityCommandTypes.PURCHASE : CityOperationTypes.BUILD;
    if (t.Kind == "KIND_CONSTRUCTIBLE") {
      const def = lk("Constructibles", key);
      const r0 = api.canStart(c.id, opT, { ConstructibleType: def.$index }, false);
      let plots = [...(r0.Plots || []), ...(r0.ExpandUrbanPlots || [])];
      let plotIdx = null;
      if (x != null && y != null) plotIdx = GameplayMap.getIndexFromXY(x, y);
      else if (r0.InProgress && r0.Plots && r0.Plots.length) plotIdx = r0.Plots[0];
      else { plotIdx = CB.bestPlacement(c, def); if (plotIdx == null || !plots.includes(plotIdx)) plotIdx = plots[0]; }
      if (plotIdx == null) return { ok: false, error: "no valid placement", reasons: (r0.FailureReasons || []).map(T) };
      const l = GameplayMap.getLocationFromIndex(plotIdx); args.X = l.x; args.Y = l.y;
    }
    const r = api.canStart(c.id, opT, args, false);
    if (!r.Success) return { ok: false, reasons: (r.FailureReasons || []).map(T) };
    api.sendRequest(c.id, opT, args);
    return { ok: true, queued: key, at: args.X != null ? [args.X, args.Y] : undefined, purchased: !!purchase };
  };

  CB.clearQueue = (cityS) => {
    const c = cityById(cityS); if (!c) return { ok: false };
    const n = safe(() => c.BuildQueue.getQueue().length, 0);
    for (let i = n - 1; i >= 0; i--) safe(() => Game.CityOperations.sendRequest(c.id, CityOperationTypes.BUILD, { InsertMode: CityOperationsParametersValues.RemoveAt, QueueLocation: i }));
    return { ok: true, removed: n };
  };

  CB.upgradeTown = (cityS) => {
    const c = cityById(cityS); if (!c) return { ok: false };
    const a = { Directive: OrderTypes.ORDER_TOWN_UPGRADE };
    const r = Game.CityCommands.canStart(c.id, CityCommandTypes.PURCHASE, a, false);
    if (!r.Success) return { ok: false, reasons: (r.FailureReasons || []).map(T), cost: safe(() => c.Gold.getTownUpgradeCost()) };
    Game.CityCommands.sendRequest(c.id, CityCommandTypes.PURCHASE, a); return { ok: true };
  };

  CB.townFocusOptions = (cityS) => {
    const c = cityById(cityS); if (!c || !c.isTown) return { error: "not a town" };
    const r = safe(() => Game.CityCommands.canStart(c.id, CityCommandTypes.CHANGE_GROWTH_MODE, { Type: GrowthTypes.PROJECT }, false));
    const opts = [{ key: "GROWTH", name: "Growth (default, town grows)" }];
    for (const id of (r && r.Projects) || []) { const pr = lk("Projects", id); if (pr) opts.push({ key: pr.ProjectType, name: T(pr.Name), desc: T(pr.Description).slice(0, 150) }); }
    return opts;
  };
  CB.setTownFocus = (cityS, what) => {
    const c = cityById(cityS); if (!c) return { ok: false };
    let args;
    if (norm(what) === "GROWTH") args = { Type: GrowthTypes.EXPAND, ProjectType: ProjectTypes.NO_PROJECT, City: c.id.id };
    else { const m = pick(CB.townFocusOptions(cityS), what); if (!m || m.key == "GROWTH") return { ok: false, error: "unknown focus" }; args = { Type: GrowthTypes.PROJECT, ProjectType: lk("Projects", m.key).$hash, City: c.id.id }; }
    const r = Game.CityCommands.canStart(c.id, CityCommandTypes.CHANGE_GROWTH_MODE, args, false);
    if (!r.Success) return { ok: false, reasons: (r.FailureReasons || []).map(T) };
    Game.CityCommands.sendRequest(c.id, CityCommandTypes.CHANGE_GROWTH_MODE, args);
    safe(() => Game.CityOperations.sendRequest(c.id, CityOperationTypes.CONSIDER_TOWN_PROJECT, {}));
    return { ok: true };
  };

  // population placement
  CB.growthOptions = (cityS) => {
    const c = cityById(cityS) || (P().Cities.getCities() || []).find(c => safe(() => c.Growth.isReadyToPlacePopulation)); if (!c) return { error: "no city needs placement" };
    const out = { city: T(c.name), cityId: cid(c.id), expand: [], specialists: [] };
    const r = safe(() => Game.CityCommands.canStart(c.id, CityCommandTypes.EXPAND, {}, false));
    if (r && r.Plots) r.Plots.forEach((pi, i) => {
      const l = GameplayMap.getLocationFromIndex(pi);
      const y = {}; for (const [yt, amt] of safe(() => GameplayMap.getYields(pi, me()), [])) { const n = YN[safe(() => lk("Yields", yt).YieldType)]; if (n && amt) y[n] = amt; }
      const imp = safe(() => T(lk("Constructibles", r.ConstructibleTypes[i]).Name));
      const res = safe(() => { const rt = GameplayMap.getResourceType(l.x, l.y); return rt >= 0 ? T(lk("Resources", rt).Name) : null; });
      out.expand.push({ at: xy(l), yields: y, improvement: imp, resource: res || undefined, score: Object.values(y).reduce((a, b) => a + b, 0) + (res ? 1.5 : 0) });
    });
    out.expand.sort((a, b) => b.score - a.score);
    if (!c.isTown && safe(() => c.Workers.getCityWorkerCap(), 0) > 0) {
      for (const inf of safe(() => c.Workers.GetAllPlacementInfo(), []) || []) {
        if (inf.IsBlocked || inf.NumWorkers >= inf.MaxWorkers) continue;
        const l = GameplayMap.getLocationFromIndex(inf.PlotIndex);
        const gain = {}; (inf.NextYields || []).forEach((v, i) => { const d = v - ((inf.CurrentYields || [])[i] || 0); const n = YN[safe(() => GameInfo.Yields[i].YieldType)]; if (d && n) gain[n] = Math.round(d * 10) / 10; });
        out.specialists.push({ at: xy(l), workers: `${inf.NumWorkers}/${inf.MaxWorkers}`, gain });
      }
    }
    return out;
  };
  CB.placePop = (cityS, x, y, specialist = false) => {
    const c = cityById(cityS) || (P().Cities.getCities() || []).find(c => safe(() => c.Growth.isReadyToPlacePopulation)); if (!c) return { ok: false, error: "no city" };
    if (specialist) return PO(PlayerOperationTypes.ASSIGN_WORKER, { Location: GameplayMap.getIndexFromXY(x, y), Amount: 1 });
    const a = { X: x, Y: y };
    const r = Game.CityCommands.canStart(c.id, CityCommandTypes.EXPAND, a, false);
    if (!r.Success) return { ok: false, reasons: (r.FailureReasons || []).map(T) };
    Game.CityCommands.sendRequest(c.id, CityCommandTypes.EXPAND, a); return { ok: true };
  };

  // ---------- tech / civics ----------
  const nodeUnlocks = (nt) => {
    const nd = lk("ProgressionTreeNodes", nt); if (!nd) return [];
    const depth = safe(() => Game.ProgressionTrees.getNode(me(), nt).depthUnlocked, 0);
    return Array.from(GameInfo.ProgressionTreeNodeUnlocks).filter(u => u.ProgressionTreeNodeType == nd.ProgressionTreeNodeType && !u.Hidden && u.UnlockDepth == depth + 1).map(u => {
      const tb = u.TargetKind == "KIND_UNIT" ? "Units" : u.TargetKind == "KIND_CONSTRUCTIBLE" ? "Constructibles" : u.TargetKind == "KIND_TRADITION" ? "Traditions" : u.TargetKind == "KIND_PROJECT" ? "Projects" : u.TargetKind == "KIND_DIPLOMATIC_ACTION" ? "DiplomacyActions" : null;
      const nm = tb ? safe(() => T(lk(tb, u.TargetType).Name)) : null;
      return nm || null;
    }).filter(Boolean);
  };
  const treeOptions = (isTech) => {
    const p = P(); const src = isTech ? p.Techs : p.Culture;
    return (safe(() => src.getAllAvailableNodeTypes(), []) || []).map(nt => {
      const nd = lk("ProgressionTreeNodes", nt);
      const depth = safe(() => Game.ProgressionTrees.getNode(me(), nt).depthUnlocked, 0);
      return { key: nd ? nd.ProgressionTreeNodeType : String(nt), name: T(nd && nd.Name) + (depth > 0 ? ` (mastery ${depth + 1})` : ""), turns: safe(() => src.getTurnsForNode(nt)), unlocks: nodeUnlocks(nt).slice(0, 8) };
    });
  };
  CB.techOptions = () => treeOptions(true);
  CB.civicOptions = () => treeOptions(false);
  const setNode = (isTech, what) => {
    const m = pick(treeOptions(isTech), what); if (!m) return { ok: false, error: `'${what}' not available`, options: treeOptions(isTech).map(o => o.name) };
    const nt = lk("ProgressionTreeNodes", m.key).$hash;
    const r = PO(isTech ? PlayerOperationTypes.SET_TECH_TREE_NODE : PlayerOperationTypes.SET_CULTURE_TREE_NODE, { ProgressionTreeNodeType: nt });
    r.chosen = m.name; return r;
  };
  CB.setTech = (w) => setNode(true, w);
  CB.setCivic = (w) => setNode(false, w);

  // ---------- government / policies ----------
  CB.governmentOptions = () => Array.from(GameInfo.StartingGovernments || []).map(g => { const d = lk("Governments", g.GovernmentType); return d ? { key: d.GovernmentType, name: T(d.Name), celebrationChoices: Array.from(GameInfo.GoldenAges).filter(a => a.GoldenAgeType.startsWith("GOLDEN_AGE_" + d.GovernmentType.replace("GOVERNMENT_", "") + "_")).map(a => T(a.Description).slice(0, 160)) } : null; }).filter(Boolean).filter((v, i, a) => a.findIndex(x => x.key == v.key) == i);
  CB.setGovernment = (w) => { const m = pick(CB.governmentOptions(), w); if (!m) return { ok: false, error: "unknown government" }; return PO(PlayerOperationTypes.CHANGE_GOVERNMENT, { GovernmentType: lk("Governments", m.key).$index, Action: PlayerOperationParameters.Activate }); };

  const slotName = { POLICY_CULTURE_SLOT: "policy", TRADITION_CULTURE_SLOT: "tradition", CRISIS_CULTURE_SLOT: "crisis" };
  CB.policies = () => {
    const cu = P().Culture; const out = { slots: {}, active: [], available: [] };
    for (const s in slotName) {
      const st = CultureSlotTypes[s]; if (st == null) continue;
      out.slots[slotName[s]] = safe(() => cu.getNumCultureSlots(st), 0);
      for (const h of safe(() => cu.getActiveTraditions(st), []) || []) { const d = lk("Traditions", h); if (d) out.active.push({ key: d.TraditionType, name: T(d.Name), slot: slotName[s] }); }
    }
    const seen = new Set(out.active.map(a => a.key));
    for (const s in slotName) {
      const st = CultureSlotTypes[s]; if (st == null) continue;
      for (const h of safe(() => cu.getUnlockedTraditions(st), []) || []) { const d = lk("Traditions", h); if (d && !seen.has(d.TraditionType)) { seen.add(d.TraditionType); out.available.push({ key: d.TraditionType, name: T(d.Name), kind: slotName[d.CultureSlotType] || d.CultureSlotType, desc: T(d.Description).slice(0, 220) }); } }
    }
    return out;
  };
  CB.setPolicy = (w, on = true) => {
    const pol = CB.policies(); const list = on ? pol.available : pol.active;
    const m = pick(list, w); if (!m) return { ok: false, error: `'${w}' not in ${on ? "available" : "active"} list` };
    const r = PO(PlayerOperationTypes.CHANGE_TRADITION, { TraditionType: lk("Traditions", m.key).$index, Action: on ? PlayerOperationParameters.Activate : PlayerOperationParameters.Deactivate });
    r.policy = m.name; return r;
  };
  CB.policiesDone = () => { safe(() => Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.CONSIDER_ASSIGN_TRADITIONS, {})); return { ok: true }; };

  CB.celebrationOptions = () => (safe(() => P().Culture.getGoldenAgeChoices(), []) || []).map(h => { const d = lk("GoldenAges", h); return d ? { key: d.GoldenAgeType, name: T(d.Name), desc: T(d.Description).slice(0, 200) } : null; }).filter(Boolean);
  CB.chooseCelebration = (w) => { const m = pick(CB.celebrationOptions(), w); if (!m) return { ok: false, error: "unknown" }; return PO(PlayerOperationTypes.CHOOSE_GOLDEN_AGE, { GoldenAgeType: Database.makeHash(m.key) }); };

  // ---------- religion ----------
  CB.pantheonOptions = () => Array.from(GameInfo.Beliefs).filter(b => b.BeliefClassType == "BELIEF_CLASS_PANTHEON" && safe(() => Game.PlayerOperations.canStart(me(), PlayerOperationTypes.FOUND_PANTHEON, { BeliefType: b.$hash }, false).Success)).map(b => ({ key: b.BeliefType, name: T(b.Name), desc: T(b.Description).slice(0, 200) }));
  CB.choosePantheon = (w) => { const m = pick(CB.pantheonOptions(), w); if (!m) return { ok: false, error: "unknown pantheon" }; return PO(PlayerOperationTypes.FOUND_PANTHEON, { BeliefType: Database.makeHash(m.key) }); };
  CB.religionOptions = () => ({ canFound: safe(() => P().Religion.canCreateReligion()), religions: Array.from(GameInfo.Religions).filter(r => !safe(() => Game.Religion.hasBeenFounded(r.ReligionType))).map(r => ({ key: r.ReligionType, name: T(r.Name) })).slice(0, 12) });
  CB.foundReligion = (w) => { const m = pick(CB.religionOptions().religions, w || "") || CB.religionOptions().religions[0]; if (!m) return { ok: false }; return PO(PlayerOperationTypes.FOUND_RELIGION, { ReligionType: Database.makeHash(m.key) }); };
  CB.beliefOptions = () => Array.from(GameInfo.Beliefs).filter(b => b.BeliefClassType != "BELIEF_CLASS_PANTHEON" && safe(() => Game.Religion.isBeliefClaimable(b.BeliefType)) && safe(() => Game.Religion.canHaveBelief(me(), b.$index))).map(b => ({ key: b.BeliefType, name: T(b.Name), cls: b.BeliefClassType.replace("BELIEF_CLASS_", ""), desc: T(b.Description).slice(0, 180) }));
  CB.addBelief = (w) => { const m = pick(CB.beliefOptions(), w); if (!m) return { ok: false, error: "unknown belief" }; return PO(PlayerOperationTypes.ADD_BELIEF, { BeliefType: Database.makeHash(m.key) }); };

  // ---------- attributes ----------
  CB.attributeOptions = () => {
    const out = []; const id = P().Identity;
    for (const at of GameInfo.Attributes) {
      if (!at.ProgressionTreeType || !lk("ProgressionTrees", at.ProgressionTreeType)) continue;
      const pts = safe(() => id.getAvailableAttributePoints(at.AttributeType), 0);
      for (const n of safe(() => Game.ProgressionTrees.getTreeStructure(at.ProgressionTreeType), []) || []) {
        if (safe(() => Game.ProgressionTrees.getNodeState(me(), n.nodeType)) != ProgressionTreeNodeState.NODE_STATE_OPEN) continue;
        const nd = lk("ProgressionTreeNodes", n.nodeType);
        out.push({ key: nd.ProgressionTreeNodeType, tree: at.AttributeType.replace("ATTRIBUTE_", ""), treePoints: pts, name: T(nd.Name), desc: T(nd.Description || "").slice(0, 160) });
      }
    }
    return { wildcard: safe(() => id.getWildcardPoints(), 0), nodes: out };
  };
  CB.buyAttribute = (w) => { const m = pick(CB.attributeOptions().nodes, w); if (!m) return { ok: false, error: "unknown node" }; const r = PO(PlayerOperationTypes.BUY_ATTRIBUTE_TREE_NODE, { ProgressionTreeNodeType: lk("ProgressionTreeNodes", m.key).$hash }); safe(() => Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.CONSIDER_ASSIGN_ATTRIBUTE, {})); return r; };

  // ---------- narrative events ----------
  CB.narrative = () => {
    const st = P().Stories; if (!st) return null;
    const valid = (x) => x && x.id != null && x.id != -1;
    let sid = safe(() => st.getFirstPendingDiscoveryLastMetID());
    if (!valid(sid)) sid = safe(() => st.getFirstPendingMetId());
    if (!valid(sid)) return null;
    const story = safe(() => st.find(sid)); if (!story) return null;
    const def = lk("NarrativeStories", story.type); if (!def) return null;
    let links = def.VariableLinks ? safe(() => st.getOrderedLinks(sid), []) : Array.from(GameInfo.NarrativeStory_Links).filter(l => l.FromNarrativeStoryType == def.NarrativeStoryType).map(l => l.ToNarrativeStoryType);
    const opts = [];
    for (const l of links || []) {
      const ld = lk("NarrativeStories", l); if (!ld) continue;
      const okAct = ld.Activation == "LINKED" || (["LINKED_REQUISITE", "LINKED_COMMON", "LINKED_SUBJECT_REQUISITE"].includes(ld.Activation) && safe(() => st.determineRequisiteLink(ld.NarrativeStoryType, sid)));
      if (!okAct) continue;
      const afford = !ld.Cost || safe(() => st.canAfford(ld.NarrativeStoryType), true);
      opts.push({ key: ld.NarrativeStoryType, text: T(safe(() => st.determineNarrativeInjection(sid, ld.$hash, StoryTextTypes.OPTION), "")), reward: T(safe(() => st.determineNarrativeInjection(sid, ld.$hash, StoryTextTypes.REWARD), "")), affordable: afford });
    }
    return { storyId: cid(sid), title: T(safe(() => st.determineNarrativeInjection(sid, def.$hash, StoryTextTypes.IMPERATIVE), "") || def.Name), body: T(safe(() => st.determineNarrativeInjection(sid, def.$hash, StoryTextTypes.BODY), "")).slice(0, 400), options: opts };
  };
  CB.chooseNarrative = (key) => {
    const n = CB.narrative(); if (!n) return { ok: false, error: "no pending narrative" };
    let k = "CLOSE";
    if (n.options.length) { const m = typeof key === "number" ? n.options[key] : pick(n.options.map(o => ({ key: o.key, name: o.text })), key || ""); k = (m || n.options.find(o => o.affordable) || n.options[0]).key; }
    Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.CHOOSE_NARRATIVE_STORY_DIRECTION, { TargetType: k, Target: parseCid(n.storyId), Action: PlayerOperationParameters.Activate });
    return { ok: true, chose: k };
  };

  // ---------- misc pending ----------
  CB.cityStateBonusOptions = () => { const cs = safe(() => Game.CityStates.getCityStateBonusToSelect(me())); if (cs == null || cs < 0) return null; return { cityState: cs, name: CB.playerName(cs), options: Array.from(GameInfo.CityStateBonuses).filter(b => safe(() => Game.CityStates.canHaveBonus(me(), cs, b.CityStateBonusType))).map(b => ({ key: b.CityStateBonusType, name: T(b.Name), desc: T(b.Description).slice(0, 180) })) }; };
  CB.chooseCityStateBonus = (w) => { const o = CB.cityStateBonusOptions(); if (!o) return { ok: false }; const m = pick(o.options, w || "") || o.options[0]; return PO(PlayerOperationTypes.CHOOSE_CITY_STATE_BONUS, { OtherPlayer: o.cityState, CityStateBonusType: Database.makeHash(m.key) }); };

  CB.promotionOptions = (uidS) => {
    const uid = parseCid(uidS); const out = [];
    for (const d of GameInfo.UnitPromotionDisciplineDetails) {
      const args = { PromotionType: Database.makeHash(d.UnitPromotionType), PromotionDisciplineType: Database.makeHash(d.UnitPromotionDisciplineType) };
      if (safe(() => Game.UnitCommands.canStart(uid, UnitCommandTypes.PROMOTE, args, false).Success)) { const pd = lk("UnitPromotions", d.UnitPromotionType); out.push({ key: d.UnitPromotionType, discipline: d.UnitPromotionDisciplineType, name: T(pd && pd.Name), desc: T(pd && pd.Description).slice(0, 160) }); }
    }
    return out;
  };
  CB.promote = (uidS, w) => {
    // commendations pass canStart but a request for one can be silently ignored, leaving an army commander
    // blocking the turn with a promotion still pending, so by default take a regular promotion first
    const opts = CB.promotionOptions(uidS).sort((a, b) => /COMMENDATION/.test(a.key) - /COMMENDATION/.test(b.key));
    const m = pick(opts, w || "") || opts[0]; if (!m) return { ok: false, error: "no promotions available" };
    const args = { PromotionType: Database.makeHash(m.key), PromotionDisciplineType: Database.makeHash(m.discipline) };
    Game.UnitCommands.sendRequest(parseCid(uidS), UnitCommandTypes.PROMOTE, args); return { ok: true, promotion: m.name };
  };

  CB.autoAssignResources = () => {
    const p = P(); let n = 0;
    const cities = (p.Cities.getCities() || []).filter(c => !c.isTown).concat((p.Cities.getCities() || []).filter(c => c.isTown));
    for (const res of safe(() => p.Resources.getResources(), []) || []) {
      if (res.isAssigned || safe(() => res.assignedCity != null && res.assignedCity.id != -1)) continue;
      for (const c of cities) {
        if (safe(() => c.Resources.getAssignedResources().length >= c.Resources.getAssignedResourcesCap(), true)) continue;
        const args = { Location: res.isOffMap ? { x: -1, y: -1 } : GameplayMap.getLocationFromIndex(res.value), City: c.id.id, Flags: !!res.isOffMap, ID: res.isOffMap ? res.value : -1 };
        if (safe(() => Game.PlayerOperations.canStart(me(), PlayerOperationTypes.ASSIGN_RESOURCE, args, false).Success)) { Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.ASSIGN_RESOURCE, args); n++; break; }
      }
    }
    safe(() => Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.CONSIDER_ASSIGN_RESOURCE, {}));
    return { ok: true, assigned: n };
  };

  CB.razeDecision = (cityS, decision = "KEEP") => {
    const c = cityById(cityS) || (P().Cities.getCities() || []).find(c => c.isJustConqueredFrom); if (!c) return { ok: false };
    const d = { LIBERATE_FOUNDER: 0, LIBERATE_PREVIOUS_OWNER: 1, KEEP: 2, RAZE: 3 }[String(decision).toUpperCase()] ?? 2;
    Game.CityCommands.sendRequest(c.id, CityCommandTypes.DESTROY, { Directive: d }); return { ok: true };
  };

  // ---------- age transition ----------
  CB.nextCivOptions = () => {
    const prm = safe(() => GameSetup.findPlayerParameter(me(), "AgeTransitionPlayerCivilization")); if (!prm) return null;
    return (prm.domain.possibleValues || []).filter(v => v.invalidReason == GameSetupDomainValueInvalidReason.Valid && v.value != "RANDOM").map(v => { const d = lk("Civilizations", v.value); return { key: v.value, name: d ? T(d.Name) : v.value, desc: d ? T(d.Description || "").slice(0, 160) : "" }; });
  };
  CB.chooseNextCiv = (w) => {
    const o = CB.nextCivOptions() || []; const m = pick(o, w || "") || o[0]; if (!m) return { ok: false, error: "no options" };
    GameSetup.setPlayerParameterValue(me(), "AgeTransitionPlayerCivilization", m.key);
    const r = PO(PlayerOperationTypes.SET_AGE_TRANSITION_DATA, { Finished: true }); r.civ = m.key; return r;
  };
  CB.legacyCards = () => { const as = P().AdvancedStart; if (!as) return null; return { points: safe(() => as.getLegacyPoints()), cards: (safe(() => as.getAvailableCards(), []) || []).map(c => ({ id: c.id, name: T(c.name), desc: T(c.description).slice(0, 150), cost: c.cost, canAdd: safe(() => Game.PlayerOperations.canStart(me(), PlayerOperationTypes.ADVANCED_START_MODIFY_DECK, { Type: "ADD", ID: c.id }, false).Success) })) }; };
  CB.legacyAdd = (id) => PO(PlayerOperationTypes.ADVANCED_START_MODIFY_DECK, { Type: "ADD", ID: id });
  CB.legacyFinish = () => {
    const as = P().AdvancedStart; let used = 0;
    for (const c of safe(() => as.getCards(), []) || []) for (const e of c.effects || []) { if (e.isPlacementEffect) continue; for (let i = 0; i < (e.amount || 1); i++) { if (safe(() => Game.PlayerOperations.canStart(me(), PlayerOperationTypes.ADVANCED_START_USE_EFFECT, { ID: e.id }, false).Success)) { Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.ADVANCED_START_USE_EFFECT, { ID: e.id }); used++; } } }
    const r = PO(PlayerOperationTypes.ADVANCED_START_MARK_COMPLETED, {}); r.effectsUsed = used; return r;
  };

  // ---------- diplomacy ----------
  CB.players = () => {
    const dip = P().Diplomacy; const out = [];
    for (const p of Players.getAlive()) {
      if (p.id == me() || p.isBarbarian) continue;
      if (!safe(() => dip.hasMet(p.id))) continue;
      const o = { id: p.id, name: CB.playerName(p.id), kind: p.isMajor ? "major" : p.isMinor ? "city-state" : p.isIndependent ? "independent" : "other" };
      if (safe(() => dip.isAtWarWith(p.id))) o.war = true;
      if (safe(() => dip.hasAllied(p.id))) o.allied = true;
      if (p.isMajor) {
        o.relationship = safe(() => Object.keys(DiplomacyPlayerRelationships).find(k => DiplomacyPlayerRelationships[k] == dip.getRelationshipEnum(p.id)).replace("PLAYER_RELATIONSHIP_", ""));
        o.score = safe(() => p.Stats ? undefined : undefined);
        o.cities = safe(() => p.Cities.getCities().length);
        o.military = safe(() => Math.round(p.Stats.getMilitaryStrength ? p.Stats.getMilitaryStrength() : 0));
      }
      if (p.isMinor) { const s = safe(() => p.Influence.getSuzerain()); if (s != null && s >= 0) o.suzerain = s == me() ? "me" : CB.playerName(s); }
      out.push(o);
    }
    out.push({ myMilitary: safe(() => Math.round(P().Stats.getMilitaryStrength())) });
    return out;
  };

  CB.diploPending = () => {
    const out = { statements: CB.pendingStatements.slice(-10), responses: [] };
    const N = Game.Notifications;
    for (const id of N.getIdsForPlayer(me()) || []) {
      const tn = safe(() => N.getTypeName(N.find(id).Type), "");
      if (tn == "NOTIFICATION_DIPLOMATIC_RESPONSE_REQUIRED") {
        const n = N.find(id); const rd = safe(() => Game.Diplomacy.getResponseDataForUI(n.Target.id)); if (!rd) continue;
        out.responses.push({ actionId: rd.actionID, from: CB.playerName(rd.initialPlayer), title: T(rd.titleString), request: T(rd.requestString), desc: T(rd.descriptionString).slice(0, 250), options: (rd.responseList || []).map(r => ({ type: r.responseType, name: T(r.responseName), desc: T(r.responseDescription).slice(0, 150), cost: r.cost })) });
      }
    }
    // an ally at war asking us to join (answer_call_to_arms)
    const cta = safe(() => CB.callToArms(), []); if (cta && cta.length) out.callToArms = cta;
    // incoming deals
    for (const s of out.statements) {
      if (s.dealAction != null && s.dealAction != -1 && s.dealAction != DiplomacyDealProposalActions.ACCEPTED && s.dealAction != DiplomacyDealProposalActions.REJECTED) {
        const deal = { direction: DiplomacyDealDirection.OUTGOING, player1: me(), player2: s.from };
        const wd = safe(() => Game.DiplomacyDeals.getWorkingDeal(deal));
        if (wd) s.dealItems = (wd.itemIds || []).map(i => { const it = safe(() => Game.DiplomacyDeals.getWorkingDealItem(deal, i)); return it ? JSON.stringify(it).slice(0, 200) : null; });
      }
      s.fromName = CB.playerName(s.from);
    }
    return out;
  };
  CB.respondAction = (actionId, choice) => {
    const rd = safe(() => Game.Diplomacy.getResponseDataForUI(actionId)); if (!rd) return { ok: false, error: "no such action" };
    const opts = rd.responseList || []; const m = typeof choice === "number" && choice < 10 ? opts[choice] : opts.find(o => norm(T(o.responseName)).includes(norm(choice))) || opts.find(o => o.responseType == choice);
    if (!m) return { ok: false, error: "unknown response", options: opts.map(o => T(o.responseName)) };
    return PO(PlayerOperationTypes.RESPOND_DIPLOMATIC_ACTION, { ID: rd.actionID, Type: m.responseType });
  };
  CB.respondFirstMeet = (sessionId, other, attitude = "FRIENDLY") => {
    const t = DiplomacyPlayerFirstMeets["PLAYER_REALATIONSHIP_FIRSTMEET_" + String(attitude).toUpperCase()] ?? DiplomacyPlayerFirstMeets.PLAYER_REALATIONSHIP_FIRSTMEET_NEUTRAL;
    let r = PO(PlayerOperationTypes.RESPOND_DIPLOMATIC_FIRST_MEET, { Player1: me(), Player2: other, Type: t });
    if (!r.ok && t != DiplomacyPlayerFirstMeets.PLAYER_REALATIONSHIP_FIRSTMEET_NEUTRAL) { r = PO(PlayerOperationTypes.RESPOND_DIPLOMATIC_FIRST_MEET, { Player1: me(), Player2: other, Type: DiplomacyPlayerFirstMeets.PLAYER_REALATIONSHIP_FIRSTMEET_NEUTRAL }); r.note = "fell back to NEUTRAL"; }
    if (sessionId != null && sessionId >= 0) safe(() => Game.DiplomacySessions.closeSession(sessionId));
    for (const s of CB.pendingStatements.filter(s => s.from == other)) safe(() => Game.DiplomacySessions.closeSession(s.sessionId));
    CB.pendingStatements = CB.pendingStatements.filter(s => s.sessionId != sessionId && s.from != other);
    safe(() => { const N = Game.Notifications; for (const id of N.getIdsForPlayer(me()) || []) { const n = N.find(id); if (N.getTypeName(n.Type) == "NOTIFICATION_PLAYER_MET" && (n.Player == other || n.Player2 == other)) N.dismiss(id); } });
    return r;
  };
  CB.closeSession = (sessionId) => { safe(() => Game.DiplomacySessions.closeSession(sessionId)); CB.pendingStatements = CB.pendingStatements.filter(s => s.sessionId != sessionId); safe(() => InterfaceMode.switchToDefault()); return { ok: true }; };
  CB.answerDeal = (other, accept) => {
    const deal = { direction: DiplomacyDealDirection.OUTGOING, player1: me(), player2: other };
    safe(() => Game.DiplomacyDeals.sendWorkingDeal(deal, accept ? DiplomacyDealProposalActions.ACCEPTED : DiplomacyDealProposalActions.REJECTED));
    for (const s of CB.pendingStatements.filter(s => s.from == other)) safe(() => Game.DiplomacySessions.closeSession(s.sessionId));
    CB.pendingStatements = CB.pendingStatements.filter(s => s.from != other);
    return { ok: true };
  };
  CB.diploActions = (target) => {
    const list = safe(() => Game.Diplomacy.getProjectDataForUI(me(), target == null ? -1 : target, DiplomacyActionTargetTypes.NO_DIPLOMACY_TARGET, DiplomacyActionGroups.NO_DIPLOMACY_ACTION_GROUP, -1, DiplomacyActionTargetTypes.NO_DIPLOMACY_TARGET), []) || [];
    return list.filter(a => a.projectStatus == DiplomacyProjectStatus.PROJECT_AVAILABLE || (a.projectStatus == DiplomacyProjectStatus.PROJECT_NO_VIABLE_TARGETS && (a.targetList1 || []).length)).map(a => ({ key: a.actionTypeName, name: T(a.actionDisplayName || a.actionTypeName), desc: T(a.projectDescription).slice(0, 160), cost: safe(() => (a.targetList1.find(t => t.targetID == target) || a.targetList1[0]).costYieldD), needsSecondTarget: (a.targetList2 || []).length > 0, second: (a.targetList2 || []).slice(0, 6).map(t => ({ param: t.parameterName, id: t.targetID, name: T(t.targetName) })) }));
  };
  CB.startDiploAction = (target, actionKey, secondParam, secondId) => {
    const list = safe(() => Game.Diplomacy.getProjectDataForUI(me(), target, DiplomacyActionTargetTypes.NO_DIPLOMACY_TARGET, DiplomacyActionGroups.NO_DIPLOMACY_ACTION_GROUP, -1, DiplomacyActionTargetTypes.NO_DIPLOMACY_TARGET), []) || [];
    const a = list.find(x => norm(x.actionTypeName) == norm(actionKey)) || list.find(x => norm(x.actionTypeName).includes(norm(actionKey)) || norm(T(x.actionDisplayName)).includes(norm(actionKey)));
    if (!a) return { ok: false, error: "action not available", available: CB.diploActions(target).map(x => x.key) };
    const tp = Players.get(target);
    const args = { Amount: 1, Player1: me(), Type: a.actionType };
    if (tp && tp.isMajor) args.Player2 = target; else args.ID = target;
    if (!args.Player2) args.Player2 = target;
    if (secondParam != null) { const f = { Player3: "Player3", Amount2: "Amount2", Unit: "Unit", City: "City" }[secondParam] || "ID2"; args[f] = secondId; }
    const r = safe(() => Game.PlayerOperations.canStart(me(), a.operationType, args, false));
    if (!r || !r.Success) return { ok: false, reasons: safe(() => (r.FailureReasons || []).map(T), []) };
    Game.PlayerOperations.sendRequest(me(), a.operationType, args); return { ok: true, started: a.actionTypeName };
  };
  CB.supportAction = (actionId, forInitiator = true) => PO(PlayerOperationTypes.SUPPORT_DIPLOMATIC_ACTION, { ID: actionId, Type: DiplomacyTokenTypes.DIPLOMACY_TOKEN_GLOBAL, Amount: 1, SubType: !!forInitiator });
  CB.ongoingActions = () => (safe(() => Game.Diplomacy.getPlayerEvents(me()), []) || []).map(e => ({ id: e.uniqueID, type: e.actionTypeName, from: CB.playerName(e.initialPlayer), target: CB.playerName(e.targetPlayer), progress: `${e.progressScore}/${e.completionScore}` })).slice(0, 20);
  CB.declareWar = (target, formal = true) => {
    const dip = P().Diplomacy;
    const useFormal = formal && safe(() => dip.canDeclareWarOn(target, WarTypes.FORMAL_WAR).Success);
    return Object.assign(PO(PlayerOperationTypes.DECLARE_WAR, { Player1: me(), Player2: target, Type: useFormal ? DiplomacyActionTypes.DIPLOMACY_ACTION_DECLARE_FORMAL_WAR : DiplomacyActionTypes.DIPLOMACY_ACTION_DECLARE_WAR }), { formal: !!useFormal });
  };
  CB.proposePeace = (target) => {
    const dip = P().Diplomacy; const c = safe(() => dip.canMakePeaceWith(target));
    if (!c || !c.Success) return { ok: false, reasons: safe(() => (c.FailureReasons || []).map(T), []) };
    const deal = { direction: DiplomacyDealDirection.OUTGOING, player1: me(), player2: target };
    Game.DiplomacyDeals.clearWorkingDeal(deal);
    Game.DiplomacyDeals.addItemToWorkingDeal(deal, { type: DiplomacyDealItemTypes.AGREEMENTS, agreementType: DiplomacyDealItemAgreementTypes.MAKE_PEACE });
    Game.DiplomacyDeals.sendWorkingDeal(deal, DiplomacyDealProposalActions.PROPOSED); return { ok: true, note: "peace proposed; AI answers via a statement" };
  };
  CB.formAlliance = (target) => PO(PlayerOperationTypes.FORM_ALLIANCE, { Player1: me(), Player2: target, Type: DiplomacyActionTypes.DIPLOMACY_ACTION_FORM_ALLIANCE });

  // ---------- consolidated pending choices ----------
  CB.pending = () => {
    const out = [];
    const N = Game.Notifications;
    const seen = new Set();
    for (const id of N.getIdsForPlayer(me()) || []) {
      if (!safe(() => N.getBlocksTurnAdvancement(id))) continue;
      const n = safe(() => N.find(id)); const tn = safe(() => N.getTypeName(n.Type), "?").replace("NOTIFICATION_", "");
      if (seen.has(tn) && tn != "CHOOSE_CITY_PRODUCTION" && tn != "CHOOSE_TOWN_PROJECT") continue; seen.add(tn);
      const e = { type: tn, notification: cid(id) };
      if (n && n.Target && (tn == "CHOOSE_CITY_PRODUCTION" || tn == "CHOOSE_TOWN_PROJECT")) { e.city = cid(n.Target); e.cityName = safe(() => T(Cities.get(n.Target).name)); }
      if (n && n.Location && n.Location.x >= 0) e.at = xy(n.Location);
      if (tn == "TRADITIONS_AVAILABLE") { const pol = safe(() => CB.policies()); if (pol) { e.slots = pol.slots; e.active = pol.active.map(a => a.name); e.newCards = pol.available.map(a => a.name); e.howToResolve = "set_policies(activate=[...], deactivate=[...]) to swap, or set_policies() with empty lists to keep current cards"; } }
      if (tn == "PLAYER_MET" && n) {
        const other = n.Player == me() ? n.Player2 : n.Player;
        e.player = other; e.name = CB.playerName(other);
        e.howToResolve = `respond_first_meet(session_id=-1, player_id=${other}, attitude=FRIENDLY|NEUTRAL|UNFRIENDLY)`;
        e.greetingCosts = safe(() => { const c = (t) => Game.Diplomacy.getFirstMeetResponseCostAndRelDelta(DiplomacyPlayerFirstMeets["PLAYER_REALATIONSHIP_FIRSTMEET_" + t], me()); return { FRIENDLY: c("FRIENDLY"), NEUTRAL: c("NEUTRAL"), UNFRIENDLY: c("UNFRIENDLY"), note: "[influence cost, relationship change]" }; });
        seen.delete("PLAYER_MET");
      }
      out.push(e);
    }
    if (!seen.has("CHOOSE_NARRATIVE_STORY_DIRECTION") && safe(() => CB.narrative())) out.push({ type: "NARRATIVE_EVENT (non-blocking, use choice_options narrative)" });
    return { blocking: CB.blockingType(), items: out, readyUnits: CB.readyUnits().length, diplomacy: CB.pendingStatements.length };
  };

  CB.dismissAdvisor = () => {
    const N = Game.Notifications; let k = 0;
    for (const id of N.getIdsForPlayer(me()) || []) { const tn = safe(() => N.getTypeName(N.find(id).Type), ""); if (tn.startsWith("NOTIFICATION_ADVISOR_WARNING") || tn == "NOTIFICATION_VIEW_ADVISOR_WARNING") { safe(() => Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.VIEWED_ADVISOR_WARNING, { Target: id })); k++; } }
    const t = Game.Notifications.getEndTurnBlockingType(me());
    if (t == EndTurnBlockingTypes.VIEW_ADVISOR_WARNING) { const nid = N.findEndTurnBlocking(me(), t); if (nid) { safe(() => Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.VIEWED_ADVISOR_WARNING, { Target: nid })); k++; } }
    return { ok: true, dismissed: k };
  };

  CB.gameStatus = () => {
    const vm = Game.VictoryManager; const out = {};
    out.victories = (safe(() => vm.getVictories(), []) || []).map(v => ({ team: v.team, victory: safe(() => lk("Victories", v.victory).VictoryType), place: v.place }));
    out.defeated = safe(() => vm.getLatestPlayerDefeat(me()) != DefeatTypes.NO_DEFEAT);
    out.ageOver = safe(() => Game.AgeProgressManager.isAgeOver); out.finalAge = safe(() => Game.AgeProgressManager.isFinalAge);
    out.canTransition = safe(() => Game.AgeProgressManager.canTransitionToNextAge(me()));
    out.myTeam = P().team;
    return out;
  };

  return "CB installed v" + CB.version;
})()
