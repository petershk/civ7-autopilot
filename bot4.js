// CivBot part 4: spectator features - camera follow and a compact world map for the dashboard.
(function () {
  const CB = globalThis.CB;
  const me = () => GameContext.localPlayerID;
  const safe = (f, d = null) => { try { const v = f(); return v === undefined ? d : v; } catch (e) { return d; } };
  const parseCid = (s) => { if (typeof s === "object") return s; const [o, i, t] = String(s).split(";").map(Number); return { owner: o, id: i, type: t }; };
  const lk = (tbl, h) => { try { return GameInfo[tbl].lookup(h); } catch (e) { return null; } };
  CB.version = 4;

  // ---------- camera follow (purely visual) ----------
  CB.focusPlot = (x, y, zoom = 0.45) => { safe(() => Camera.lookAtPlot({ x, y }, { zoom })); CB.lastFocus = { x, y, turn: Game.turn }; return { ok: true }; };
  CB.focusUnit = (uidS) => { const u = safe(() => Units.get(parseCid(uidS))); if (!u) return { ok: false }; return CB.focusPlot(u.location.x, u.location.y); };
  CB.focusCity = (cityS) => {
    let c = safe(() => Cities.get(parseCid(cityS)));
    if (!c) { const n = String(cityS || "").toLowerCase(); c = (Players.get(me()).Cities.getCities() || []).find(k => safe(() => Locale.compose(k.name).toLowerCase().includes(n))); }
    if (!c) c = safe(() => Players.get(me()).Cities.getCapital());
    return c ? CB.focusPlot(c.location.x, c.location.y, 0.4) : { ok: false };
  };

  // ---------- world map for the dashboard (only what we have revealed) ----------
  // Per plot one char: '.' hidden, 'o' ocean, 'w' coast/lake, 'M' mountain, 'l' land. Owners as separate array.
  CB.worldMap = () => {
    const W = GameplayMap.getGridWidth(), H = GameplayMap.getGridHeight();
    const rev = safe(() => GameplayMap.getRevealedStates(me())) || [];
    let terr = "", vis = ""; const owner = new Array(W * H).fill(-1);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = y * W + x; const idx = GameplayMap.getIndexFromXY(x, y);
      const rs = rev.length ? rev[idx] : safe(() => GameplayMap.getRevealedState(me(), x, y), 0);
      if (rs == RevealedStates.HIDDEN) { terr += "."; vis += "0"; continue; }
      vis += rs == RevealedStates.VISIBLE ? "2" : "1";
      if (GameplayMap.isWater(x, y)) terr += safe(() => lk("Terrains", GameplayMap.getTerrainType(x, y)).TerrainType == "TERRAIN_OCEAN") ? "o" : "w";
      else if (GameplayMap.isMountain(x, y)) terr += "M";
      else { const t = safe(() => lk("Terrains", GameplayMap.getTerrainType(x, y)).TerrainType, ""); terr += t.includes("HILL") ? "h" : "l"; }
      owner[i] = safe(() => GameplayMap.getOwner(x, y), -1);
    }
    const players = {};
    const note = (pid) => {
      if (pid == null || pid < 0 || players[pid]) return;
      const p = Players.get(pid); if (!p) return;
      players[pid] = { name: CB.playerName(pid), me: pid == me(), kind: p.isMajor ? "major" : p.isMinor ? "city-state" : "independent",
        color: safe(() => UI.Player.getPrimaryColorValueAsString(pid), "rgb(128,128,128)"), color2: safe(() => UI.Player.getSecondaryColorValueAsString(pid), "rgb(200,200,200)") };
    };
    owner.forEach(note);
    const cities = [];
    for (const p of Players.getAlive()) {
      for (const c of safe(() => p.Cities.getCities(), []) || []) {
        const rs = safe(() => GameplayMap.getRevealedState(me(), c.location.x, c.location.y), 0);
        if (rs == RevealedStates.HIDDEN) continue;
        note(p.id);
        cities.push({ x: c.location.x, y: c.location.y, owner: p.id, name: safe(() => Locale.compose(c.name), ""), capital: !!c.isCapital, town: !!c.isTown, pop: p.id == me() || rs == RevealedStates.VISIBLE ? c.population : null });
      }
    }
    const units = [];
    for (const p of Players.getAlive()) {
      for (const u of safe(() => p.Units.getUnits(), []) || []) {
        if (p.id != me() && !safe(() => Visibility.isVisible(me(), u.id), false)) continue;
        if (!u.location || u.location.x < 0) continue;
        note(p.id);
        const def = lk("Units", u.type) || {};
        units.push({ x: u.location.x, y: u.location.y, owner: p.id, type: (def.UnitType || "").replace("UNIT_", "").toLowerCase(), civ: def.FormationClass == "FORMATION_CLASS_CIVILIAN" });
      }
    }
    return { w: W, h: H, terr, vis, owner, players, cities, units, focus: CB.lastFocus || null, turn: Game.turn };
  };

  // Full per-turn history from the game's own statistics (what the end-of-game graphs use),
  // restricted to us + majors we have met. -> {players:{id:{name,me}}, rows:[{turn, players:[{id, score, sci, ...}]}]}
  const SUMKEYS = { Score: "score", Science: "sci", Culture: "cult", Gold: "gold", Production: "prod", Food: "food",
    Happiness: "happy", Influence: "infl", Population: "pop", TechsAcquired: "techs", CitiesFounded: "cities",
    UnitsKilled: "kills", WondersConstructed: "wonders", BuildingsConstructed: "buildings" };
  CB.summaryHistory = () => {
    const objs = {}; for (const o of safe(() => Game.Summary.getObjects(), []) || []) if (o.type == "Player") objs[o.ID] = o.ownerPlayer;
    const dip = Players.get(me()).Diplomacy;
    const allowed = (pid) => pid == me() || (safe(() => Players.get(pid).isMajor) && safe(() => dip.hasMet(pid)));
    const byTurn = {}; const players = {};
    for (const ds of safe(() => Game.Summary.getDataSets(), []) || []) {
      const key = SUMKEYS[ds.ID]; if (!key) continue;
      const pid = objs[ds.owner]; if (pid == null || !allowed(pid)) continue;
      players[pid] = players[pid] || { name: CB.playerName(pid), me: pid == me() };
      for (const v of ds.values || []) {
        const row = byTurn[v.x] || (byTurn[v.x] = {});
        const pr = row[pid] || (row[pid] = { id: pid, name: players[pid].name, me: pid == me() });
        pr[key] = Math.round(v.y * 10) / 10;
      }
    }
    const rows = Object.keys(byTurn).map(Number).sort((a, b) => a - b).map(t => ({ turn: t, players: Object.values(byTurn[t]) }));
    return { players, rows };
  };

  // Tech & civic trees for the dashboard: nodes with column (depth), links, state, progress, unlocks.
  CB.trees = () => {
    const p = Players.get(me()); const T = (x) => safe(() => Locale.compose(x).replace(/\[[^\]]*\]/g, "").trim(), x);
    const stateName = (v) => safe(() => Object.keys(ProgressionTreeNodeState).find(k => ProgressionTreeNodeState[k] == v).replace("NODE_STATE_", "").toLowerCase(), "?");
    const kindTbl = { KIND_UNIT: "Units", KIND_CONSTRUCTIBLE: "Constructibles", KIND_TRADITION: "Traditions", KIND_PROJECT: "Projects", KIND_DIPLOMATIC_ACTION: "DiplomacyActions" };
    const unlockRows = Array.from(GameInfo.ProgressionTreeNodeUnlocks);
    const build = (treeType, kind, src) => {
      const def = lk("ProgressionTrees", treeType); if (!def) return null;
      const tree = safe(() => Game.ProgressionTrees.getTree(me(), treeType));
      const active = safe(() => tree.nodes[tree.activeNodeIndex].nodeType);
      const target = safe(() => src.getTargetNode());
      const nodes = [];
      for (const n of safe(() => Game.ProgressionTrees.getTreeStructure(treeType), []) || []) {
        const nd = lk("ProgressionTreeNodes", n.nodeType); if (!nd) continue;
        const info = safe(() => Game.ProgressionTrees.getNode(me(), n.nodeType)) || {};
        const unlocks = unlockRows.filter(u => u.ProgressionTreeNodeType == nd.ProgressionTreeNodeType && !u.Hidden && kindTbl[u.TargetKind])
          .map(u => ({ depth: u.UnlockDepth, name: safe(() => T(lk(kindTbl[u.TargetKind], u.TargetType).Name)), kind: u.TargetKind.replace("KIND_", "").toLowerCase() }))
          .filter(u => u.name);
        nodes.push({ key: nd.ProgressionTreeNodeType, name: T(nd.Name), depth: n.treeDepth, children: (n.connectedNodeTypes || []).map(c => safe(() => lk("ProgressionTreeNodes", c).ProgressionTreeNodeType)).filter(Boolean),
          state: stateName(safe(() => Game.ProgressionTrees.getNodeState(me(), n.nodeType))), progress: info.progress || 0,
          cost: safe(() => src.getNodeCost(n.nodeType)), turns: safe(() => src.getTurnsForNode(n.nodeType)),
          depthUnlocked: info.depthUnlocked || 0, maxDepth: info.maxDepth || 1, active: n.nodeType == active, target: n.nodeType == target && target != null && target != -1,
          unlocks });
      }
      return { key: def.ProgressionTreeType, name: T(def.Name) || def.ProgressionTreeType, kind, nodes };
    };
    const out = [];
    const tt = safe(() => p.Techs.getTreeType()); if (tt != null) out.push(build(tt, "tech", p.Techs));
    for (const ct of safe(() => p.Culture.getAvailableTrees(), []) || []) out.push(build(ct, "civic", p.Culture));
    return { turn: Game.turn, age: safe(() => lk("Ages", Game.age).AgeType), trees: out.filter(Boolean) };
  };

  return "CB installed v" + CB.version;
})()
