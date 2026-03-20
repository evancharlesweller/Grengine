from typing import Any, Dict, List

from .subclasses import collect_subclass_spell_refs, resolve_spell_refs


def subclass_spell_grants(campaign_id: str, sheet: Dict[str, Any], spells_db: Dict[str, Any]) -> Dict[str, Any]:
    class_levels = sheet.get('class_levels') if isinstance(sheet.get('class_levels'), dict) else {}
    subclasses = sheet.get('subclasses') if isinstance(sheet.get('subclasses'), dict) else {}
    out = {
        "always_prepared": [],
        "bonus_known": [],
        "expanded_access_spell_ids": [],
        "expanded_access_meta": [],
    }
    seen = set()
    for class_key, subclass_id in subclasses.items():
        ck = str(class_key or '').strip().lower()
        level = int(class_levels.get(ck, 0) or 0)
        refs = collect_subclass_spell_refs(ck, subclass_id, level)
        for key in ('always_prepared', 'bonus_known'):
            resolved = resolve_spell_refs(spells_db, refs.get(key) or [])
            for sid in resolved:
                if (key, sid) in seen:
                    continue
                seen.add((key, sid))
                out[key].append(sid)
        for exp in refs.get('expanded_access') or []:
            if not isinstance(exp, dict):
                continue
            meta = dict(exp)
            meta['class_key'] = ck
            meta['subclass_id'] = str(subclass_id or '').strip().lower()
            meta['class_level'] = level
            out['expanded_access_meta'].append(meta)

            spell_names = exp.get('spell_names') if isinstance(exp, dict) else []
            resolved = resolve_spell_refs(spells_db, spell_names or [])
            for sid in resolved:
                if sid not in out['expanded_access_spell_ids']:
                    out['expanded_access_spell_ids'].append(sid)

            schools = [str(s or '').strip().lower() for s in (exp.get('schools') or []) if str(s or '').strip()]
            if schools:
                cap = _expanded_access_spell_level_cap(ck, level)
                for sid, row in _iter_spell_rows(spells_db):
                    school = str(row.get('school') or '').strip().lower()
                    spell_level = _spell_level_from_row(row)
                    if school not in schools:
                        continue
                    if cap > 0 and spell_level > cap:
                        continue
                    if sid not in out['expanded_access_spell_ids']:
                        out['expanded_access_spell_ids'].append(sid)
    return out


def _iter_spell_rows(spells_db: Dict[str, Any]):
    if not isinstance(spells_db, dict):
        return []
    rows = spells_db.get('spells') if isinstance(spells_db.get('spells'), list) else None
    if isinstance(rows, list):
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get('spell_id') or row.get('id') or '').strip()
            if sid:
                out.append((sid, row))
        return out
    out = []
    for sid, row in spells_db.items():
        if isinstance(row, dict):
            out.append((str(sid), row))
    return out


def _spell_level_from_row(row: Dict[str, Any]) -> int:
    try:
        return max(0, int((row or {}).get('level', 0) or 0))
    except Exception:
        return 0


def _expanded_access_spell_level_cap(class_key: str, class_level: int) -> int:
    ck = str(class_key or '').strip().lower()
    lvl = max(0, int(class_level or 0))
    if ck in {'fighter', 'rogue'}:
        if lvl >= 19:
            return 4
        if lvl >= 13:
            return 3
        if lvl >= 7:
            return 2
        if lvl >= 3:
            return 1
        return 0
    return 9
