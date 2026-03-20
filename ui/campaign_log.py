# ui/campaign_log.py
import os
import json
import time
from typing import Any, Dict, Optional, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QSpinBox, QCheckBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------
# Logger (JSONL on disk)
# ----------------------------
class CampaignLogger:
    """
    Append-only campaign log. Writes JSON Lines to:
      <campaign_path>/campaign_log.jsonl
    """
    def __init__(self, campaign_path: str, filename: str = "campaign_log.jsonl"):
        self.campaign_path = campaign_path
        self.log_path = os.path.join(campaign_path, filename)
        os.makedirs(self.campaign_path, exist_ok=True)

    def _write(self, payload: Dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("ts", _now_iso())
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def system(self, event: str, **data: Any) -> None:
        self._write({"type": "system", "event": event, **data})

    def encounter(self, event: str, **data: Any) -> None:
        self._write({"type": "encounter", "event": event, **data})

    def combat(self, event: str, **data: Any) -> None:
        self._write({"type": "combat", "event": event, **data})

    def event(self, event: str, **data: Any) -> None:
        """Back-compat alias used throughout MainWindow for combat-facing events."""
        self._write({"type": "combat", "event": event, **data})

    def read_tail_lines(self, n: int = 200) -> List[str]:
        """
        Returns the last N lines as raw UTF-8 text lines.
        Reads from end of file (safe for larger logs).
        """
        if not os.path.exists(self.log_path):
            return []

        chunk_size = 64 * 1024
        data = b""
        with open(self.log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            pos = size

            while pos > 0 and data.count(b"\n") <= (n + 1):
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + data

        lines = data.splitlines()[-n:]
        out: List[str] = []
        for b in lines:
            try:
                out.append(b.decode("utf-8", errors="replace"))
            except Exception:
                out.append(str(b))
        return out


# ----------------------------
# Formatting helpers
# ----------------------------
def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _short_id(x: str) -> str:
    x = (x or "").strip()
    if not x:
        return ""
    # UUID-like => shorten
    if len(x) > 12:
        return x[:8]
    return x


def _fmt_kv_compact(obj: Dict[str, Any], keep: Optional[List[str]] = None) -> str:
    """
    Compact fallback: show a small set of useful keys.
    """
    if keep is None:
        keep = [
            "pending_attack_id", "attacker_token_id", "target_token_id",
            "player_id", "weapon_ref", "d20", "total_attack", "target_ac",
            "damage", "target_hp", "target_max_hp"
        ]
    parts = []
    for k in keep:
        if k in obj:
            v = obj.get(k)
            if v is None or v == "":
                continue
            parts.append(f"{k}={v}")
    return " ".join(parts)


def format_log_event(obj: Dict[str, Any], *, show_ts: bool = False) -> str:
    """
    Convert a JSON log record into a readable console-like line.
    Your logger uses: type, event, ts.
    """
    ts = obj.get("ts", "")
    typ = (obj.get("type") or "").lower()
    ev = (obj.get("event") or "").lower()

    prefix = f"[{ts}] " if (show_ts and ts) else ""

    # ---- SYSTEM / ENCOUNTER ----
    if typ == "system":
        # Example: system("Campaign loaded", campaign_path=...)
        msg = obj.get("event", "")
        return f"{prefix}[SYS] {msg}"

    if typ == "encounter":
        # Example: encounter save/load
        if ev == "save":
            p = obj.get("save_path", "")
            m = obj.get("map", "")
            n = obj.get("token_count", "")
            return f"{prefix}[ENC] Saved encounter tokens={n} map={m} path={p}"
        if ev == "load":
            p = obj.get("load_path", "")
            return f"{prefix}[ENC] Loading encounter path={p}"
        if ev == "loaded":
            m = obj.get("map", "")
            n = obj.get("token_count", "")
            return f"{prefix}[ENC] Loaded encounter tokens={n} map={m}"
        return f"{prefix}[ENC] {obj.get('event','')} {_fmt_kv_compact(obj)}".rstrip()

    if typ == "combat":
        # --- Pending / ARM events ---
        if ev == "arm_pc_attack":
            pid = _short_id(obj.get("pending_attack_id", ""))
            atk = _short_id(obj.get("attacker_token_id", ""))
            tgt = _short_id(obj.get("target_token_id", ""))
            player = obj.get("player_id", "")
            weapon = obj.get("weapon_ref", "")
            return f"{prefix}[ARM] PC attack armed pid={pid} attacker={atk} target={tgt} player={player} weapon={weapon}".rstrip()

        if ev == "cancel_pending":
            pid = _short_id(obj.get("pending_attack_id", ""))
            return f"{prefix}[ARM] Cancelled pending attack pid={pid}"

        if ev == "pending_expired":
            pid = _short_id(obj.get("pending_attack_id", ""))
            return f"{prefix}[ARM] Pending attack expired pid={pid}"

        if ev == "force_resolve_npc":
            atk = _short_id(obj.get("attacker_token_id", ""))
            tgt = _short_id(obj.get("target_token_id", ""))
            return f"{prefix}[NPC] Force resolve attacker={atk} target={tgt}"

        # --- NPC events (THIS is what you just added) ---
        if ev == "npc_attack_aborted_missing_tokens":
            atk = _short_id(obj.get("attacker_token_id", ""))
            tgt = _short_id(obj.get("target_token_id", ""))
            return f"{prefix}[NPC] Attacker/target missing in state. attacker={atk} target={tgt}".rstrip()

        if ev == "npc_attack_start":
            an = obj.get("attacker_name", "") or _short_id(obj.get("attacker_token_id", ""))
            tn = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            return f"{prefix}[ARM] Attacker set to {an} ({obj.get('attacker_token_id','')}) | Target set to {tn} ({obj.get('target_token_id','')})".rstrip()

        if ev == "npc_weapon_not_found":
            weapon = obj.get("weapon_ref", "")
            return f"{prefix}[NPC] Weapon '{weapon}' not found in items.json".rstrip()

        if ev == "npc_attack_roll":
            an = obj.get("attacker_name", "") or _short_id(obj.get("attacker_token_id", ""))
            tn = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            d20 = _safe_int(obj.get("d20", 0))
            total = _safe_int(obj.get("total_attack", 0))
            ac = _safe_int(obj.get("target_ac", 0))
            hit = bool(obj.get("hit", False))
            return f"{prefix}[NPC] {an} -> {tn}: d20={d20} total={total} vs AC={ac} => {'HIT' if hit else 'MISS'}".rstrip()

        if ev == "npc_hit":
            dmg_expr = obj.get("damage_expr", "")
            dmg = _safe_int(obj.get("damage", 0))
            breakdown = obj.get("damage_breakdown", "")
            hp = _safe_int(obj.get("target_hp", 0))
            mhp = _safe_int(obj.get("target_max_hp", 0))
            return f"{prefix}[NPC] Damage {dmg_expr} => {dmg} ({breakdown}) | Target HP now {hp}/{mhp}".rstrip()

        # --- PC roll pipeline ---
        if ev == "roll_received":
            pid = _short_id(obj.get("pending_attack_id", ""))
            player = obj.get("player_id", "")
            mode = obj.get("mode", "")
            d20 = _safe_int(obj.get("d20", 0))
            return f"{prefix}[PC] Roll received d20={d20} mode={mode} player={player} pid={pid}".rstrip()

        if ev == "miss":
            an = obj.get("attacker_name", "") or _short_id(obj.get("attacker_token_id", ""))
            tn = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            total = _safe_int(obj.get("total_attack", 0))
            ac = _safe_int(obj.get("target_ac", 0))
            nat20 = bool(obj.get("nat20", False))
            nat1 = bool(obj.get("nat1", False))
            flags = []
            if nat20:
                flags.append("NAT20")
            if nat1:
                flags.append("NAT1")
            f = (" " + ",".join(flags)) if flags else ""
            return f"{prefix}[PC] {an} -> {tn}: total={total} vs AC={ac} => MISS{f}".rstrip()

        if ev == "hit":
            an = obj.get("attacker_name", "") or _short_id(obj.get("attacker_token_id", ""))
            tn = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            total = _safe_int(obj.get("total_attack", 0))
            ac = _safe_int(obj.get("target_ac", 0))
            dmg_expr = obj.get("damage_expr", "")
            dmg = _safe_int(obj.get("damage", 0))
            breakdown = obj.get("damage_breakdown", "")
            hp = _safe_int(obj.get("target_hp", 0))
            mhp = _safe_int(obj.get("target_max_hp", 0))
            return (
                f"{prefix}[PC] {an} -> {tn}: total={total} vs AC={ac} => HIT | "
                f"Damage {dmg_expr} => {dmg} ({breakdown}) | Target HP now {hp}/{mhp}"
            ).rstrip()

        if ev == "reaction_refreshed":
            name = obj.get("name", "") or _short_id(obj.get("token_id", ""))
            return f"{prefix}[REACTION] {name} reaction refreshed".rstrip()

        if ev == "reaction_spent":
            name = obj.get("name", "") or _short_id(obj.get("token_id", ""))
            reason = obj.get("reason", "reaction")
            return f"{prefix}[REACTION] {name} spent reaction ({reason})".rstrip()

        if ev == "reaction_requested":
            rk = obj.get("reaction_kind", "reaction")
            reactor = obj.get("reactor_name", "") or _short_id(obj.get("reactor_token_id", ""))
            target = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            return f"{prefix}[REACTION] Request {rk} {reactor} -> {target}".rstrip()

        if ev == "reaction_resolved":
            rk = obj.get("reaction_kind", "reaction")
            reactor = obj.get("reactor_name", "") or _short_id(obj.get("reactor_token_id", ""))
            target = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            result = obj.get("result", "")
            total = _safe_int(obj.get("total_attack", 0))
            ac = _safe_int(obj.get("target_ac", 0))
            return f"{prefix}[REACTION] {rk} {reactor} -> {target}: total={total} vs AC={ac} => {result}".rstrip()

        if ev == "reaction_damage":
            rk = obj.get("reaction_kind", "reaction")
            reactor = obj.get("reactor_name", "") or _short_id(obj.get("reactor_token_id", ""))
            target = obj.get("target_name", "") or _short_id(obj.get("target_token_id", ""))
            dmg = _safe_int(obj.get("damage", 0))
            dtype = obj.get("damage_type", "")
            return f"{prefix}[REACTION] {rk} damage {reactor} -> {target}: {dmg} {dtype}".rstrip()

        if ev == "save_requested":
            name = obj.get("name", "") or _short_id(obj.get("token_id", ""))
            ability = obj.get("ability", "")
            dc = _safe_int(obj.get("dc", 0))
            mode = obj.get("mode", "normal")
            label = obj.get("label", "")
            return f"{prefix}[SAVE] Requested {ability} DC {dc} for {name} mode={mode} label={label}".rstrip()

        if ev == "save_resolved":
            name = obj.get("name", "") or _short_id(obj.get("token_id", ""))
            ability = obj.get("ability", "")
            d20 = _safe_int(obj.get("d20", 0))
            mod = _safe_int(obj.get("modifier", 0))
            total = _safe_int(obj.get("total", 0))
            dc = _safe_int(obj.get("dc", 0))
            success = bool(obj.get("success", False))
            return f"{prefix}[SAVE] {name} {ability}: d20={d20} mod={mod} total={total} vs DC {dc} => {'SUCCESS' if success else 'FAIL'}".rstrip()

        if ev == "death_save":
            name = obj.get("display_name", "") or _short_id(obj.get("token_id", ""))
            d20 = _safe_int(obj.get("d20", 0))
            s = _safe_int(obj.get("successes", 0))
            f = _safe_int(obj.get("failures", 0))
            st = obj.get("death_state", "")
            hp = _safe_int(obj.get("hp", 0))
            return f"{prefix}[PC] Death save {name}: d20={d20} S={s} F={f} state={st} hp={hp}".rstrip()

        if ev == "damage_applied":
            name = obj.get("token_name", "") or _short_id(obj.get("token_id", ""))
            base = _safe_int(obj.get("base_damage", obj.get("amount", 0)))
            final = _safe_int(obj.get("final_damage", obj.get("amount", 0)))
            hp_before = _safe_int(obj.get("hp_before", 0))
            hp_after = _safe_int(obj.get("hp_after", 0))
            max_hp = _safe_int(obj.get("max_hp_after", obj.get("target_max_hp", 0)))
            dtype = obj.get("damage_type", "")
            source = obj.get("source_kind", "")
            steps = ",".join(list(obj.get("damage_steps", []) or []))
            extras = []
            if dtype:
                extras.append(dtype)
            if source:
                extras.append(source)
            if steps:
                extras.append(steps)
            extra_txt = f" [{' | '.join(extras)}]" if extras else ""
            return f"{prefix}[DMG] {name}: {hp_before}/{max_hp} -> {hp_after}/{max_hp} amt={final} (base={base}){extra_txt}".rstrip()

        if ev == "hazard_triggered":
            name = obj.get("token_name", "") or _short_id(obj.get("token_id", ""))
            hx = _safe_int(obj.get("gx", 0)); hy = _safe_int(obj.get("gy", 0))
            trig = obj.get("trigger", "")
            htype = obj.get("hazard_type", "hazard")
            dmg = _safe_int(obj.get("damage", 0))
            return f"{prefix}[HAZ] {name} @({hx},{hy}) {trig} {htype} => {dmg}".rstrip()

        if ev == "cloud_damage":
            name = obj.get("token_name", "") or _short_id(obj.get("token_id", ""))
            kind = obj.get("cloud_kind", "cloud")
            trig = obj.get("trigger", "")
            dmg = _safe_int(obj.get("damage", 0))
            dtype = obj.get("damage_type", "")
            return f"{prefix}[CLOUD] {name} {kind} {trig} => {dmg} {dtype}".rstrip()

        if ev == "fall_damage":
            name = obj.get("token_name", "") or _short_id(obj.get("token_id", ""))
            drop_ft = _safe_int(obj.get("drop_ft", 0))
            dmg = _safe_int(obj.get("damage", 0))
            saved = bool(obj.get("saved", False))
            return f"{prefix}[FALL] {name} drop={drop_ft}ft => {dmg} {'(saved)' if saved else '(failed save)'}".rstrip()

        if ev == "status_damage":
            name = obj.get("name", "") or _short_id(obj.get("token_id", ""))
            src = obj.get("source", "status")
            amt = _safe_int(obj.get("amount", 0))
            dtype = obj.get("damage_type", "")
            return f"{prefix}[STATUS] {name} {src} => {amt} {dtype}".rstrip()

        if ev == "save_effect_resolved":
            name = obj.get("token_name", "") or _short_id(obj.get("token_id", ""))
            after = _safe_int(obj.get("damage_after", 0))
            before = _safe_int(obj.get("damage_before", 0))
            success = bool(obj.get("success", False))
            sk = obj.get("source_kind", "save")
            return f"{prefix}[SAVE] {name} {sk}: {'SUCCESS' if success else 'FAIL'} damage {before}->{after}".rstrip()

        # fallback combat line (compact, not full dict)
        return f"{prefix}[COMBAT] {obj.get('event','')} {_fmt_kv_compact(obj)}".rstrip()


    # ---- Default fallback ----
    # Still readable, not dumping full dict
    return f"{prefix}[{(typ or 'log').upper()}] {obj.get('event','')} {_fmt_kv_compact(obj)}".rstrip()


# ----------------------------
# Widget
# ----------------------------
class CampaignLogWidget(QWidget):
    """
    Live view of CampaignLogger JSONL file.
    Provides .refresh() so MainWindow can timer-call it.
    """
    def __init__(self, logger: CampaignLogger, initial_tail: int = 200, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.logger = logger
        self._tail_n = int(initial_tail)
        self._show_ts = False

        # When paused, refresh() becomes a no-op (prevents Clear View from re-filling)
        self._paused = False

        layout = QVBoxLayout(self)

        # Top controls
        top = QHBoxLayout()
        top.addWidget(QLabel("Tail:"))

        self.tail_spin = QSpinBox()
        self.tail_spin.setRange(10, 5000)
        self.tail_spin.setValue(self._tail_n)
        self.tail_spin.valueChanged.connect(self._on_tail_changed)
        top.addWidget(self.tail_spin)

        self.chk_combat_only = QCheckBox("Combat only")
        self.chk_combat_only.setChecked(False)
        self.chk_combat_only.stateChanged.connect(lambda _: self.refresh(force=True))
        top.addWidget(self.chk_combat_only)

        self.chk_show_ts = QCheckBox("Show timestamps")
        self.chk_show_ts.setChecked(False)
        self.chk_show_ts.stateChanged.connect(self._on_show_ts_changed)
        top.addWidget(self.chk_show_ts)

        # NEW: Pause (freeze view)
        self.chk_pause = QCheckBox("Pause")
        self.chk_pause.setChecked(False)
        self.chk_pause.stateChanged.connect(self._on_pause_changed)
        top.addWidget(self.chk_pause)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(lambda: self.refresh(force=True))
        top.addWidget(self.btn_refresh)

        self.btn_clear = QPushButton("Clear View")
        self.btn_clear.clicked.connect(self._clear_view_only)
        top.addWidget(self.btn_clear)

        top.addStretch(1)
        layout.addLayout(top)

        # Text view
        self.text = QTextEdit()
        self.text.setReadOnly(True)

        # Wrap lines so you can read long entries
        self.text.setLineWrapMode(QTextEdit.WidgetWidth)

        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)

        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        self.text.setFont(font)

        layout.addWidget(self.text)

        self.refresh(force=True)

    def _on_tail_changed(self, v: int) -> None:
        self._tail_n = int(v)
        self.refresh(force=True)

    def _on_show_ts_changed(self) -> None:
        self._show_ts = self.chk_show_ts.isChecked()
        self.refresh(force=True)

    def _on_pause_changed(self) -> None:
        self._paused = self.chk_pause.isChecked()

    def _clear_view_only(self) -> None:
        # Clear the widget AND pause so it doesn't instantly refill on the next timer refresh
        self.text.clear()
        self.chk_pause.setChecked(True)  # sets self._paused via handler

    def _is_scrolled_to_bottom(self) -> bool:
        vbar = self.text.verticalScrollBar()
        return vbar.value() >= (vbar.maximum() - 2)

    def refresh(self, force: bool = False) -> None:
        """
        Reload the last N log lines and display them.
        - If paused and not forced, no-op.
        - Preserves scroll position unless user is at bottom (then stays at bottom).
        """
        if self._paused and not force:
            return

        vbar = self.text.verticalScrollBar()
        at_bottom = self._is_scrolled_to_bottom()
        prev_value = vbar.value()

        lines = self.logger.read_tail_lines(self._tail_n)
        out_lines: List[str] = []

        combat_only = self.chk_combat_only.isChecked()

        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except Exception:
                out_lines.append(line)
                continue

            if combat_only and (obj.get("type") != "combat"):
                continue

            out_lines.append(format_log_event(obj, show_ts=self._show_ts))

        self.text.setPlainText("\n".join(out_lines))

        # Never force horizontal scroll to the right
        self.text.horizontalScrollBar().setValue(0)

        # Restore scroll position
        if at_bottom:
            vbar.setValue(vbar.maximum())
        else:
            # Clamp to new range
            vbar.setValue(min(prev_value, vbar.maximum()))