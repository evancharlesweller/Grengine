# engine/services/map_metadata_service.py
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from engine.map_metadata import default_meta, load_meta_file, save_meta_file, validate_meta

def derive_meta_path(map_image_path: str) -> str:
    """Default: sibling file '<image>.meta.json'."""
    base, _ext = os.path.splitext(map_image_path)
    return base + ".meta.json"

def ensure_loaded(map_image_path: str, width_cells: int, height_cells: int, grid_px: int) -> Tuple[Dict[str, Any], str]:
    meta_path = derive_meta_path(map_image_path)
    meta = load_meta_file(meta_path, width_cells, height_cells, grid_px)
    meta = validate_meta(meta, width_cells, height_cells, grid_px)
    return meta, meta_path

def save(map_image_path: str, meta: Dict[str, Any], width_cells: int, height_cells: int, grid_px: int) -> Tuple[bool, str]:
    meta_path = derive_meta_path(map_image_path)
    try:
        meta2 = validate_meta(meta, width_cells, height_cells, grid_px)
        save_meta_file(meta_path, meta2)
        return True, meta_path
    except Exception:
        return False, meta_path