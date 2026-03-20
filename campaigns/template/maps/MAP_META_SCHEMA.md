

## Hazards (B-X1)

Optional `hazards` list.

```json
"hazards": [
  {"x": 10, "y": 4, "trigger": "enter", "hazard_type": "fire", "damage": "1d6"},
  {"x": 12, "y": 7, "trigger": "turn_start", "hazard_type": "poison", "damage": "1d4+1"}
]
```

- `trigger`: `enter` | `turn_start` | `turn_end`
- `hazard_type`: free text identifier (recommended: fire/acid/poison/pit/etc.)
- `damage`: dice expression (`NdS+M`) or integer string
