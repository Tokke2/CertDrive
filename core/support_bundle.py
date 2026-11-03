"""Privacy-first export of sanitized application diagnostics."""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Explicit allowlist: never include logs, paths, settings, customer data, labels,
# certificate files, printer names, or arbitrary keys passed by the caller.
_FIELD_ALLOWLIST: dict[str, frozenset[str]] = {
    "app": frozenset({"name", "version", "current_commit"}),
    "runtime": frozenset({"python", "os", "os_release", "architecture", "qt"}),
    "plugins": frozenset({"loaded", "failed"}),
    "updater": frozenset({"pending", "pending_commit"}),
    "label_printer": frozenset({
        "bpac_available",
        "installed_printer_count",
        "selected_printer_available",
        "driver_available",
        "label_mapping_available",
        "automatic_print_ready",
    }),
    "display": frozenset({"width", "height", "scale_factor"}),
}


def _sanitize(diagnostics: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for section, allowed_fields in _FIELD_ALLOWLIST.items():
        source = diagnostics.get(section)
        if not isinstance(source, dict):
            continue
        values: dict[str, Any] = {}
        for key in allowed_fields:
            value = source.get(key)
            if value is None:
                continue
            if section == "plugins" and key in {"loaded", "failed"}:
                if isinstance(value, (list, tuple, set)):
                    values[key] = sorted({str(item)[:80] for item in value})
                elif isinstance(value, dict):
                    values[key] = sorted({str(item)[:80] for item in value.keys()})
                continue
            if isinstance(value, bool):
                values[key] = value
            elif isinstance(value, (int, float)):
                values[key] = value
            elif isinstance(value, str):
                values[key] = value[:160]
        if values:
            clean[section] = values
    return clean


def write_support_bundle(destination: Path | str, diagnostics: dict[str, Any]) -> Path:
    """Create a ZIP with sanitized diagnostics only; raw logs/data are excluded."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "privacy": {
            "raw_logs_included": False,
            "customer_data_included": False,
            "documents_or_templates_included": False,
            "printer_names_or_paths_included": False,
        },
        "diagnostics": _sanitize(diagnostics),
    }
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr(
                "README.txt",
                "IntygsDrive support diagnostics. This bundle contains only allowlisted, "
                "sanitized metadata. Raw logs, customer data, certificates, templates, "
                "settings, printer names, and local paths are not included.\n",
            )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
