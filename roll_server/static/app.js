let timer = null;

let lastRoll = null;
let activeRollRequest = null;
let armedBuffer = []; // collects rolls for advantage/disadvantage requests
let needDamage = null; // legacy NEED_DAMAGE message context
let activePendingAttack = null; // FIFO-selected pending attack (hidden from UI)
let hasPending = false;

let activeSheet = null;
let activeNoteId = null; // currently selected note for edit

let handouts = [];
let activeHandoutId = null;
let itemsDb = null;
let itemsIndex = {}; // item_id -> {item_id,name,type,weight,icon,category}
let spellsDb = null;
let spellsIndex = {};
let sheetDirtyPatch = {};
let sheetSaveTimer = null;
let sheetLastLoadedAt = 0;

let activeCampaignId = "";
let activePlayerId = "";
let activeCharacterId = "";
let lastLogs = [];
let lastResultsSeen = 0;
let feedEntries = []; // combined messages + results
let inventoryRenderSeq = 0;
let activeRestState = null;
let activeLevelUpState = null;
let activeLevelUpSignature = "";
let levelUpAwaitingRoll = false;
let activeSpellChoiceState = null;


const seenDedupeKeys = new Set();
const seenPendingKeys = new Set();

const ROLL_PRIORITY = {
  "death_save": 0,
  "save": 1,
  "check": 2,
  "attack_to_hit": 3,
  "damage": 4,
  "other": 5,
};

function rollPriority(kind) {
  const k = (kind || "").toLowerCase().trim();
  return (k in ROLL_PRIORITY) ? ROLL_PRIORITY[k] : 10;
}

const el = (id) => {
  const n = document.getElementById(id);
  if (!n) console.warn("Missing element id:", id);
  return n;
};

function setStatus(text, cls = "muted") {
  const s = el("status");
  if (!s) return;
  s.className = `status ${cls}`;
  s.textContent = text;
}

function setNet(online) {
  const n = el('netStatus');
  if (!n) return;
  if (online) { n.textContent = 'Online'; n.className = 'net netOk mono'; }
  else { n.textContent = 'Offline'; n.className = 'net netBad mono'; }
}

function escapeHtml(s) {
  return (s || "").toString().replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ---------------------------
// Auth / session storage
// ---------------------------
function getToken() { return localStorage.getItem("grengine_token") || ""; }
function setToken(t) { localStorage.setItem("grengine_token", t || ""); }
function clearToken() { localStorage.removeItem("grengine_token"); }

function authHeaders() {
  const tok = getToken();
  return tok ? { "Authorization": `Bearer ${tok}` } : {};
}

function apiBase() {
  const cid = (activeCampaignId || "").trim();
  return `/api/campaigns/${encodeURIComponent(cid)}`;
}

function buildItemsIndex(db) {
  const idx = {};
  const push = (arr, category) => {
    if (!Array.isArray(arr)) return;
    for (const it of arr) {
      if (!it || typeof it !== "object") continue;
      const item_id = (it.item_id || it.id || it.weapon_id || it.armor_id || "").toString().trim();
      if (!item_id) continue;
      idx[item_id] = {
        item_id,
        name: (it.name || item_id).toString(),
        type: (it.type || category || "item").toString(),
        weight: Number(it.weight || 0) || 0,
        icon: (it.icon || "").toString(),
        category: category || "misc",
      };
    }
  };
  db = (db && typeof db === "object") ? db : {};
  push(db.weapons, "weapon");
  push(db.armors, "armor");
  push(db.health_items, "consumable");
  push(db.misc_items, "misc");
  return idx;
}

async function loadItemsDbOnce() {
  if (itemsDb) return itemsDb;
  try {
    const db = await apiGet("/items");
    itemsDb = db;
    itemsIndex = buildItemsIndex(db);
    return db;
  } catch (e) {
    console.warn("Failed to load items db:", e);
    itemsDb = { weapons: [], armors: [], health_items: [], misc_items: [] };
    itemsIndex = {};
    return itemsDb;
  }
}

async function loadSpellsDbOnce(force = false) {
  if (!force && spellsDb) return spellsDb;
  try {
    const data = await apiGet("/spells");
    const arr = (data && Array.isArray(data.spells)) ? data.spells : [];
    spellsDb = arr;
    spellsIndex = {};
    for (const row of arr) {
      if (!row || typeof row !== "object") continue;
      const sid = String(row.spell_id || row.id || "").trim();
      if (!sid) continue;
      spellsIndex[sid] = row;
    }
    return spellsDb;
  } catch (e) {
    console.warn("Failed to load spells db:", e);
    spellsDb = [];
    spellsIndex = {};
    return spellsDb;
  }
}

async function apiGet(path) {
  const r = await fetch(`${apiBase()}${path}`, { cache: "no-store", headers: { ...authHeaders() } });
  const text = await r.text();
  if (!r.ok) throw new Error(`${r.status} ${text}`);
  return JSON.parse(text);
}

async function apiPost(path, payload) {
  const r = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(payload || {})
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${r.status} ${text}`);
  return JSON.parse(text);
}


async function apiPatch(path, payload) {
  const r = await fetch(`${apiBase()}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(payload || {})
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${r.status} ${text}`);
  return JSON.parse(text);
}

async function apiDelete(path) {
  const r = await fetch(`${apiBase()}${path}`, {
    method: "DELETE",
    headers: { ...authHeaders() }
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`${r.status} ${text}`);
  return text ? JSON.parse(text) : {};
}

// ---------------------------
// Dice helpers
// ---------------------------
function parseDamageExpr(expr) {
  const s = (expr || "").trim().toLowerCase();
  const m = s.match(/^(\d*)d(\d+)\s*([+-]\s*\d+)?$/);
  if (!m) return null;

  const count = m[1] ? parseInt(m[1], 10) : 1;
  const sides = parseInt(m[2], 10);
  const mod = m[3] ? parseInt(m[3].replace(/\s+/g, ""), 10) : 0;

  if (!Number.isFinite(count) || !Number.isFinite(sides) || sides <= 0 || count <= 0) return null;
  return { count, sides, mod, expr: (expr || "").trim() };
}

function rollNDY(count, sides) {
  const dice = [];
  for (let i = 0; i < count; i++) dice.push(1 + Math.floor(Math.random() * sides));
  return dice;
}

function rollDamage(expr, isCrit) {
  const parsed = parseDamageExpr(expr);
  if (!parsed) return { expr: (expr || "").trim(), dice: [], modifier: 0, total: 0, crit_applied: false };

  const diceCount = isCrit ? (parsed.count * 2) : parsed.count;
  const dice = rollNDY(diceCount, parsed.sides);
  const total = dice.reduce((a, b) => a + b, 0) + parsed.mod;

  return { expr: parsed.expr, dice, modifier: parsed.mod, total, crit_applied: !!isCrit };
}

// ---------------------------
// UI helpers
// ---------------------------
function setCampaignMeta(campaignId, playerId, characterName) {
  const meta = el("campaignMeta");
  if (!meta) return;
  meta.textContent = `Campaign: ${campaignId || "—"} • Player: ${playerId || "—"} • Character: ${characterName || "—"}`;
}

function showLoginScreen() {
  const ls = el("loginScreen");
  const as = el("appScreen");
  if (ls) ls.classList.remove("hidden");
  if (as) as.classList.add("hidden");
  const topHeader = el("topHeader");
  if (topHeader) topHeader.classList.remove("hidden");
  const btnLogoutFloat = el("btnLogoutFloat");
  if (btnLogoutFloat) btnLogoutFloat.classList.add("hidden");
}

function showAppScreen() {
  const ls = el("loginScreen");
  const as = el("appScreen");
  if (ls) ls.classList.add("hidden");
  if (as) as.classList.remove("hidden");
  const topHeader = el("topHeader");
  if (topHeader) topHeader.classList.add("hidden");
  const btnLogoutFloat = el("btnLogoutFloat");
  if (btnLogoutFloat) btnLogoutFloat.classList.remove("hidden");
}


// Tabs were removed; the portal is a single-page layout.
function enableTabs(_enabled) { /* no-op */ }
function switchTab(_name) { /* no-op */ }



const FEATURE_LABELS = {
  fighter_fighting_style: {name: 'Fighting Style', desc: 'Adopt a combat specialization that grants a persistent martial bonus based on the style chosen for this character.'},
  fighter_second_wind: {name: 'Second Wind', desc: 'On your turn, you can use a bonus action to regain hit points. This refreshes on a short or long rest.'},
  barbarian_rage: {name: 'Rage', desc: 'Enter a battle rage for offensive and defensive benefits. Rage uses are tracked as class resources, and the current runtime applies bludgeoning, piercing, and slashing resistance while active.'},
  barbarian_unarmored_defense: {name: 'Unarmored Defense', desc: 'While not wearing armor, your AC uses 10 + DEX modifier + CON modifier if that is better than your normal armor calculation.'},
  barbarian_reckless_attack: {name: 'Reckless Attack', desc: 'Make your attacks with abandon. This runtime currently tracks the state as a combat toggle, but does not yet inject full attack-roll advantage/disadvantage automatically.'},
  monk_unarmored_defense: {name: 'Unarmored Defense', desc: 'While not wearing armor, your AC uses 10 + DEX modifier + WIS modifier if that is better than your normal armor calculation.'},
  monk_martial_arts: {name: 'Martial Arts', desc: 'You are trained to fight effectively with unarmed strikes and monk weapons.'},
  monk_flurry_of_blows: {name: 'Flurry of Blows', desc: 'Spend 1 ki point to declare Flurry of Blows. The extra unarmed strikes remain player-tracked, but the ki cost is enforced.'},
  monk_patient_defense: {name: 'Patient Defense', desc: 'Spend 1 ki point to take the Dodge posture as a declared monk defensive effect. Attack rolls against you are broadly treated at disadvantage until your next turn.'},
  monk_step_of_the_wind: {name: 'Step of the Wind', desc: 'Spend 1 ki point to declare Dash or Disengage with monk mobility. Movement remains player-tracked.'},
  monk_deflect_missiles: {name: 'Deflect Missiles', desc: 'Arm your reaction to reduce the damage from the next ranged weapon attack that hits you.'},
  monk_stunning_strike: {name: 'Stunning Strike', desc: 'Arm Stunning Strike after spending 1 ki. Your next melee hit forces a Constitution save or the target is stunned.'},
  monk_stillness_of_mind: {name: 'Stillness of Mind', desc: 'Use your action to end a charmed or frightened effect on yourself where tracked on the sheet.'},
  monk_diamond_soul: {name: 'Diamond Soul', desc: 'Spend 1 ki to reroll a failed saving throw. All-save proficiency is applied automatically at this level.'},
  monk_empty_body: {name: 'Empty Body', desc: 'Spend 4 ki to become broadly invisible and resistant to all damage except force until ended.'},
  paladin_divine_sense: {name: 'Divine Sense', desc: 'Track a use of Divine Sense. Detection specifics remain player and DM interpreted until spell/supernatural state handling expands.'},
  paladin_lay_on_hands: {name: 'Lay on Hands', desc: 'You have a pool of healing that can be spent to restore hit points. Choose how many points to spend when you use it. The pool refreshes on a long rest.'},
  paladin_cleansing_touch: {name: 'Cleansing Touch', desc: 'Track a use of Cleansing Touch to end one spell on yourself or a willing creature. Full spell-state removal will come with deeper spell integration.'},
  great_weapon_master: {name: 'Great Weapon Master', desc: 'Toggle the -5 to hit / +10 damage trade-off for heavy melee weapon attacks. When enabled the engine applies the penalty/bonus automatically during attack resolution.'},
  sharpshooter: {name: 'Sharpshooter', desc: 'Toggle the -5 to hit / +10 damage trade-off for ranged weapon attacks. When enabled the engine applies the penalty/bonus automatically during attack resolution.'},
  fighter_action_surge: {name: 'Action Surge', desc: 'Push yourself beyond your normal limits for a moment. Full turn-economy enforcement is not wired yet.'},
  fighter_indomitable: {name: 'Indomitable', desc: 'Reroll a failed saving throw. This runtime currently tracks usage, but does not yet inject the reroll into save resolution automatically.'},
  rogue_sneak_attack: {name: 'Sneak Attack', desc: 'Once per turn, you deal extra damage when you hit with a qualifying finesse or ranged attack under the normal Sneak Attack conditions. The engine now adds this automatically when the conditions are met.'},
  rogue_thieves_cant: {name: "Thieves' Cant", desc: 'You know the secret mix of dialect, jargon, and coded signs used by rogues.'},
  rogue_cunning_action: {name: 'Cunning Action', desc: 'Declare Dash, Disengage, or Hide as your rogue bonus-action utility. The engine records the declared choice but does not hard-enforce action economy.'},
  rogue_uncanny_dodge: {name: 'Uncanny Dodge', desc: 'Arm your reaction to halve the damage from the next attack that hits you. The runtime consumes the effect on the next incoming attack damage.'},
  rogue_stroke_of_luck: {name: 'Stroke of Luck', desc: 'Track your once-per-rest Stroke of Luck use. Full attack/check override handling is still mostly manual.'},
  elf_darkvision: {name: 'Darkvision', desc: 'You can see in darkness out to 60 feet as if it were dim light.'},
  elf_trance: {name: 'Trance', desc: 'You do not sleep in the normal sense and instead enter a meditative trance during rest.'},
  elf_keen_senses: {name: 'Keen Senses', desc: 'You have heightened senses and gain proficiency in Perception.'},
  sun_elf_solar_affinity: {name: 'Solar Affinity', desc: 'Sun Elves are naturally attuned to daylight and radiant themes within your setting.'},
  sun_elf_radiant_lineage: {name: 'Radiant Lineage', desc: 'Your bloodline carries radiant affinity. The current passive implementation grants radiant resistance.'},
  moon_elf_night_attunement: {name: 'Night Attunement', desc: 'Moon Elves are more naturally aligned with darkness and nocturnal conditions.'},
  wood_elf_forest_affinity: {name: 'Forest Affinity', desc: 'Wood Elves move more naturally through the wilds and gain enhanced mobility tied to terrain.'},
  dwarf_darkvision: {name: 'Darkvision', desc: 'You can see in darkness out to 60 feet as if it were dim light.'},
  dwarf_stonecraft: {name: 'Stonecraft', desc: 'You have practiced knowledge of stonework, masonry, and related craftsmanship.'},
  dwarf_poison_resilience: {name: 'Poison Resilience', desc: 'You are especially hardy against poison. The current passive implementation grants poison resistance.'},
  deep_dwarf_deepvision: {name: 'Deepvision', desc: 'Your dark-adapted sight extends farther than normal darkvision.'},
  deep_dwarf_stone_endurance: {name: 'Stone Endurance', desc: 'You can draw on dwarven resilience to blunt incoming harm. This is tracked as a limited-use racial resource.'},
  deep_dwarf_subterranean_instinct: {name: 'Subterranean Instinct', desc: 'Life below the earth has sharpened your reactions and instincts underground.'},
  deep_dwarf_light_sensitivity_minor: {name: 'Light Sensitivity (Minor)', desc: 'Bright light is mildly uncomfortable and can impose minor situational drawbacks.'},
  dramau_scaled_hide: {name: 'Scaled Hide', desc: 'Your natural scales can set a baseline AC when you are not wearing armor.'},
  stormen_giant_blooded_frame: {name: 'Giant-Blooded Frame', desc: 'Stormen are larger-framed and physically imposing, with chassis benefits tied to their build.'},
  stormen_stone_strider: {name: 'Stone Strider', desc: 'You move confidently across rough stone and similar difficult footing.'},
  stormen_mountain_born: {name: 'Mountain Born', desc: 'You are naturally adapted to harsh mountain conditions. The current passive implementation grants cold resistance.'},
};

function humanizeFeatureId(value) {
  const raw = (value || '').toString().trim();
  if (!raw) return '';
  return raw.replace(/[_-]+/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
}

function describeFeatureId(value) {
  const key = (value || '').toString().trim().toLowerCase();
  const spec = FEATURE_LABELS[key];
  if (spec) return `• ${spec.name}
  ${spec.desc}`;
  return `• ${humanizeFeatureId(value)}`;
}

function fmtMod(score) {
  const s = Number(score || 0);
  const m = Math.floor((s - 10) / 2);
  return (m >= 0) ? `+${m}` : `${m}`;
}

function modInt(score) {
  const s = Number(score || 0);
  return Math.floor((s - 10) / 2);
}

function playerCanReadLanguage(lang) {
  const name = (lang || "").trim().toLowerCase();
  if (!name) return true;
  const sheet = activeSheet || {};
  const prof = sheet.proficiencies || {};
  const langs = Array.isArray(prof.languages) ? prof.languages : [];
  for (const L of langs) {
    const n = String((L && L.name) ? L.name : "").trim().toLowerCase();
    if (n && n === name) return !!(L && L.read);
  }
  return false;
}

function scrambleText(text, seedStr) {
  // Deterministic scramble for unreadable handouts (language gating).
  let h = 2166136261;
  const s = String(seedStr || "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  let seed = (h >>> 0) || 1;
  const rnd = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const A = "abcdefghijklmnopqrstuvwxyz";
  const B = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  let out = "";
  for (const ch of String(text || "")) {
    const iA = A.indexOf(ch);
    const iB = B.indexOf(ch);
    if (iA >= 0) out += A[Math.floor(rnd() * A.length)];
    else if (iB >= 0) out += B[Math.floor(rnd() * B.length)];
    else out += ch;
  }
  return out;
}

const SAVE_LIST = [
  ["str", "STR"], ["dex", "DEX"], ["con", "CON"], ["int", "INT"], ["wis", "WIS"], ["cha", "CHA"],
];

const SKILL_LIST = [
  ["acrobatics", "Acrobatics", "dex"],
  ["animal_handling", "Animal Handling", "wis"],
  ["arcana", "Arcana", "int"],
  ["athletics", "Athletics", "str"],
  ["deception", "Deception", "cha"],
  ["history", "History", "int"],
  ["insight", "Insight", "wis"],
  ["intimidation", "Intimidation", "cha"],
  ["investigation", "Investigation", "int"],
  ["medicine", "Medicine", "wis"],
  ["nature", "Nature", "int"],
  ["perception", "Perception", "wis"],
  ["performance", "Performance", "cha"],
  ["persuasion", "Persuasion", "cha"],
  ["religion", "Religion", "int"],
  ["sleight_of_hand", "Sleight of Hand", "dex"],
  ["stealth", "Stealth", "dex"],
  ["survival", "Survival", "wis"],
];

function renderProficiencyGrids(sheet) {
  const saveGrid = el("saveGrid");
  const skillGrid = el("skillGrid");
  if (!saveGrid || !skillGrid) return;

  const creating = isCreatingSheet(sheet);
  const pb = Number((sheet._derived && sheet._derived.proficiency_bonus) ? sheet._derived.proficiency_bonus : 2);
  const ab = sheet.abilities || {};
  const prof = sheet.proficiencies || {};
  const ps = (prof.saves && typeof prof.saves === "object") ? prof.saves : {};
  const pk = (prof.skills && typeof prof.skills === "object") ? prof.skills : {};

  const mkRow = (key, label, bonus, checked, onToggle) => {
    const wrap = document.createElement("div");
    wrap.className = "skillRow";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!checked;
    cb.disabled = !creating;
    cb.onchange = () => onToggle(!!cb.checked);

    const name = document.createElement("div");
    name.className = "skillName";
    name.textContent = label;

    const bn = document.createElement("div");
    bn.className = "mono bonus";
    bn.textContent = (bonus >= 0) ? `+${bonus}` : `${bonus}`;

    wrap.appendChild(cb);
    wrap.appendChild(name);
    wrap.appendChild(bn);
    return wrap;
  };

  saveGrid.innerHTML = "";
  for (const [k, lbl] of SAVE_LIST) {
    const b = modInt(ab[k] ?? 10) + (ps[k] ? pb : 0);
    saveGrid.appendChild(
      mkRow(`save_${k}`, `${lbl} Save`, b, !!ps[k], (v) => queueSheetPatch(`proficiencies.saves.${k}`, v))
    );
  }

  skillGrid.innerHTML = "";
  for (const [key, lbl, abil] of SKILL_LIST) {
    const b = modInt(ab[abil] ?? 10) + (pk[key] ? pb : 0);
    skillGrid.appendChild(
      mkRow(`skill_${key}`, `${lbl} (${abil.toUpperCase()})`, b, !!pk[key], (v) => queueSheetPatch(`proficiencies.skills.${key}`, v))
    );
  }
}

function renderInventory(sheet) {
  const tbl = el("invTable");
  const wout = el("invWeightOut");
  const eout = el("invEncOut");
  const addSel = el("invAddSelect");
  const btnAdd = el("btnInvAdd");
  const eqW = el("equipWeapon");
  const eqA = el("equipArmor");
  if (!tbl) return;

  const inv = Array.isArray(sheet.inventory) ? sheet.inventory : [];
  const abilities = sheet.abilities || {};
  const str = Number(abilities.str ?? abilities.STR ?? 10) || 10;
  const capacity = Math.max(0, str * 15);

  const seq = ++inventoryRenderSeq;

  (async () => {
    await loadItemsDbOnce();
    if (seq !== inventoryRenderSeq) return;

    if (addSel) {
      addSel.innerHTML = "";
      const opt0 = document.createElement("option");
      opt0.value = "";
      opt0.textContent = "(select an item)";
      addSel.appendChild(opt0);

      const all = Object.values(itemsIndex);
      all.sort((a, b) => (a.name || a.item_id).localeCompare(b.name || b.item_id));
      for (const it of all) {
        const opt = document.createElement("option");
        opt.value = it.item_id;
        const w = (it.weight && it.weight > 0) ? ` • ${it.weight} lb` : "";
        opt.textContent = `${it.name} (${it.item_id})${w}`;
        addSel.appendChild(opt);
      }
    }

    const mkEquip = (sel, list, currentId, emptyLabel) => {
      if (!sel) return;
      sel.innerHTML = "";
      const o0 = document.createElement("option");
      o0.value = "";
      o0.textContent = emptyLabel;
      sel.appendChild(o0);

      const arr = Array.isArray(list) ? list : [];
      const opts = arr.map(x => ({
        item_id: (x.item_id || x.id || "").toString(),
        name: (x.name || x.item_id || x.id || "").toString(),
      })).filter(x => x.item_id);

      opts.sort((a, b) => a.name.localeCompare(b.name));

      for (const it of opts) {
        const opt = document.createElement("option");
        opt.value = it.item_id;
        opt.textContent = it.name;
        if (it.item_id === currentId) opt.selected = true;
        sel.appendChild(opt);
      }
    };

    const eq = (sheet.equipped && typeof sheet.equipped === "object") ? sheet.equipped : {};
    mkEquip(eqW, itemsDb ? itemsDb.weapons : [], (eq.weapon_id || "").toString(), "Weapon: (none)");
    mkEquip(eqA, itemsDb ? itemsDb.armors : [], (eq.armor_id || "").toString(), "Armor: (none)");

    if (btnAdd) {
      btnAdd.disabled = false;
      btnAdd.onclick = async () => {
        const sel = el("invAddSelect");
        const qtyN = el("invAddQty");
        const item_id = sel ? (sel.value || "").trim() : "";
        const qty = Math.max(1, parseInt((qtyN && qtyN.value) ? qtyN.value : "1", 10) || 1);
        if (!item_id) return;
        try {
          await apiPost("/inventory/mine/add", { item_id, qty });
          if (qtyN) qtyN.value = "1";
          if (sel) sel.value = "";
          await loadSheetOnce(true);
        } catch (e) {
          console.error("[INV_ADD] failed", e);
          setStatus(`Add item failed: ${e}`, "err");
        }
      };
    }

    if (eqW) {
      eqW.onchange = async () => {
        try {
          const weapon_id = (eqW.value || "").trim();
          await apiPost("/equip/mine", { weapon_id });
          const fresh = await apiGet("/sheet/mine");
          activeSheet = fresh;
          sheetLastLoadedAt = Date.now();
          renderSheetFromState(fresh);
        } catch (e) {
          console.error("[EQUIP_WEAPON] failed", e);
          setStatus(`Equip weapon failed: ${e}`, "err");
        }
      };
    }

    if (eqA) {
      eqA.onchange = async () => {
        try {
          const armor_id = (eqA.value || "").trim();
          await apiPost("/equip/mine", { armor_id });
          const fresh = await apiGet("/sheet/mine");
          activeSheet = fresh;
          sheetLastLoadedAt = Date.now();
          renderSheetFromState(fresh);
        } catch (e) {
          console.error("[EQUIP_ARMOR] failed", e);
          setStatus(`Equip armor failed: ${e}`, "err");
        }
      };
    }
  })();

  const iconFor = (it) => {
    const id = (it.item_id || "").toString();
    const fromDb = itemsIndex[id];
    const icon = (it.icon || (fromDb ? fromDb.icon : "") || "").toString().trim();
    if (icon) return icon;
    const name = (it.name || (fromDb ? fromDb.name : "") || id || "?").toString();
    return name.substring(0, 1).toUpperCase();
  };

  const _isImageIcon = (s) => {
    const v = (s || "").toString().trim().toLowerCase();
    return !!v && (v.endsWith(".png") || v.endsWith(".jpg") || v.endsWith(".jpeg") || v.endsWith(".webp") || v.endsWith(".gif") || v.endsWith(".svg"));
  };

  const _resolveIconUrl = (icon) => {
    const v = (icon || "").toString().trim();
    if (!v) return "";
    if (v.startsWith("http://") || v.startsWith("https://") || v.startsWith("data:") || v.startsWith("/")) return v;
    return `/static/${v}`;
  };

  const nameFor = (it) => {
    const id = (it.item_id || "").toString();
    const fromDb = itemsIndex[id];
    return (it.name || (fromDb ? fromDb.name : "") || id || "item").toString();
  };

  const weightFor = (it) => {
    const id = (it.item_id || "").toString();
    const fromDb = itemsIndex[id];
    const w = (it.weight !== undefined && it.weight !== null) ? Number(it.weight) : (fromDb ? Number(fromDb.weight) : 0);
    return Number.isFinite(w) ? w : 0;
  };

  const _renderInvIcon = (container, it) => {
    if (!container) return;
    container.innerHTML = "";
    const raw = iconFor(it);
    if (_isImageIcon(raw)) {
      const img = document.createElement("img");
      img.alt = (nameFor(it) || "icon").toString();
      img.src = _resolveIconUrl(raw);
      img.style.width = "100%";
      img.style.height = "100%";
      img.style.objectFit = "cover";
      img.style.borderRadius = "6px";
      img.loading = "lazy";
      img.onerror = () => {
        container.innerHTML = "";
        const letter = (nameFor(it) || "?").toString().substring(0, 1).toUpperCase();
        container.textContent = letter;
      };
      container.appendChild(img);
    } else {
      container.textContent = raw.toString().substring(0, 2);
    }
  };

  let totalW = 0;
  for (const it of inv) {
    if (!it) continue;
    const qty = Math.max(0, Number(it.qty ?? 1) || 0);
    totalW += qty * weightFor(it);
  }
  totalW = Math.round(totalW * 100) / 100;

  if (wout) wout.textContent = `Weight: ${totalW} / ${capacity} lb`;
  if (eout) {
    const state = (totalW <= capacity) ? "OK" : "OVER";
    eout.textContent = `Encumbrance: ${state}`;
  }

  const handoutRows = (Array.isArray(handouts) ? handouts : []).map(h => ({
    _virtualType: "handout",
    handout_id: String(h.handout_id || "").trim(),
    name: String(h.title || "Handout"),
    qty: 1,
    weight: 0,
    unreadable: !!h.unreadable,
    unreadable_mode: h.unreadable_mode || "",
    text: h.text || "",
    payload: h.payload || {},
    created_at: h.created_at || 0,
  }));

  const rows = [
    ...inv.map((it, idx) => ({ ...it, _rowType: "inventory", _index: idx })),
    ...handoutRows.map(h => ({ ...h, _rowType: "handout_virtual" })),
  ];

  const head = document.createElement("div");
  head.className = "invRow invHeader";
  head.innerHTML = `<div></div><div>Item</div><div style="text-align:right;">Wt</div><div style="text-align:right;">Qty</div><div style="text-align:right;">Actions</div>`;

  tbl.innerHTML = "";
  tbl.appendChild(head);

  if (rows.length === 0) {
    const r = document.createElement("div");
    r.className = "invRow";
    r.innerHTML = `<div class="invIcon">—</div><div class="muted">(empty)</div><div></div><div></div><div></div>`;
    tbl.appendChild(r);
    return;
  }

  rows.forEach((it) => {
    const row = document.createElement("div");
    row.className = "invRow";

    const qty = Math.max(0, Number(it.qty ?? 1) || 0);
    const w = weightFor(it);

    const isNote = (it.type === "note");
    const isHandout = (it._rowType === "handout_virtual");
    const id = isHandout ? String(it.handout_id || "") : String(it.item_id || it.note_id || "");

    const icon = document.createElement("div");
    icon.className = "invIcon";
    if (isHandout) {
      icon.textContent = "H";
    } else {
      _renderInvIcon(icon, it);
    }

    const nm = document.createElement("div");
    nm.className = "invItemName";

    let suffix = "";
    if (isNote) suffix = ` <span class="muted">(note)</span>`;
    if (isHandout) suffix = ` <span class="muted">(handout${it.unreadable ? ", unreadable" : ""})</span>`;

    nm.innerHTML = `<div>${escapeHtml(nameFor(it))}${suffix}</div>`;

    const wt = document.createElement("div");
    wt.className = "mono";
    wt.style.textAlign = "right";
    wt.textContent = (Number.isFinite(w) ? (w === 0 ? "0" : `${w}`) : "0");

    const q = document.createElement("div");
    q.className = "invQty";

    if (isHandout) {
      const qn = document.createElement("div");
      qn.className = "mono qtyNum";
      qn.textContent = "—";
      q.appendChild(qn);
    } else {
      const btnMinus = document.createElement("button");
      btnMinus.className = "btn ghost";
      btnMinus.type = "button";
      btnMinus.textContent = "-";
      btnMinus.disabled = isNote;
      btnMinus.onclick = async () => {
        await apiPost("/inventory/mine/adjust", { index: it._index, delta: -1 });
        await loadSheetOnce(true);
      };

      const btnPlus = document.createElement("button");
      btnPlus.className = "btn ghost";
      btnPlus.type = "button";
      btnPlus.textContent = "+";
      btnPlus.disabled = isNote;
      btnPlus.onclick = async () => {
        await apiPost("/inventory/mine/adjust", { index: it._index, delta: 1 });
        await loadSheetOnce(true);
      };

      const qn = document.createElement("div");
      qn.className = "mono qtyNum";
      qn.textContent = String(qty);

      q.appendChild(btnMinus);
      q.appendChild(qn);
      q.appendChild(btnPlus);
    }

    const acts = document.createElement("div");
    acts.className = "invActions";

    const btnUse = document.createElement("button");
    btnUse.className = "btn";
    btnUse.type = "button";

    if (isHandout) {
      btnUse.textContent = "Open";
      btnUse.onclick = async () => {
        const h = (Array.isArray(handouts) ? handouts : []).find(x => String(x.handout_id || "") === String(it.handout_id || ""));
        if (!h) return;

        activeHandoutId = h.handout_id;
        const payload = h.payload || {};
        const lang = String(payload.language || payload.lang || h.language || "").trim();

        let canRead = true;
        try {
          canRead = playerCanReadLanguage(lang);
        } catch (e) {
          canRead = false;
        }

        const unreadableMode = String(h.unreadable_mode || payload.unreadable_mode || "blocked").trim().toLowerCase();
        const titleEl = el("handoutTitle");
        const bodyEl = el("handoutBody");
        const view = el("handoutView");

        if (titleEl) titleEl.textContent = (h.title || "Handout");

        let body = (h.text ?? payload.text ?? payload.body ?? payload.content ?? payload.message ?? "").toString().trim();
        const isUnreadable = (lang && !canRead) || Boolean(h.unreadable);

        if (isUnreadable) {
          body = (unreadableMode === "scramble")
            ? (body || "You can't read this handout.")
            : "You can't read this handout.";
        } else {
          body = body || "(Empty handout.)";
        }

        if (bodyEl) bodyEl.textContent = body;
        if (view) {
          view.classList.remove("hidden");
          view.style.display = "block";
        }

        if (!h.read) {
          try { await apiPost("/handouts/read", { handout_id: h.handout_id }); } catch (_) {}
          h.read = true;
        }
      };
    } else {
      const canUse = !!(String((it.effect || "")).trim()) || String(it.type || "").toLowerCase() === "consumable";
      btnUse.textContent = isNote ? "Edit" : "Use";
      btnUse.disabled = !isNote && !canUse;
      btnUse.onclick = async () => {
        if (isNote) {
          const notes = (activeSheet && Array.isArray(activeSheet.notes)) ? activeSheet.notes : [];
          const n = notes.find(n0 => String(n0.note_id || "") === String(it.note_id || ""));
          if (!n) return;
          activeNoteId = String(n.note_id || "");
          const t = el("noteTitle");
          const x = el("noteText");
          if (t) t.value = (n.title || "").toString();
          if (x) x.value = (n.text || "").toString();
          setNoteEditingBanner();
          renderNotesFromState(activeSheet);
          return;
        }

        try {
          await apiPost("/inventory/mine/use", { index: it._index });
          await loadSheetOnce(true);
        } catch (e) {
          console.warn(e);
        }
      };
    }

    const btnSend = document.createElement("button");
    btnSend.className = "btn ghost";
    btnSend.type = "button";
    btnSend.textContent = "Send";
    btnSend.disabled = false;

    btnSend.onclick = async () => {
      const chars = await loadPartyCharacters(true);
      const me = activeCharacterId || "";
      const options = chars.filter(c => c && c.character_id && c.character_id !== me);

      if (!options.length) {
        alert("No other characters available.");
        return;
      }

      const list = options.map(c => `${c.character_id} (${c.display_name || c.character_id})`).join("\n");
      const toChar = prompt("Send to which character_id?\n" + list, options[0].character_id);
      if (!toChar) return;

      try {
        if (isHandout) {
          await apiPost("/handouts/mine/share", {
            handout_id: String(it.handout_id || "").trim(),
            to_character_id: toChar.trim(),
          });
        } else if (isNote) {
          await apiPost(`/notes/mine/${encodeURIComponent(String(it.note_id || "").trim())}/send`, {
            to_character_id: toChar.trim(),
          });
        } else {
          const qraw = prompt(`Quantity to send (1-${qty})`, "1");
          const qn = Math.max(1, Math.min(qty, parseInt(qraw || "1", 10) || 1));
          await apiPost("/inventory/mine/transfer", {
            index: it._index,
            to_character_id: toChar.trim(),
            qty: qn,
          });
        }

        await loadSheetOnce(true);
        await pollHandoutsOnce();
      } catch (e) {
        console.warn(e);
        alert("Send failed.");
      }
    };

    const btnRm = document.createElement("button");
    btnRm.className = "btn ghost";
    btnRm.type = "button";

    if (isHandout) {
      btnRm.textContent = "Hide";
      btnRm.disabled = true;
    } else if (isNote) {
      btnRm.textContent = "Delete";
      btnRm.onclick = async () => {
        if (!it.note_id) return;
        await apiDelete(`/notes/mine/${encodeURIComponent(String(it.note_id || "").trim())}`);
        if (activeNoteId === String(it.note_id || "")) {
          activeNoteId = null;
          setNoteEditingBanner();
        }
        await loadSheetOnce(true);
      };
    } else {
      btnRm.textContent = "Remove";
      btnRm.onclick = async () => {
        await apiPost("/inventory/mine/remove", { index: it._index });
        await loadSheetOnce(true);
      };
    }

    acts.appendChild(btnUse);
    acts.appendChild(btnSend);
    acts.appendChild(btnRm);

    row.appendChild(icon);
    row.appendChild(nm);
    row.appendChild(wt);
    row.appendChild(q);
    row.appendChild(acts);
    tbl.appendChild(row);
  });
}

function setSheetSaveStatus(text, cls) {
  const out = el("sheetSaveOut");
  if (!out) return;
  out.textContent = text;
  out.className = `hint mono ${cls || ""}`.trim();
}

function setSheetStatusText(text) {
  const out = el("sheetStatusOut");
  if (out) out.textContent = text || "—";
}

function setInputDisabled(id, disabled) {
  const n = el(id);
  if (n) n.disabled = !!disabled;
}

function readInt(id, fallback) {
  const n = el(id);
  if (!n) return fallback;
  const v = parseInt(String(n.value || "").trim(), 10);
  return Number.isFinite(v) ? v : fallback;
}

function writeVal(id, v) {
  const n = el(id);
  if (n) n.value = (v === null || v === undefined) ? "" : String(v);
}

function setText(id, v) {
  const n = el(id);
  if (n) n.textContent = (v === null || v === undefined || v === "") ? "—" : String(v);
}

function isCreatingSheet(sheet) {
  const lc = (sheet && sheet.lifecycle) ? sheet.lifecycle : {};
  return String((lc && lc.status) ? lc.status : "active").toLowerCase().trim() === "creating";
}


let _partyCache = { ts: 0, characters: [] };
let _lastSheetRefreshTs = 0;
let _lastSheetUpdatedAt = 0;

async function loadPartyCharacters(force=false) {
  const now = Date.now();
  if (!force && _partyCache.characters.length && (now - _partyCache.ts) < 15000) return _partyCache.characters;
  try {
    const data = await apiGet("/party/characters");
    const chars = Array.isArray(data.characters) ? data.characters : [];
    _partyCache = { ts: now, characters: chars };
    return chars;
  } catch (e) {
    return _partyCache.characters || [];
  }
}
async function loadSheetOnce(force=false) {
  const now = Date.now();
  if (!force && activeSheet && (now - sheetLastLoadedAt) < 1500) return;
  const sheet = await apiGet("/sheet/mine");
  activeSheet = sheet;
  sheetLastLoadedAt = now;
  const updatedAt = Number((sheet && sheet.updated_at) || 0);
  if (updatedAt) _lastSheetUpdatedAt = updatedAt;
  renderSheetFromState(sheet);
  await pollRestStateOnce();
  await pollLevelUpStateOnce();
}

async function pollRestStateOnce() {
  try {
    const data = await apiGet("/rest/mine/status");
    activeRestState = (data && typeof data === "object") ? data : { active: false };

    if (activeSheet) {
      renderShortRestPanel(activeRestState, activeSheet);
    }
  } catch (e) {
    activeRestState = { active: false };
    if (activeSheet) {
      renderShortRestPanel(activeRestState, activeSheet);
    }
  }
}

async function pollLevelUpStateOnce() {
  try {
    const data = await apiGet("/levelup/mine/status");
    activeLevelUpState = (data && typeof data === "object") ? data : { active: false };
    if (activeSheet) renderLevelUpPanel(activeLevelUpState, activeSheet);
  } catch (e) {
    activeLevelUpState = { active: false };
    if (activeSheet) renderLevelUpPanel(activeLevelUpState, activeSheet);
  }
}

async function pollSheetStateOnce() {
  const now = Date.now();
  if ((now - _lastSheetRefreshTs) < 1200) return;
  _lastSheetRefreshTs = now;

  const sheet = await apiGet("/sheet/mine");
  const updatedAt = Number((sheet && sheet.updated_at) || 0);
  const effectsA = JSON.stringify((activeSheet && activeSheet.combat_effects) || []);
  const effectsB = JSON.stringify((sheet && sheet.combat_effects) || []);
  const shouldRender = !activeSheet || updatedAt !== _lastSheetUpdatedAt || effectsA !== effectsB;

  activeSheet = sheet;
  sheetLastLoadedAt = now;
  if (updatedAt) _lastSheetUpdatedAt = updatedAt;
  if (shouldRender) renderSheetFromState(sheet);
}

function refreshAbilityModsFromInputs() {
  const vals = {
    str: readInt("abStr", 10),
    dex: readInt("abDex", 10),
    con: readInt("abCon", 10),
    int: readInt("abInt", 10),
    wis: readInt("abWis", 10),
    cha: readInt("abCha", 10),
  };

  setText("modStr", fmtMod(vals.str));
  setText("modDex", fmtMod(vals.dex));
  setText("modCon", fmtMod(vals.con));
  setText("modInt", fmtMod(vals.int));
  setText("modWis", fmtMod(vals.wis));
  setText("modCha", fmtMod(vals.cha));
}


function renderCombatEffects(sheet) {
  const host = el("effectsList");
  if (!host) return;

  const explicitEffects = Array.isArray(sheet && sheet.combat_effects) ? [...sheet.combat_effects] : [];
  const combat = (sheet && typeof sheet.combat === "object" && sheet.combat) ? sheet.combat : {};

  // Derive always-important class/combat states so they are visible even if
  // the server did not explicitly write them into combat_effects.
  if (combat.rage_active) {
    const rageBonus = Number(combat.rage_damage_bonus || 0) || 0;
    const rageParts = ["resistance: bludgeoning, piercing, slashing"];
    if (rageBonus > 0) rageParts.push(`melee damage bonus: +${rageBonus}`);
    explicitEffects.unshift({
      name: "Rage",
      source: "barbarian",
      summary: rageParts.join(" • "),
    });
  }

  if (combat.reckless_attack_active) {
    explicitEffects.unshift({
      name: "Reckless Attack",
      source: "barbarian",
      summary: "Your melee attack rolls have advantage. Attack rolls against you also have advantage.",
    });
  }

  if (combat.patient_defense_active) {
    explicitEffects.unshift({
      name: "Patient Defense",
      source: "monk",
      summary: "Attack rolls against you are treated at disadvantage until your next turn.",
    });
  }
  if (combat.deflect_missiles_armed) {
    explicitEffects.unshift({
      name: "Deflect Missiles",
      source: "monk",
      summary: "Your reaction is armed to reduce the next ranged weapon attack damage against you.",
    });
  }
  if (combat.stunning_strike_armed) {
    explicitEffects.unshift({
      name: "Stunning Strike",
      source: "monk",
      summary: "Your next melee hit will force a Constitution save or stun the target.",
    });
  }
  if (combat.empty_body_active) {
    explicitEffects.unshift({
      name: "Empty Body",
      source: "monk",
      summary: "Broad invisibility and resistance to non-force damage are active.",
    });
  }

  // Feat active-toggle status indicators
  const featState2 = (sheet && sheet.feat_state && typeof sheet.feat_state === "object") ? sheet.feat_state : {};
  const feats2 = Array.isArray(sheet && sheet.feats) ? sheet.feats : [];
  if (feats2.includes("great_weapon_master") && featState2.great_weapon_master && featState2.great_weapon_master.enabled) {
    explicitEffects.unshift({
      name: "Great Weapon Master",
      source: "feat",
      summary: "-5 to hit / +10 damage active on heavy melee weapon attacks.",
    });
  }
  if (feats2.includes("sharpshooter") && featState2.sharpshooter && featState2.sharpshooter.enabled) {
    explicitEffects.unshift({
      name: "Sharpshooter",
      source: "feat",
      summary: "-5 to hit / +10 damage active on ranged weapon attacks.",
    });
  }

  if (!explicitEffects.length) {
    host.textContent = "No active effects.";
    return;
  }

  host.innerHTML = "";
  for (const fx of explicitEffects) {
    const row = document.createElement("div");
    row.className = "noteRow";
    const bits = [];
    if (fx.source) bits.push(`source: ${fx.source}`);
    if (fx.summary) bits.push(String(fx.summary));
    else if (fx.rounds_remaining !== null && fx.rounds_remaining !== undefined) bits.push(`${fx.rounds_remaining} rounds left`);

    row.innerHTML =
      `<div class="noteTitle">${escapeHtml(fx.name || "Effect")}</div>` +
      `<div class="noteMeta mono">${escapeHtml(bits.join(" • ") || "active")}</div>`;

    host.appendChild(row);
  }
}

function renderSheetFromState(sheet) {
  if (!sheet) return;
  const creating = isCreatingSheet(sheet);

  const lc = sheet.lifecycle || {};
  setSheetStatusText(`State: ${lc.status || "active"}`);

  const pb = (sheet._derived && sheet._derived.proficiency_bonus) ? sheet._derived.proficiency_bonus : "—";
  setText("sheetProfBonus", pb);

  writeVal("sheetName", sheet.display_name || "");
  writeVal("sheetClass", (sheet.meta && sheet.meta.class) ? sheet.meta.class : "");
  writeVal("sheetRace", (sheet.meta && sheet.meta.race) ? sheet.meta.race : "");
  writeVal("sheetBackground", (sheet.meta && sheet.meta.background) ? sheet.meta.background : "");
  writeVal("sheetLevel", (sheet.meta && sheet.meta.level) ? sheet.meta.level : 1);

  const st = sheet.stats || {};
  const res = sheet.resources || {};
  writeVal("sheetMaxHp", st.max_hp || 0);
  writeVal("sheetCurHp", (st.current_hp !== undefined) ? st.current_hp : (res.current_hp ?? 0));
  writeVal("sheetTempHp", res.temp_hp ?? 0);

  const ab = sheet.abilities || {};
  const abilityMap = {
    "abStr": ab.str, "abDex": ab.dex, "abCon": ab.con,
    "abInt": ab.int, "abWis": ab.wis, "abCha": ab.cha
  };
  for (const [id, val] of Object.entries(abilityMap)) writeVal(id, val ?? 10);

  setText("modStr", fmtMod(ab.str ?? 10));
  setText("modDex", fmtMod(ab.dex ?? 10));
  setText("modCon", fmtMod(ab.con ?? 10));
  setText("modInt", fmtMod(ab.int ?? 10));
  setText("modWis", fmtMod(ab.wis ?? 10));
  setText("modCha", fmtMod(ab.cha ?? 10));

  refreshAbilityModsFromInputs();
  
  renderProficiencyGrids(sheet);
  renderOtherProficiencies(sheet);
  renderLanguages(sheet);

  renderCombatPanel(sheet);
  renderCombatEffects(sheet);
  renderCurrency(sheet);
  renderBackground(sheet);
  renderFeatures(sheet);
  renderShortRestPanel(activeRestState, sheet);
  renderLevelUpPanel(activeLevelUpState, sheet);
  renderAbilityActions(sheet);
  renderSpellcasting(sheet);
  renderDetails(sheet);
  renderInventory(sheet);
  renderNotesFromState(sheet);
  setNoteEditingBanner();

  const creatingOnlyDisabled = !creating;
  setInputDisabled("sheetName", creatingOnlyDisabled);
  setInputDisabled("sheetClass", creatingOnlyDisabled);
  setInputDisabled("sheetRace", creatingOnlyDisabled);
  setInputDisabled("sheetBackground", creatingOnlyDisabled);
  setInputDisabled("sheetLevel", creatingOnlyDisabled);
  for (const id of ["abStr","abDex","abCon","abInt","abWis","abCha"]) setInputDisabled(id, creatingOnlyDisabled);

  setInputDisabled("sheetCurHp", false);
  setInputDisabled("sheetTempHp", false);
  setInputDisabled("sheetMaxHp", !creating);

  // Creation-only fields
  // NOTE: languages must remain editable post-finalize because they gate handout readability.
  const creatingOnlyDisabled2 = !creating;
  for (const id of ["profArmor","profWeapons","profTools","profOther"]) {
    const n = el(id);
    if (!n) continue;
    n.disabled = creatingOnlyDisabled2;
  }

  const btnFinalize = el("btnFinalize");
  if (btnFinalize) btnFinalize.disabled = !creating;

  setSheetSaveStatus("Saved.", "ok");
}

function renderOtherProficiencies(sheet) {
  const creating = isCreatingSheet(sheet);
  const prof = (sheet && sheet.proficiencies) ? sheet.proficiencies : {};
  const other = (prof && typeof prof.other === "object" && prof.other) ? prof.other : {};
  writeVal("profArmor", other.armor || "");
  writeVal("profWeapons", other.weapons || "");
  writeVal("profTools", other.tools || "");
  writeVal("profOther", other.other || "");
  for (const id of ["profArmor","profWeapons","profTools","profOther"]) setInputDisabled(id, !creating);
}

function renderLanguages(sheet) {
  // Languages are always editable because they affect play / readability.
  const host = el("langList");
  if (!host) return;

  const prof = (sheet && typeof sheet.proficiencies === "object" && sheet.proficiencies)
    ? sheet.proficiencies
    : {};

  const langs = Array.isArray(prof.languages) ? prof.languages : [];

  host.innerHTML = "";

  if (!langs.length) {
    const empty = document.createElement("div");
    empty.className = "hint mono";
    empty.textContent = "No languages set.";
    host.appendChild(empty);
    return;
  }

  langs.forEach((L, idx) => {
    const row = document.createElement("div");
    row.className = "langRow";

    const name = document.createElement("input");
    name.className = "inp";
    name.value = (L && L.name) ? String(L.name) : "";
    name.onchange = async () => {
      const next = langs.map((x, i) =>
        i === idx
          ? {
              ...x,
              name: (name.value || "").trim(),
            }
          : x
      );
      await _saveLanguages(next);
    };
    row.appendChild(name);

    const mk = (label, key) => {
      const wrap = document.createElement("label");
      wrap.className = "mini";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!(L && L[key]);
      cb.onchange = async () => {
        const next = langs.map((x, i) =>
          i === idx
            ? {
                ...x,
                [key]: !!cb.checked,
              }
            : x
        );
        await _saveLanguages(next);
      };

      wrap.appendChild(cb);
      wrap.appendChild(document.createTextNode(" " + label));
      return wrap;
    };

    row.appendChild(mk("Speak", "speak"));
    row.appendChild(mk("Read", "read"));
    row.appendChild(mk("Write", "write"));

    const del = document.createElement("button");
    del.className = "btn danger";
    del.textContent = "×";
    del.onclick = async () => {
      const next = langs.filter((_, i) => i !== idx);
      await _saveLanguages(next);
    };
    row.appendChild(del);

    host.appendChild(row);
  });
}

function renderCombatPanel(sheet) {
  const ab = sheet.abilities || {};
  const st = sheet.stats || {};
  const cb = (sheet.combat && typeof sheet.combat === "object") ? sheet.combat : {};

  setText("outAC", (st.defense !== undefined) ? String(st.defense) : "—");
  setText("outSpeed", (st.movement_ft !== undefined) ? String(st.movement_ft) : "—");

  const dexMod = modInt(ab.dex ?? 10);
  setText("outInit", (dexMod >= 0 ? "+" : "") + String(dexMod));

  setText(
    "outAttacksPerAction",
    String((cb.attacks_per_action !== undefined) ? cb.attacks_per_action : 1)
  );

  const prof = sheet.proficiencies || {};
  const skills = (prof && typeof prof.skills === "object" && prof.skills) ? prof.skills : {};
  const wisMod = modInt(ab.wis ?? 10);
  const percProf = !!skills["perception"];
  const pb = Number((sheet._derived && sheet._derived.proficiency_bonus) ? sheet._derived.proficiency_bonus : 0) || 0;
  const passive = 10 + wisMod + (percProf ? pb : 0);
  setText("outPassivePerception", String(passive));

  const insp = el("combatInspiration");
  if (insp) insp.checked = !!cb.inspiration;

  writeVal("hitDieSides", cb.hit_die_sides ?? 8);
  writeVal("hitDiceTotal", cb.hit_dice_total ?? 1);
  writeVal("hitDiceUsed", cb.hit_dice_used ?? 0);

  const ds = (cb.death_saves && typeof cb.death_saves === "object") ? cb.death_saves : {};
  writeVal("dsSucc", ds.successes ?? 0);
  writeVal("dsFail", ds.failures ?? 0);

  setInputDisabled("hitDieSides", true);
  setInputDisabled("hitDiceTotal", true);
  setInputDisabled("hitDiceUsed", true);
}

async function _saveLanguages(langs) {
  try {
    const clean = Array.isArray(langs)
      ? langs.map(x => ({
          name: String(x?.name || "").trim(),
          speak: !!x?.speak,
          read: !!x?.read,
          write: !!x?.write,
        })).filter(x => x.name)
      : [];

    await apiPatch("/sheet/mine", {
      patch: {
        proficiencies: {
          languages: clean,
        },
      },
    });

    await loadSheetOnce(true);
    setSheetSaveStatus("Saved.", "ok");
  } catch (e) {
    console.warn("[LANG] save failed", e);
    setSheetSaveStatus("Save failed.", "err");
  }
}


function renderCurrency(sheet) {
  const cur = (sheet.currency && typeof sheet.currency === "object") ? sheet.currency : {};
  writeVal("curCp", cur.cp ?? 0);
  writeVal("curSp", cur.sp ?? 0);
    writeVal("curGp", cur.gp ?? 0);
  }

function renderBackground(sheet) {
  const bg = (sheet.background && typeof sheet.background === "object") ? sheet.background : {};
  writeVal("bgTraits", bg.personality_traits || "");
  writeVal("bgIdeals", bg.ideals || "");
  writeVal("bgBonds", bg.bonds || "");
  writeVal("bgFlaws", bg.flaws || "");
  writeVal("bgBackstory", bg.backstory || "");
}

function renderShortRestPanel(restState, sheet) {
  activeRestState = restState && typeof restState === "object" ? restState : null;
  const panel = el("panelShortRest");
  const host = el("shortRestPanel");
  if (!panel || !host) return;

  const st = activeRestState || {};
  const isActive = !!st.active && String(st.rest_type || "") === "short_rest";
  if (!isActive) {
    panel.classList.add("hidden");
    host.innerHTML = "";
    return;
  }

  panel.classList.remove("hidden");
  const resources = (sheet && typeof sheet.resources === "object") ? sheet.resources : {};
  const remaining = Number(resources.hit_dice_remaining ?? 0);
  const participantCount = Number(st.participant_count ?? 0);
  const doneCount = Number(st.done_count ?? 0);
  const done = !!st.done;
  const participants = Array.isArray(st.participants) ? st.participants : [];

  host.innerHTML = `
    <div class="shortRestPanel">
      <div class="srTop">
        <div class="abilityActionName">Short Rest Active</div>
        <div class="abilityActionDesc">Choose how many hit dice to spend, roll them here, then mark yourself done when finished.</div>
        <div class="srState">Hit Dice Remaining: ${remaining} • Participants Done: ${doneCount}/${participantCount}</div>
      </div>
      <div class="srControls">
        <input id="srSpendDice" class="inp mono" type="number" min="1" step="1" value="1" />
        <button id="btnShortRestRoll" class="btn" type="button" ${done ? 'disabled' : ''}>Roll Hit Dice</button>
        <button id="btnShortRestDone" class="btn" type="button" ${done ? 'disabled' : ''}>Done with Short Rest</button>
        <button id="btnShortRestUndo" class="btn" type="button" ${done ? '' : 'disabled'}>Undo Done</button>
      </div>
      <div class="srParticipants">
        ${participants.map(p => `<div class="srParticipant">${escapeHtml(p.character_id || '')} — ${p.done ? 'Done' : 'Not done'}</div>`).join('')}
      </div>
    </div>
  `;

  const spendEl = el("srSpendDice");
  const rollBtn = el("btnShortRestRoll");
  const doneBtn = el("btnShortRestDone");
  const undoBtn = el("btnShortRestUndo");

  if (rollBtn) rollBtn.onclick = async () => {
    try {
      const spend = Math.max(1, parseInt((spendEl && spendEl.value) || '1', 10) || 1);
      const out = await apiPost('/rest/mine/request_hit_dice', { spend_hit_dice: spend });
      await pollRollRequestsOnce();
      updateRollRequestUI();
      updateRollControls();
      const pending = out && out.pending_roll ? out.pending_roll : null;
      const label = (pending && pending.label) ? pending.label : `Short Rest Hit Dice (${spend})`;
      setStatus(`${label} requested. Roll in the roll area.`, 'ok');
    } catch (e) {
      console.error('[SHORT_REST] request roll failed', e);
      setStatus(`Short rest roll request failed: ${e}`, 'err');
    }
  };

  if (doneBtn) doneBtn.onclick = async () => {
    try {
      const out = await apiPost('/rest/mine/done', {});
      await loadSheetOnce(true);
      await pollRestStateOnce();
      setStatus(out && out.auto_resolved ? 'Short rest complete for all participants.' : 'Marked done with short rest.', 'ok');
    } catch (e) {
      console.error('[SHORT_REST] done failed', e);
      setStatus(`Short rest done failed: ${e}`, 'err');
    }
  };

  if (undoBtn) undoBtn.onclick = async () => {
    try {
      await apiPost('/rest/mine/undo_done', {});
      await loadSheetOnce(true);
      await pollRestStateOnce();
      setStatus('Short rest reopened for this character.', 'ok');
    } catch (e) {
      console.error('[SHORT_REST] undo failed', e);
      setStatus(`Undo short rest failed: ${e}`, 'err');
    }
  };
}


function renderLevelUpPanel(state, sheet) {
  const card = el("panelLevelUp");
  const host = el("levelUpPanel");
  if (!card || !host) return;

  const clearUi = () => {
    card.classList.add("hidden");
    host.innerHTML = "";
    activeLevelUpSignature = "";
  };

  if (!state || !state.active) {
    levelUpAwaitingRoll = false;
    clearUi();
    return;
  }

  const target = Number(state.target_level || ((sheet.meta && sheet.meta.level) || 1));
  const className = state.class_name || ((sheet.meta && sheet.meta.class) || "Class");
  const requiresAsi = !!state.requires_asi;

  const currentClass = (sheet && sheet.meta && sheet.meta.class)
    ? escapeHtml(sheet.meta.class)
    : escapeHtml(className);

  const currentLevel = Number((sheet && sheet.meta && sheet.meta.level) || Math.max(1, target - 1));
  const currentMaxHp = Number(((sheet && sheet.stats) ? sheet.stats.max_hp : 0) || 0);
  const hitDieSides = Number(((sheet && sheet.combat) ? sheet.combat.hit_die_sides : 0) || 0);
  const hpMethodName = hitDieSides > 0 ? `d${hitDieSides}` : `class hit die`;
  const choiceSummary = (state && state.choices && typeof state.choices === "object") ? state.choices : {};
  const pendingChoices = (choiceSummary && typeof choiceSummary.pending === "object") ? choiceSummary.pending : {};
  const pendingRows = [
    ["Cantrips", Number(pendingChoices.cantrips || 0)],
    ["Known Spells", Number(pendingChoices.known_spells || 0)],
    ["Spellbook", Number(pendingChoices.spellbook_spells || 0)],
    ["Bonus Spells", Number(pendingChoices.bonus_spell_ids || 0)],
    ["Metamagic", Number(pendingChoices.metamagic_options || 0)],
  ].filter(([, v]) => v > 0);

  const signature = JSON.stringify({
    target,
    className,
    requiresAsi,
    cid: activeCharacterId || "",
    awaiting: !!levelUpAwaitingRoll,
    pendingChoices,
  });

  if (signature === activeLevelUpSignature) {
    card.classList.remove("hidden");
    return;
  }
  activeLevelUpSignature = signature;

  if (levelUpAwaitingRoll) {
    host.innerHTML = `
      <div class="levelUpInline">
        <div class="levelUpTop">
          <div class="levelUpTopText">
            <div class="levelUpTitle">Level Up Pending Roll</div>
            <div class="levelUpSub">${currentClass} will advance from level ${currentLevel} to level ${target} after you roll HP in the main roll area.</div>
          </div>
        </div>

        <div class="levelUpGrid">
          <div class="levelUpChip">
            <span class="levelUpChipLabel">Class</span>
            <span class="levelUpChipValue">${currentClass}</span>
          </div>
          <div class="levelUpChip">
            <span class="levelUpChipLabel">Current Level</span>
            <span class="levelUpChipValue">${currentLevel}</span>
          </div>
          <div class="levelUpChip">
            <span class="levelUpChipLabel">Target Level</span>
            <span class="levelUpChipValue">${target}</span>
          </div>
          <div class="levelUpChip">
            <span class="levelUpChipLabel">Hit Die</span>
            <span class="levelUpChipValue">${escapeHtml(hpMethodName)}</span>
          </div>
        </div>

        <div class="levelUpBlock">
          <div class="levelUpBlockTitle">Waiting For Roll</div>
          <div class="levelUpWait">
            Roll in the normal roll area now. The server will apply the result, add CON automatically, and finish the level up.
          </div>
        </div>
      </div>
    `;
    card.classList.remove("hidden");
    return;
  }

  host.innerHTML = `
    <div class="levelUpInline">
      <div class="levelUpTop">
        <div class="levelUpTopText">
          <div class="levelUpTitle">Level Up</div>
          <div class="levelUpSub">${currentClass} will advance from level ${currentLevel} to level ${target}. Choose any required options below, then either roll your hit die in the roll area or take average HP.</div>
        </div>
        <div class="levelUpActions">
          <button id="btnApplyLevelUp" class="btn ok" type="button">Confirm Level Up</button>
        </div>
      </div>

      <div class="levelUpGrid">
        <div class="levelUpChip">
          <span class="levelUpChipLabel">Class</span>
          <span class="levelUpChipValue">${currentClass}</span>
        </div>
        <div class="levelUpChip">
          <span class="levelUpChipLabel">Current Level</span>
          <span class="levelUpChipValue">${currentLevel}</span>
        </div>
        <div class="levelUpChip">
          <span class="levelUpChipLabel">Target Level</span>
          <span class="levelUpChipValue">${target}</span>
        </div>
        <div class="levelUpChip">
          <span class="levelUpChipLabel">Current Max HP</span>
          <span class="levelUpChipValue">${currentMaxHp}</span>
        </div>
      </div>

      ${
        requiresAsi
          ? `
            <div class="levelUpBlock">
              <div class="levelUpBlockTitle">Ability Score Improvement</div>
              <div class="levelUpBlockText">Choose two +1 ability score increases. You may choose the same ability twice.</div>
              <div class="levelUpFields">
                <div class="field">
                  <label class="lbl">Increase 1</label>
                  <select id="levelUpA" class="inp">
                    <option value="str">STR</option>
                    <option value="dex">DEX</option>
                    <option value="con">CON</option>
                    <option value="int">INT</option>
                    <option value="wis">WIS</option>
                    <option value="cha">CHA</option>
                  </select>
                </div>
                <div class="field">
                  <label class="lbl">Increase 2</label>
                  <select id="levelUpB" class="inp">
                    <option value="str">STR</option>
                    <option value="dex">DEX</option>
                    <option value="con">CON</option>
                    <option value="int">INT</option>
                    <option value="wis">WIS</option>
                    <option value="cha">CHA</option>
                  </select>
                </div>
              </div>
            </div>
          `
          : `
            <div class="levelUpBlock">
              <div class="levelUpBlockTitle">Ability Score Improvement</div>
              <div class="levelUpBlockText">No ability score increase choice is required at this level.</div>
            </div>
          `
      }

      <div class="levelUpBlock">
        <div class="levelUpBlockTitle">Class Choices</div>
        ${
          pendingRows.length
            ? `
              <div class="levelUpBlockText">Finish these spellcasting/class choices before confirming the level up.</div>
              <div class="levelUpGrid">
                ${pendingRows.map(([label, amt]) => `
                  <div class="levelUpChip">
                    <span class="levelUpChipLabel">${escapeHtml(label)}</span>
                    <span class="levelUpChipValue">${amt}</span>
                  </div>
                `).join("")}
              </div>
              <div class="levelUpActions" style="margin-top:10px;">
                <button id="btnLevelUpOpenCantrips" class="btn ghost small" type="button">Cantrips</button>
                <button id="btnLevelUpOpenKnown" class="btn ghost small" type="button">Known</button>
                <button id="btnLevelUpOpenPrepared" class="btn ghost small" type="button">Prepared</button>
                <button id="btnLevelUpOpenSpellbook" class="btn ghost small" type="button">Spellbook</button>
                <button id="btnLevelUpOpenBonus" class="btn ghost small" type="button">Bonus</button>
                <button id="btnLevelUpOpenMetamagic" class="btn ghost small" type="button">Metamagic</button>
              </div>
            `
            : `<div class="levelUpBlockText">No additional spell or class selections are required before applying this level.</div>`
        }
      </div>

      <div class="levelUpBlock">
        <div class="levelUpBlockTitle">HP Gain</div>
        <div class="levelUpFields">
          <div class="field">
            <label class="lbl">HP Gain Method</label>
            <select id="levelUpHpMethod" class="inp">
              <option value="average">Take Average</option>
              <option value="roll">Roll ${escapeHtml(hpMethodName)}</option>
            </select>
          </div>
          <div class="field">
            <label class="lbl">Roll Rule</label>
            <div class="inp readOnlyLike">If you choose roll, the main roll area will handle it. CON is added automatically after the roll.</div>
          </div>
        </div>
      </div>
    </div>
  `;

  [["#btnLevelUpOpenCantrips","cantrips"],["#btnLevelUpOpenKnown","known"],["#btnLevelUpOpenPrepared","prepared"],["#btnLevelUpOpenSpellbook","spellbook"],["#btnLevelUpOpenBonus","bonus"],["#btnLevelUpOpenMetamagic","metamagic"]].forEach(([sel, kind]) => {
    const b = host.querySelector(sel);
    if (b) b.onclick = () => openSpellChoiceModal(kind);
  });

  const btn = host.querySelector("#btnApplyLevelUp");
  if (btn) {
    btn.onclick = async () => {
      try {
        btn.disabled = true;

        const payload = requiresAsi
          ? {
              increase_a: (host.querySelector("#levelUpA") && host.querySelector("#levelUpA").value)
                ? host.querySelector("#levelUpA").value
                : "",
              increase_b: (host.querySelector("#levelUpB") && host.querySelector("#levelUpB").value)
                ? host.querySelector("#levelUpB").value
                : "",
            }
          : {};

        payload.hp_gain_method =
          (host.querySelector("#levelUpHpMethod") && host.querySelector("#levelUpHpMethod").value)
            ? host.querySelector("#levelUpHpMethod").value
            : "average";

        const out = await apiPost("/levelup/mine/submit", payload);

        if (out && out.pending_roll) {
          levelUpAwaitingRoll = true;
          activeLevelUpSignature = "";
          await pollRollRequestsOnce();
          updateRollRequestUI();
          updateRollControls();
          renderLevelUpPanel(state, activeSheet || sheet);
          const pending = out.pending_roll || {};
          setStatus(`${pending.label || "Level-up HP roll"} requested. Roll in the roll area.`, "ok");
          return;
        }

        const fresh = (out && out.sheet) ? out.sheet : await apiGet("/sheet/mine");
        activeSheet = fresh;
        sheetLastLoadedAt = Date.now();
        levelUpAwaitingRoll = false;
        renderSheetFromState(fresh);
        await pollLevelUpStateOnce();
        setStatus(`Level up applied. You are now level ${((fresh.meta || {}).level) || target}.`, "ok");
      } catch (e) {
        console.error("[LEVELUP] submit failed", e);
        setStatus(`Level up failed: ${e}`, "err");
      } finally {
        const b = host.querySelector("#btnApplyLevelUp");
        if (b) b.disabled = false;
      }
    };
  }

  card.classList.remove("hidden");
}

function renderAbilityActions(sheet) {
  const host = el("abilityActions");
  if (!host) return;
  host.innerHTML = "";

  const abilityIds = Array.isArray(sheet.ability_ids) ? sheet.ability_ids : [];
  const pools = (sheet.resource_pools && typeof sheet.resource_pools === "object") ? sheet.resource_pools : {};
  const combat = (sheet.combat && typeof sheet.combat === "object") ? sheet.combat : {};
  const supported = [];

  if (abilityIds.includes("fighter_second_wind")) {
    const p = pools.second_wind || {};
    supported.push({ id: "fighter_second_wind", name: "Second Wind", desc: "Use a bonus action to regain hit points. This runtime is wired through the server.", state: `Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use" });
  }
  if (abilityIds.includes("fighter_action_surge")) {
    const p = pools.action_surge || {};
    supported.push({ id: "fighter_action_surge", name: "Action Surge", desc: "Spend your Action Surge use. The engine is only tracking usage right now; it is not enforcing extra-action economy.", state: `Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use" });
  }
  if (abilityIds.includes("fighter_indomitable")) {
    const p = pools.indomitable || {};
    supported.push({ id: "fighter_indomitable", name: "Indomitable", desc: "Spend an Indomitable use when you reroll a failed saving throw. The server is tracking the use count right now; save reroll enforcement is still manual.", state: `Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use" });
  }
  if (abilityIds.includes("barbarian_rage")) {
    const p = pools.rage || {};
    const active = !!combat.rage_active;
    const dmgBonus = Number(combat.rage_damage_bonus ?? 0) || 0;
    supported.push({ id: "barbarian_rage", name: "Rage", desc: "Toggle rage on or off. While active, the runtime grants bludgeoning, piercing, and slashing resistance.", state: `${active ? "Active" : "Inactive"} • Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""} • Rage Damage: +${dmgBonus}`, btn: active ? "End Rage" : "Use" });
  }
  if (abilityIds.includes("barbarian_reckless_attack")) {
    const active = !!combat.reckless_attack_active;
    supported.push({ id: "barbarian_reckless_attack", name: "Reckless Attack", desc: "Toggle reckless attack tracking on or off.", state: `${active ? "Marked Active" : "Inactive"}`, btn: active ? "Clear" : "Mark" });
  }
  if (abilityIds.includes("rogue_cunning_action")) {
    const declared = String(combat.cunning_action_declared || '').trim();
    supported.push({ id: "rogue_cunning_action", name: "Cunning Action", desc: "Declare Dash, Disengage, or Hide as your rogue bonus-action utility.", state: declared ? `Declared: ${humanizeFeatureId(declared)}` : "No declaration set", btn: "Set", modeSelect: ["dash", "disengage", "hide"] });
  }
  if (abilityIds.includes("rogue_uncanny_dodge")) {
    const active = !!combat.uncanny_dodge_armed;
    supported.push({ id: "rogue_uncanny_dodge", name: "Uncanny Dodge", desc: "Arm or clear your reaction to halve the damage from the next attack that hits you.", state: `${active ? "Armed" : "Inactive"}`, btn: active ? "Clear" : "Arm" });
  }
  if (abilityIds.includes("rogue_stroke_of_luck")) {
    const p = pools.stroke_of_luck || {};
    const active = !!combat.stroke_of_luck_armed;
    supported.push({ id: "rogue_stroke_of_luck", name: "Stroke of Luck", desc: "Arm Stroke of Luck so your next attack roll or ability check can be treated as a 20.", state: `${active ? "Armed" : "Inactive"} • Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: active ? "Clear" : "Arm" });
  }
  if (abilityIds.includes("monk_flurry_of_blows")) {
    const p = pools.ki || {};
    supported.push({ id: "monk_flurry_of_blows", name: "Flurry of Blows", desc: "Spend 1 ki to declare Flurry of Blows. Extra attacks remain player-tracked.", state: `Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use" });
  }
  if (abilityIds.includes("monk_patient_defense")) {
    const p = pools.ki || {};
    const active = !!combat.patient_defense_active;
    supported.push({ id: "monk_patient_defense", name: "Patient Defense", desc: "Spend 1 ki to enter a defensive stance until your next turn.", state: `${active ? "Active" : "Inactive"} • Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}`, btn: "Use" });
  }
  if (abilityIds.includes("monk_step_of_the_wind")) {
    const p = pools.ki || {};
    const declared = String(combat.step_of_the_wind_mode || '').trim();
    supported.push({ id: "monk_step_of_the_wind", name: "Step of the Wind", desc: "Spend 1 ki to declare Dash or Disengage as monk mobility.", state: declared ? `Declared: ${humanizeFeatureId(declared)} • Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}` : `Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}`, btn: "Use", modeSelect: ["dash", "disengage"] });
  }
  if (abilityIds.includes("monk_deflect_missiles")) {
    const active = !!combat.deflect_missiles_armed;
    supported.push({ id: "monk_deflect_missiles", name: "Deflect Missiles", desc: "Arm or clear your reaction to reduce the next ranged weapon attack damage against you.", state: active ? "Armed" : "Inactive", btn: active ? "Clear" : "Arm" });
  }
  if (abilityIds.includes("monk_stunning_strike")) {
    const p = pools.ki || {};
    const active = !!combat.stunning_strike_armed;
    supported.push({ id: "monk_stunning_strike", name: "Stunning Strike", desc: "Arm Stunning Strike for your next melee hit after spending 1 ki.", state: `${active ? "Armed" : "Inactive"} • Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}`, btn: active ? "Clear" : "Arm" });
  }
  if (abilityIds.includes("monk_stillness_of_mind")) {
    supported.push({ id: "monk_stillness_of_mind", name: "Stillness of Mind", desc: "Clear broadly tracked charmed or frightened effects from your sheet.", state: "Action", btn: "Use" });
  }
  if (abilityIds.includes("monk_diamond_soul")) {
    const p = pools.ki || {};
    supported.push({ id: "monk_diamond_soul", name: "Diamond Soul", desc: "Spend 1 ki to declare a reroll of a failed saving throw.", state: `Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}`, btn: "Use" });
  }
  if (abilityIds.includes("monk_empty_body")) {
    const p = pools.ki || {};
    const active = !!combat.empty_body_active;
    supported.push({ id: "monk_empty_body", name: "Empty Body", desc: "Spend 4 ki to become broadly invisible and resistant to all damage except force until ended.", state: `${active ? "Active" : "Inactive"} • Ki: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}`, btn: active ? "End" : "Use" });
  }
  if (abilityIds.includes("paladin_divine_sense")) {
    const p = pools.divine_sense || {};
    const active = !!combat.divine_sense_active;
    supported.push({ id: "paladin_divine_sense", name: "Divine Sense", desc: "Track a use of Divine Sense.", state: `${active ? "Marked Active" : "Inactive"} • Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use" });
  }
  if (abilityIds.includes("paladin_lay_on_hands")) {
    const p = pools.lay_on_hands || {};
    supported.push({ id: "paladin_lay_on_hands", name: "Lay on Hands", desc: "Spend points from the pool to restore HP.", state: `Pool: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use", amountInput: true, amountValue: 1, targetInput: true });
  }
  if (abilityIds.includes("paladin_cleansing_touch")) {
    const p = pools.cleansing_touch || {};
    supported.push({ id: "paladin_cleansing_touch", name: "Cleansing Touch", desc: "Track a use of Cleansing Touch.", state: `Uses: ${Number(p.current ?? 0)}/${Number(p.max ?? 0)}${p.refresh ? ` (${humanizeFeatureId(p.refresh)})` : ""}`, btn: "Use" });
  }

  // --- Feat active toggles (GWM, Sharpshooter) ---
  // These read from sheet.feats[] and sheet.feat_state[feat_id].enabled
  // The combat engine already reads feat_state on the server; this just gives the player the UX to flip it.
  const feats = Array.isArray(sheet.feats) ? sheet.feats : [];
  const featState = (sheet.feat_state && typeof sheet.feat_state === "object") ? sheet.feat_state : {};

  if (feats.includes("great_weapon_master")) {
    const enabled = !!(featState.great_weapon_master && featState.great_weapon_master.enabled);
    supported.push({
      id: "feat_toggle_great_weapon_master",
      name: "Great Weapon Master",
      desc: "Toggle -5 to hit / +10 damage for heavy melee weapon attacks. The engine reads this during attack resolution.",
      state: enabled ? "Active — -5 to hit, +10 damage" : "Inactive — normal attack rolls",
      btn: enabled ? "Disable" : "Enable",
      _featToggle: "great_weapon_master",
      _featEnabled: enabled,
    });
  }

  if (feats.includes("sharpshooter")) {
    const enabled = !!(featState.sharpshooter && featState.sharpshooter.enabled);
    supported.push({
      id: "feat_toggle_sharpshooter",
      name: "Sharpshooter",
      desc: "Toggle -5 to hit / +10 damage for ranged weapon attacks. The engine reads this during attack resolution.",
      state: enabled ? "Active — -5 to hit, +10 damage" : "Inactive — normal attack rolls",
      btn: enabled ? "Disable" : "Enable",
      _featToggle: "sharpshooter",
      _featEnabled: enabled,
    });
  }

  for (const ab of supported) {
    const card = document.createElement("div");
    card.className = "abilityActionCard";
    card.innerHTML = `
      <div class="abilityActionMeta">
        <div class="abilityActionName">${ab.name}</div>
        <div class="abilityActionDesc">${ab.desc}</div>
        <div class="abilityActionState">${ab.state}</div>
      </div>
      <div class="abilityActionBtns"></div>
    `;
    const btnWrap = card.querySelector('.abilityActionBtns');
    let amountEl = null;
    let targetEl = null;
    let modeEl = null;
    if (Array.isArray(ab.modeSelect) && ab.modeSelect.length) {
      modeEl = document.createElement('select');
      modeEl.className = 'inp';
      modeEl.style.width = '140px';
      for (const opt of ab.modeSelect) {
        const o = document.createElement('option');
        o.value = String(opt);
        o.textContent = humanizeFeatureId(opt);
        const currentMode = (ab.id === 'rogue_cunning_action') ? String(combat.cunning_action_declared || '').trim() : String(combat.step_of_the_wind_mode || '').trim();
        if (String(opt) === currentMode) o.selected = true;
        modeEl.appendChild(o);
      }
      btnWrap.appendChild(modeEl);
    }
    if (ab.amountInput) {
      amountEl = document.createElement('input');
      amountEl.type = 'number';
      amountEl.min = '1';
      amountEl.step = '1';
      amountEl.value = String(ab.amountValue || 1);
      amountEl.className = 'inp';
      amountEl.style.width = '76px';
      amountEl.title = 'Amount';
      btnWrap.appendChild(amountEl);
    }
    if (ab.targetInput) {
      targetEl = document.createElement('input');
      targetEl.type = 'text';
      targetEl.placeholder = 'target id (optional)';
      targetEl.className = 'inp';
      targetEl.style.width = '150px';
      btnWrap.appendChild(targetEl);
    }
    const btn = document.createElement('button');
    btn.className = 'btn';
    btn.textContent = ab.btn;
    btn.onclick = async () => {
      try {
        const payload = { ability_id: ab.id };
        if (amountEl) payload.amount = Math.max(1, parseInt(amountEl.value || '1', 10) || 1);
        if (modeEl && String(modeEl.value || '').trim()) payload.mode = String(modeEl.value || '').trim();
        if (targetEl && String(targetEl.value || '').trim()) payload.target_character_id = String(targetEl.value || '').trim();
        const out = await apiPost('/abilities/mine/use', payload);
        const fresh = (out && out.sheet) ? out.sheet : await apiGet('/sheet/mine');
        activeSheet = fresh;
        sheetLastLoadedAt = Date.now();
        renderSheetFromState(fresh);
        if (ab.id === 'fighter_second_wind' && out && out.heal !== undefined) setStatus(`Second Wind restored ${out.heal} HP.`, 'ok');
        else if (ab.id === 'fighter_action_surge') setStatus('Action Surge marked used.', 'ok');
        else if (ab.id === 'fighter_indomitable') setStatus('Indomitable marked used.', 'ok');
        else if (ab.id === 'barbarian_rage') setStatus((out && out.active) ? 'Rage activated.' : 'Rage ended.', 'ok');
        else if (ab.id === 'barbarian_reckless_attack') setStatus((out && out.active) ? 'Reckless Attack marked active.' : 'Reckless Attack cleared.', 'ok');
        else if (ab.id === 'rogue_cunning_action') setStatus(`Cunning Action declared: ${humanizeFeatureId((out && out.declared) || (payload.mode || 'dash'))}.`, 'ok');
        else if (ab.id === 'rogue_uncanny_dodge') setStatus((out && out.active) ? 'Uncanny Dodge armed.' : 'Uncanny Dodge cleared.', 'ok');
        else if (ab.id === 'rogue_stroke_of_luck') setStatus((out && out.active) ? 'Stroke of Luck armed.' : 'Stroke of Luck cleared.', 'ok');
        else if (ab.id === 'monk_flurry_of_blows') setStatus('Flurry of Blows declared and ki spent.', 'ok');
        else if (ab.id === 'monk_patient_defense') setStatus('Patient Defense activated.', 'ok');
        else if (ab.id === 'monk_step_of_the_wind') setStatus(`Step of the Wind declared: ${humanizeFeatureId((out && out.declared) || (payload.mode || 'disengage'))}.`, 'ok');
        else if (ab.id === 'monk_deflect_missiles') setStatus((out && out.active) ? 'Deflect Missiles armed.' : 'Deflect Missiles cleared.', 'ok');
        else if (ab.id === 'monk_stunning_strike') setStatus((out && out.active) ? 'Stunning Strike armed.' : 'Stunning Strike cleared.', 'ok');
        else if (ab.id === 'monk_stillness_of_mind') setStatus('Stillness of Mind applied.', 'ok');
        else if (ab.id === 'monk_diamond_soul') setStatus('Diamond Soul reroll declared and ki spent.', 'ok');
        else if (ab.id === 'monk_empty_body') setStatus((out && out.active) ? 'Empty Body activated.' : 'Empty Body ended.', 'ok');
        else if (ab.id === 'paladin_lay_on_hands') { const tgt = out && out.target_character_id ? ` on ${out.target_character_id}` : ''; setStatus(`Lay on Hands restored ${out.heal || 0} HP${tgt} (spent ${out.spent || 0}).`, 'ok'); }
      } catch (e) {
        console.error('[ABILITY_USE] failed', e);
        setStatus(`Ability failed: ${e}`, 'err');
      }
    };
    btnWrap.appendChild(btn);
    host.appendChild(card);
  }
}



function renderFeatures(sheet) {
  const host = document.getElementById("featTraits");
  if (!host) return;

  const ft = (sheet.features && typeof sheet.features === "object") ? sheet.features : {};
  const traitIds = Array.isArray(sheet.trait_ids) ? sheet.trait_ids : [];
  const abilityIds = Array.isArray(sheet.ability_ids) ? sheet.ability_ids : [];
  const resourcePools = (sheet.resource_pools && typeof sheet.resource_pools === "object")
    ? sheet.resource_pools
    : {};

  const esc = (s) => String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  const renderCards = (title, items) => {
    if (!items.length) return "";
    return `
      <div class="featureGroup">
        <div class="featureGroupTitle">${esc(title)}</div>
        ${items.map(item => `
          <div class="featureCard">
            <div class="featureName">${esc(item.name)}</div>
            <div class="featureDesc">${esc(item.desc)}</div>
          </div>
        `).join("")}
      </div>
    `;
  };

  const abilityCards = abilityIds.map(id => {
    const full = describeFeatureId(id);
    const parts = String(full).split(" — ");
    return {
      name: parts[0] || humanizeFeatureId(id),
      desc: parts.slice(1).join(" — ") || ""
    };
  });

  const traitCards = traitIds.map(id => {
    const full = describeFeatureId(id);
    const parts = String(full).split(" — ");
    return {
      name: parts[0] || humanizeFeatureId(id),
      desc: parts.slice(1).join(" — ") || ""
    };
  });

  const resourceCards = Object.entries(resourcePools).map(([k, v]) => {
    const cur = Number((v || {}).current ?? 0);
    const max = Number((v || {}).max ?? 0);
    const refresh = String((v || {}).refresh || "").trim();
    return {
      name: humanizeFeatureId(k),
      desc: `${cur}/${max}${refresh ? ` (${humanizeFeatureId(refresh)})` : ""}`
    };
  });

  let introBlock = "";
  const baseText = String(ft.features_and_traits || "").trim();
  if (baseText) {
    introBlock = `
      <div class="featureGroup">
        <div class="featureGroupTitle">Overview</div>
        <div class="featureCard">
          <div class="featureDesc">${esc(baseText)}</div>
        </div>
      </div>
    `;
  }

  host.innerHTML =
    introBlock +
    renderCards("Abilities", abilityCards) +
    renderCards("Traits", traitCards) +
    renderCards("Resources", resourceCards);
}

function spellSourceFor(sheet, spellId) {
  const sc = (sheet && typeof sheet.spellcasting === "object" && sheet.spellcasting) ? sheet.spellcasting : {};
  const cantrips = Array.isArray(sc.cantrips) ? sc.cantrips : String(sc.cantrips || "").split(",").map(x => x.trim()).filter(Boolean);
  const known = Array.isArray(sc.known_spells) ? sc.known_spells : String(sc.known_spells || "").split(",").map(x => x.trim()).filter(Boolean);
  const prepared = Array.isArray(sc.prepared_spells) ? sc.prepared_spells : String(sc.prepared_spells || "").split(",").map(x => x.trim()).filter(Boolean);
  const spellbook = Array.isArray(sc.spellbook_spells) ? sc.spellbook_spells : String(sc.spellbook_spells || "").split(",").map(x => x.trim()).filter(Boolean);
  if (cantrips.includes(spellId)) return "Cantrip";
  if (prepared.includes(spellId)) return "Prepared";
  if (known.includes(spellId)) return "Known";
  if (spellbook.includes(spellId)) return "Spellbook";
  return "Spell";
}

function spellIdListFromText(value) {
  return String(value || "").replace(/\r?\n/g, ",").split(",").map(v => v.trim()).filter(Boolean);
}

function availableSlotSources(sheet, sc, spell, slotLevel) {
  const opts = [{ value: "auto", label: "Auto" }];
  const desiredLevel = Number(slotLevel || 0) || 0;
  const slotOptions = Array.isArray(sc.slot_options) ? sc.slot_options : [];
  const seen = new Set(["auto"]);
  for (const row of slotOptions) {
    if (!row || typeof row !== "object") continue;
    const lvl = Number(row.slot_level || 0) || 0;
    const src = String(row.source || "").trim();
    if (!src || seen.has(src)) continue;
    if (desiredLevel > 0 && lvl > 0 && lvl < desiredLevel) continue;
    seen.add(src);
    opts.push({ value: src, label: `${src} (${lvl > 0 ? `Lv ${lvl}` : "slot"})` });
  }
  const pools = (sheet && typeof sheet.resource_pools === "object" && sheet.resource_pools) ? sheet.resource_pools : {};
  const spellLevel = Number((spell && spell.level) || 0) || 0;
  const arcanumPool = (spellLevel >= 6) ? (pools[`mystic_arcanum_${spellLevel}`] || {}) : {};
  const arcanumCur = Number(arcanumPool.current || 0) || 0;
  if (spellLevel >= 6 && arcanumCur > 0 && !seen.has("arcanum")) {
    opts.push({ value: "arcanum", label: `arcanum (Lv ${spellLevel})` });
  }
  return opts;
}

function availableSlotLevels(sc, spell) {
  const level = Number((spell && spell.level) || 0) || 0;
  if (level <= 0) return [0];
  const rows = (sc && typeof sc.spells === "object" && sc.spells) ? sc.spells : {};
  const out = [];
  for (let lvl = level; lvl <= 9; lvl++) {
    const row = (rows[String(lvl)] && typeof rows[String(lvl)] === "object") ? rows[String(lvl)] : {};
    const total = parseInt(row.total ?? 0, 10) || 0;
    const used = parseInt(row.used ?? 0, 10) || 0;
    if ((total - used) > 0) out.push(lvl);
  }
  if (!out.length) out.push(level);
  return out;
}

async function declareSpellCast(spellId, slotLevel, targetHint, notes, slotSource = "auto", metamagicOptions = []) {
  const payload = {
    spell_id: String(spellId || "").trim(),
    slot_level: Number(slotLevel || 0),
    consume_slot: Number(slotLevel || 0) > 0,
    slot_source: String(slotSource || "auto").trim() || "auto",
    metamagic_options: Array.isArray(metamagicOptions) ? metamagicOptions.map(v => String(v || "").trim()).filter(Boolean) : [],
    target_hint: String(targetHint || "").trim(),
    notes: String(notes || "").trim(),
  };
  const out = await apiPost("/spells/mine/declare", payload);
  await loadSheetOnce(true);
  return out;
}

async function saveSpellList(kind, ids) {
  const routeMap = {
    cantrips: "/spells/mine/cantrips/set",
    known: "/spells/mine/known/set",
    prepared: "/spells/mine/prepared/set",
    bonus: "/spells/mine/bonus/set",
  };
  const route = routeMap[String(kind || "").trim()];
  if (!route) throw new Error(`Unknown spell list kind: ${kind}`);
  const out = await apiPost(route, { spell_ids: Array.isArray(ids) ? ids : [], replace: true });
  await loadSheetOnce(true);
  return out;
}

async function saveSpellbookList(ids) {
  const out = await apiPost("/spells/mine/spellbook/set", { spell_ids: Array.isArray(ids) ? ids : [], replace: true });
  await loadSheetOnce(true);
  return out;
}

async function saveMetamagicOptions(ids) {
  const out = await apiPost("/spells/mine/metamagic/set", { option_ids: Array.isArray(ids) ? ids : [], replace: true });
  await loadSheetOnce(true);
  return out;
}

async function wizardLearnSpell(spellId) {
  const out = await apiPost("/spells/mine/wizard/learn", { spell_id: String(spellId || "").trim() });
  await loadSheetOnce(true);
  return out;
}

function spellLevelLabel(spell) {
  const level = Number((spell && spell.level) || 0) || 0;
  return level <= 0 ? "Cantrip" : `Level ${level}`;
}

function summarizeSpellForUi(spell) {
  if (!spell || typeof spell !== "object") return "";
  const bits = [];
  const school = String(spell.school || "").trim();
  if (school) bits.push(school);
  const castTime = String(spell.casting_time || "").trim();
  if (castTime) bits.push(castTime);
  const rangeVal = spell.range_ft ?? (spell.cast && spell.cast.range_ft);
  const rangeNum = Number(rangeVal || 0) || 0;
  bits.push(rangeNum > 0 ? `${rangeNum} ft` : "Self/Touch");
  if (spell.save_type) bits.push(`${String(spell.save_type).toUpperCase()} save`);
  else if (spell.attack_roll) bits.push("Attack roll");
  const dmg = (spell.damage && typeof spell.damage === "object") ? spell.damage : {};
  if (dmg.expr) bits.push(`${dmg.heal ? 'Heal' : 'Damage'} ${dmg.expr}${dmg.type ? ` ${dmg.type}` : ''}`);
  const duration = String(spell.duration || "").trim();
  if (duration) bits.push(`Duration ${duration}`);
  if (spell.concentration) bits.push("Concentration");
  if (spell.reaction) bits.push("Reaction");
  return bits.join(" • ");
}

function spellEntrySummary(spell) {
  if (!spell || typeof spell !== "object") return "No spell data loaded.";
  const explicit = String(spell.description || spell.desc || spell.text || "").trim();
  if (explicit) return explicit;
  return summarizeSpellForUi(spell) || "No spell description available yet.";
}

function parseSpellListValue(value) {
  return Array.isArray(value)
    ? value.slice().map(v => String(v || "").trim()).filter(Boolean)
    : String(value || "").split(",").map(v => v.trim()).filter(Boolean);
}

function maxCastableSpellLevel(sc) {
  const spells = (sc && typeof sc.spells === "object" && sc.spells) ? sc.spells : {};
  let maxLevel = 0;
  for (let lvl = 1; lvl <= 9; lvl++) {
    const row = spells[String(lvl)];
    if (!row || typeof row !== "object") continue;
    const total = Number(row.total || 0) || 0;
    if (total > 0) maxLevel = lvl;
  }
  return maxLevel;
}

function filterSpellIdsByMaxLevel(ids, maxLevel) {
  const out = [];
  for (const id of (Array.isArray(ids) ? ids : [])) {
    const spell = spellsIndex[id];
    if (!spell) {
      out.push(id);
      continue;
    }
    const lvl = Number(spell.level || 0) || 0;
    if (lvl <= 0 || lvl <= maxLevel) out.push(id);
  }
  return out;
}

function spellManagerConfigFor(sheet, kind) {
  const baseSc = (sheet && typeof sheet.spellcasting === "object" && sheet.spellcasting) ? sheet.spellcasting : {};
  const previewSc = (activeLevelUpState && activeLevelUpState.active && activeLevelUpState.choices && typeof activeLevelUpState.choices.preview_spellcasting === "object")
    ? activeLevelUpState.choices.preview_spellcasting
    : null;
  const sc = (previewSc && Object.keys(previewSc).length) ? { ...baseSc, ...previewSc } : baseSc;
  const classes = Array.isArray(sc.spellcasting_classes) ? sc.spellcasting_classes.map(v => String(v || "").toLowerCase()) : [];
  const knownMode = String(sc.known_mode || "none").toLowerCase();
  const isWizard = classes.includes("wizard");
  const isCreating = isCreatingSheet(sheet);
  const isLeveling = !!(activeLevelUpState && activeLevelUpState.active);
  const isPreparedCaster = knownMode === "prepared";
  const isKnownCaster = knownMode === "known";

  const allowedIds = parseSpellListValue(sc.allowed_spell_ids);
  const allowedCantripIds = parseSpellListValue(sc.allowed_cantrip_ids);
  const rawAllowedLeveledIds = parseSpellListValue(sc.allowed_leveled_spell_ids);

  const maxLevel = maxCastableSpellLevel(sc);
  const allowedLeveledIds = filterSpellIdsByMaxLevel(rawAllowedLeveledIds, maxLevel);

  const spellbookIds = filterSpellIdsByMaxLevel(parseSpellListValue(sc.spellbook_spells), maxLevel);
  const knownIds = filterSpellIdsByMaxLevel(parseSpellListValue(sc.known_spells), maxLevel);
  const preparedIds = filterSpellIdsByMaxLevel(parseSpellListValue(sc.prepared_spells), maxLevel);
  const bonusIds = filterSpellIdsByMaxLevel(parseSpellListValue(sc.bonus_spell_ids), maxLevel);
  const metamagicIds = parseSpellListValue(sc.metamagic_options);

  const cantripLimit = Number(sc.cantrip_limit || 0) || 0;
  const knownLimit = Number(sc.known_limit || 0) || 0;
  const prepLimit = Number(sc.preparation_max || 0) || 0;
  const bonusLimit = Number(sc.bonus_spell_limit || 0) || 0;
  const metamagicLimit = Number(sc.metamagic_choice_limit || 0) || 0;

  const base = {
    kind,
    title: "Manage Spells",
    subtitle: "",
    routeKind: kind,
    selected: [],
    candidates: [],
    limit: 0,
    allowManage: false,
    emptyText: "No legal entries available.",
  };

  if (kind === "cantrips") {
    return {
      ...base,
      title: "Manage Cantrips",
      subtitle: isCreating || isLeveling
        ? "Choose cantrips granted by your class progression."
        : "Cantrips are normally chosen during creation or level up.",
      selected: parseSpellListValue(sc.cantrips),
      candidates: allowedCantripIds,
      limit: cantripLimit,
      allowManage: isCreating || isLeveling,
    };
  }

  if (kind === "known") {
    return {
      ...base,
      title: "Manage Known Spells",
      subtitle: isCreating || isLeveling
        ? "Choose the spells your class permanently knows."
        : "Known spells should normally change during creation or level up.",
      selected: knownIds,
      candidates: allowedLeveledIds,
      limit: knownLimit,
      allowManage: isKnownCaster && (isCreating || isLeveling),
    };
  }

  if (kind === "prepared") {
    const prepPool = isWizard ? spellbookIds : allowedLeveledIds;
    return {
      ...base,
      title: "Manage Prepared Spells",
      subtitle: isWizard
        ? "Choose today’s prepared spells from your spellbook."
        : "Choose today’s prepared spells from your class spell list.",
      selected: preparedIds,
      candidates: prepPool,
      limit: prepLimit,
      allowManage: isPreparedCaster || isWizard,
    };
  }

  if (kind === "spellbook") {
    return {
      ...base,
      title: "Manage Spellbook",
      subtitle: isCreating || isLeveling
        ? "Choose learned Wizard spells for creation or level up."
        : "Spellbook growth outside level up should come from special learning events like scrolls or books.",
      selected: spellbookIds,
      candidates: allowedLeveledIds,
      limit: Math.max(spellbookIds.length, Number(sc.spellbook_minimum || 0) || 0),
      allowManage: isWizard && (isCreating || isLeveling),
    };
  }

  if (kind === "bonus") {
    return {
      ...base,
      title: "Manage Bonus Spells",
      subtitle: "Base-class bonus spell storage. Subclass/domain handling stays for Phase H.",
      selected: bonusIds,
      candidates: allowedIds,
      limit: bonusLimit,
      allowManage: bonusLimit > 0 || bonusIds.length > 0,
    };
  }

  if (kind === "metamagic") {
    const metaOptions = [
      "careful_spell",
      "distant_spell",
      "empowered_spell",
      "extended_spell",
      "heightened_spell",
      "quickened_spell",
      "seeking_spell",
      "subtle_spell",
      "transmuted_spell",
      "twinned_spell",
    ];
    return {
      ...base,
      title: "Manage Metamagic",
      subtitle: isCreating || isLeveling
        ? "Choose your Sorcerer metamagic options."
        : "Metamagic choices normally change during leveling, not ordinary play.",
      routeKind: "metamagic",
      selected: metamagicIds,
      candidates: metaOptions,
      limit: metamagicLimit,
      allowManage: classes.includes("sorcerer") && (isCreating || isLeveling),
    };
  }

  return base;
}

function buildSpellSummaryCards(sheet) {
  const sc = (sheet && typeof sheet.spellcasting === "object" && sheet.spellcasting) ? sheet.spellcasting : {};
  const items = [
    { key: "cantrips", label: "Cantrips", value: parseSpellListValue(sc.cantrips) },
    { key: "known", label: "Known Spells", value: parseSpellListValue(sc.known_spells) },
    { key: "prepared", label: "Prepared Spells", value: parseSpellListValue(sc.prepared_spells) },
    { key: "spellbook", label: "Spellbook", value: parseSpellListValue(sc.spellbook_spells) },
    { key: "bonus", label: "Bonus Spells", value: parseSpellListValue(sc.bonus_spell_ids) },
    { key: "metamagic", label: "Metamagic", value: parseSpellListValue(sc.metamagic_options) },
  ];
  return items;
}

function summaryBodyForIds(ids) {
  if (!Array.isArray(ids) || !ids.length) return "None";
  const shown = ids.slice(0, 4);
  const text = shown.join(", ");
  return ids.length > 4 ? `${text}, ...` : text;
}

function openSpellChoiceModal(kind) {
  if (!activeSheet) return;
  const cfg = spellManagerConfigFor(activeSheet, kind);
  if (!cfg.allowManage) {
    setText("spellManagerStatus", cfg.subtitle || "That spell list is not editable right now.");
    return;
  }

  activeSpellChoiceState = {
    ...cfg,
    working: new Set(cfg.selected || []),
    filter: "",
    levelFilter: "",
  };

  const modal = el("spellChoiceModal");
  if (modal) {
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
  }

  writeVal("spellChoiceSearch", "");
  writeVal("spellChoiceLevelFilter", "");
  setText("spellChoiceTitle", cfg.title);
  setText("spellChoiceSubtitle", cfg.subtitle || "Select entries and save.");
  refreshSpellChoiceModal();
}

function closeSpellChoiceModal() {
  activeSpellChoiceState = null;
  const modal = el("spellChoiceModal");
  if (modal) {
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }
}

function refreshSpellChoiceModal() {
  const host = el("spellChoiceList");
  if (!host) return;
  host.innerHTML = "";

  const state = activeSpellChoiceState;
  if (!state) return;

  const search = String((el("spellChoiceSearch") && el("spellChoiceSearch").value) || "").trim().toLowerCase();
  const levelFilterRaw = (el("spellChoiceLevelFilter") && el("spellChoiceLevelFilter").value) || "";
  const levelFilter = levelFilterRaw === "" ? null : Number(levelFilterRaw);

  state.filter = search;
  state.levelFilter = levelFilterRaw;

  let pool = Array.isArray(state.candidates) ? state.candidates.slice() : [];
  pool = pool.filter(Boolean);

  if (state.routeKind === "metamagic") {
    pool = pool.filter(id => {
      if (!search) return true;
      return String(id).toLowerCase().includes(search);
    });
  } else {
    pool = pool.filter(id => {
      const spell = spellsIndex[id] || { name: id, spell_id: id, id };
      const hay = `${String(spell.name || "")} ${String(id || "")}`.toLowerCase();
      if (search && !hay.includes(search)) return false;
      if (levelFilter !== null) {
        const lvl = Number(spell.level || 0) || 0;
        if (lvl !== levelFilter) return false;
      }
      return true;
    });
  }

  const limit = Number(state.limit || 0) || 0;
  const selectedCount = state.working.size;
  setText(
    "spellChoiceCount",
    `${selectedCount} selected${limit > 0 ? ` / ${limit}` : ""} • ${pool.length} shown`
  );

  if (!pool.length) {
    host.innerHTML = `<div class="hint mono">${escapeHtml(state.emptyText || "No entries available.")}</div>`;
    return;
  }

  for (const id of pool) {
    const row = document.createElement("label");
    row.className = "spellChoiceRow";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.working.has(id);

    cb.onchange = () => {
      if (cb.checked) {
        if (limit > 0 && state.working.size >= limit) {
          cb.checked = false;
          setText("spellManagerStatus", `Selection limit reached (${limit}).`);
          return;
        }
        state.working.add(id);
      } else {
        state.working.delete(id);
      }
      setText(
        "spellChoiceCount",
        `${state.working.size} selected${limit > 0 ? ` / ${limit}` : ""} • ${pool.length} shown`
      );
    };

    const body = document.createElement("div");

    if (state.routeKind === "metamagic") {
      body.innerHTML = `
        <div class="spellChoiceName">${escapeHtml(id)}</div>
        <div class="hint mono spellChoiceMeta">metamagic option</div>
      `;
    } else {
      const spell = spellsIndex[id] || { spell_id: id, id, name: id };
      body.innerHTML = `
        <div class="spellChoiceName">${escapeHtml(String(spell.name || id))} · ${escapeHtml(spellLevelLabel(spell))}</div>
        <div class="hint mono spellChoiceMeta">${escapeHtml(id)}${spell.school ? ` • ${escapeHtml(String(spell.school))}` : ""}</div>
        <div class="spellChoiceDesc">${escapeHtml(spellEntrySummary(spell))}</div>
      `;
    }

    row.appendChild(cb);
    row.appendChild(body);
    host.appendChild(row);
  }
}

async function commitSpellChoiceModal() {
  const state = activeSpellChoiceState;
  if (!state) return;

  const ids = Array.from(state.working);

  try {
    if (state.routeKind === "metamagic") {
      await saveMetamagicOptions(ids);
      await pollLevelUpStateOnce();
      setText("spellManagerStatus", `${state.title} saved.`);
    } else if (state.routeKind === "spellbook") {
      await saveSpellbookList(ids);
      await pollLevelUpStateOnce();
      setText("spellManagerStatus", "Spellbook saved.");
    } else {
      await saveSpellList(state.routeKind, ids);
      await pollLevelUpStateOnce();
      setText("spellManagerStatus", `${state.title} saved.`);
    }
    closeSpellChoiceModal();
  } catch (e) {
    setText("spellManagerStatus", `${state.title} failed: ${e.message || e}`);
  }
}

function renderSpellManagerActions(sheet) {
  const host = el("spellManagerActions");
  if (!host) return;
  host.innerHTML = "";

  const actions = [
    { key: "cantrips", label: "Manage Cantrips" },
    { key: "known", label: "Manage Known Spells" },
    { key: "prepared", label: "Manage Prepared Spells" },
    { key: "spellbook", label: "Manage Spellbook" },
    { key: "bonus", label: "Manage Bonus Spells" },
    { key: "metamagic", label: "Manage Metamagic" },
  ];

  for (const a of actions) {
    const cfg = spellManagerConfigFor(sheet, a.key);
    const btn = document.createElement("button");
    btn.className = "btn";
    btn.type = "button";
    btn.textContent = a.label;
    btn.disabled = !cfg.allowManage;
    btn.onclick = () => openSpellChoiceModal(a.key);
    host.appendChild(btn);
  }
}

function renderSpellSummary(sheet) {
  const host = el("spellSummaryCards");
  if (!host) return;
  host.innerHTML = "";

  const items = buildSpellSummaryCards(sheet);
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "spellSummaryCard";
    card.innerHTML = `
      <div class="spellSummaryHead">
        <div class="spellSummaryTitle">${escapeHtml(item.label)}</div>
        <div class="tag">${Array.isArray(item.value) ? item.value.length : 0}</div>
      </div>
      <div class="spellSummaryBody">${escapeHtml(summaryBodyForIds(item.value))}</div>
    `;
    host.appendChild(card);
  }
}

function collectSpellGroups(sheet) {
  const sc = (sheet && typeof sheet.spellcasting === "object" && sheet.spellcasting) ? sheet.spellcasting : {};
  const parseList = (value) => Array.isArray(value) ? value.slice() : String(value || "").split(",").map(v => v.trim()).filter(Boolean);
  const groups = [
    { key: "cantrips", label: "Cantrips", ids: parseList(sc.cantrips) },
    { key: "known", label: "Known Spells", ids: parseList(sc.known_spells) },
    { key: "prepared", label: "Prepared Spells", ids: parseList(sc.prepared_spells) },
    { key: "spellbook", label: "Spellbook", ids: parseList(sc.spellbook_spells) },
    { key: "bonus", label: "Bonus Spells", ids: parseList(sc.bonus_spell_ids) },
  ];
  const seen = new Set();
  const library = [];
  for (const g of groups) {
    for (const sid of g.ids) {
      if (!sid || seen.has(sid)) continue;
      seen.add(sid);
      library.push(sid);
    }
  }
  return { groups, library };
}

function renderSpellLibrary(sheet) {
  const host = el("spellLibrary");
  if (!host) return;
  host.innerHTML = "";

  const { groups } = collectSpellGroups(sheet || {});
  const visibleGroups = groups.filter(g => Array.isArray(g.ids) && g.ids.length);

  if (!visibleGroups.length) {
    host.innerHTML = '<div class="hint mono">No spells stored yet for this character.</div>';
    return;
  }

  for (const group of visibleGroups) {
    const section = document.createElement("details");
    section.className = "featureGroup";
    section.open = /cantrip|known/i.test(group.label);

    const title = document.createElement("summary");
    title.className = "featureGroupTitle";
    title.innerHTML = `<span>${escapeHtml(group.label)}</span><span class="tag">${group.ids.length}</span>`;
    section.appendChild(title);

    const body = document.createElement("div");
    body.className = "featureGroupBody";

    for (const sid of group.ids) {
      const spell = spellsIndex[sid] || { spell_id: sid, id: sid, name: sid };
      const card = document.createElement("div");
      card.className = "featureCard spellLibraryCard";
      card.innerHTML = `
        <div class="featureName">${escapeHtml(String(spell.name || sid))} · ${escapeHtml(spellLevelLabel(spell))}</div>
        <div class="hint mono spellLibMeta">${escapeHtml(sid)}${spell.school ? ` • ${escapeHtml(String(spell.school))}` : ""}</div>
        <div class="featureDesc">${escapeHtml(spellEntrySummary(spell))}</div>
      `;
      body.appendChild(card);
    }

    section.appendChild(body);
    host.appendChild(section);
  }
}

function declarableSpellIdsForSheet(sheet) {
  const sc = (sheet && sheet.spellcasting && typeof sheet.spellcasting === "object") ? sheet.spellcasting : {};
  const out = [];
  const seen = new Set();

  const pushIds = (value) => {
    const arr = Array.isArray(value)
      ? value
      : String(value || "").split(",").map(v => v.trim()).filter(Boolean);
    for (const sid of arr) {
      if (!sid || seen.has(sid)) continue;
      seen.add(sid);
      out.push(sid);
    }
  };

  pushIds(sc.cantrips);
  pushIds(sc.prepared_spells);
  pushIds(sc.known_spells);
  pushIds(sc.bonus_spell_ids);

  return out.filter(Boolean);
}

function renderSpellActionCards(sheet) {
  const host = el("spellActions");
  if (!host) return;
  host.innerHTML = "";

  const sc = (sheet && sheet.spellcasting && typeof sheet.spellcasting === "object") ? sheet.spellcasting : {};
  const allIds = declarableSpellIdsForSheet(sheet);

  if (!allIds.length) {
    host.innerHTML = '<div class="hint mono">No declarable spells available right now.</div>';
    return;
  }

  const grouped = new Map();
  for (const sid of allIds) {
    const spell = spellsIndex[sid];
    if (!spell) continue;
    const lvl = Number(spell.level || 0) || 0;
    if (!grouped.has(lvl)) grouped.set(lvl, []);
    grouped.get(lvl).push({ sid, spell });
  }

  const levelOrder = Array.from(grouped.keys()).sort((a, b) => a - b);

  for (const lvl of levelOrder) {
    const entries = grouped.get(lvl) || [];
    const details = document.createElement("details");
    details.className = "spellLevelGroup";
    details.open = lvl <= 1;

    const label = lvl <= 0 ? "Cantrips" : `Level ${lvl}`;
    details.innerHTML = `
      <summary class="spellLevelSummary">
        <span>${escapeHtml(label)}</span>
        <span class="tag">${entries.length}</span>
      </summary>
      <div class="spellLevelBody"></div>
    `;

    const body = details.querySelector(".spellLevelBody");

    for (const { sid, spell } of entries) {
      const card = document.createElement("div");
      card.className = "spellCard";

      const source = spellSourceFor(sheet, sid);
      const level = Number(spell.level || 0) || 0;
      const mode = String(spell.target_mode || (spell.targeting && spell.targeting.kind) || "").trim();
      const isReaction = !!spell.reaction;
      const damage = (spell.damage && typeof spell.damage === "object") ? spell.damage : {};
      const slotChoices = availableSlotLevels(sc, spell);
      const slotHtml = slotChoices
        .map(v => `<option value="${v}">${v === 0 ? "Cantrip" : "Lv " + v}</option>`)
        .join("");

      const metamagicChoices = Array.isArray(sc.metamagic_options) ? sc.metamagic_options : [];
      const metamagicHtml = metamagicChoices.length
        ? `<input class="inp mono" data-role="metamagic" placeholder="metamagic ids comma-separated" value="${escapeHtml(metamagicChoices.join(", "))}" />`
        : '';

      card.innerHTML = `
        <div class="spellCardHead">
          <div>
            <div class="spellCardTitle">${escapeHtml(String(spell.name || sid))}</div>
            <div class="hint mono spellCardMeta">${escapeHtml(source)} • L${level} • ${escapeHtml(mode || "spell")} • ${escapeHtml(String(spell.range_ft || 0))} ft${isReaction ? " • Reaction" : ""}</div>
            <div class="hint mono spellCardMeta">${escapeHtml(String(damage.expr || ""))}${damage.type ? ` • ${escapeHtml(String(damage.type))}` : ""}${spell.save_type ? ` • save ${escapeHtml(String(spell.save_type).toUpperCase())}` : ""}</div>
          </div>
          <div class="tag">${level <= 0 ? "0" : String(level)}</div>
        </div>

        <div class="spellCardControls">
          <select class="inp mono tinySel" data-role="slot">${slotHtml}</select>
          <select class="inp mono tinySel" data-role="slotSource">
            <option value="auto">Auto</option>
            <option value="shared">Shared</option>
            <option value="pact">Pact</option>
            <option value="arcanum">Arcanum</option>
          </select>
          <input class="inp" data-role="target" placeholder="${isReaction ? "target / trigger note" : "target / point / note"}" />
          <button class="btn" type="button">Declare</button>
          ${metamagicHtml}
        </div>
      `;

      const btn = card.querySelector("button");
      if (btn) {
        btn.onclick = async () => {
          try {
            const slotSel = card.querySelector('[data-role="slot"]');
            const slotSourceSel = card.querySelector('[data-role="slotSource"]');
            const targetInp = card.querySelector('[data-role="target"]');
            const metamagicInp = card.querySelector('[data-role="metamagic"]');

            const slotLevel = slotSel ? parseInt(slotSel.value || "0", 10) || 0 : 0;
            const note = targetInp ? String(targetInp.value || "").trim() : "";
            const slotSource = slotSourceSel ? String(slotSourceSel.value || "auto").trim().toLowerCase() : "auto";
            const metamagicOptions = metamagicInp
              ? String(metamagicInp.value || "").split(",").map(v => v.trim()).filter(Boolean)
              : [];

            await declareSpell({
              spell_id: sid,
              slot_level: slotLevel,
              note,
              slot_source: slotSource,
              metamagic_options: metamagicOptions,
            });

            setStatus(`Declared spell: ${spell.name || sid}`, "ok");
          } catch (e) {
            setStatus(`Spell declare failed: ${e.message || e}`, "err");
          }
        };
      }

      if (body) body.appendChild(card);
    }

    host.appendChild(details);
  }
}

function renderSpellcasting(sheet) {
  const sc = (sheet && sheet.spellcasting && typeof sheet.spellcasting === "object") ? sheet.spellcasting : {};
  const panel = el("panelSpellcasting");

  const classList = Array.isArray(sc.spellcasting_classes) ? sc.spellcasting_classes : [];
  const hasAnySpellList =
    parseSpellListValue(sc.cantrips).length ||
    parseSpellListValue(sc.known_spells).length ||
    parseSpellListValue(sc.prepared_spells).length ||
    parseSpellListValue(sc.spellbook_spells).length ||
    parseSpellListValue(sc.bonus_spell_ids).length;

  const hasSpellcasting =
    classList.length > 0 ||
    String(sc.known_mode || "none").toLowerCase() !== "none" ||
    hasAnySpellList;

  if (panel) panel.classList.toggle("hidden", !hasSpellcasting);
  if (!hasSpellcasting) return;

  setText("scClass", sc.class || "—");
  setText("scAbility", sc.ability || "—");
  setText("scDc", (sc.save_dc ?? "—"));
  setText("scAtk", (sc.attack_bonus ?? "—"));

  const modeTxt = String(sc.known_mode || "none");
  const classTxt = Array.isArray(sc.spellcasting_classes) && sc.spellcasting_classes.length
    ? sc.spellcasting_classes.join("/")
    : (sc.class || "none");
  const creating = isCreatingSheet(sheet);
  const leveling = !!(activeLevelUpState && activeLevelUpState.active);
  const highestSpellLevel = maxCastableSpellLevel(sc);

  const wizardHint = (Array.isArray(sc.spellcasting_classes) && sc.spellcasting_classes.includes("wizard"))
    ? "\nWizard: Spellbook = learned leveled spells. Prepared = today’s loadout."
    : "";

  const phaseHint = creating
    ? "\nCreation mode: spell choices are editable through the manager buttons."
    : leveling
      ? "\nLevel-up mode: level-gain spell choices are editable through the manager buttons."
      : "\nNormal play: learned spell lists are read-only; use preparation or special learn flows when legal.";

  setText(
    "spellManagerStatus",
    `Class ${classTxt} • Model ${modeTxt} • Max Spell Level ${highestSpellLevel} • Known cap ${Number(sc.known_limit || 0)} • Cantrips ${Number(sc.cantrip_limit || 0)} • Prepared ${Number(sc.preparation_max || 0)} • Bonus ${Number(sc.bonus_spell_limit || 0)} • Metamagic ${Number(sc.metamagic_choice_limit || 0)}${wizardHint}${phaseHint}`
  );

  renderSpellSummary(sheet);
  renderSpellManagerActions(sheet);
  renderSpellLibrary(sheet);

  const host = el("spellSlots");
  if (!host) return;
  const spells = (sc.spells && typeof sc.spells === "object") ? sc.spells : {};
  host.innerHTML = "";

  for (let lvl = 1; lvl <= 9; lvl++) {
    const key = String(lvl);
    const rowState = (spells[key] && typeof spells[key] === "object") ? spells[key] : {};
    const total = parseInt(rowState.total ?? 0, 10) || 0;
    const used = parseInt(rowState.used ?? 0, 10) || 0;
    const remaining = Math.max(0, total - used);
    if (total <= 0 && used <= 0) continue;

    const row = document.createElement("div");
    row.className = "slotRow";
    row.innerHTML = `
      <div class="tag">Lv ${lvl}</div>
      <div class="hint mono">Total</div>
      <div class="inp staticField mono">${total}</div>
      <div class="hint mono">Used</div>
      <div class="inp staticField mono">${used}</div>
      <div class="hint mono">Remaining</div>
      <div class="inp staticField mono">${remaining}</div>
    `;
    host.appendChild(row);
  }

  renderSpellActionCards(sheet);
}

function renderDetails(sheet) {
  const dt = (sheet.details && typeof sheet.details === "object") ? sheet.details : {};
  writeVal("dtAge", dt.age || "");
  writeVal("dtHeight", dt.height || "");
  writeVal("dtWeight", dt.weight || "");
  writeVal("dtEyes", dt.eyes || "");
  writeVal("dtSkin", dt.skin || "");
  writeVal("dtHair", dt.hair || "");
  writeVal("dtAppearance", dt.appearance || "");
  writeVal("dtAllies", dt.allies_and_organizations || "");
  writeVal("dtTreasure", dt.treasure || "");
}

function queueSheetPatch(dotPath, value) {
  const parts = dotPath.split(".");
  let cur = sheetDirtyPatch;
  for (let i = 0; i < parts.length; i++) {
    const k = parts[i];
    if (i === parts.length - 1) cur[k] = value;
    else {
      if (!cur[k] || typeof cur[k] !== "object") cur[k] = {};
      cur = cur[k];
    }
  }

  setSheetSaveStatus("Unsaved changes…", "warn");
  if (sheetSaveTimer) clearTimeout(sheetSaveTimer);
  sheetSaveTimer = setTimeout(() => flushSheetPatch().catch(e => setStatus(`Save failed: ${e}`, "err")), 450);
}

async function flushSheetPatch() {
  if (!sheetDirtyPatch || Object.keys(sheetDirtyPatch).length === 0) return;
  const patch = sheetDirtyPatch;
  sheetDirtyPatch = {};
  setSheetSaveStatus("Saving…", "muted");
  await apiPatch("/sheet/mine", { patch, finalize: false });
  await loadSheetOnce(true);
  setSheetSaveStatus("Saved.", "ok");
}

async function finalizeSheet() {
  setSheetSaveStatus("Finalizing…", "muted");
  await apiPatch("/sheet/mine", { patch: {}, finalize: true });
  await loadSheetOnce(true);
  setSheetSaveStatus("Finalized (locked).", "ok");
}

function setNoteEditingBanner() {
  const out = el("noteEditingOut");
  if (!out) return;
  if (activeNoteId) {
    out.textContent = `Editing: ${activeNoteId}`;
  } else {
    out.textContent = "New note";
  }
}

function clearNoteEditor() {
  activeNoteId = null;
  const t = el("noteTitle");
  const x = el("noteText");
  if (t) t.value = "";
  if (x) x.value = "";
  setText("noteSaveOut", "—");
  setNoteEditingBanner();
  renderNotesFromState(activeSheet);
}

async function saveNoteClicked() {
  const t = el("noteTitle");
  const x = el("noteText");
  const title = t ? (t.value || "").trim() : "";
  const text = x ? (x.value || "").trim() : "";
  if (!text && !title) {
    setText("noteSaveOut", "Enter a title or text.");
    return;
  }
  setText("noteSaveOut", "Saving…");
  if (activeNoteId) {
    await apiPatch(`/notes/mine/${activeNoteId}`, { title, text });
  } else {
    await apiPost("/notes/mine", { title, text });
  }
  await loadSheetOnce(true);
  setText("noteSaveOut", "Saved." );
}

async function deleteNoteClicked() {
  if (!activeNoteId) return;
  setText("noteSaveOut", "Deleting…");
  await apiDelete(`/notes/mine/${activeNoteId}`);
  activeNoteId = null;
  await loadSheetOnce(true);
  setText("noteSaveOut", "Deleted.");
  setNoteEditingBanner();
}

function renderNotesFromState(sheet) {
  const list = el("noteList");
  const btnNew = el("btnNewNote");
  const btnSave = el("btnSaveNote");
  const btnDel = el("btnDeleteNote");
  if (btnNew) btnNew.disabled = false;
  if (btnSave) btnSave.disabled = false;
  if (btnDel) btnDel.disabled = !activeNoteId;
  if (!list) return;

  const notes = (sheet && Array.isArray(sheet.notes)) ? sheet.notes : [];
  if (!notes.length) {
    list.textContent = "No notes yet.";
    return;
  }

  const sorted = notes.slice().filter(n => n && typeof n === "object").sort((a, b) => {
    const au = Number(a.updated_at || a.created_at || 0) || 0;
    const bu = Number(b.updated_at || b.created_at || 0) || 0;
    return bu - au;
  });

  list.innerHTML = "";
  for (const n of sorted) {
    const nid = (n.note_id || "").toString();
    if (!nid) continue;
    const item = document.createElement("div");
    item.className = "noteItem" + (activeNoteId === nid ? " active" : "");
    const title = document.createElement("div");
    title.textContent = (n.title || "Note").toString();
    const meta = document.createElement("div");
    meta.className = "hint mono";
    const ts = Number(n.updated_at || n.created_at || 0) || 0;
    meta.textContent = ts ? new Date(ts * 1000).toLocaleString() : "";
    item.appendChild(title);
    item.appendChild(meta);
    item.onclick = () => {
      activeNoteId = nid;
      const t = el("noteTitle");
      const x = el("noteText");
      if (t) t.value = (n.title || "").toString();
      if (x) x.value = (n.text || "").toString();
      setNoteEditingBanner();
      renderNotesFromState(activeSheet);
    };
    list.appendChild(item);
  }
}


function updateRollRequestUI() {
  const banner = el("rollRequestBanner");
  const title = el("rollRequestTitle");
  const meta = el("rollRequestMeta");
  const expectedOut = el("expectedOut");

  if (!banner || !title || !meta || !expectedOut) return;

  if (activeRollRequest) {
    banner.classList.remove("hidden");
    const lbl = activeRollRequest.label || activeRollRequest.roll_kind || "Requested roll";
    title.textContent = lbl;

    const dc = (activeRollRequest.dc === null || activeRollRequest.dc === undefined) ? "" : ` DC ${activeRollRequest.dc}`;
    const am = (activeRollRequest.adv_mode && activeRollRequest.adv_mode !== "normal") ? ` • ${activeRollRequest.adv_mode}` : "";
    meta.textContent = `Roll pending${am}${dc}`;
    expectedOut.textContent = `d${activeRollRequest.expected_sides} (${activeRollRequest.expected_count_min}-${activeRollRequest.expected_count_max})`;
  } else if (needDamage) {
    banner.classList.remove("hidden");
    title.textContent = "Damage";
    meta.textContent = "Roll pending";
    expectedOut.textContent = (needDamage.damage_expr || "damage dice");
  } else if (hasPending) {
    banner.classList.remove("hidden");
    title.textContent = "Attack (to-hit)";
    meta.textContent = "Roll pending";
    expectedOut.textContent = "d20 (1)";
  } else {
    banner.classList.add("hidden");
    expectedOut.textContent = "—";
  }
}

function updateRollControls() {
  const btnRoll = el("btnRoll");
  const freeSides = el("freeSides");
  const freeCount = el("freeCount");

  const serverRequestActive = !!(activeRollRequest || needDamage || hasPending);
  const freeAllowed = !serverRequestActive;

  if (btnRoll) btnRoll.disabled = false; // always allowed; logic inside handles state
  if (freeSides) freeSides.disabled = !freeAllowed;
  if (freeCount) freeCount.disabled = !freeAllowed;
}

function clearRollUI() {
  lastRoll = null;
  const ro = el("rollOut"); if (ro) ro.textContent = "—";
  const co = el("chosenOut"); if (co) co.textContent = "—";
}

function getFreeCount() {
  const fc = el("freeCount");
  let v = fc ? Number(fc.value || "1") : 1;
  if (!Number.isFinite(v) || v < 1) v = 1;
  v = Math.min(100, Math.floor(v));
  if (fc) fc.value = String(v);
  localStorage.setItem("grengine_free_roll_count", String(v));
  return v;
}

function loadFreeCount() {
  const saved = localStorage.getItem("grengine_free_roll_count");
  const fc = el("freeCount");
  if (fc && saved) {
    const v = Math.max(1, Math.min(100, parseInt(saved, 10) || 1));
    fc.value = String(v);
  }
}

// ---------------------------
// Rendering: unified activity feed + audit log
// ---------------------------
function _pushFeed(clsName, html) {
  feedEntries.unshift({ clsName, html });
  if (feedEntries.length > 200) feedEntries = feedEntries.slice(0, 200);
  renderFeed();
}

function renderFeed() {
  const out = el("feed");
  const empty = el("noFeed");
  if (!out || !empty) return;

  if (!feedEntries.length) {
    empty.style.display = "block";
    out.innerHTML = "";
    return;
  }

  empty.style.display = "none";
  out.innerHTML = "";
  for (const e of feedEntries) {
    const div = document.createElement("div");
    div.className = e.clsName;
    div.innerHTML = e.html;
    out.appendChild(div);
  }
}

function pushResult(res) {
  const attacker = res.attacker_name || res.attacker_token_id || "";
  const target = res.target_name || res.target_token_id || "";
  const verdict = (res.result || "MISS").toUpperCase();
  const dmg = res.damage | 0;
  const nat = (res.nat20 ? " (NAT20)" : (res.nat1 ? " (NAT1)" : ""));
  const hp = (typeof res.target_hp === "number" && typeof res.target_max_hp === "number")
    ? ` • HP ${res.target_hp}/${res.target_max_hp}` : "";

  _pushFeed(
    "pending",
    `
      <div class="top">
        <div><b>${escapeHtml(verdict)}</b>${escapeHtml(nat)} — ${escapeHtml(attacker)} → ${escapeHtml(target)}</div>
        <div class="mono">${escapeHtml(res.attack_id || "")}</div>
      </div>
      <div class="kv">
        <span><b>d20:</b> ${res.roll}</span>
        <span><b>Total:</b> ${res.total}</span>
        <span><b>AC:</b> ${res.ac}</span>
        <span><b>DMG:</b> ${dmg}${hp}</span>
      </div>
    `
  );
}

async function submitReactionChoice(requestId, choice, data) {
  return await apiPost("/reactions/respond", {
    request_id: String(requestId || ""),
    choice: String(choice || "decline"),
    reaction_kind: String((data && data.reaction_kind) || ""),
    spell_id: String((data && data.spell_id) || ""),
    payload: data || {},
  });
}

function pushMsg(m) {
  const txt = (m.text || "").toString();
  const data = (m && typeof m.data === 'object' && m.data) ? m.data : {};
  const isReactionChoice = String(data.type || "") === "REACTION_CHOICE" && data.request_id;
  const meta = (!isReactionChoice && Object.keys(data).length)
    ? `<div class="mono small">${escapeHtml(JSON.stringify(data))}</div>` : "";
  const slotOptions = Array.isArray(data.slot_options) ? data.slot_options.map(v => Number(v || 0)).filter(v => v > 0) : [];
  const selectedSlot = Number(data.recommended_slot_level || data.slot_level || (slotOptions.length ? slotOptions[0] : 0) || 0);
  const slotUi = (isReactionChoice && slotOptions.length > 1)
    ? `<div class="row" style="gap:8px; margin-top:8px; align-items:center;">
         <label class="small" for="react-slot-${escapeHtml(String(m.message_id || ""))}">Slot</label>
         <select class="input" data-role="slot-level" id="react-slot-${escapeHtml(String(m.message_id || ""))}" style="max-width:120px;">
           ${slotOptions.map(v => `<option value="${v}" ${v === selectedSlot ? "selected" : ""}>Level ${v}</option>`).join("")}
         </select>
       </div>`
    : "";
  const actions = isReactionChoice
    ? `${slotUi}<div class="row" style="gap:8px; margin-top:8px;">
         <button class="btn" data-role="accept">Accept</button>
         <button class="btn secondary" data-role="decline">Decline</button>
       </div>`
    : "";

  const html = `
      <div class="top">
        <div><b>${escapeHtml(String(m.kind || "info").toUpperCase())}</b></div>
        <div class="mono">${escapeHtml(String(m.message_id || ""))}</div>
      </div>
      <div>${escapeHtml(txt)}</div>
      ${meta}
      ${actions}
    `;

  _pushFeed(`msg ${String(m.kind || "info").toLowerCase()}`, html);

  if (isReactionChoice) {
    setTimeout(() => {
      const feed = el("feed");
      if (!feed) return;
      const cards = Array.from(feed.children || []);
      const card = cards.find(n => (n.innerHTML || "").includes(String(m.message_id || "")));
      if (!card) return;
      const btnA = card.querySelector('[data-role="accept"]');
      const btnD = card.querySelector('[data-role="decline"]');
      const bind = async (choice) => {
        try {
          if (btnA) btnA.disabled = true;
          if (btnD) btnD.disabled = true;
          const slotSel = card.querySelector('[data-role="slot-level"]');
          const payload = Object.assign({}, data || {});
          if (slotSel && slotSel.value) payload.slot_level = Number(slotSel.value || 0) || 0;
          await submitReactionChoice(data.request_id, choice, payload);
          setStatus(`Reaction ${choice} sent.`, 'ok');
        } catch (e) {
          setStatus(`Reaction send failed: ${e.message || e}`, 'err');
          if (btnA) btnA.disabled = false;
          if (btnD) btnD.disabled = false;
        }
      };
      if (btnA) btnA.onclick = () => bind('accept');
      if (btnD) btnD.onclick = () => bind('decline');
    }, 0);
  }
}

function renderLogs() {
  const list = el("logList");
  if (!list) return;

  if (!lastLogs || lastLogs.length === 0) {
    list.textContent = "—";
    return;
  }

  const lines = [];
  for (const e of lastLogs) {
    const ts = e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : "";
    const t = e.type || "";
    if (t === "ROLL_REQUESTED") {
      const exp = (e.expected && e.expected.sides) ? `d${e.expected.sides}` : "";
      lines.push(`[${ts}] REQUESTED ${exp} ${e.label || ""} (id=${e.request_id || ""})`);
    } else if (t === "ROLL_SUBMITTED") {
      lines.push(`[${ts}] SUBMITTED d${e.die_sides || ""} rolls=${JSON.stringify(e.rolls || [])} chosen=${e.chosen} ${e.label || ""}`);
    } else if (t === "PENDING_ATTACK") {
      lines.push(`[${ts}] PENDING_ATTACK id=${e.pending_attack_id || ""} ${e.attacker || ""} -> ${e.target || ""} dmg=${e.damage_expr || ""}`);
    } else {
      lines.push(`[${ts}] ${t} ${JSON.stringify(e)}`);
    }
  }
  list.textContent = lines.join("\n");
}

// ---------------------------
// Polling
// ---------------------------
async function pollPendingOnce() {
  const data = await apiGet("/pending_attacks/mine");
  const pending = (data && data.pending_attacks) ? data.pending_attacks : [];
  // Deterministic FIFO: created_at then expires_at
  pending.sort((a, b) => {
    const ca = Number(a.created_at || 0);
    const cb = Number(b.created_at || 0);
    if (ca !== cb) return ca - cb;
    return Number(a.expires_at || 0) - Number(b.expires_at || 0);
  });
  hasPending = pending.length > 0;
  activePendingAttack = hasPending ? pending[0] : null;

  // Add audit lines to log (local) when new pending attacks appear
  for (const pa of pending) {
    const key = `${pa.pending_attack_id || ""}:${pa.created_at || ""}`;
    if (!key.trim()) continue;
    if (seenPendingKeys.has(key)) continue;
    seenPendingKeys.add(key);
    // push a synthetic log entry
    lastLogs = (lastLogs || []).concat([{
      ts: Math.floor(Date.now() / 1000),
      type: "PENDING_ATTACK",
      pending_attack_id: pa.pending_attack_id || "",
      attacker: (pa.attacker && (pa.attacker.name || pa.attacker.token_id)) || "",
      target: (pa.target && (pa.target.name || pa.target.token_id)) || "",
      damage_expr: pa.damage_expr || ""
    }]);
    if (lastLogs.length > 200) lastLogs = lastLogs.slice(-200);
  }
}

async function pollRollRequestsOnce() {
  const data = await apiGet("/roll_requests/mine");
  const reqs = (data && data.roll_requests) ? data.roll_requests : [];

  // Priority tier then FIFO within tier by expires_at (server provides expires ordering)
  reqs.sort((a, b) => {
    const pa = rollPriority(a.roll_kind);
    const pb = rollPriority(b.roll_kind);
    if (pa !== pb) return pa - pb;
    const ea = Number(a.expires_at || 0);
    const eb = Number(b.expires_at || 0);
    return ea - eb;
  });

  const next = (reqs.length > 0) ? reqs[0] : null;

  // If request changes, clear buffer
  if (!next || !activeRollRequest || (next.request_id !== activeRollRequest.request_id)) {
    armedBuffer = [];
    armedBuffer._rid = "";
  }
  activeRollRequest = next;
}

async function pollResultsOnce() {
  const data = await apiGet("/attack_results/mine");
  const results = (data && data.results) ? data.results : [];
  for (const res of results) pushResult(res);
}

async function pollMessagesOnce() {
  const data = await apiGet("/messages/mine");
  const msgs = (data && data.messages) ? data.messages : [];

  for (const m of msgs) {
    pushMsg(m);

    // Legacy NEED_DAMAGE capture (no damage panel; handled via Roll button)
    try {
      const d = (m && m.data) ? m.data : {};
      if (d && d.type === "NEED_DAMAGE" && d.attack_id) {
        const key = String(d.dedupe_key || (`NEED_DAMAGE:${d.attack_id}`));
        if (seenDedupeKeys.has(key)) continue;
        seenDedupeKeys.add(key);

        needDamage = {
          attack_id: String(d.attack_id),
          damage_expr: String(d.damage_expr || "").trim(),
          crit: !!d.crit,
          weapon_name: String(d.weapon_name || ""),
          target_name: String(d.target_name || "")
        };
      }
    } catch (_) {}
  }
}


async function pollHandoutsOnce() {
  const data = await apiGet("/handouts/mine");
  handouts = (data && Array.isArray(data.handouts)) ? data.handouts : [];
  renderHandouts();
}

function renderHandouts() {
  const list = el("handoutList");
  const view = el("handoutView");
  const titleEl = el("handoutTitle");
  const bodyEl = el("handoutBody");
  const btnClose = el("handoutClose");
  if (btnClose) btnClose.onclick = () => {
    activeHandoutId = null;
    if (view) view.classList.add("hidden");
  };
  if (!list) return;

  if (!handouts.length) {
    list.textContent = "No handouts.";
    return;
  }
  list.innerHTML = "";
  for (const h of handouts) {
    const row = document.createElement("div");
    row.className = "noteRow";
    const badge = h.read ? "" : " • NEW";
    row.innerHTML = `<div class="noteTitle">${escapeHtml(h.title || "Handout")}${badge}</div>
                     <div class="noteMeta mono">${new Date((h.created_at||0)*1000).toLocaleString()}</div>`;
row.onclick = async () => {
  activeHandoutId = h.handout_id;
  const payload = h.payload || {};

  const lang = String(payload.language || payload.lang || h.language || "").trim();

  let canRead = true;
  try {
    canRead = playerCanReadLanguage(lang);
  } catch (e) {
    console.warn("playerCanReadLanguage failed; defaulting to unreadable-safe open", e);
    canRead = false;
  }

  const unreadableMode = String(h.unreadable_mode || payload.unreadable_mode || "blocked")
    .trim()
    .toLowerCase();

  // Title ONLY (no language / unreadable hints)
  if (titleEl) titleEl.textContent = (h.title || "Handout");

  // Pull body from all likely keys
  let body = (h.text ?? payload.text ?? payload.body ?? payload.content ?? payload.message ?? "").toString().trim();

  const isUnreadable = (lang && !canRead) || Boolean(h.unreadable);

  if (isUnreadable) {
    if (unreadableMode === "scramble") {
      body = body || "You can't read this handout.";
    } else {
      body = "You can't read this handout.";
    }
  } else {
    body = body || "(Empty handout.)";
  }

  if (bodyEl) bodyEl.textContent = body;

  if (view) {
    view.classList.remove("hidden");
    view.style.display = "block";
  }

  // Mark read (do not re-render immediately)
  if (!h.read) {
    try { await apiPost("/handouts/read", { handout_id: h.handout_id }); } catch (e) {}
    h.read = true;
  }
};
    list.appendChild(row);
  }
}




async function pollLogsOnce() {
  // server-side log entries
  const data = await apiGet("/logs/mine?limit=200");
  const logs = (data && data.logs) ? data.logs : [];
  // merge with local synthetic pending logs (keep last 200)
  const merged = (logs || []).slice(-200);
  // append any synthetic entries that aren't in server logs
  if (lastLogs && lastLogs.length) {
    for (const e of lastLogs) {
      if (e.type === "PENDING_ATTACK") merged.push(e);
    }
  }
  lastLogs = merged.slice(-200);
  renderLogs();
}

async function pollTick() {
  try {
    await loadSpellsDbOnce();
    await pollPendingOnce();
    await pollRollRequestsOnce();
    await pollResultsOnce();
    await pollMessagesOnce();
    await pollHandoutsOnce();
    await pollLogsOnce();
    await pollSheetStateOnce();
    await pollRestStateOnce();
    await pollLevelUpStateOnce();

    updateRollRequestUI();
    updateRollControls();

    setNet(true);
    // Do not spam the main status line; it's used for UX feedback.
    const s = el("status");
    if (s && (s.classList.contains("muted") || s.textContent === "Idle")) {
      setStatus("Online", "ok");
    }
  } catch (e) {
    setNet(false);
    // Keep existing status content if it is not muted.
    const s = el("status");
    if (s && !s.classList.contains("muted")) return;
    setStatus(`Offline`, "warn");
  }
}

function startPolling() {
  if (timer) clearInterval(timer);
  timer = setInterval(pollTick, 1000);
  pollTick();
}

function stopPolling() {
  if (timer) clearInterval(timer);
  timer = null;
}

// ---------------------------
// Roll button
// ---------------------------
async function submitActiveRollRequest(mode, sides, rolls, chosen) {
  if (!activeRollRequest) throw new Error("No active roll request");
  const req = activeRollRequest;
  const payload = {
    request_id: req.request_id,
    die_sides: sides,
    rolls: rolls,
    mode: mode || "normal",
    chosen: chosen,
    extras: {}
  };
  const out = await apiPost("/roll_requests/submit", payload);
  const result = (out && typeof out === 'object') ? (out.result || {}) : {};
  const extras = (result && typeof result.extras === 'object') ? result.extras : {};

  if (result.roll_kind === 'short_rest_hit_dice') {
    const healed = Number(extras.healed || 0);
    const spend = Number(extras.spent_hit_dice || 0);
    const rollsTxt = Array.isArray(result.rolls) ? result.rolls.join(', ') : '';
    setStatus(`Short rest roll: [${rollsTxt}] spent ${spend} hit dice and healed ${healed} HP.`, 'ok');
  } else if (result.roll_kind === 'levelup_hit_die') {
    const hpGain = Number(extras.hp_gain || 0);
    const newLevel = Number(extras.new_level || 0);
    const rollsTxt = Array.isArray(result.rolls) ? result.rolls.join(', ') : '';
    levelUpAwaitingRoll = false;
    activeLevelUpSignature = "";
    setStatus(`Level-up HP roll: [${rollsTxt}] gained ${hpGain} HP. You are now level ${newLevel || '?'}.`, 'ok');
  } else {
    setStatus("Roll submitted.", "ok");
  }
  activeRollRequest = null;
  needDamage = null;
  hasPending = false;
  activePendingAttack = null;
  await pollTick();
  await loadSheetOnce(true);
  await pollLevelUpStateOnce();
}

async function submitPendingAttackRoll(pa, mode, rolls) {
  if (!activeCharacterId) {
    throw new Error("No active character selected");
  }

  const cleanMode = String(mode || "normal").toLowerCase().trim();
  const payload = {
    pending_attack_id: pa.pending_attack_id,
    mode: cleanMode,
    rolls: Array.isArray(rolls) ? rolls : [rolls],
    attacker_character_id: activeCharacterId
  };

  await apiPost("/rolls", payload);
  setStatus("To-hit submitted.", "ok");
  await pollTick();
}

async function submitDamageLegacy() {
  if (!needDamage || !needDamage.attack_id) throw new Error("No damage requested");
  const dmg = rollDamage(needDamage.damage_expr, !!needDamage.crit);
  const payload = {
    attack_id: needDamage.attack_id,
    damage_roll: {
      expr: needDamage.damage_expr,
      dice: dmg.dice || [],
      modifier: dmg.modifier || 0,
      total: dmg.total || 0,
      crit_applied: !!dmg.crit_applied
    }
  };
  await apiPost("/damage_rolls", payload);
  setStatus("Damage submitted.", "ok");

  // Display
  const ro = el("rollOut"); if (ro) ro.textContent = JSON.stringify(dmg.dice || []);
  const co = el("chosenOut"); if (co) co.textContent = `total=${dmg.total}`;

  needDamage = null;
  await pollTick();
}

function bindRollButton() {
  const btn = document.querySelector("#btnRoll");
  if (!btn) return;

  btn.disabled = false;
  btn.onclick = null;
  btn.onclick = () => rollClicked().catch(e => {
    console.error("[ROLL] click failed", e);
    setStatus(`Roll failed: ${e}`, "err");
  });
}

async function rollClicked() {
  setStatus("ROLL CLICKED", "warn");
  console.log("[ROLL] clicked", {
    activeRollRequest,
    needDamage,
    activePendingAttack,
    activeCharacterId,
    activePlayerId
  });
  // 1) Generic roll requests (server-driven expected + adv_mode)
  if (activeRollRequest) {
    const sides = Number(activeRollRequest.expected_sides || 20);
    const reqMode = (activeRollRequest.adv_mode || "normal").toLowerCase().trim();
    const minCount = Number(activeRollRequest.expected_count_min || 1);

    const wantsTwo = (reqMode === "advantage" || reqMode === "disadvantage" || minCount >= 2);

    // bind buffer to request id
    const rid = String(activeRollRequest.request_id || "");
    if (armedBuffer._rid !== rid) {
      armedBuffer = [];
      armedBuffer._rid = rid;
    }

    const rollOne = () => 1 + Math.floor(Math.random() * sides);

    if (wantsTwo) {
      const r = rollOne();
      armedBuffer.push(r);

      const ro = el("rollOut"); if (ro) ro.textContent = JSON.stringify(armedBuffer);
      const co = el("chosenOut"); if (co) co.textContent = "—";

      if (armedBuffer.length < 2) {
        setStatus("Roll pending: 1/2 captured. Roll again.", "warn");
        return;
      }

      const rolls = armedBuffer.slice(0, 2);
      const chosen = (reqMode === "disadvantage") ? Math.min(...rolls) : Math.max(...rolls);

      const ro2 = el("rollOut"); if (ro2) ro2.textContent = JSON.stringify(rolls);
      const co2 = el("chosenOut"); if (co2) co2.textContent = String(chosen);

      await submitActiveRollRequest(reqMode, sides, rolls, chosen);
      armedBuffer = [];
      armedBuffer._rid = "";
      return;
    } else {
      const r = rollOne();
      const ro = el("rollOut"); if (ro) ro.textContent = JSON.stringify([r]);
      const co = el("chosenOut"); if (co) co.textContent = String(r);
      await submitActiveRollRequest("normal", sides, [r], r);
      return;
    }
  }

  // 2) Legacy NEED_DAMAGE (no UI panel; Roll submits damage)
  if (needDamage) {
    await submitDamageLegacy();
    return;
  }

  // 3) Pending attack to-hit (legacy Option B): FIFO auto-bound, hidden from UI
  if (activePendingAttack) {
    const reqMode = String(activePendingAttack.roll_mode || "normal").toLowerCase().trim();
    const wantsTwo = (reqMode === "advantage" || reqMode === "disadvantage" || reqMode === "adv" || reqMode === "dis");
    const rollOne = () => 1 + Math.floor(Math.random() * 20);

    const rid = String(activePendingAttack.pending_attack_id || "");
    if (armedBuffer._rid !== rid) {
      armedBuffer = [];
      armedBuffer._rid = rid;
    }

    if (wantsTwo) {
      const r = rollOne();
      armedBuffer.push(r);
      const ro = el("rollOut"); if (ro) ro.textContent = JSON.stringify(armedBuffer);
      const co = el("chosenOut"); if (co) co.textContent = "—";
      if (armedBuffer.length < 2) {
        setStatus("Attack roll pending: 1/2 captured. Roll again.", "warn");
        return;
      }
      const rolls = armedBuffer.slice(0, 2);
      const mode = (reqMode === "disadvantage" || reqMode === "dis") ? "disadvantage" : "advantage";
      const chosen = (mode === "disadvantage") ? Math.min(...rolls) : Math.max(...rolls);
      const ro2 = el("rollOut"); if (ro2) ro2.textContent = JSON.stringify(rolls);
      const co2 = el("chosenOut"); if (co2) co2.textContent = String(chosen);
      await submitPendingAttackRoll(activePendingAttack, mode, rolls);
      armedBuffer = [];
      armedBuffer._rid = "";
      return;
    }

    const r = rollOne();
    const ro = el("rollOut"); if (ro) ro.textContent = JSON.stringify([r]);
    const co = el("chosenOut"); if (co) co.textContent = String(r);
    await submitPendingAttackRoll(activePendingAttack, "normal", [r]);
    return;
  }

  // 4) Free roll (only when idle)
  const fs = el("freeSides");
  const sides = fs ? Number(fs.value || "20") : 20;
  const count = getFreeCount();

  const rolls = rollNDY(count, sides);
  const ro = el("rollOut"); if (ro) ro.textContent = JSON.stringify(rolls);

  // display: sum for multi-dice free rolls, else single
  const co = el("chosenOut");
  if (co) co.textContent = (count === 1) ? String(rolls[0]) : `sum=${rolls.reduce((a, b) => a + b, 0)}`;

  setStatus("Free roll complete.", "ok");
}

// ---------------------------
// Login + character selection
// ---------------------------
async function loadActiveCampaign() {
  // Single-campaign mode: always use server default
  try {
    const data = await apiGet("/campaigns"); // GET /api/campaigns
    const def = (data && data.default) ? String(data.default).trim() : "";
    if (def) return def;

    const arr = (data && Array.isArray(data.campaigns)) ? data.campaigns : [];
    if (arr.length) return String(arr[0]).trim();
  } catch (e) {
    console.warn("loadActiveCampaign fallback:", e);
  }

  // Final fallback if endpoint fails
  return "Test";
}

function setLoggedInUI(isLoggedIn) {
  const btnLogin = el("btnLogin");
  const btnLogout = el("btnLogout");
  const btnLogoutTop = el("btnLogoutTop");
  const characterSelect = el("characterSelect");
  const btnEnter = el("btnEnter");
  const btnCreate = el("btnCreateChar");

  if (btnLogin) btnLogin.disabled = isLoggedIn;
  if (btnLogout) btnLogout.disabled = !isLoggedIn;
  if (btnLogoutTop) {
    btnLogoutTop.disabled = !isLoggedIn;
    btnLogoutTop.classList.toggle("hidden", !isLoggedIn);
  }

  const btnLogoutFloat = el("btnLogoutFloat");
  if (btnLogoutFloat) {
    btnLogoutFloat.disabled = !isLoggedIn;
    // visible only when in app screen
    const as = el("appScreen");
    const inApp = as && !as.classList.contains("hidden");
    btnLogoutFloat.classList.toggle("hidden", !isLoggedIn || !inApp);
  }

  // Hide the big header when in app screen
  const topHeader = el("topHeader");
  if (topHeader) {
    const as = el("appScreen");
    const inApp = as && !as.classList.contains("hidden");
    topHeader.classList.toggle("hidden", inApp);
  }

  if (characterSelect) characterSelect.disabled = !isLoggedIn;
  if (btnEnter) btnEnter.disabled = !isLoggedIn;
  if (btnCreate) btnCreate.disabled = !isLoggedIn;
const btnNewNote = el("btnNewNote");
  const btnSaveNote = el("btnSaveNote");
  const btnDeleteNote = el("btnDeleteNote");
  if (btnNewNote) btnNewNote.disabled = true;
  if (btnSaveNote) btnSaveNote.disabled = true;
  if (btnDeleteNote) btnDeleteNote.disabled = true;
}

async function refreshCharacters() {
  const data = await apiGet("/characters");
  const chars = data.characters || [];
  const active = data.active_character_id || "";

  const sel = el("characterSelect");
  if (!sel) return;

  sel.innerHTML = "";
  for (const c of chars) {
    const opt = document.createElement("option");
    opt.value = c.character_id;
    opt.textContent = `${c.display_name || c.character_id}`;
    sel.appendChild(opt);
  }

  if (active && chars.find(x => x.character_id === active)) {
    sel.value = active;
  } else {
    // try last selection
    const last = localStorage.getItem("grengine_last_character_id") || "";
    if (last && chars.find(x => x.character_id === last)) sel.value = last;
  }

  if (!chars.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No characters assigned to this player.";
    sel.appendChild(opt);
  }
}


function toggleCreateCharPanel(show) {
  const panel = el("createCharPanel");
  if (!panel) return;
  panel.classList.toggle("hidden", !show);
}

function readCreateCharacterPayload() {
  const name = ((el("ccDisplayName") && el("ccDisplayName").value) || "").trim() || "New Character";
  const character_id = ((el("ccCharacterId") && el("ccCharacterId").value) || "").trim();
  const class_name = ((el("ccClass") && el("ccClass").value) || "").trim();
  const race = ((el("ccRace") && el("ccRace").value) || "").trim();
  const background = ((el("ccBackground") && el("ccBackground").value) || "").trim();
  const level = Math.max(1, Math.min(20, readInt("ccLevel", 1)));
  let max_hp = readInt("ccMaxHp", 10);
  if (!Number.isFinite(max_hp) || max_hp < 1) max_hp = 10;
  return { display_name: name, character_id, class_name, race, background, level, max_hp, current_hp: max_hp, auto_apply_defaults: true };
}

async function createCharConfirmClicked() {
  try {
    const payload = readCreateCharacterPayload();
    const data = await apiPost("/characters/create", payload);
    await refreshCharacters();
    const sel = el("characterSelect");
    if (sel && data && data.character_id) sel.value = data.character_id;
    toggleCreateCharPanel(false);
    setStatus("Character created with autofill. Select it and Enter.", "ok");
  } catch (e) {
    setStatus(`Create failed: ${e}`, "err");
  }
}

function createCharCancelClicked() {
  toggleCreateCharPanel(false);
}


async function createCharClicked() {
  toggleCreateCharPanel(true);
}

async function loginClicked() {
  try {
    const playerIdEl = el("playerId");
    const pinEl = el("pin");
    const playerId = playerIdEl ? (playerIdEl.value || "").trim() : "";
    const pin = pinEl ? (pinEl.value || "").trim() : "";

    if (!activeCampaignId) {
      activeCampaignId = "Test";
    }
    if (!playerId) { setStatus("Enter Player ID.", "warn"); return; }
    if (!pin) { setStatus("Enter PIN.", "warn"); return; }

    const r = await fetch(`${apiBase()}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_id: playerId, pin })
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`${r.status} ${text}`);
    const data = JSON.parse(text);

    setToken(data.token);
    activePlayerId = String(data.player_id || playerId || "");
    setLoggedInUI(true);
    setCampaignMeta(activeCampaignId, (activePlayerId||"—"), "—");
    setStatus(`Logged in as ${data.player_id}`, "ok");

    await refreshCharacters();
  } catch (e) {
    clearToken();
    setLoggedInUI(false);
    setStatus(`Login failed: ${e}`, "err");
  }
}

async function logoutClicked() {
  try { await apiPost("/auth/logout", {}); } catch (_) {}
  clearToken();
  setLoggedInUI(false);
  stopPolling();
  enableTabs(false);
  showLoginScreen();
  setStatus("Logged out.", "muted");
}

async function enterClicked() {
  const sel = el("characterSelect");
  const charId = sel ? (sel.value || "").trim() : "";
  if (!charId) { setStatus("Select a character first.", "warn"); return; }
  try {
    await apiPost("/characters/select", { character_id: charId });
    activeCharacterId = charId;
    localStorage.setItem("grengine_last_character_id", charId);

    // Update header meta
    const displayName = sel.options[sel.selectedIndex]?.textContent || charId;
    setCampaignMeta(activeCampaignId, (activePlayerId||"—"), displayName);

    showAppScreen();
    bindRollButton();
    enableTabs(true);
    startPolling();
    loadSheetOnce(true).catch(() => {});

    const btnNewNote = el("btnNewNote");
    const btnSaveNote = el("btnSaveNote");
    const btnDeleteNote = el("btnDeleteNote");
    if (btnNewNote) btnNewNote.disabled = false;
    if (btnSaveNote) btnSaveNote.disabled = false;
    if (btnDeleteNote) btnDeleteNote.disabled = false;
    setNoteEditingBanner();

    setStatus("Ready.", "ok");
  } catch (e) {
    setStatus(`Select failed: ${e}`, "err");
  }
}

// ---------------------------
// Boot
// ---------------------------
function wireUI() {
  console.log("[wireUI] start");
  const btnLogin = el("btnLogin"); if (btnLogin) btnLogin.onclick = loginClicked;
  const btnLogout = el("btnLogout");
  const btnLogoutTop = el("btnLogoutTop");
  const btnLogoutFloat = el("btnLogoutFloat");
  if (btnLogout) btnLogout.onclick = logoutClicked;
  if (btnLogoutTop) btnLogoutTop.onclick = logoutClicked;
  if (btnLogoutFloat) btnLogoutFloat.onclick = logoutClicked;
  const btnEnter = el("btnEnter"); if (btnEnter) btnEnter.onclick = enterClicked;
  const btnCreate = el("btnCreateChar"); if (btnCreate) btnCreate.onclick = createCharClicked;

const btnCreateOk = el("btnCreateCharConfirm");
const btnCreateCancel = el("btnCreateCharCancel");
// Avoid breaking wireUI if these panel helpers are not implemented.
if (btnCreateOk) btnCreateOk.onclick = createCharConfirmClicked;
if (btnCreateCancel) btnCreateCancel.onclick = createCharCancelClicked;
    bindRollButton();
  const btnRoll = el("btnRoll");
  if (btnRoll) {
    btnRoll.disabled = false;
    btnRoll.onclick = () => {
      console.log("[ROLL] onclick fired");
      return rollClicked().catch(e => {
        console.error("[ROLL] click failed", e);
        setStatus(`Roll failed: ${e}`, "err");
      });
    };
  }

  const btnNewNote = el("btnNewNote");
  if (btnNewNote) btnNewNote.onclick = () => clearNoteEditor();
  const btnSaveNote = el("btnSaveNote");
  if (btnSaveNote) btnSaveNote.onclick = () => saveNoteClicked().catch(e => setText("noteSaveOut", `Save failed: ${e}`));
  const btnDeleteNote = el("btnDeleteNote");
  if (btnDeleteNote) btnDeleteNote.onclick = () => deleteNoteClicked().catch(e => setText("noteSaveOut", `Delete failed: ${e}`));


// Sheet inputs (debounced PATCH)
const bind = (id, fn) => { const n = el(id); if (n) n.oninput = fn; };
bind("sheetName", () => queueSheetPatch("display_name", (el("sheetName").value || "").trim()));
bind("sheetClass", () => queueSheetPatch("meta.class", (el("sheetClass").value || "").trim()));
bind("sheetRace", () => queueSheetPatch("meta.race", (el("sheetRace").value || "").trim()));
bind("sheetBackground", () => queueSheetPatch("meta.background", (el("sheetBackground").value || "").trim()));
bind("sheetLevel", () => queueSheetPatch("meta.level", readInt("sheetLevel", 1)));
bind("sheetAlignment", () => queueSheetPatch("meta.alignment", (el("sheetAlignment").value || "").trim()));

bind("sheetCurHp", () => queueSheetPatch("stats.current_hp", readInt("sheetCurHp", 0)));
bind("sheetTempHp", () => queueSheetPatch("resources.temp_hp", readInt("sheetTempHp", 0)));
bind("sheetMaxHp", () => queueSheetPatch("stats.max_hp", readInt("sheetMaxHp", 10)));

const abMap = [
  ["abStr", "str"],
  ["abDex", "dex"],
  ["abCon", "con"],
  ["abInt", "int"],
  ["abWis", "wis"],
  ["abCha", "cha"],
];

for (const [id, key] of abMap) {
  const n = el(id);
  if (!n) continue;

  const commitAbility = async () => {
    let v = parseInt(String(n.value || "").trim(), 10);
    if (!Number.isFinite(v)) v = 10;
    v = Math.max(1, Math.min(30, v));
    n.value = String(v);

    // local preview first
    activeSheet = activeSheet || {};
    activeSheet.abilities = {
      ...(activeSheet.abilities || {}),
      [key]: v,
    };
    refreshAbilityModsFromInputs();

    try {
      await apiPatch("/sheet/mine", {
        patch: {
          abilities: {
            [key]: v,
          },
        },
        finalize: false,
      });
      await loadSheetOnce(true);
    } catch (e) {
      console.warn("[ABILITIES] save failed", e);
      setStatus(`Ability save failed: ${e}`, "err");
    }
  };

  n.oninput = refreshAbilityModsFromInputs;
  n.onchange = commitAbility;
  n.onblur = commitAbility;
}

// Other proficiencies (creation-only)
bind("profArmor", () => queueSheetPatch("proficiencies.other.armor", (el("profArmor").value || "").trim()));
bind("profWeapons", () => queueSheetPatch("proficiencies.other.weapons", (el("profWeapons").value || "").trim()));
bind("profTools", () => queueSheetPatch("proficiencies.other.tools", (el("profTools").value || "").trim()));
bind("profOther", () => queueSheetPatch("proficiencies.other.other", (el("profOther").value || "").trim()));

// Languages (creation-only)
// Languages (always editable)
// Languages (restored from 1.6.17 behaviour)
const btnLangAdd = el("btnLangAdd");
const langNewName = el("langNewName");

const refreshLangAddButton = () => {
  const nm = langNewName ? String(langNewName.value || "").trim() : "";
  if (langNewName) {
    langNewName.disabled = false;
    langNewName.removeAttribute("disabled");
  }
  if (btnLangAdd) {
    btnLangAdd.disabled = (nm.length === 0);
    if (nm.length === 0) btnLangAdd.setAttribute("disabled", "disabled");
    else btnLangAdd.removeAttribute("disabled");
  }
};

if (langNewName) {
  langNewName.disabled = false;
  langNewName.removeAttribute("disabled");
  langNewName.oninput = refreshLangAddButton;
  langNewName.onchange = refreshLangAddButton;
  langNewName.onkeyup = refreshLangAddButton;
}

if (btnLangAdd) {
  btnLangAdd.type = "button";
  btnLangAdd.onclick = async () => {
    const nm = langNewName ? String(langNewName.value || "").trim() : "";
    if (!nm) return;

    const sheet = activeSheet || {};
    const prof = (sheet.proficiencies && typeof sheet.proficiencies === "object")
      ? sheet.proficiencies
      : {};
    const langs = Array.isArray(prof.languages) ? prof.languages.slice() : [];

    const key = nm.toLowerCase();
    if (langs.some(l => l && typeof l === "object" && String(l.name || "").trim().toLowerCase() === key)) {
      if (langNewName) langNewName.value = "";
      refreshLangAddButton();
      return;
    }

    langs.push({
      name: nm,
      speak: true,
      read: true,
      write: true,
    });

    await _saveLanguages(langs);

    if (langNewName) langNewName.value = "";
    refreshLangAddButton();
  };
}

refreshLangAddButton();

// Combat helpers
const insp = el("combatInspiration");
if (insp) insp.onchange = () => queueSheetPatch("combat.inspiration", !!insp.checked);
// hit die fields are class-driven and read-only in the portal.
// hit die fields are class-driven and read-only in the portal.
// hit die fields are class-driven and read-only in the portal.
bind("dsSucc", () => queueSheetPatch("combat.death_saves.successes", readInt("dsSucc", 0)));
bind("dsFail", () => queueSheetPatch("combat.death_saves.failures", readInt("dsFail", 0)));

// Currency
bind("curCp", () => queueSheetPatch("currency.cp", readInt("curCp", 0)));
bind("curSp", () => queueSheetPatch("currency.sp", readInt("curSp", 0)));
bind("curGp", () => queueSheetPatch("currency.gp", readInt("curGp", 0)));

// Background
bind("bgTraits", () => queueSheetPatch("background.personality_traits", (el("bgTraits").value || "").trim()));
bind("bgIdeals", () => queueSheetPatch("background.ideals", (el("bgIdeals").value || "").trim()));
bind("bgBonds", () => queueSheetPatch("background.bonds", (el("bgBonds").value || "").trim()));
bind("bgFlaws", () => queueSheetPatch("background.flaws", (el("bgFlaws").value || "").trim()));
bind("bgBackstory", () => queueSheetPatch("background.backstory", (el("bgBackstory").value || "").trim()));

// Features / traits
// featTraits is generated/read-only in the portal.

// Details
bind("dtAge", () => queueSheetPatch("details.age", (el("dtAge").value || "").trim()));
bind("dtHeight", () => queueSheetPatch("details.height", (el("dtHeight").value || "").trim()));
bind("dtWeight", () => queueSheetPatch("details.weight", (el("dtWeight").value || "").trim()));
bind("dtEyes", () => queueSheetPatch("details.eyes", (el("dtEyes").value || "").trim()));
bind("dtSkin", () => queueSheetPatch("details.skin", (el("dtSkin").value || "").trim()));
bind("dtHair", () => queueSheetPatch("details.hair", (el("dtHair").value || "").trim()));
bind("dtAppearance", () => queueSheetPatch("details.appearance", (el("dtAppearance").value || "").trim()));
bind("dtAllies", () => queueSheetPatch("details.allies_and_organizations", (el("dtAllies").value || "").trim()));
bind("dtTreasure", () => queueSheetPatch("details.treasure", (el("dtTreasure").value || "").trim()));

const btnSpellChoiceClose = el("btnSpellChoiceClose");
if (btnSpellChoiceClose) btnSpellChoiceClose.onclick = () => closeSpellChoiceModal();

const btnSpellChoiceSave = el("btnSpellChoiceSave");
if (btnSpellChoiceSave) btnSpellChoiceSave.onclick = () => {
  commitSpellChoiceModal().catch(e => setText("spellManagerStatus", `Spell manager save failed: ${e.message || e}`));
};

const spellChoiceSearch = el("spellChoiceSearch");
if (spellChoiceSearch) spellChoiceSearch.oninput = () => refreshSpellChoiceModal();

const spellChoiceLevelFilter = el("spellChoiceLevelFilter");
if (spellChoiceLevelFilter) spellChoiceLevelFilter.onchange = () => refreshSpellChoiceModal();

const spellChoiceModal = el("spellChoiceModal");
if (spellChoiceModal) {
  spellChoiceModal.onclick = (ev) => {
    if (ev.target === spellChoiceModal) closeSpellChoiceModal();
  };
}

const btnFinalize = el("btnFinalize");
if (btnFinalize) btnFinalize.onclick = () => finalizeSheet().catch(e => setStatus(`Finalize failed: ${e}`, "err"));

  const freeCount = el("freeCount");
  if (freeCount) freeCount.onchange = () => getFreeCount();

  const btnExport = el('btnExportAudit');
  if (btnExport) btnExport.onclick = () => {
    try {
      const blob = new Blob([JSON.stringify({campaign_id: activeCampaignId, player_id: activePlayerId, feed: feedEntries, logs: lastLogs, at: Date.now()}, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `grengine_audit_${(activePlayerId||'player')}_${new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { console.warn('export failed', e); }
  };
  console.log("[wireUI] end");
}

async function boot() {
  wireUI();
  loadFreeCount();

  try {
    activeCampaignId = await loadActiveCampaign();
  } catch (e) {
    console.warn("Failed to determine active campaign, defaulting:", e);
    activeCampaignId = "Test";
  }

  setCampaignMeta(activeCampaignId, (activePlayerId||"—"), "—", "—");
  enableTabs(false);

  // Resume session if token exists
  const tok = getToken();
  if (!tok) {
    showLoginScreen();
    setLoggedInUI(false);
    setStatus("Please login.", "muted");
    return;
  }

  try {
    const me = await apiGet("/auth/me");
    activePlayerId = (me && me.player_id) ? String(me.player_id) : activePlayerId;
    setLoggedInUI(true);
    setCampaignMeta(activeCampaignId, (activePlayerId||"—"), "—");
    await refreshCharacters();

    // auto-enter if server has active_character_id or last saved
    const data = await apiGet("/auth/me");
    const activeChar = (data && data.active_character_id) ? String(data.active_character_id) : "";
    if (activeChar) {
      activeCharacterId = activeChar;
      // select and enter
      const sel = el("characterSelect");
      if (sel) sel.value = activeChar;
      await enterClicked();
      return;
    }

    showLoginScreen();
    setStatus("Select character to enter.", "ok");
  } catch (_) {
    clearToken();
    showLoginScreen();
    setLoggedInUI(false);
    setStatus("Session expired. Please login.", "warn");
  }
}

boot();
