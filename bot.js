// CivBot helper library, injected into Civ VII's "App UI" JS context via the FireTuner port.
// Every public function returns plain JSON-serialisable data. Installed as globalThis.CB.
(function () {
  const CB = {};
  const INV = { X: -9999, Y: -9999, UnitAbilityType: -1 };
  const me = () => GameContext.localPlayerID;
  const P = () => Players.get(me());
  const lk = (tbl, h) => { try { return GameInfo[tbl].lookup(h); } catch (e) { return null; } };
  const loc = (s) => { try { return Locale.compose(s); } catch (e) { return s; } };
  const strip = (s) => (s || "").replace(/\[icon:(?:YIELD_)?([A-Z_]+)\]/gi, (m, a) => a.toLowerCase().replace(/_/g, " ")).replace(/\[[^\]]*\]/g, "").replace(/\s+/g, " ").trim();
  const cid = (c) => c ? `${c.owner};${c.id};${c.type}` : null;
  const parseCid = (s) => { if (typeof s === "object") return s; const [o, i, t] = String(s).split(";").map(Number); return { owner: o, id: i, type: t }; };
  const xy = (l) => l ? [l.x, l.y] : null;
  const safe = (f, d = null) => { try { const v = f(); return v === undefined ? d : v; } catch (e) { return d; } };
  CB.version = 1;
  CB.log = [];
  CB.pendingStatements = [];

  // ---------- engine event capture ----------
  if (!globalThis.__cbListeners) {
    globalThis.__cbListeners = true;
    const push = (kind, d) => { const l = globalThis.CB && globalThis.CB.log; if (l) { l.push({ turn: Game.turn, kind, d }); if (l.length > 200) l.shift(); } };
    engine.on("DiplomacyStatement", (data) => {
      try {
        const v = data.values || {};
        if (v.ToPlayer != GameContext.localPlayerID) return;
        const stmt = safe(() => Game.DiplomacySessions.getKeyNameOrNumber(v.StatementType));
        const rec = { sessionId: data.sessionId, from: v.FromPlayer, stmt, dealAction: v.DealAction, respondingTo: v.RespondingToDealAction, turn: Game.turn };
        if (globalThis.CB) globalThis.CB.pendingStatements.push(rec);
        push("DiplomacyStatement", rec);
      } catch (e) { }
    });
    engine.on("DiplomacySessionClosed", (d) => { if (globalThis.CB) globalThis.CB.pendingStatements = globalThis.CB.pendingStatements.filter(s => s.sessionId != d.sessionId); });
    engine.on("DiplomacyDeclareWar", (d) => push("DeclareWar", { acting: d.actingPlayer, reacting: d.reactingPlayer }));
    engine.on("DiplomacyMakePeace", (d) => push("MakePeace", { acting: d.actingPlayer, reacting: d.reactingPlayer }));
    engine.on("UnitKilledInCombat", (d) => push("UnitKilled", d));
    engine.on("CityProductionCompleted", (d) => { if (d.cityID && d.cityID.owner == GameContext.localPlayerID) push("ProductionDone", { city: safe(() => strip(loc(Cities.get(d.cityID).name))) }); });
    engine.on("GameAgeEnded", (d) => push("GameAgeEnded", d));
    engine.on("TeamVictory", (d) => push("TeamVictory", d));
    engine.on("PlayerDefeat", (d) => push("PlayerDefeat", d));
  }

  // ---------- generic helpers ----------
  CB.playerName = (pid) => {
    const p = Players.get(pid); if (!p) return `player${pid}`;
    const civ = safe(() => strip(loc(lk("Civilizations", p.civilizationType).Name)), "");
    const ldr = safe(() => strip(loc(lk("Leaders", p.leaderType).Name)), "");
    if (p.isMajor) return `${ldr} (${civ})`;
    return strip(loc(p.name)) || civ || `player${pid}`;
  };

  CB.plotInfo = (x, y) => {
    const t = safe(() => lk("Terrains", GameplayMap.getTerrainType(x, y)).TerrainType.replace("TERRAIN_", ""));
    const b = safe(() => lk("Biomes", GameplayMap.getBiomeType(x, y)).BiomeType.replace("BIOME_", ""));
    const f = safe(() => { const ft = GameplayMap.getFeatureType(x, y); return ft >= 0 ? lk("Features", ft).FeatureType.replace("FEATURE_", "") : null; });
    const r = safe(() => { const rt = GameplayMap.getResourceType(x, y); return rt >= 0 ? lk("Resources", rt).ResourceType.replace("RESOURCE_", "") : null; });
    const owner = safe(() => GameplayMap.getOwner(x, y));
    const o = { x, y, terrain: t, biome: b };
    if (f) o.feature = f; if (r) o.resource = r;
    if (safe(() => GameplayMap.isRiver(x, y))) o.river = true;
    if (owner != null && owner >= 0) o.owner = owner;
    const units = safe(() => MapUnits.getUnits(x, y), []);
    if (units && units.length) o.units = units.map(u => { const uu = Units.get(u); return uu ? `${uu.owner == me() ? "own" : "p" + uu.owner}:${safe(() => lk("Units", uu.type).UnitType.replace("UNIT_", ""))}` : "?"; });
    return o;
  };

  // Compact ASCII-ish map: one token per revealed tile
  CB.mapAround = (cx, cy, r = 5) => {
    const rows = [];
    const rev = safe(() => GameplayMap.getRevealedStates(me()));
    const W = GameplayMap.getGridWidth(), H = GameplayMap.getGridHeight();
    for (let y = cy - r; y <= cy + r; y++) {
      if (y < 0 || y >= H) continue;
      const cells = [];
      for (let x0 = cx - r; x0 <= cx + r; x0++) {
        const x = ((x0 % W) + W) % W;
        const idx = GameplayMap.getIndexFromXY(x, y);
        const rs = rev ? rev[idx] : GameplayMap.getRevealedState(me(), x, y);
        if (rs == RevealedStates.HIDDEN) { cells.push("?"); continue; }
        let c;
        if (GameplayMap.isWater(x, y)) c = safe(() => lk("Terrains", GameplayMap.getTerrainType(x, y)).TerrainType == "TERRAIN_OCEAN") ? "~" : "w";
        else if (GameplayMap.isMountain(x, y)) c = "M";
        else {
          const t = safe(() => lk("Terrains", GameplayMap.getTerrainType(x, y)).TerrainType, "");
          c = t.includes("HILL") ? "h" : "."; // flat
          const ft = GameplayMap.getFeatureType(x, y);
          if (ft >= 0) c = c === "h" ? "H" : "f";
        }
        if (GameplayMap.getResourceType(x, y) >= 0) c += "$";
        const city = safe(() => MapCities.getCity(x, y));
        if (city) { const cc = Cities.get(city); c = cc && cc.owner == me() ? "C" : "X"; }
        const us = safe(() => MapUnits.getUnits(x, y), []);
        if (us && us.length) { const u = Units.get(us[0]); if (u) c += u.owner == me() ? "u" : "e"; }
        const ow = GameplayMap.getOwner(x, y);
        if (ow >= 0 && ow != me() && !city) c += "!";
        cells.push(c);
      }
      rows.push(`y${y}: ` + cells.join(" "));
    }
    return { origin: [cx - r, cy - r], legend: ". flat, h hill, f/H forest-ish, M mountain, w shallow water, ~ ocean, ? unexplored, $ resource, C own city, X foreign city, u own unit, e other unit, ! foreign territory", rows };
  };

  // ---------- units ----------
  const COMMON_OPS = ["UNITOPERATION_FOUND_CITY", "UNITOPERATION_FORTIFY", "UNITOPERATION_SLEEP", "UNITOPERATION_SKIP_TURN",
    "UNITOPERATION_REST_UNTIL_HEALED", "UNITOPERATION_AUTOMATE_EXPLORE", "UNITOPERATION_ALERT", "UNITOPERATION_RANGE_ATTACK",
    "UNITOPERATION_EXCAVATE", "UNITOPERATION_SPREAD_RELIGION", "UNITOPERATION_PILLAGE", "UNITOPERATION_FOUND_CITY_ADJACENT_PLOT",
    "UNITOPERATION_REINFORCE_ARMY", "UNITOPERATION_MAKE_TRADE_ROUTE", "UNITOPERATION_EMBARK", "UNITOPERATION_DISEMBARK"];
  const COMMON_CMDS = ["UNITCOMMAND_UPGRADE", "UNITCOMMAND_WAKE", "UNITCOMMAND_CANCEL", "UNITCOMMAND_PACK_ARMY", "UNITCOMMAND_UNPACK_ARMY",
    "UNITCOMMAND_PROMOTE", "UNITCOMMAND_CONSTRUCT", "UNITCOMMAND_DELETE", "UNITCOMMAND_ADD_TO_ARMY", "UNITCOMMAND_REMOVE_FROM_ARMY",
    "UNITCOMMAND_COMMANDER_ATTACK", "UNITCOMMAND_ARMY_OVERRUN", "UNITCOMMAND_MAKE_TRADE_ROUTE", "UNITCOMMAND_RESETTLE", "UNITCOMMAND_DEFENSIVE_PERIMETER"];

  CB.unitActions = (uidS, full) => {
    const uid = parseCid(uidS); const out = [];
    const ops = full ? Array.from(GameInfo.UnitOperations).map(r => r.OperationType) : COMMON_OPS;
    const cmds = full ? Array.from(GameInfo.UnitCommands).map(r => r.CommandType) : COMMON_CMDS;
    for (const op of ops) {
      if (op === "UNITOPERATION_MOVE_TO") continue;
      const r = safe(() => Game.UnitOperations.canStart(uid, op, {}, false));
      const r2 = r && !r.Success ? safe(() => Game.UnitOperations.canStart(uid, op, INV, false)) : null;
      const ok = (r && r.Success) || (r2 && r2.Success);
      if (ok || (r && r.Plots && r.Plots.length)) out.push(op.replace("UNITOPERATION_", "op:") + ((r && r.Plots && r.Plots.length) ? `(targets:${r.Plots.length})` : ""));
    }
    for (const c of cmds) {
      const r = safe(() => Game.UnitCommands.canStart(uid, c, {}, false));
      const r2 = r && !r.Success ? safe(() => Game.UnitCommands.canStart(uid, c, INV, false)) : null;
      const ok = (r && r.Success) || (r2 && r2.Success);
      if (ok || (r && r.Plots && r.Plots.length)) out.push(c.replace("UNITCOMMAND_", "cmd:") + ((r && r.Plots && r.Plots.length) ? `(targets:${r.Plots.length})` : ""));
    }
    return out;
  };

  CB.unitSummary = (u, withActions) => {
    const def = lk("Units", u.type) || {};
    const o = {
      id: cid(u.id), type: (def.UnitType || "?").replace("UNIT_", ""), at: xy(u.location),
      moves: safe(() => u.Movement.movementMovesRemaining), maxMoves: safe(() => u.Movement.maxMoves),
      hp: safe(() => u.Health.maxDamage - u.Health.damage), maxHp: safe(() => u.Health.maxDamage),
    };
    const act = safe(() => u.activityType);
    if (act != null) { const n = Object.keys(UnitActivityTypes).find(k => UnitActivityTypes[k] == act); if (n && n !== "NONE") o.activity = n; }
    const qd = safe(() => Units.getQueuedOperationDestination(u.id));
    if (qd && qd.x >= 0) o.headingTo = xy(qd);
    if (u.isCommanderUnit) { o.commander = true; o.army = safe(() => { const a = Armies.get(u.armyId); return a ? a.unitCount : 0; }); }
    else if (safe(() => ComponentID.isValid(u.armyId))) o.inArmy = true;
    if (def.FoundCity) o.canFound = true;
    if (safe(() => u.Combat.canAttack) && safe(() => u.Combat.attacksRemaining) > 0) o.canAttack = true;
    if (safe(() => u.Experience.canPromote)) o.canPromote = true;
    if (u.isEmbarked) o.embarked = true;
    const str = safe(() => u.Combat.getMeleeStrength(false)); if (str) o.str = str;
    const rs = safe(() => u.Combat.rangedStrength); if (rs) o.ranged = rs;
    if (withActions) o.actions = CB.unitActions(u.id);
    return o;
  };

  CB.units = (withActions = false) => (P().Units.getUnits() || []).filter(u => u.isOnMap !== false).map(u => CB.unitSummary(u, withActions));

  CB.readyUnits = () => (P().Units.getUnits() || []).filter(u => safe(() => u.Movement.movementMovesRemaining) > 0 && (safe(() => u.activityType) == UnitActivityTypes.AWAKE || safe(() => u.activityType) == UnitActivityTypes.NONE) && !(safe(() => Units.getQueuedOperationDestination(u.id)) || { x: -1 }).x >= 0).map(u => cid(u.id));

  const resolveOp = (name) => {
    name = String(name).toUpperCase().replace(/^OP:/, "UNITOPERATION_").replace(/^CMD:/, "UNITCOMMAND_");
    if (name.startsWith("UNITOPERATION_") || name.startsWith("UNITCOMMAND_")) return name;
    if (GameInfo.UnitOperations.find(r => r.OperationType == "UNITOPERATION_" + name)) return "UNITOPERATION_" + name;
    if (GameInfo.UnitCommands.find(r => r.CommandType == "UNITCOMMAND_" + name)) return "UNITCOMMAND_" + name;
    return name;
  };

  CB.unitDo = (uidS, action, x, y, extra) => {
    const uid = parseCid(uidS); const u = Units.get(uid); if (!u) return { ok: false, error: "no such unit" };
    const name = resolveOp(action);
    if ((name === "UNITOPERATION_FORTIFY" || name === "HOLD" || name === "UNITOPERATION_HOLD") && !CB._inHold) {
      CB._inHold = true;
      try {
        const f = name === "UNITOPERATION_FORTIFY" ? CB.unitDo(uidS, "UNITOPERATION_FORTIFY", x, y, extra) : { ok: false };
        if (f.ok) return f;
        for (const alt of ["UNITOPERATION_ALERT", "UNITOPERATION_SKIP_TURN"]) { const r = CB.unitDo(uidS, alt); if (r.ok) return { ok: true, action: alt, note: "fortify unavailable here (" + (f.reasons || []).join("; ") + "); used " + alt + " instead" }; }
        return f;
      } finally { CB._inHold = false; }
    }
    const isCmd = name.startsWith("UNITCOMMAND_");
    const api = isCmd ? Game.UnitCommands : Game.UnitOperations;
    let args = (x != null && y != null && x !== -1) ? { X: x, Y: y } : Object.assign({}, INV);
    if (extra) Object.assign(args, extra);
    if (name === "UNITCOMMAND_CONSTRUCT" && !args.ConstructibleType) {
      const r0 = api.canStart(uid, name, INV, false); if (r0 && r0.BestConstructible != null) args.ConstructibleType = r0.BestConstructible;
    }
    let r = safe(() => api.canStart(uid, name, args, false));
    if ((!r || !r.Success) && x == null) { const r2 = safe(() => api.canStart(uid, name, {}, false)); if (r2 && r2.Success) { r = r2; args = {}; } }
    if (!r || !r.Success) {
      const targets = safe(() => (api.canStart(uid, name, {}, false).Plots || []).slice(0, 30).map(i => xy(GameplayMap.getLocationFromIndex(i))), []);
      return { ok: false, action: name, reasons: safe(() => (r.FailureReasons || []).map(s => strip(loc(s))), []), validTargets: targets };
    }
    api.sendRequest(uid, name, args);
    return { ok: true, action: name };
  };

  CB.moveTo = (uidS, x, y) => {
    const uid = parseCid(uidS); const u = Units.get(uid); if (!u) return { ok: false, error: "no such unit" };
    const war = safe(() => P().Diplomacy.willMoveStartWar(uid, { x, y }));
    if (war && war.Success) return { ok: false, error: `moving there would declare war on player ${war.Player2}; use declare_war first if intended` };
    const ct = safe(() => Game.Combat.testAttackInto(uid, { X: x, Y: y }));
    if (ct == CombatTypes.COMBAT_RANGED) {
      const a = { X: x, Y: y };
      if (Game.UnitOperations.canStart(uid, UnitOperationTypes.RANGE_ATTACK, a, false).Success) { Game.UnitOperations.sendRequest(uid, UnitOperationTypes.RANGE_ATTACK, a); return { ok: true, did: "ranged attack" }; }
    }
    const args = { X: x, Y: y, Modifiers: UnitOperationMoveModifiers.ATTACK + UnitOperationMoveModifiers.MOVE_IGNORE_UNEXPLORED_DESTINATION };
    const r = Game.UnitOperations.canStart(uid, UnitOperationTypes.MOVE_TO, args, false);
    if (!r.Success) {
      // swap with own unit?
      const sw = safe(() => Game.UnitOperations.canStart(uid, "UNITOPERATION_SWAP_UNITS", { X: x, Y: y }, false));
      if (sw && sw.Success) { Game.UnitOperations.sendRequest(uid, "UNITOPERATION_SWAP_UNITS", { X: x, Y: y }); return { ok: true, did: "swap" }; }
      return { ok: false, reasons: (r.FailureReasons || []).map(s => strip(loc(s))) };
    }
    const path = safe(() => Units.getPathTo(uid, { x, y }));
    Game.UnitOperations.sendRequest(uid, UnitOperationTypes.MOVE_TO, args);
    return { ok: true, did: ct == CombatTypes.COMBAT_MELEE ? "melee attack" : "move", turns: path && path.turns && path.turns.length ? path.turns[path.turns.length - 1] : null };
  };

  // The game's recommender can return nothing when good land is scarce. Then list every known tile that
  // passes the basic rules (land, passable, not owned by anyone else, at least 4 tiles from any settlement),
  // nearest first, so the agent can still judge the remaining options.
  const fallbackSettleSpots = (from, n) => {
    const cities = [];
    for (const pl of Players.getAlive()) for (const c of safe(() => pl.Cities.getCities(), []) || []) cities.push(c.location);
    const W = GameplayMap.getGridWidth(), H = GameplayMap.getGridHeight(), out = [];
    const home = safe(() => GameplayMap.getLandmassRegionId(from.x, from.y));
    for (let x = 0; x < W; x++) for (let y = 0; y < H; y++) {
      if (GameplayMap.isWater(x, y) || GameplayMap.isImpassable(x, y)) continue;
      if (!safe(() => GameplayMap.getRevealedState(me(), x, y))) continue;
      const own = GameplayMap.getOwner(x, y); if (own != -1 && own != me()) continue;
      const dmin = Math.min(99, ...cities.map(c => GameplayMap.getPlotDistance(x, y, c.x, c.y))); if (dmin < 4) continue;
      const other = safe(() => GameplayMap.getLandmassRegionId(x, y)) != home;
      out.push({ at: [x, y], dist: GameplayMap.getPlotDistance(from.x, from.y, x, y),
        why: `fallback scan (the game suggested nothing): unclaimed, ${dmin} tiles from nearest settlement${other ? ", other landmass (needs embarking)" : ""}${safe(() => P().isDistantLands({ x, y })) ? ", Distant Lands" : ""}` });
    }
    return out.sort((a, b) => a.dist - b.dist).slice(0, n);
  };
  CB.settleSpots = (uidS, n = 4) => {
    const u = uidS ? Units.get(parseCid(uidS)) : null;
    const from = u ? u.location : safe(() => Cities.get(P().Cities.getCityIds()[0]).location);
    const recs = safe(() => P().AI.getBestSettleLocationsForSettler(n, from), []) || [];
    if (!recs.length && from) return fallbackSettleSpots(from, n);
    return recs.map(r => ({ at: xy(r.location), dist: from ? GameplayMap.getPlotDistance(from.x, from.y, r.location.x, r.location.y) : null, why: (r.factors || []).map(f => (f.positive ? "+" : "-") + strip(loc(f.title))).join(", ") }));
  };

  CB.enemiesNear = (radius = 6) => {
    const seen = {}; const out = [];
    const myUnits = P().Units.getUnits() || [];
    const centers = myUnits.map(u => u.location).concat((P().Cities.getCities() || []).map(c => c.location));
    const dip = P().Diplomacy;
    for (const c of centers) {
      for (const i of safe(() => GameplayMap.getPlotIndicesInRadius(c.x, c.y, radius), [])) {
        if (seen[i]) continue; seen[i] = 1;
        const l = GameplayMap.getLocationFromIndex(i);
        for (const id of safe(() => MapUnits.getUnits(l.x, l.y), [])) {
          const eu = Units.get(id); if (!eu || eu.owner == me()) continue;
          if (!safe(() => Visibility.isVisible(me(), id), true)) continue;
          const op = Players.get(eu.owner);
          const hostile = safe(() => dip.isAtWarWith(eu.owner)) || (op && (op.isIndependent || op.isBarbarian));
          out.push({ owner: eu.owner, who: CB.playerName(eu.owner), hostile: !!hostile, type: safe(() => lk("Units", eu.type).UnitType.replace("UNIT_", "")), at: xy(l), hp: safe(() => eu.Health.maxDamage - eu.Health.damage) });
        }
      }
    }
    return out.slice(0, 40);
  };

  // ---------- notifications / blockers ----------
  CB.notifications = () => {
    const N = Game.Notifications;
    return (N.getIdsForPlayer(me()) || []).map(id => {
      const n = safe(() => N.find(id)) || {};
      return { id: cid(id), type: safe(() => N.getTypeName(n.Type), "?").replace("NOTIFICATION_", ""), blocks: !!safe(() => N.getBlocksTurnAdvancement(id)), msg: strip(safe(() => N.getMessage(id), "")), summary: strip(safe(() => N.getSummary(id), "")).slice(0, 200), at: n.Location && n.Location.x >= 0 ? xy(n.Location) : undefined };
    });
  };
  CB.blockingType = () => {
    const t = Game.Notifications.getEndTurnBlockingType(me());
    const k = Object.keys(EndTurnBlockingTypes).find(k => EndTurnBlockingTypes[k] == t);
    if (k) return k;
    // not in the JS enum: name it after the blocking notification
    const nid = safe(() => Game.Notifications.findEndTurnBlocking(me(), t));
    const nm = nid ? safe(() => Game.Notifications.getTypeName(Game.Notifications.find(nid).Type)) : null;
    return nm ? nm.replace("NOTIFICATION_", "") : String(t);
  };
  CB.turnState = () => ({ turn: Game.turn, active: !!P().isTurnActive, sent: safe(() => GameContext.hasSentTurnComplete()), blocking: CB.blockingType(), inGame: safe(() => UI.isInGame()) });
  CB.endTurn = () => {
    const p = P();
    if (!p.isTurnActive) return { ok: false, error: "turn not active" };
    if (GameContext.hasSentTurnComplete()) return { ok: true, note: "already sent" };
    const b = CB.blockingType();
    if (b !== "NONE") return { ok: false, blocking: b, notifications: CB.notifications().filter(n => n.blocks) };
    safe(() => UI.Player.deselectAllUnits());
    GameContext.sendTurnComplete();
    return { ok: true };
  };

  globalThis.CB = Object.assign(globalThis.CB || {}, CB);
  return "CB installed v" + CB.version;
})()
