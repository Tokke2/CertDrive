# -*- coding: utf-8 -*-
"""Persistent association between certificate templates and Brother labels."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_label_mappings(path: Path | str) -> dict[str, str]:
    source = Path(path)
    if not source.is_file():
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
    mapping: Any = data.get("templates", {}) if isinstance(data, dict) else {}
    if not isinstance(mapping, dict):
        return {}
    result: dict[str, str] = {}
    for template, label in mapping.items():
        template_name = str(template or "").strip()
        label_name = str(label or "").strip()
        if (
            template_name
            and label_name
            and Path(label_name).name == label_name
            and Path(label_name).suffix.casefold() == ".lbx"
        ):
            result[template_name] = label_name
    return result


def save_label_mappings(path: Path | str, mappings: dict[str, str]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {
        str(template).strip(): str(label).strip()
        for template, label in mappings.items()
        if str(template).strip()
        and str(label).strip()
        and Path(str(label).strip()).name == str(label).strip()
        and Path(str(label).strip()).suffix.casefold() == ".lbx"
    }
    payload = {"version": 1, "templates": cleaned}
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
