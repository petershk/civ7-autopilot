// CivBot part 3: heuristic auto-resolution so the game never stalls. Extends globalThis.CB.
(function () {
  const CB = globalThis.CB;
  const me = () => GameContext.localPlayerID;
  const P = () => Players.get(me());
  const lk = (tbl, h) => { try { return GameInfo[tbl].lookup(h); } catch (e) { return null; } };
  const cid = (c) => c ? `${c.owner};${c.id};${c.type}` : null;
  const safe = (f, d = null) => { try { const v = f(); return v === undefined ? d : v; } catch (e) { return d; } };
  const INV = { X: -9999, Y: -9999, UnitAbilityType: -1 };
  CB.version = 3;
  CB.settlerTargets = CB.settlerTargets || {};

  // Give a unit a "stay put" order: fortify if possible, else skip. A unit in a city can't fortify, and with
  // enemies in view alert/sleep are refused too, which left "Command Units" blocking the turn. So after the
  // usual orders: send skip-turn unchecked, then step onto a quiet tile of our own next to it and hold there.
  const HOLD_OPS = ["UNITOPERATION_FORTIFY", "UNITOPERATION_ALERT", "UNITOPERATION_SKIP_TURN", "UNITOPERATION_SLEEP"];
  const stillReady = (uidS) => CB.readyUnits().includes(uidS);
  const holdOnce = (uidS) => { for (const a of HOLD_OPS) { const r = CB.unitDo(uidS, a); if (r.ok) return r; } return null; };
  CB.hold = (uidS) => {
    const r = holdOnce(uidS); if (r) return r;
    const u = (P().Units.getUnits() || []).find(v => cid(v.id) == uidS); if (!u) return { ok: false, error: "no such unit" };
    safe(() => Game.UnitOperations.sendRequest(u.id, "UNITOPERATION_SKIP_TURN", {}));
    if (!stillReady(uidS)) return { ok: true, action: "UNITOPERATION_SKIP_TURN", note: "sent unchecked" };
    const foreignNear = (x, y) => safe(() => GameplayMap.getPlotIndicesInRadius(x, y, 1), []).some(i => {
      const l = GameplayMap.getLocationFromIndex(i);
      return safe(() => MapUnits.getUnits(l.x, l.y), []).some(id => safe(() => Units.get(id).owner) != me());
    });
    const spots = safe(() => GameplayMap.getPlotIndicesInRadius(u.location.x, u.location.y, 1), [])
      .map(i => GameplayMap.getLocationFromIndex(i))
      .filter(l => (l.x != u.location.x || l.y != u.location.y) && safe(() => GameplayMap.getOwner(l.x, l.y)) == me()
        && !safe(() => MapUnits.getUnits(l.x, l.y), []).length)
      .sort((a, b) => foreignNear(a.x, a.y) - foreignNear(b.x, b.y));
    for (const l of spots) {
      if (!safe(() => CB.moveTo(uidS, l.x, l.y), {}).ok) continue;
      const h = holdOnce(uidS);
      if (h || !stillReady(uidS)) return { ok: true, action: h ? h.action : "moved", note: `stepped to ${l.x},${l.y} to hold` };
    }
    return { ok: false };
  };

  const isReady = (u) => safe(() => u.Movement.movementMovesRemaining, 0) > 0 &&
    [UnitActivityTypes.AWAKE, UnitActivityTypes.NONE].includes(safe(() => u.activityType)) &&
    !((safe(() => Units.getQueuedOperationDestination(u.id)) || { x: -1 }).x >= 0);

  // Promote if possible, then still give the unit an order: army commanders can report canPromote
  // while no promotion actually applies, and an unordered unit blocks the turn ("Command Units").
  CB.autoUnit = (u) => {
    let pre = "";
    if (safe(() => u.Experience.canPromote)) { const r = CB.promote(cid(u.id)); if (r.ok) pre = "promoted, "; }
    if (!isReady(u)) return pre + "no moves";
    return pre + autoUnitOrder(u);
  };
  const autoUnitOrder = (u) => {
    const def = lk("Units", u.type) || {}; const id = cid(u.id); const t = def.UnitType || "";
    if (def.FoundCity) {
      const here = safe(() => Game.UnitOperations.canStart(u.id, "UNITOPERATION_FOUND_CITY", INV, false).Success);
      const tgt = CB.settlerTargets[id];
      if (tgt && u.location.x == tgt[0] && u.location.y == tgt[1] && here) { CB.unitDo(id, "FOUND_CITY"); return "founded"; }
      // keep heading for a chosen site (Jev's pick, or ours from an earlier turn); drop it if it's no longer reachable
      if (tgt && !(u.location.x == tgt[0] && u.location.y == tgt[1])) {
        const m = CB.moveTo(id, tgt[0], tgt[1]); if (m.ok) return "settler->" + tgt;
        delete CB.settlerTargets[id];
      }
      const spots = CB.settleSpots(id, 3);
      if (spots.length) {
        const s = spots[0];
        if (s.at[0] == u.location.x && s.at[1] == u.location.y && here) { CB.unitDo(id, "FOUND_CITY"); return "founded"; }
        CB.settlerTargets[id] = s.at;
        const m = CB.moveTo(id, s.at[0], s.at[1]); if (m.ok) return "settler->" + s.at;
      }
      if (here) { CB.unitDo(id, "FOUND_CITY"); return "founded(fallback)"; }
      CB.hold(id); return "settler hold";
    }
    if (t.includes("SCOUT") || def.CoreClass == "CORE_CLASS_RECON") { const r = CB.unitDo(id, "AUTOMATE_EXPLORE"); if (r.ok) return "explore"; }
    if (safe(() => u.Health.damage, 0) > 30) { const r = CB.unitDo(id, "REST_UNTIL_HEALED"); if (r.ok) return "heal"; }
    // ranged units: shoot anything hostile in range
    if (safe(() => u.Combat.attacksRemaining, 0) > 0 && safe(() => u.Combat.rangedStrength, 0) > 0) {
      const r = safe(() => Game.UnitOperations.canStart(u.id, UnitOperationTypes.RANGE_ATTACK, {}, false));
      if (r && r.Plots) for (let i = 0; i < r.Plots.length; i++) {
        if (r.Modifiers && r.Modifiers[i] == OperationPlotModifiers.NONE) continue;
        const l = GameplayMap.getLocationFromIndex(r.Plots[i]);
        const a = { X: l.x, Y: l.y };
        if (safe(() => Game.UnitOperations.canStart(u.id, UnitOperationTypes.RANGE_ATTACK, a, false).Success)) { Game.UnitOperations.sendRequest(u.id, UnitOperationTypes.RANGE_ATTACK, a); return "ranged attack " + [l.x, l.y]; }
      }
    }
    const up = CB.unitDo(id, "UNITCOMMAND_UPGRADE"); if (up.ok) return "upgraded";
    if (def.CoreClass == "CORE_CLASS_SUPPORT" || def.FormationClass == "FORMATION_CLASS_CIVILIAN") {
      const c = CB.unitDo(id, "UNITCOMMAND_CONSTRUCT"); if (c.ok) return "construct";
      const tr = CB.unitDo(id, "UNITCOMMAND_MAKE_TRADE_ROUTE"); if (tr.ok) return "trade route";
      const ex = CB.unitDo(id, "EXCAVATE"); if (ex.ok) return "excavate";
      const sp = CB.unitDo(id, "SPREAD_RELIGION"); if (sp.ok) return "spread";
    }
    CB.hold(id); return "hold";
  };

  CB.autoUnits = () => {
    const log = [];
    for (const u of P().Units.getUnits() || []) if (isReady(u) || safe(() => u.Experience.canPromote)) log.push(`${(lk("Units", u.type) || {}).UnitType}: ${CB.autoUnit(u)}`);
    return log;
  };

  const TECH_PREF = ["POTTERY", "WRITING", "IRRIGATION", "MASONRY", "CURRENCY", "BRONZE", "MATHEMATICS", "ENGINEERING", "CARTOGRAPHY", "NAVIGATION", "EDUCATION", "ACADEMICS"];
  const choosePref = (opts, pref) => {
    for (const p of pref) { const o = opts.find(o => o.key.toUpperCase().includes(p)); if (o) return o; }
    return opts.slice().sort((a, b) => (a.turns || 99) - (b.turns || 99))[0];
  };

  CB.autoProduction = (c) => {
    const opts = CB.buildOptions(cid(c.id));
    const cities = P().Cities.getCities().length; const limit = safe(() => P().Stats.settlementCap, 0) || 99;
    const settlers = P().Units.getUnits().filter(u => (lk("Units", u.type) || {}).FoundCity).length;
    const military = safe(() => P().Stats.getNumCombatUnits ? P().Stats.getNumCombatUnits() : 0, 0);
    let choice = null;
    const threats = safe(() => CB.enemiesNear(5).filter(e => e.hostile).length, 0);
    if (threats >= 2 && military < cities * 2 + 2) choice = opts.units.filter(u => !/SETTLER|SCOUT|MERCHANT|MISSIONARY|COMMANDER/.test(u.key)).sort((a, b) => (a.turns || 99) - (b.turns || 99))[0];
    if (!choice && !c.isTown && cities + settlers < Math.max(limit, 3) + 1 && cities < 12) choice = opts.units.find(u => u.key.includes("SETTLER"));
    if (!choice && military < cities + 1) choice = opts.units.filter(u => !/SETTLER|SCOUT|MERCHANT|MISSIONARY|COMMANDER/.test(u.key)).sort((a, b) => (a.turns || 99) - (b.turns || 99))[0];
    if (!choice) choice = opts.buildings.filter(b => b.cls != "wonder" && !b.repair).sort((a, b) => (a.turns || 99) - (b.turns || 99))[0];
    if (!choice) choice = opts.buildings.find(b => b.repair) || opts.buildings[0];
    if (!choice) choice = opts.units[0];
    if (!choice) choice = opts.projects[0];
    return choice ? Object.assign(CB.build(cid(c.id), choice.key), { item: choice.name }) : { ok: false, error: "nothing buildable" };
  };

  // Resolve every blocker with heuristics. Returns a log of what it did.
  CB.autoResolve = (includeUnits = true) => {
    const log = [];
    const N = Game.Notifications;
    // diplomacy statements
    for (const s of CB.pendingStatements.slice()) {
      if (s.stmt && String(s.stmt).includes("FIRST_MEET") || s.stmt == "GREETING" || s.stmt == "FIRST_MEET") { CB.respondFirstMeet(s.sessionId, s.from, "FRIENDLY"); log.push("first meet friendly " + s.from); continue; }
      if (s.dealAction != null && s.dealAction != -1 && s.dealAction != DiplomacyDealProposalActions.ACCEPTED && s.dealAction != DiplomacyDealProposalActions.REJECTED) { CB.answerDeal(s.from, false); log.push("rejected deal from " + s.from); continue; }
      CB.closeSession(s.sessionId); log.push("closed session " + s.stmt);
    }
    for (const r of CB.diploPending().responses) {
      const cheap = r.options.find(o => !o.cost) || r.options[r.options.length - 1];
      CB.respondAction(r.actionId, r.options.indexOf(cheap)); log.push("responded " + r.title + " -> " + cheap.name);
    }
    for (const id of N.getIdsForPlayer(me()) || []) {
      if (!safe(() => N.getBlocksTurnAdvancement(id))) continue;
      const n = safe(() => N.find(id)); const tn = safe(() => N.getTypeName(n.Type), "");
      try {
        switch (tn) {
          case "NOTIFICATION_CHOOSE_TECH": { const o = choosePref(CB.techOptions(), TECH_PREF); if (o) log.push("tech " + CB.setTech(o.key).chosen); break; }
          case "NOTIFICATION_CHOOSE_CULTURE_NODE": { const o = choosePref(CB.civicOptions(), ["MYSTICISM", "CODE_OF_LAWS", "CHIEFDOM", "DISCIPLINE", "PUBLIC_LIFE"]); if (o) log.push("civic " + CB.setCivic(o.key).chosen); break; }
          case "NOTIFICATION_CHOOSE_CITY_PRODUCTION": { const c = Cities.get(n.Target); if (c) log.push("prod " + c.name + ": " + JSON.stringify(CB.autoProduction(c))); break; }
          case "NOTIFICATION_CHOOSE_TOWN_PROJECT": { const c = Cities.get(n.Target); if (c) { CB.setTownFocus(cid(c.id), "GROWTH"); safe(() => Game.CityOperations.sendRequest(c.id, CityOperationTypes.CONSIDER_TOWN_PROJECT, {})); log.push("town focus growth"); } break; }
          case "NOTIFICATION_NEW_POPULATION": {
            for (let k = 0; k < 4; k++) { const g = CB.growthOptions(); if (g.error) break; const best = g.expand[0]; if (best) { CB.placePop(g.cityId, best.at[0], best.at[1]); log.push("expand " + best.at); } else if (g.specialists[0]) { CB.placePop(g.cityId, g.specialists[0].at[0], g.specialists[0].at[1], true); log.push("specialist"); } else break; }
            break;
          }
          case "NOTIFICATION_CHOOSE_GOVERNMENT": { const o = CB.governmentOptions()[0]; if (o) log.push("gov " + JSON.stringify(CB.setGovernment(o.key))); break; }
          case "NOTIFICATION_TRADITIONS_AVAILABLE": case "NOTIFICATION_CRISIS": {
            for (let k = 0; k < 6; k++) { const pol = CB.policies(); const ok = pol.available.some(a => CB.setPolicy(a.key, true).ok); if (!ok) break; }
            CB.policiesDone(); log.push("policies filled"); break;
          }
          case "NOTIFICATION_CHOOSE_GOLDEN_AGE": { const o = CB.celebrationOptions()[0]; if (o) { CB.chooseCelebration(o.key); log.push("celebration " + o.name); } break; }
          case "NOTIFICATION_CHOOSE_PANTHEON": { const o = CB.pantheonOptions()[0]; if (o) { CB.choosePantheon(o.key); log.push("pantheon " + o.name); } break; }
          case "NOTIFICATION_CHOOSE_RELIGION": { CB.foundReligion(); log.push("religion"); break; }
          case "NOTIFICATION_CHOOSE_BELIEF": { const o = CB.beliefOptions()[0]; if (o) { CB.addBelief(o.key); log.push("belief " + o.name); } break; }
          case "NOTIFICATION_CAN_BUY_ATTRIBUTE_SKILL": { const o = CB.attributeOptions().nodes[0]; if (o) { CB.buyAttribute(o.key); log.push("attribute " + o.name); } else safe(() => Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.CONSIDER_ASSIGN_ATTRIBUTE, {})); break; }
          case "NOTIFICATION_CHOOSE_NARRATIVE_STORY_DIRECTION": case "NOTIFICATION_CHOOSE_DISCOVERY_STORY_DIRECTION": case "NOTIFICATION_CHOOSE_AUTO_NARRATIVE_STORY_DIRECTION": { const r = CB.chooseNarrative(0); log.push("narrative " + JSON.stringify(r)); break; }
          case "NOTIFICATION_CHOOSE_CITY_STATE_BONUS": { CB.chooseCityStateBonus(); log.push("cs bonus"); break; }
          case "NOTIFICATION_ASSIGN_NEW_RESOURCES": { log.push("resources " + JSON.stringify(CB.autoAssignResources())); break; }
          case "NOTIFICATION_CONSIDER_RAZE_CITY": { CB.razeDecision(null, "KEEP"); log.push("keep city"); break; }
          case "NOTIFICATION_CHOOSE_CIVILIZATION": { log.push("next civ " + JSON.stringify(CB.chooseNextCiv())); break; }
          case "NOTIFICATION_AGE_TRANSITION": case "NOTIFICATION_ADVANCED_START": { log.push("legacies " + JSON.stringify(CB.autoLegacies())); break; }
          case "NOTIFICATION_UNIT_PROMOTION_AVAILABLE": { const u = Units.get(n.Target); if (u) log.push("promote " + JSON.stringify(CB.promote(cid(u.id)))); break; }
          case "NOTIFICATION_PLAYER_MET": { const other = n.Player == me() ? n.Player2 : n.Player; log.push("first meet " + other + " " + JSON.stringify(CB.respondFirstMeet(-1, other, "FRIENDLY"))); break; }
          // fallback only (the agent normally answers): joining keeps the alliance; declining would end it
          case "NOTIFICATION_DIPLOMATIC_ALLY_AT_WAR": { log.push("call to arms: " + JSON.stringify(CB.answerCallToArms(true))); break; }
          default:
            if (tn.startsWith("NOTIFICATION_ADVISOR_WARNING") || tn.includes("ADVISOR")) { CB.dismissAdvisor(); log.push("advisor dismissed"); }
        }
      } catch (e) { log.push("ERR " + tn + ": " + e); }
    }
    if (CB.blockingType() == "VIEW_ADVISOR_WARNING") { CB.dismissAdvisor(); log.push("advisor"); }
    if (includeUnits) for (const l of CB.autoUnits()) log.push(l);
    return log;
  };

  // Last resort for end-turn blockers that survive autoResolve: a stale promotion notification, or an
  // informational one like "espionage detected". Promote the notification's own unit if it is a promotion,
  // then dismiss it (or activate it when it can't be dismissed), which is what the UI does on a click.
  CB.unstick = () => {
    const N = Game.Notifications; const log = [];
    // "Command Units": some unit still has moves and no order (e.g. a settler whose target is unreachable)
    if (CB.blockingType() == "UNITS") for (const id of CB.readyUnits()) {
      const r = safe(() => CB.hold(id), {});
      log.push((r && r.ok ? "held unit " : "could not hold unit ") + id);
    }
    // a unit with a queued move it can no longer make (path blocked, target taken) isn't "ready", yet it still
    // blocks the turn: take it from the blocking notification, cancel the stale move and re-order it
    if (CB.blockingType() == "UNITS" && !CB.readyUnits().length) {
      const nid = safe(() => N.findEndTurnBlocking(me(), N.getEndTurnBlockingType(me())));
      const tgt = nid && safe(() => N.find(nid).Target);
      const u = tgt && safe(() => Units.get(tgt));
      if (u && u.owner == me()) {
        const id = cid(u.id);
        safe(() => Game.UnitCommands.sendRequest(u.id, "UNITCOMMAND_CANCEL", {}));
        delete CB.settlerTargets[id];
        const r = (lk("Units", u.type) || {}).FoundCity ? safe(() => CB.autoUnit(Units.get(u.id)), "?") : JSON.stringify(safe(() => CB.hold(id), {}));
        log.push("stuck unit " + id + ": cancelled its queued move -> " + r);
      }
    }
    // findEndTurnBlocking needs the blocking type; without it the engine may return null or an unrelated notification
    const blocker = () => {
      if (CB.blockingType() == "NONE") return null;  // hasEndTurnBlocking can say false while the turn is blocked
      return safe(() => N.findEndTurnBlocking(me(), N.getEndTurnBlockingType(me())))
        || (N.getIdsForPlayer(me()) || []).find(id => safe(() => N.getBlocksTurnAdvancement(id))) || null;
    };
    for (let k = 0; k < 8; k++) {
      const nid = blocker(); if (!nid) break;
      const n = safe(() => N.find(nid)); const tn = safe(() => N.getTypeName(n.Type), "?").replace("NOTIFICATION_", "");
      // advisor warnings are cleared by marking them viewed (activating only opens them). They often come in a
      // chain: the next one appears a moment after this is dismissed, so callers re-check and repeat.
      if (tn.startsWith("ADVISOR_WARNING") || CB.blockingType() == "VIEW_ADVISOR_WARNING") { log.push("advisor " + JSON.stringify(CB.dismissAdvisor())); break; }
      // a call to arms needs an answer, not a click (activating it just opens the call-to-arms screen)
      if (tn == "DIPLOMATIC_ALLY_AT_WAR") { log.push("call to arms: " + JSON.stringify(CB.answerCallToArms(true))); break; }
      if (tn == "UNIT_PROMOTION_AVAILABLE" && n && n.Target) { const u = Units.get(n.Target); if (u) log.push("promote " + JSON.stringify(CB.promote(cid(u.id)))); }
      if (safe(() => N.canUserDismissNotification(nid))) { safe(() => N.dismiss(nid)); log.push("dismissed " + tn); }
      else { safe(() => N.activate(nid)); log.push("activated " + tn); }
      const after = blocker();
      if (after && after.id == nid.id) break;  // dismissal is applied asynchronously; the caller re-checks
    }
    return log;
  };

  // Call to arms: an ally went to war and asks us to join. The game's own screen offers exactly two
  // answers: accept (we declare war on the ally's enemy) or decline (our alliance with them ENDS).
  const callsToArms = () => {
    const N = Game.Notifications; const out = [];
    for (const id of N.getIdsForPlayer(me()) || []) {
      const n = safe(() => N.find(id));
      if (!n || safe(() => N.getTypeName(n.Type)) != "NOTIFICATION_DIPLOMATIC_ALLY_AT_WAR") continue;
      const aid = safe(() => n.Target && n.Target.id != null && n.Target.id != -1 ? n.Target.id : Game.Diplomacy.getNextCallToArms(me()), -1);
      const d = aid != -1 ? safe(() => Game.Diplomacy.getDiplomaticEventData(aid)) : null;
      if (!d) continue;
      const dip = P().Diplomacy; const allyIsTarget = !!safe(() => dip.hasAllied(d.targetPlayer));
      const ally = allyIsTarget ? d.targetPlayer : d.initialPlayer, enemy = allyIsTarget ? d.initialPlayer : d.targetPlayer;
      out.push({ nid: id, ally, enemy, dip });
    }
    return out;
  };
  CB.callToArms = () => callsToArms().map(c => ({
    ally: c.ally, allyName: CB.playerName(c.ally), enemy: c.enemy, enemyName: CB.playerName(c.enemy),
    alreadyAtWarWithEnemy: !!safe(() => c.dip.isAtWarWith(c.enemy)), canDeclareWar: !!safe(() => c.dip.canDeclareWarOn(c.enemy).Success),
    howToResolve: "answer_call_to_arms(accept=true) declares war on the enemy; accept=false declines, which ENDS our alliance with the ally"
  }));
  CB.answerCallToArms = (accept) => {
    const c = callsToArms()[0]; if (!c) return { ok: false, error: "no call to arms pending" };
    let r;
    if (accept) r = safe(() => c.dip.isAtWarWith(c.enemy)) ? { ok: true, note: "already at war" } : CB.declareWar(c.enemy, true);
    else {
      Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.CANCEL_ALLIANCE, { Player1: me(), Player2: c.ally, Type: DiplomacyActionTypes.DIPLOMACY_ACTION_FORM_ALLIANCE });
      r = { ok: true };
    }
    // the notice can outlive the answer; clear it and leave the call-to-arms screen, as the game's screen does
    safe(() => { if (Game.Notifications.canUserDismissNotification(c.nid)) Game.Notifications.dismiss(c.nid); });
    safe(() => { const im = globalThis.__cbMods && globalThis.__cbMods.im; if (im && im.isInInterfaceMode("INTERFACEMODE_CALL_TO_ARMS")) im.switchToDefault(); });
    return Object.assign({ answered: accept ? `joined ${CB.playerName(c.ally)}'s war on ${CB.playerName(c.enemy)}` : `declined; alliance with ${CB.playerName(c.ally)} ended` }, r);
  };

  // In-game rules lookup (the Civilopedia's source data): find entries whose name or type matches the query
  // and return the game's own description plus key numbers.
  const RULE_TABLES = [["Units", "UnitType"], ["Constructibles", "ConstructibleType"], ["ProgressionTreeNodes", "ProgressionTreeNodeType"],
    ["Traditions", "TraditionType"], ["Resources", "ResourceType"], ["Projects", "ProjectType"], ["Governments", "GovernmentType"],
    ["Beliefs", "BeliefType"], ["LegacyPaths", "LegacyPathType"], ["Civilizations", "CivilizationType"], ["Leaders", "LeaderType"],
    ["Terrains", "TerrainType"], ["Features", "FeatureType"], ["Biomes", "BiomeType"], ["UnitAbilities", "UnitAbilityType"]];
  const txt = (k) => { if (!k) return ""; const s = safe(() => Locale.compose(k), ""); return (s && s != k ? s : "").replace(/\[[^\]]*\]/g, "").replace(/\s+/g, " ").trim(); };
  CB.lookupRules = (query, limit = 8) => {
    const q = String(query || "").toLowerCase().trim(); if (!q) return { error: "empty query" };
    const out = [];
    for (const [tbl, key] of RULE_TABLES) {
      for (const row of safe(() => Array.from(GameInfo[tbl]), []) || []) {
        const type = row[key] || ""; const name = txt(row.Name);
        if (!name.toLowerCase().includes(q) && !String(type).toLowerCase().includes(q.replace(/\s+/g, "_"))) continue;
        const e = { table: tbl, type, name: name || type };
        const d = txt(row.Description) || txt(row.Tooltip); if (d) e.description = d.slice(0, 500);
        for (const f of ["Cost", "BaseMoves", "BaseSightRange", "Combat", "RangedCombat", "Range", "Age", "Population", "Happiness", "Maintenance"])
          if (row[f] != null && row[f] !== "" && row[f] !== 0) e[f] = row[f];
        out.push(e); if (out.length >= limit) return out;
      }
    }
    return out.length ? out : { none: `no rules entry matches '${query}'` };
  };

  CB.autoLegacies = () => {
    const as = P().AdvancedStart; if (!as) return { ok: false };
    let added = 0;
    for (let pass = 0; pass < 3; pass++) for (const c of safe(() => as.getAvailableCards(), []) || []) {
      if (safe(() => Game.PlayerOperations.canStart(me(), PlayerOperationTypes.ADVANCED_START_MODIFY_DECK, { Type: "ADD", ID: c.id }, false).Success)) { Game.PlayerOperations.sendRequest(me(), PlayerOperationTypes.ADVANCED_START_MODIFY_DECK, { Type: "ADD", ID: c.id }); added++; }
    }
    const f = CB.legacyFinish(); f.added = added; return f;
  };

  return "CB installed v" + CB.version;
})();

// ---------- keep the screen clean: dismiss purely-visual popups/screens ----------
// The bot resolves every decision through the game API, so any modal the UI opens is only a view.
// Pressing its OK/close button hides it exactly like a human click would (the underlying decision,
// if any, stays pending in the engine until the bot resolves it).
(function () {
  const CB = globalThis.CB;
  const click = (b) => { try { b.dispatchEvent(new CustomEvent("action-activate", { bubbles: true })); return true; } catch (e) { return false; } };
  const visible = (el) => { try { const s = getComputedStyle(el); return s.display != "none" && s.visibility != "hidden" && !el.classList.contains("hidden"); } catch (e) { return true; } };
  // Game UI singletons (loaded via dynamic import - same module instances the UI uses)
  if (!globalThis.__cbMods) {
    globalThis.__cbMods = {};
    import("/core/ui/context-manager/context-manager.js").then(m => globalThis.__cbMods.cm = m.ContextManager).catch(() => { });
    import("/base-standard/ui/narrative-event/narrative-popup-manager.js").then(m => globalThis.__cbMods.np = m.NarrativePopupManager).catch(() => { });
    import("/base-standard/ui/popup-sequencer/popup-sequencer.js").then(m => globalThis.__cbMods.ps = m.default).catch(() => { });
  }
  if (!globalThis.__cbMods.dm) import("/base-standard/ui/diplomacy/diplomacy-manager.js").then(m => globalThis.__cbMods.dm = m.default).catch(() => { });
  if (!globalThis.__cbMods.lm) import("/base-standard/ui/diplomacy/leader-model-manager.js").then(m => globalThis.__cbMods.lm = m.default).catch(() => { });
  if (!globalThis.__cbMods.dq) import("/core/ui/context-manager/display-queue-manager.js").then(m => globalThis.__cbMods.dq = m.DisplayQueueManager).catch(() => { });
  if (!globalThis.__cbMods.im) import("/core/ui/interface-modes/interface-modes.js").then(m => globalThis.__cbMods.im = m.InterfaceMode).catch(() => { });
  if (!globalThis.__cbMods.tr) import("/base-standard/ui-next/screens/legacies/triumph-complete-queue-manager.js").then(m => globalThis.__cbMods.tr = m.TriumphCompleteQueueManager).catch(() => { });
  // popups that are purely informational (safe to close; no decision is lost). Unknown ones are only logged.
  const SAFE_SEQUENCER_POPUPS = ["advisor-council-popup", "diplo-message-popup"];
  CB.unknownPopups = CB.unknownPopups || {};
  CB.closePopups = () => {
    const closed = [];
    const M = globalThis.__cbMods || {};
    // popup sequencer (advisor picker, etc.)
    for (let k = 0; k < 5 && M.ps && M.ps.currentPopupData; k++) {
      const id = M.ps.currentPopupData.screenId;
      if (!SAFE_SEQUENCER_POPUPS.includes(id)) { CB.unknownPopups[id] = Game.turn; break; }
      try { M.ps.closePopup(id); closed.push(id); } catch (e) { break; }
    }
    // agenda / diplomatic info messages pushed straight onto the context stack
    if (document.querySelector("diplo-message-popup") && M.cm) { try { M.cm.pop("diplo-message-popup"); closed.push("diplo-message-popup"); } catch (e) { } }
    // "triumph complete" (legacy/achievement) notice: purely informational, closes via its queue manager
    if (document.querySelector("triumph-complete-popup")) {
      try { if (M.tr) M.tr.closePopup(); else if (M.cm) M.cm.pop("triumph-complete-popup"); closed.push("triumph-complete-popup"); } catch (e) { }
    }
    // diplomacy overlays (call to arms, project reaction): views only; the decision stays pending in the
    // engine and is answered through the API (answer_call_to_arms / respond_diplomatic_action)
    if (M.im) for (const mode of ["INTERFACEMODE_CALL_TO_ARMS", "INTERFACEMODE_DIPLOMACY_PROJECT_REACTION"]) {
      try { if (M.im.isInInterfaceMode(mode)) { M.im.switchToDefault(); closed.push(mode.replace("INTERFACEMODE_", "").toLowerCase()); } } catch (e) { }
    }
    // leader dialog (taunts, greetings, deal screens): it hides the whole HUD and holds up everything queued behind
    // it (event cutscenes, popups). Close messages the bot has nothing left to answer and expired/stale deal
    // requests, then leave the leader scene. Statements still waiting for an answer are left for the agent.
    if (M.im && M.dq && M.dm && M.im.isInInterfaceMode("INTERFACEMODE_DIPLOMACY_DIALOG")) {
      try {
        const open = new Set((CB.pendingStatements || []).map(s => s.sessionId));
        for (const r of M.dq.findAll("DiplomacyDialog") || []) {
          if (open.has(r.SessionID)) continue;
          try { Game.DiplomacySessions.closeSession(r.SessionID); } catch (e) { }
          M.dq.close(r); closed.push("diplomacy-dialog");
        }
        for (const r of M.dq.findAll("DiplomacyDeal") || []) {
          const d = r.WorkingDealID ? (() => { try { return Game.DiplomacyDeals.getWorkingDeal(r.WorkingDealID); } catch (e) { return null; } })() : null;
          if (r.blockClose || !d || d.isExpired) { M.dq.close(r); closed.push("stale-deal"); }
        }
        if (M.dm.isEmpty()) { try { if (M.lm) M.lm.exitLeaderScene(); } catch (e) { } M.im.switchToDefault(); closed.push("leader-scene"); }
      } catch (e) { }
    }
    // narrative boxes that the bot already resolved through the API (engine has nothing pending)
    for (const tag of ["small-narrative-event", "screen-narrative-event"]) {
      if (document.querySelector(tag) && CB.narrative() == null && M.cm) {
        try { if (M.np) M.np.closePopup(); M.cm.pop(tag); closed.push(tag); } catch (e) { }
      }
    }
    for (let pass = 0; pass < 8; pass++) {
      let did = false;
      // cinematic placards: natural disasters, wonders completed, natural wonders found (they pause the flow)
      for (const pl of document.querySelectorAll("screen-natural-disaster-placard, screen-wonder-complete-placard, screen-natural-wonder-revealed-placard")) {
        const b = pl.querySelector("fxs-hero-button") || pl.querySelector("fxs-close-button");
        if (b && click(b)) { closed.push(pl.tagName.toLowerCase()); did = true; }
      }
      // tech / civic completed (queued, one at a time)
      const tc = document.querySelector("screen-tech-civic-complete");
      if (tc) { const b = tc.querySelector("fxs-button, fxs-close-button"); if (b && click(b)) { closed.push("tech-civic"); did = true; } }
      // any other modal screen with a close button
      for (const s of document.querySelectorAll("*")) {
        const t = s.tagName.toLowerCase();
        if (!(t.startsWith("screen-") || t.startsWith("popup-") || t.includes("-popup"))) continue;
        if (t == "screen-tech-civic-complete" || !visible(s)) continue;
        if (/endgame|victor|age-transition|legacies|dedication|advanced-start|loading/.test(t)) continue; // leave end/transition flows alone
        const b = s.querySelector("fxs-close-button");
        if (b && click(b)) { closed.push(t); did = true; }
      }
      if (!did) break;
    }
    return closed;
  };
  return "CB installed v" + CB.version;
})()
