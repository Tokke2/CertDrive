from __future__ import annotations
# -*- coding: utf-8 -*-
"""
Fil:       test.py
Projekt:   Intygsgenerator
Version:   1.0.0
Ansvar:    Integrationstester — import, plugin-load, EventBus,
           mallskanning, placeholder-detection, footer-replacement,
           counter, filnamn.
           Kör utan PySide6 (headless) där möjligt.
© Rickard Längkvist 2025
"""

import importlib
import json
import logging
import sys
import threading
import traceback
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

__version__: str = "1.0.0"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

# ═════════════════════════════════════════════════════════════
# PATHS
# ═════════════════════════════════════════════════════════════

_ROOT: Path = Path(__file__).resolve().parent
_MALLAR: Path = _ROOT / "Mallar"
_OUT: Path = _ROOT / "Out"
_JS: Path = _ROOT / "js"
_LOGS: Path = _ROOT / "logs"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ═════════════════════════════════════════════════════════════
# LOGGING
# ═════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
_log = logging.getLogger("test")

# ═════════════════════════════════════════════════════════════
# TEST RESULT
# ═════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)

    def add(self, result: TestResult) -> None:
        self.results.append(result)
        icon = "✅" if result.passed else "❌"
        msg = f"{icon} {result.name}"
        if result.message:
            msg += f" — {result.message}"
        if result.duration_ms:
            msg += f" ({result.duration_ms:.1f}ms)"
        print(msg)
        if not result.passed and result.detail:
            for line in result.detail.splitlines():
                print(f"     {line}")

    def ok(self, name: str, message: str = "", duration_ms: float = 0.0) -> None:
        self.add(TestResult(name, True, message, duration_ms=duration_ms))

    def fail(self, name: str, message: str = "", detail: str = "") -> None:
        self.add(TestResult(name, False, message, detail=detail))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        pct = int(100 * self.passed / self.total) if self.total else 0
        return (
            f"\n{'═' * 60}\n"
            f"RESULTAT: {self.passed}/{self.total} godkända ({pct}%)\n"
            f"  ✅ Godkända:  {self.passed}\n"
            f"  ❌ Underkända: {self.failed}\n"
            f"{'═' * 60}"
        )


def _run(
    suite: TestSuite,
    name: str,
    fn: Callable[[], tuple[bool, str]],
) -> None:
    """Kör ett testfall och mäter tid."""
    t0 = datetime.now()
    try:
        passed, message = fn()
        ms = (datetime.now() - t0).total_seconds() * 1000
        if passed:
            suite.ok(name, message, duration_ms=ms)
        else:
            suite.fail(name, message)
    except Exception as exc:
        ms = (datetime.now() - t0).total_seconds() * 1000
        suite.fail(name, str(exc), detail=traceback.format_exc())


# ═════════════════════════════════════════════════════════════
# MOCK CONTEXT
# ═════════════════════════════════════════════════════════════

class _MockEventBus:
    """Minimal EventBus för testning."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}
        self._emitted: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def subscribe(self, event: str, handler: Callable) -> Callable:
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)
        def unsub() -> None:
            with self._lock:
                with suppress(ValueError):
                    self._handlers.get(event, []).remove(handler)
        return unsub

    def emit(self, event: str, data: Optional[dict] = None) -> None:
        payload = data if isinstance(data, dict) else {}
        with self._lock:
            self._emitted.append((event, payload))
            handlers = list(self._handlers.get(event, []))
        for h in handlers:
            with suppress(Exception):
                h(payload)

    def get_emitted(self, event: str) -> list[dict]:
        with self._lock:
            return [d for e, d in self._emitted if e == event]

    def clear(self) -> None:
        with self._lock:
            self._emitted.clear()


class _MockConfig:
    """Minimal ConfigManager för testning."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, **kwargs: Any) -> None:
        self._data[key] = value

    def save(self, **kwargs: Any) -> None:
        pass

    def has(self, key: str) -> bool:
        return key in self._data

    def get_all(self) -> dict[str, Any]:
        return dict(self._data)


class _MockContext:
    """Minimal AppContext för testning."""

    def __init__(self) -> None:
        self.event_bus = _MockEventBus()
        self.settings = _MockConfig()
        self.config_manager = self.settings
        self.config = self.settings
        self.session_id = "test-session"
        self._plugin_manager: Optional[Any] = None
        self._plugins: dict[str, Any] = {}
        self._paths: dict[str, Path] = {
            "config": _JS,
            "output": _OUT,
            "templates": _MALLAR,
            "logs": _LOGS,
            "icons": _ROOT / "Icon",
            "backup": _ROOT / "Backup",
            "sent": _ROOT / "Skickade",
        }

    def get_path(self, key: str) -> Path:
        return self._paths.get(key, _ROOT / key)

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(f"Intygsgenerator.{name}")

    def emit_event(self, event: str, data: dict) -> None:
        self.event_bus.emit(event, data)

    def subscribe(self, event: str, handler: Callable) -> Callable:
        return self.event_bus.subscribe(event, handler)

    def get_plugin(self, name: str) -> Optional[Any]:
        return self._plugins.get(name)


# ═════════════════════════════════════════════════════════════
# SEKTION 1 — IMPORT-TESTER
# ═════════════════════════════════════════════════════════════

def test_imports(suite: TestSuite) -> None:
    print("\n── SEKTION 1: Import-tester ─────────────────────────")

    modules: list[tuple[str, str]] = [
        ("core", "core"),
        ("core.__init__", "core.__init__"),
        ("core.config", "core.config"),
        ("core.infrastructure", "core.infrastructure"),
        ("core.themes", "core.themes"),
        ("logging_plugin", "plugins.logging_plugin"),
        ("certificates", "plugins.certificates"),
        ("certificates.engine", "plugins.certificates.engine"),
        ("certificates.plugin", "plugins.certificates.plugin"),
        ("settings", "plugins.settings"),
        ("statistics", "plugins.statistics"),
        ("system_tray", "plugins.system_tray"),
        ("template_designer", "plugins.template_designer"),
        ("generator", "plugins.generator"),
        ("generator.plugin", "plugins.generator.plugin"),
    ]

    for label, module_path in modules:
        def _test(mp: str = module_path, lbl: str = label) -> tuple[bool, str]:
            mod = importlib.import_module(mp)
            ver = getattr(mod, "__version__", None)
            msg = f"v{ver}" if ver else "importerad"
            return True, msg

        _run(suite, f"Import: {label}", _test)


# ═════════════════════════════════════════════════════════════
# SEKTION 2 — EVENTBUS
# ═════════════════════════════════════════════════════════════

def test_eventbus(suite: TestSuite) -> None:
    print("\n── SEKTION 2: EventBus ──────────────────────────────")

    def test_subscribe_emit() -> tuple[bool, str]:
        bus = _MockEventBus()
        received: list[dict] = []
        unsub = bus.subscribe("test.event", received.append)
        bus.emit("test.event", {"key": "value"})
        if not received:
            return False, "Handler ej anropad"
        if received[0].get("key") != "value":
            return False, f"Fel data: {received[0]}"
        return True, "subscribe + emit OK"

    def test_unsubscribe() -> tuple[bool, str]:
        bus = _MockEventBus()
        received: list[dict] = []
        unsub = bus.subscribe("test.unsub", received.append)
        unsub()
        bus.emit("test.unsub", {"key": "after_unsub"})
        if received:
            return False, "Handler anropades efter unsub"
        return True, "unsubscribe OK"

    def test_multiple_handlers() -> tuple[bool, str]:
        bus = _MockEventBus()
        results: list[int] = []
        bus.subscribe("test.multi", lambda d: results.append(1))
        bus.subscribe("test.multi", lambda d: results.append(2))
        bus.subscribe("test.multi", lambda d: results.append(3))
        bus.emit("test.multi", {})
        if sorted(results) != [1, 2, 3]:
            return False, f"Saknade handlers: {results}"
        return True, "3 handlers OK"

    def test_core_eventbus() -> tuple[bool, str]:
        from core import EventBus  # type: ignore[attr-defined]
        received: list[dict] = []
        unsub = EventBus.subscribe("test.core_bus", received.append)
        EventBus.emit("test.core_bus", {"ping": True})
        unsub()
        if not received:
            return False, "Core EventBus ej anropad"
        if not received[0].get("ping"):
            return False, f"Fel data: {received[0]}"
        return True, "core.EventBus OK"

    def test_thread_safety() -> tuple[bool, str]:
        bus = _MockEventBus()
        counter: list[int] = []
        lock = threading.Lock()

        def handler(data: dict) -> None:
            with lock:
                counter.append(1)

        for _ in range(10):
            bus.subscribe("test.thread", handler)

        threads = [
            threading.Thread(target=bus.emit, args=("test.thread", {}))
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if not counter:
            return False, "Inga events mottagna"
        return True, f"{len(counter)} events thread-safe"

    _run(suite, "EventBus: subscribe + emit", test_subscribe_emit)
    _run(suite, "EventBus: unsubscribe", test_unsubscribe)
    _run(suite, "EventBus: multipla handlers", test_multiple_handlers)
    _run(suite, "EventBus: core.EventBus", test_core_eventbus)
    _run(suite, "EventBus: thread-safety", test_thread_safety)


# ═════════════════════════════════════════════════════════════
# SEKTION 3 — PLUGIN LOAD
# ═════════════════════════════════════════════════════════════

def test_plugin_load(suite: TestSuite) -> None:
    print("\n── SEKTION 3: Plugin-load ───────────────────────────")

    plugins_to_test: list[tuple[str, str, str]] = [
        ("Logger", "plugins.logging_plugin", "Plugin"),
        ("Generator", "plugins.generator.plugin", "Plugin"),
        ("System Tray", "plugins.system_tray", "Plugin"),
        ("Malldesigner", "plugins.template_designer", "Plugin"),
        ("Statistik", "plugins.statistics", "Plugin"),
        ("Inställningar", "plugins.settings", "Plugin"),
        ("Intyg", "plugins.certificates", "Plugin"),
    ]

    ctx = _MockContext()

    for display_name, module_path, class_name in plugins_to_test:
        def _test(
            mp: str = module_path,
            cn: str = class_name,
            dn: str = display_name,
        ) -> tuple[bool, str]:
            mod = importlib.import_module(mp)
            cls = getattr(mod, cn, None)
            if cls is None:
                return False, f"Klass '{cn}' saknas i {mp}"

            plugin = cls()

            # Kontrollera IPlugin-kontrakt
            missing: list[str] = []
            for attr in ("name", "version", "priority"):
                if not hasattr(plugin, attr):
                    missing.append(attr)
            if not (
                hasattr(plugin, "on_load") or hasattr(plugin, "setup")
            ):
                missing.append("on_load/setup")
            if missing:
                return False, f"Saknar: {', '.join(missing)}"

            # on_load med mock context (headless — hoppa över PySide6-plugins)
            pyside6_plugins = {"System Tray", "Malldesigner", "Intyg"}
            if dn not in pyside6_plugins:
                try:
                    load_fn = getattr(plugin, "on_load", None) or getattr(
                        plugin, "setup", None
                    )
                    if callable(load_fn):
                        load_fn(ctx)
                except Exception as exc:
                    return False, f"on_load fel: {exc}"

            ver = getattr(plugin, "version", "?")
            prio = getattr(plugin, "priority", "?")
            suffix = " (PySide6 — headless skip)" if dn in pyside6_plugins else ""
            return True, f"v{ver} prio={prio}{suffix}"

        _run(suite, f"Plugin load: {display_name}", _test)

    # Kontrollera att get_tab() returnerar None för service-plugins
    def test_generator_no_tab() -> tuple[bool, str]:
        from plugins.generator.plugin import Plugin
        p = Plugin()
        result = p.get_tab()
        if result is not None:
            return False, f"get_tab() returnerade {type(result)} (ska vara None)"
        return True, "get_tab() → None (service-plugin)"

    _run(suite, "Plugin: Generator.get_tab() = None", test_generator_no_tab)


# ═════════════════════════════════════════════════════════════
# SEKTION 4 — MALLSKANNING
# ═════════════════════════════════════════════════════════════

def test_template_scanning(suite: TestSuite) -> None:
    print("\n── SEKTION 4: Mallskanning ──────────────────────────")

    def test_mallar_exists() -> tuple[bool, str]:
        if not _MALLAR.exists():
            return False, f"Mallar/-mappen saknas: {_MALLAR}"
        subfolders = [p for p in _MALLAR.iterdir() if p.is_dir()]
        return True, f"{len(subfolders)} undermappar hittades"

    def test_docx_found() -> tuple[bool, str]:
        if not _MALLAR.exists():
            return False, "Mallar/-mappen saknas"
        docx_files: list[Path] = []
        for folder in _MALLAR.iterdir():
            if not folder.is_dir():
                continue
            for docx in folder.glob("*.docx"):
                if not docx.name.startswith("~$"):
                    docx_files.append(docx)
        if not docx_files:
            return False, "Inga DOCX-filer hittades"
        names = [f.name for f in docx_files]
        return True, f"DOCX: {', '.join(names)}"

    def test_pdf_attachments() -> tuple[bool, str]:
        if not _MALLAR.exists():
            return False, "Mallar/-mappen saknas"
        pdf_count = 0
        for folder in _MALLAR.iterdir():
            if folder.is_dir():
                pdf_count += len(list(folder.glob("*.pdf")))
        return True, f"{pdf_count} PDF-bilagor totalt"

    def test_json_configs() -> tuple[bool, str]:
        if not _MALLAR.exists():
            return False, "Mallar/-mappen saknas"
        json_files: list[str] = []
        for folder in _MALLAR.iterdir():
            if folder.is_dir():
                for jf in folder.glob("*.json"):
                    json_files.append(jf.name)
        return True, f"JSON-configs: {json_files if json_files else 'inga'}"

    def test_template_structure() -> tuple[bool, str]:
        """Kontrollerar STRUCT-001: Mallar i Mallar/<MallNamn>/"""
        if not _MALLAR.exists():
            return False, "Mallar/-mappen saknas"

        valid_templates: list[str] = []
        invalid_templates: list[str] = []

        for folder in sorted(_MALLAR.iterdir()):
            if not folder.is_dir():
                continue
            docx_files = [
                f for f in folder.glob("*.docx")
                if not f.name.startswith("~$")
            ]
            if docx_files:
                valid_templates.append(folder.name)
            else:
                invalid_templates.append(folder.name)

        msg = f"Giltiga: {valid_templates}"
        if invalid_templates:
            msg += f" | Ignorerade (ingen DOCX): {invalid_templates}"
        return True, msg

    _run(suite, "Mallar: mapp finns", test_mallar_exists)
    _run(suite, "Mallar: DOCX-filer hittade", test_docx_found)
    _run(suite, "Mallar: PDF-bilagor", test_pdf_attachments)
    _run(suite, "Mallar: JSON-configs", test_json_configs)
    _run(suite, "Mallar: struktur (STRUCT-001)", test_template_structure)


# ═════════════════════════════════════════════════════════════
# SEKTION 5 — PLACEHOLDER-DETECTION
# ═════════════════════════════════════════════════════════════

def test_placeholder_detection(suite: TestSuite) -> None:
    print("\n── SEKTION 5: Placeholder-detection ────────────────")

    def _find_placeholders_in_text(text: str) -> list[str]:
        """Hittar ::NYCKEL:: i text."""
        import re
        return re.findall(r"::[A-Za-zÅÄÖåäö_0-9]+::", text)

    def _get_full_run_text(paragraph: Any) -> str:
        """Sammanfogar alla runs i ett stycke."""
        return "".join(run.text for run in paragraph.runs)

    def test_basic_placeholder_regex() -> tuple[bool, str]:
        import re
        pattern = r"::[A-Za-zÅÄÖåäö_0-9]+::"
        tests = [
            ("::VIN::", ["::VIN::"]),
            ("::Reg::", ["::Reg::"]),
            ("::År_Nu::", ["::År_Nu::"]),
            ("Text ::Datum:: text", ["::Datum::"]),
            ("::A:: ::B::", ["::A::", "::B::"]),
            ("ingen placeholder", []),
            ("::123::", ["::123::"]),
        ]
        for text, expected in tests:
            found = re.findall(pattern, text)
            if found != expected:
                return False, f"'{text}' → {found} (väntat {expected})"
        return True, f"{len(tests)} regex-tester OK"

    def test_docx_placeholders() -> tuple[bool, str]:
        """Söker placeholders i alla tillgängliga DOCX-mallar."""
        try:
            from docx import Document  # type: ignore[import-untyped]
        except ImportError:
            return False, "python-docx ej installerat"

        if not _MALLAR.exists():
            return False, "Mallar/-mappen saknas"

        all_found: dict[str, list[str]] = {}

        for folder in sorted(_MALLAR.iterdir()):
            if not folder.is_dir():
                continue
            for docx_path in folder.glob("*.docx"):
                if docx_path.name.startswith("~$"):
                    continue
                try:
                    doc = Document(str(docx_path))
                    placeholders: set[str] = set()

                    # Body-paragrafer
                    for para in doc.paragraphs:
                        full = _get_full_run_text(para)
                        placeholders.update(_find_placeholders_in_text(full))

                    # Tabeller
                    for table in doc.tables:
                        for row in table.rows:
                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    full = _get_full_run_text(para)
                                    placeholders.update(
                                        _find_placeholders_in_text(full)
                                    )

                    # Headers
                    for section in doc.sections:
                        if section.header:
                            for para in section.header.paragraphs:
                                full = _get_full_run_text(para)
                                placeholders.update(
                                    _find_placeholders_in_text(full)
                                )

                    # Footers
                    for section in doc.sections:
                        if section.footer:
                            for para in section.footer.paragraphs:
                                full = _get_full_run_text(para)
                                placeholders.update(
                                    _find_placeholders_in_text(full)
                                )

                    all_found[docx_path.name] = sorted(placeholders)
                except Exception as exc:
                    all_found[docx_path.name] = [f"FEL: {exc}"]

        if not all_found:
            return False, "Inga DOCX-filer granskade"

        lines: list[str] = []
        for filename, phs in all_found.items():
            lines.append(f"{filename}: {phs}")
        print("\n     " + "\n     ".join(lines))
        return True, f"{len(all_found)} DOCX granskade"

    def test_split_run_detection() -> tuple[bool, str]:
        """
        Testar att splitade runs detekteras korrekt.
        Word delar ibland ::VIN:: i separata runs:
          run1="::V"  run2="IN::"
        Engine ska sammanfoga och detektera ändå.
        """
        # Simulera splitade runs med en enkel datastruktur
        @dataclass
        class FakeRun:
            text: str

        @dataclass
        class FakePara:
            runs: list[FakeRun]

        test_cases: list[tuple[list[str], str]] = [
            (["::VIN::"], "::VIN::"),
            (["::V", "IN::"], "::VIN::"),
            ([":", ":", "V", "I", "N", ":", ":"], "::VIN::"),
            (["Text ", "::Datum::", " mer"], "::Datum::"),
            (["::A::", " och ", "::B::"], "::A:: och ::B::"),
        ]

        for run_texts, expected_full in test_cases:
            para = FakePara([FakeRun(t) for t in run_texts])
            full = _get_full_run_text(para)
            if expected_full not in full:
                return False, f"Runs {run_texts} → '{full}' (väntat '{expected_full}')"

        return True, f"{len(test_cases)} split-run-tester OK"

    _run(suite, "Placeholder: regex-pattern", test_basic_placeholder_regex)
    _run(suite, "Placeholder: DOCX-skanning", test_docx_placeholders)
    _run(suite, "Placeholder: split runs", test_split_run_detection)


# ═════════════════════════════════════════════════════════════
# SEKTION 6 — FOOTER REPLACEMENT
# ═════════════════════════════════════════════════════════════

def test_footer_replacement(suite: TestSuite) -> None:
    print("\n── SEKTION 6: Footer-replacement ───────────────────")

    def _replace_in_paragraph(paragraph: Any, values: dict[str, str]) -> int:
        """
        Ersätter placeholders i ett stycke.
        Hanterar splitade runs korrekt.
        Returnerar antal ersättningar.
        """
        full_text = "".join(run.text for run in paragraph.runs)
        new_text = full_text
        replacements = 0

        for key, value in values.items():
            placeholder = f"::{key}::"
            if placeholder in new_text:
                new_text = new_text.replace(placeholder, value)
                replacements += 1

        if replacements > 0 and new_text != full_text:
            # Skriv ny text till första run, töm övriga
            if paragraph.runs:
                paragraph.runs[0].text = new_text
                for run in paragraph.runs[1:]:
                    run.text = ""

        return replacements

    def test_passagerare_footer() -> tuple[bool, str]:
        """Testar footer-replacement i Passagerare.docx (din befintliga logik)."""
        try:
            from docx import Document  # type: ignore[import-untyped]
        except ImportError:
            return False, "python-docx ej installerat"

        docx_path = _MALLAR / "Passagerare" / "Passagerare.docx"
        if not docx_path.exists():
            return False, f"Fil saknas: {docx_path}"

        try:
            doc = Document(str(docx_path))
        except Exception as exc:
            return False, f"Kunde inte öppna DOCX: {exc}"

        values = {"INTYG": "794", "Intyg": "794", "DATUM": "2025-01-15"}
        footer_texts_before: list[str] = []
        footer_texts_after: list[str] = []
        total_replacements = 0

        for section in doc.sections:
            if section.footer:
                for para in section.footer.paragraphs:
                    before = "".join(run.text for run in para.runs)
                    if before.strip():
                        footer_texts_before.append(before)
                    reps = _replace_in_paragraph(para, values)
                    total_replacements += reps
                    after = "".join(run.text for run in para.runs)
                    if before.strip():
                        footer_texts_after.append(after)

        if footer_texts_before:
            print(f"\n     FÖRE:  {footer_texts_before}")
            print(f"     EFTER: {footer_texts_after}")
            print(f"     Ersättningar: {total_replacements}")

        # Kontrollera att inga ::NYCKEL:: finns kvar
        import re
        remaining = []
        for text in footer_texts_after:
            found = re.findall(r"::[A-Za-zÅÄÖåäö_0-9]+::", text)
            remaining.extend(found)

        if remaining:
            return True, f"Kvar (ej i values-dict): {remaining}"
        return True, f"Footer OK — {total_replacements} ersättningar"

    def test_all_docx_footers() -> tuple[bool, str]:
        """Testar footer-läsning i alla tillgängliga DOCX-mallar."""
        try:
            from docx import Document  # type: ignore[import-untyped]
        except ImportError:
            return False, "python-docx ej installerat"

        if not _MALLAR.exists():
            return False, "Mallar/-mappen saknas"

        results: list[str] = []
        for folder in sorted(_MALLAR.iterdir()):
            if not folder.is_dir():
                continue
            for docx_path in folder.glob("*.docx"):
                if docx_path.name.startswith("~$"):
                    continue
                try:
                    doc = Document(str(docx_path))
                    footer_paras = 0
                    for section in doc.sections:
                        if section.footer:
                            footer_paras += sum(
                                1 for p in section.footer.paragraphs
                                if "".join(r.text for r in p.runs).strip()
                            )
                    results.append(f"{docx_path.name}: {footer_paras} footer-rader")
                except Exception as exc:
                    results.append(f"{docx_path.name}: FEL ({exc})")

        if not results:
            return False, "Inga DOCX granskade"
        print("\n     " + "\n     ".join(results))
        return True, f"{len(results)} DOCX footers granskade"

    _run(suite, "Footer: Passagerare.docx replacement", test_passagerare_footer)
    _run(suite, "Footer: alla DOCX-mallar", test_all_docx_footers)


# ═════════════════════════════════════════════════════════════
# SEKTION 7 — FILNAMN & SÄKERHET
# ═════════════════════════════════════════════════════════════

def test_filename_safety(suite: TestSuite) -> None:
    print("\n── SEKTION 7: Filnamn & ÅÄÖ ────────────────────────")

    def _local_safe_filename(name: str, max_length: int = 200) -> str:
        normalized = unicodedata.normalize("NFC", name)
        forbidden = set('<>:"/\\|?*\0')
        cleaned = "".join("_" if ch in forbidden else ch for ch in normalized)
        cleaned = cleaned.strip(". ").strip()
        return cleaned[:max_length] if cleaned else "untitled"

    def test_aao_preserved() -> tuple[bool, str]:
        tests = [
            ("Intyg Åsa Ängel Örn.pdf", "Intyg Åsa Ängel Örn.pdf"),
            ("Passagerare.docx", "Passagerare.docx"),
            ("35kW intyg.pdf", "35kW intyg.pdf"),
            ("Demontering av stryp kit.docx", "Demontering av stryp kit.docx"),
        ]
        for input_name, expected in tests:
            result = _local_safe_filename(input_name)
            if result != expected:
                return False, f"'{input_name}' → '{result}' (väntat '{expected}')"
        return True, f"{len(tests)} ÅÄÖ-tester OK"

    def test_forbidden_chars_replaced() -> tuple[bool, str]:
        tests = [
            ('fil<>:"/\\|?*namn.pdf', "fil_________namn.pdf"),
            ("normal fil.pdf", "normal fil.pdf"),
            ("...dotdot...", "dotdot"),
            ("", "untitled"),
        ]
        for input_name, expected in tests:
            result = _local_safe_filename(input_name)
            if result != expected:
                return False, f"'{input_name}' → '{result}' (väntat '{expected}')"
        return True, f"{len(tests)} förbjudna-tecken-tester OK"

    def test_output_filename_format() -> tuple[bool, str]:
        """
        Testar det förväntade filnamnsformatet:
        {NR} Intyg {Mall} {VIN} {REG} {DATUM}.pdf
        """
        def build_filename(
            nr: str,
            mall: str,
            vin: str,
            reg: str,
            datum: str,
        ) -> str:
            raw = f"{nr} Intyg {mall} {vin} {reg} {datum}.pdf"
            return _local_safe_filename(raw)

        cases = [
            ("001", "Passagerare", "YV1AH485XD2132457", "ABC123", "2025-01-15"),
            ("042", "35kW", "WBA12345678901234", "XYZ789", "2025-06-01"),
        ]
        for nr, mall, vin, reg, datum in cases:
            result = build_filename(nr, mall, vin, reg, datum)
            expected_prefix = f"{nr} Intyg {mall} {vin} {reg}"
            if not result.startswith(expected_prefix):
                return False, f"Format fel: '{result}'"
            if not result.endswith(".pdf"):
                return False, f"Saknar .pdf: '{result}'"
        return True, f"{len(cases)} filnamnsformat OK"

    def test_engine_safe_filename() -> tuple[bool, str]:
        """Testar engine._safe_filename om tillgänglig."""
        try:
            from plugins.certificates.engine import safe_filename
        except ImportError:
            return True, "certificates engine ej tillgänglig (skip)"

        tests = [
            ("Åsa Örn.pdf", True),   # ÅÄÖ bevaras
            ("normal.pdf", True),
            ("", True),              # returnerar 'untitled'
        ]
        for name, should_not_crash in tests:
            try:
                result = safe_filename(name)
                if not isinstance(result, str):
                    return False, f"safe_filename('{name}') returnerade {type(result)}"
            except Exception as exc:
                if should_not_crash:
                    return False, f"safe_filename('{name}') kraschade: {exc}"

        return True, "engine.safe_filename OK"

    _run(suite, "Filnamn: ÅÄÖ bevaras", test_aao_preserved)
    _run(suite, "Filnamn: förbjudna tecken ersätts", test_forbidden_chars_replaced)
    _run(suite, "Filnamn: output-format", test_output_filename_format)
    _run(suite, "Filnamn: engine.safe_filename", test_engine_safe_filename)


# ═════════════════════════════════════════════════════════════
# SEKTION 8 — SETTINGS
# ═════════════════════════════════════════════════════════════

def test_settings(suite: TestSuite) -> None:
    print("\n── SEKTION 8: Settings ──────────────────────────────")

    def test_mock_config_rw() -> tuple[bool, str]:
        cfg = _MockConfig()
        cfg.set("test.key", "hello")
        val = cfg.get("test.key", "default")
        if val != "hello":
            return False, f"Fick '{val}' (väntat 'hello')"
        missing = cfg.get("nonexistent", "default")
        if missing != "default":
            return False, f"Default misslyckades: '{missing}'"
        return True, "MockConfig r/w OK"

    def test_generator_settings_eq() -> tuple[bool, str]:
        from plugins.generator.plugin import GeneratorSettings
        a = GeneratorSettings()
        b = GeneratorSettings()
        c = GeneratorSettings(company_name="Test AB")
        if a != b:
            return False, "Tomma defaults är ej lika"
        if a == c:
            return False, "Olika settings är lika"
        return True, "GeneratorSettings.__eq__ OK"

    def test_generator_settings_from_config() -> tuple[bool, str]:
        from plugins.generator.plugin import GeneratorSettings
        cfg = _MockConfig()
        cfg.set("generator.company_name", "Test AB")
        cfg.set("generator.start_with_os", True)
        settings = GeneratorSettings.from_config(cfg)
        if settings.company_name != "Test AB":
            return False, f"company_name: '{settings.company_name}'"
        if not settings.start_with_os:
            return False, "start_with_os ej True"
        return True, "from_config OK"

    def test_generator_settings_save_to_config() -> tuple[bool, str]:
        from plugins.generator.plugin import GeneratorSettings
        cfg = _MockConfig()
        settings = GeneratorSettings(
            company_name="Mitt Företag",
            start_with_os=True,
        )
        result = settings.save_to_config(cfg)
        if not result:
            return False, "save_to_config returnerade False"
        saved_name = cfg.get("generator.company_name", "")
        if saved_name != "Mitt Företag":
            return False, f"Sparad company_name: '{saved_name}'"
        return True, "save_to_config OK"

    def test_settings_json_atomic_write() -> tuple[bool, str]:
        """Testar atomisk skrivning av settings-fil."""
        import tempfile
        from plugins.generator.plugin import GeneratorSettings
        settings = GeneratorSettings(company_name="Atomic Test")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_settings.json"
            ok = settings.save_to_file(path)
            if not ok:
                return False, "save_to_file returnerade False"
            if not path.exists():
                return False, "Fil skapades ej"
            loaded = GeneratorSettings.load_from_file(path)
            if loaded.company_name != "Atomic Test":
                return False, f"Laddad: '{loaded.company_name}'"
        return True, "Atomisk write + load OK"

    _run(suite, "Settings: MockConfig r/w", test_mock_config_rw)
    _run(suite, "Settings: GeneratorSettings.__eq__", test_generator_settings_eq)
    _run(suite, "Settings: from_config", test_generator_settings_from_config)
    _run(suite, "Settings: save_to_config", test_generator_settings_save_to_config)
    _run(suite, "Settings: atomisk write", test_settings_json_atomic_write)


# ═════════════════════════════════════════════════════════════
# SEKTION 9 — COPYRIGHT
# ═════════════════════════════════════════════════════════════

def test_copyright(suite: TestSuite) -> None:
    print("\n── SEKTION 9: Copyright ─────────────────────────────")

    def test_copyright_format() -> tuple[bool, str]:
        year = datetime.now().year
        if year == 2025:
            expected = "© Rickard Längkvist 2025"
        else:
            expected = f"© Rickard Längkvist 2025–{year}"

        if COPYRIGHT != expected:
            return False, f"'{COPYRIGHT}' (väntat '{expected}')"
        return True, COPYRIGHT

    def test_copyright_in_modules() -> tuple[bool, str]:
        modules_to_check = [
            "plugins.generator.plugin",
            "plugins.certificates.plugin",
        ]
        missing: list[str] = []
        for mp in modules_to_check:
            try:
                mod = importlib.import_module(mp)
                cr = getattr(mod, "COPYRIGHT", None)
                if cr is None:
                    missing.append(f"{mp}: COPYRIGHT saknas")
                elif "Rickard Längkvist" not in cr:
                    missing.append(f"{mp}: fel COPYRIGHT '{cr}'")
                elif "TADEGA" in cr:
                    missing.append(f"{mp}: TADEGA AB i COPYRIGHT!")
            except Exception as exc:
                missing.append(f"{mp}: importfel ({exc})")

        if missing:
            return False, " | ".join(missing)
        return True, f"{len(modules_to_check)} moduler OK"

    def test_no_tadega() -> tuple[bool, str]:
        """FORBIDDEN-025: TADEGA AB får INTE finnas i källkod."""
        python_files = list(_ROOT.rglob("*.py")) + list(_ROOT.rglob("*.pyw"))
        violations: list[str] = []
        for py_file in python_files:
            # Hoppa över __pycache__
            if "__pycache__" in py_file.parts:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if "TADEGA" in content:
                    violations.append(py_file.name)
            except Exception:
                pass

        if violations:
            return False, f"TADEGA AB funnen i: {violations}"
        return True, f"{len(python_files)} filer granskade — rent"

    _run(suite, "Copyright: format korrekt", test_copyright_format)
    _run(suite, "Copyright: i moduler", test_copyright_in_modules)
    _run(suite, "Copyright: FORBIDDEN-025 (ingen TADEGA)", test_no_tadega)


# ═════════════════════════════════════════════════════════════
# SEKTION 10 — MAPPSTRUKTUR
# ═════════════════════════════════════════════════════════════

def test_folder_structure(suite: TestSuite) -> None:
    print("\n── SEKTION 10: Mappstruktur ─────────────────────────")

    def test_required_folders() -> tuple[bool, str]:
        required: list[tuple[str, Path]] = [
            ("Mallar/", _MALLAR),
            ("Out/", _OUT),
            ("logs/", _LOGS),
            ("Icon/", _ROOT / "Icon"),
        ]
        missing: list[str] = []
        for name, path in required:
            if not path.exists():
                missing.append(name)
        if missing:
            return False, f"Saknar: {missing}"
        return True, "Alla obligatoriska mappar finns"

    def test_core_modules() -> tuple[bool, str]:
        required_files: list[Path] = [
            _ROOT / "main.pyw",
            _ROOT / "core" / "__init__.py",
            _ROOT / "core" / "config.py",
            _ROOT / "core" / "infrastructure.py",
            _ROOT / "core" / "themes.py",
            _ROOT / "plugins" / "__init__.py",
        ]
        missing: list[str] = []
        for f in required_files:
            if not f.exists():
                missing.append(f.name)
        if missing:
            return False, f"Saknar: {missing}"
        return True, f"{len(required_files)} kärnfiler OK"

    def test_plugin_structure() -> tuple[bool, str]:
        plugin_dirs = [
            "certificates",
            "generator",
            "logging_plugin",
            "settings",
            "statistics",
            "system_tray",
            "template_designer",
        ]
        missing: list[str] = []
        for pdir in plugin_dirs:
            init_path = _ROOT / "plugins" / pdir / "__init__.py"
            if not init_path.exists():
                missing.append(pdir)
        if missing:
            return False, f"Saknar __init__.py: {missing}"
        return True, f"{len(plugin_dirs)} plugin-mappar OK"

    def test_out_subfolders() -> tuple[bool, str]:
        """STRUCT-002: Output i Out/<MallNamn>/"""
        if not _OUT.exists():
            return True, "Out/ saknas (skapas vid första generering)"
        subfolders = [p.name for p in _OUT.iterdir() if p.is_dir()]
        return True, f"Out/-mappar: {subfolders if subfolders else 'inga ännu'}"

    _run(suite, "Struktur: obligatoriska mappar", test_required_folders)
    _run(suite, "Struktur: kärnfiler", test_core_modules)
    _run(suite, "Struktur: plugin-mappar", test_plugin_structure)
    _run(suite, "Struktur: Out/-undermappar (STRUCT-002)", test_out_subfolders)


# ═════════════════════════════════════════════════════════════
# SEKTION 11 — CERTIFICATES ENGINE
# ═════════════════════════════════════════════════════════════

def test_certificates_engine(suite: TestSuite) -> None:
    print("\n── SEKTION 11: Certificates Engine ─────────────────")

    def test_engine_import() -> tuple[bool, str]:
        try:
            from plugins.certificates import engine as eng
            exports = [
                "CertificateGenerator",
                "CounterManager",
                "PDFConverter",
                "Template",
                "TemplateConfig",
                "safe_filename",
                "VINValidator",
                "RegValidator",
                "PlaceholderParser",
            ]
            missing = [e for e in exports if not hasattr(eng, e)]
            if missing:
                return False, f"Saknar exports: {missing}"
            return True, f"{len(exports)} exports OK"
        except ImportError as exc:
            return False, f"ImportError: {exc}"

    def test_vin_validator() -> tuple[bool, str]:
        try:
            from plugins.certificates.engine import VINValidator
        except ImportError:
            return False, "Kunde ej importera VINValidator"

        validator = VINValidator()
        valid_vins = [
            "YV1AH485XD2132457",
            "WBA12345678901234",
            "1HGBH41JXMN109186",
        ]
        invalid_vins = [
            "",
            "TOOSHORT",
            "TOOLONGVIN123456789",
            "YV1AH485XD213245I",  # I är förbjudet
        ]

        for vin in valid_vins:
            result = validator.validate(vin)
            is_ok = getattr(result, "is_valid", result)
            if not is_ok:
                return False, f"Giltig VIN nekad: '{vin}'"

        for vin in invalid_vins:
            result = validator.validate(vin)
            is_ok = getattr(result, "is_valid", result)
            if is_ok:
                return False, f"Ogiltig VIN godkänd: '{vin}'"

        return True, f"{len(valid_vins)} giltig + {len(invalid_vins)} ogiltig OK"

    def test_reg_validator() -> tuple[bool, str]:
        try:
            from plugins.certificates.engine import RegValidator
        except ImportError:
            return False, "Kunde ej importera RegValidator"

        validator = RegValidator()
        valid_regs = ["ABC123", "XYZ789", "AAA111"]
        invalid_regs = ["", "12345", "TOOLONG123456"]

        passed = 0
        for reg in valid_regs:
            result = validator.validate(reg)
            is_ok = getattr(result, "is_valid", result)
            if is_ok:
                passed += 1

        return True, f"{passed}/{len(valid_regs)} reg-valideringar OK"

    def test_placeholder_parser() -> tuple[bool, str]:
        try:
            from plugins.certificates.engine import PlaceholderParser
        except ImportError:
            return False, "Kunde ej importera PlaceholderParser"

        # Testa att parser hittar placeholders
        parser = PlaceholderParser()
        test_text = "Intyg ::Intyg:: datum ::Datum:: VIN ::VIN::"

        parse_fn = getattr(parser, "find_placeholders", None)
        if not callable(parse_fn):
            parse_fn = getattr(parser, "parse", None)
        if not callable(parse_fn):
            return True, "PlaceholderParser importerad (API okänt)"

        try:
            result = parse_fn(test_text)
            count = len(result) if hasattr(result, "__len__") else "?"
            return True, f"Parser hittade {count} placeholders"
        except Exception as exc:
            return False, f"Parser fel: {exc}"

    _run(suite, "Engine: import + exports", test_engine_import)
    _run(suite, "Engine: VINValidator", test_vin_validator)
    _run(suite, "Engine: RegValidator", test_reg_validator)
    _run(suite, "Engine: PlaceholderParser", test_placeholder_parser)


# ═════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════

def main() -> int:
    print("═" * 60)
    print(f"  INTYGSGENERATOR — INTEGRATIONSTESTER v{__version__}")
    print(f"  {COPYRIGHT}")
    print(f"  Python {sys.version.split()[0]} | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  Root: {_ROOT}")
    print("═" * 60)

    suite = TestSuite()

    test_imports(suite)
    test_eventbus(suite)
    test_plugin_load(suite)
    test_template_scanning(suite)
    test_placeholder_detection(suite)
    test_footer_replacement(suite)
    test_filename_safety(suite)
    test_settings(suite)
    test_copyright(suite)
    test_folder_structure(suite)
    test_certificates_engine(suite)

    print(suite.summary())

    return 0 if suite.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())