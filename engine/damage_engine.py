from __future__ import annotations

from typing import Any, Dict, Iterable


def normalize_damage_type(value: str) -> str:
    text = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'necrotic': 'necrotic',
        'radiant': 'radiant',
        'fire': 'fire',
        'cold': 'cold',
        'lightning': 'lightning',
        'acid': 'acid',
        'poison': 'poison',
        'psychic': 'psychic',
        'force': 'force',
        'thunder': 'thunder',
        'piercing': 'piercing',
        'slashing': 'slashing',
        'bludgeoning': 'bludgeoning',
        'fall': 'fall',
        'falling': 'fall',
    }
    return aliases.get(text, text)


def normalize_tag(value: str) -> str:
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_')


def _normalize_str_collection(value: Any, *, as_tags: bool = False) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        raw = value.replace(';', ',').split(',')
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = []
    for item in raw:
        norm = normalize_tag(item) if as_tags else normalize_damage_type(item)
        if norm and norm not in out:
            out.append(norm)
    return out


def _merge_bucket(out: list[str], incoming: Iterable[str], *, as_tags: bool = False) -> None:
    for item in incoming:
        norm = normalize_tag(item) if as_tags else normalize_damage_type(item)
        if norm and norm not in out:
            out.append(norm)


def _build_rule(entry: Any, default_bucket: str = 'resistance') -> Dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    action = str(entry.get('action') or entry.get('kind') or entry.get('bucket') or default_bucket).strip().lower()
    if action not in ('resistance', 'immunity', 'vulnerability'):
        action = default_bucket
    damage_types = (
        entry.get('damage_types')
        or entry.get('types')
        or entry.get('damage_type')
        or entry.get('type')
        or []
    )
    require_tags = (
        entry.get('require_tags')
        or entry.get('required_tags')
        or entry.get('all_tags')
        or entry.get('tags')
        or []
    )
    unless_tags = (
        entry.get('unless_tags')
        or entry.get('ignore_if_tags')
        or entry.get('bypassed_by_tags')
        or entry.get('except_tags')
        or []
    )
    source_kinds = entry.get('source_kinds') or entry.get('source_kind') or []
    return {
        'action': action,
        'damage_types': _normalize_str_collection(damage_types),
        'require_tags': _normalize_str_collection(require_tags, as_tags=True),
        'unless_tags': _normalize_str_collection(unless_tags, as_tags=True),
        'source_kinds': _normalize_str_collection(source_kinds, as_tags=True),
        'label': str(entry.get('label', '') or '').strip(),
    }


def extract_source_meta(source: Any) -> Dict[str, Any]:
    if isinstance(source, dict):
        raw = dict(source)
    else:
        raw = {}
    tags: list[str] = []
    _merge_bucket(tags, _normalize_str_collection(raw.get('tags', []), as_tags=True), as_tags=True)
    _merge_bucket(tags, _normalize_str_collection(raw.get('properties', []), as_tags=True), as_tags=True)

    source_kind = normalize_tag(raw.get('source_kind', '') or raw.get('kind', '') or raw.get('category', ''))
    if source_kind:
        _merge_bucket(tags, [source_kind], as_tags=True)

    material = normalize_tag(raw.get('material', '') or raw.get('weapon_material', '') or '')
    if material:
        _merge_bucket(tags, [material], as_tags=True)

    if raw.get('is_weapon', False) or source_kind == 'weapon':
        _merge_bucket(tags, ['weapon'], as_tags=True)
    if raw.get('is_spell', False) or source_kind == 'spell':
        _merge_bucket(tags, ['spell'], as_tags=True)
    if bool(raw.get('magical', False) or raw.get('is_magical', False)):
        _merge_bucket(tags, ['magical'], as_tags=True)
    else:
        _merge_bucket(tags, ['nonmagical'], as_tags=True)
    if bool(raw.get('silvered', False) or raw.get('is_silvered', False)):
        _merge_bucket(tags, ['silvered'], as_tags=True)
    return {
        'tags': tags,
        'source_kind': source_kind,
    }


def extract_damage_profile(actor: Any) -> Dict[str, Any]:
    buckets = {
        'immunities': [],
        'resistances': [],
        'vulnerabilities': [],
    }
    reductions: Dict[str, int] = {}
    conditional_rules: list[Dict[str, Any]] = []

    direct_mappings = [
        ('damage_immunities', 'immunities'),
        ('immunities', 'immunities'),
        ('damage_resistances', 'resistances'),
        ('resistances', 'resistances'),
        ('damage_vulnerabilities', 'vulnerabilities'),
        ('vulnerabilities', 'vulnerabilities'),
    ]
    for attr_name, bucket_name in direct_mappings:
        try:
            raw_attr = getattr(actor, attr_name, [])
            _merge_bucket(buckets[bucket_name], _normalize_str_collection(raw_attr))
            if isinstance(raw_attr, (list, tuple)):
                for item in raw_attr:
                    rule = _build_rule(item, bucket_name[:-1] if bucket_name.endswith('s') else bucket_name)
                    if rule:
                        conditional_rules.append(rule)
        except Exception:
            pass

    try:
        profile = dict(getattr(actor, 'damage_profile', {}) or {})
    except Exception:
        profile = {}
    for bucket_name in ('immunities', 'resistances', 'vulnerabilities'):
        raw_bucket = profile.get(bucket_name, [])
        _merge_bucket(buckets[bucket_name], _normalize_str_collection(raw_bucket))
        if isinstance(raw_bucket, (list, tuple)):
            for item in raw_bucket:
                rule = _build_rule(item, bucket_name[:-1] if bucket_name.endswith('s') else bucket_name)
                if rule:
                    conditional_rules.append(rule)

    for rules_key in ('rules', 'conditional_rules', 'modifiers'):
        raw_rules = profile.get(rules_key, [])
        if isinstance(raw_rules, (list, tuple)):
            for item in raw_rules:
                rule = _build_rule(item)
                if rule:
                    conditional_rules.append(rule)

    raw_reductions = profile.get('reductions', {}) or {}
    if isinstance(raw_reductions, dict):
        for k, v in raw_reductions.items():
            key = normalize_damage_type(k)
            try:
                reductions[key] = max(int(v), reductions.get(key, 0))
            except Exception:
                continue

    return {
        'immunities': buckets['immunities'],
        'resistances': buckets['resistances'],
        'vulnerabilities': buckets['vulnerabilities'],
        'reductions': reductions,
        'conditional_rules': conditional_rules,
    }


def _rule_applies(rule: Dict[str, Any], damage_type: str, source_meta: Dict[str, Any]) -> bool:
    rule_types = set(rule.get('damage_types', []) or [])
    if rule_types and damage_type not in rule_types:
        return False
    source_tags = set(source_meta.get('tags', []) or [])
    required = set(rule.get('require_tags', []) or [])
    if required and not required.issubset(source_tags):
        return False
    unless = set(rule.get('unless_tags', []) or [])
    if unless and source_tags.intersection(unless):
        return False
    source_kinds = set(rule.get('source_kinds', []) or [])
    source_kind = normalize_tag(source_meta.get('source_kind', '') or '')
    if source_kinds and source_kind not in source_kinds:
        return False
    return True


def resolve_damage(amount: int, damage_type: str = '', *, actor: Any = None, source: Any = None) -> Dict[str, Any]:
    base = max(0, int(amount or 0))
    dtype = normalize_damage_type(damage_type)
    profile = extract_damage_profile(actor) if actor is not None else {
        'immunities': [], 'resistances': [], 'vulnerabilities': [], 'reductions': {}, 'conditional_rules': []
    }
    source_meta = extract_source_meta(source)
    steps: list[str] = []
    final = base

    if dtype and dtype in set(profile.get('immunities', []) or []):
        steps.append(f'immunity:{dtype}')
        final = 0
    else:
        if dtype and dtype in set(profile.get('resistances', []) or []):
            final = final // 2
            steps.append(f'resistance:{dtype}')
        if dtype and dtype in set(profile.get('vulnerabilities', []) or []):
            final = final * 2
            steps.append(f'vulnerability:{dtype}')

        for rule in list(profile.get('conditional_rules', []) or []):
            if not _rule_applies(rule, dtype, source_meta):
                continue
            action = str(rule.get('action', '') or '')
            label = str(rule.get('label', '') or '').strip()
            suffix = f':{label}' if label else ''
            if action == 'immunity':
                final = 0
                steps.append(f'conditional_immunity:{dtype}{suffix}')
                break
            if action == 'resistance':
                final = final // 2
                steps.append(f'conditional_resistance:{dtype}{suffix}')
            elif action == 'vulnerability':
                final = final * 2
                steps.append(f'conditional_vulnerability:{dtype}{suffix}')

        reductions = dict(profile.get('reductions', {}) or {})
        red = int(reductions.get(dtype, reductions.get('all', 0)) or 0)
        if red > 0:
            final = max(0, final - red)
            steps.append(f'reduction:{red}')

    return {
        'base_damage': int(base),
        'final_damage': int(max(0, final)),
        'damage_type': dtype,
        'steps': steps,
        'profile': profile,
        'source_meta': source_meta,
        'resisted': any('resistance:' in s for s in steps),
        'immune': any('immunity:' in s for s in steps),
        'vulnerable': any('vulnerability:' in s for s in steps),
    }
