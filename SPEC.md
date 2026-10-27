# INTYGSGENERATOR — MASTER SPEC v10.7 (samlad)

**Renskriven och samlad:** 2026-08-05

**Projekt:** Intygsgenerator

**Python:** 3.12+

**UI:** PySide6

**Entry point:** `main.pyw`

**Arkitektur:** pluginbaserad, EventBus-driven och cross-platform

> Detta är projektets **enda specifikationsfil**. Den ersätter alla tidigare spridda dokument och samlar i en fil:
>
> - **Del I** — Master spec v10.7 (normativ specifikation)
> - **Del II** — Smart Cache-specifikationen (förslag v0.1, bilaga till §26)
> - **Del III** — Granskningslogg och förslagsrunda 1 (sessionsstatus)

## Innehåll

- **Del I — Master spec v10.7:** §1 Syfte · §2 Teknikregler · §3 Copyright · §4 Struktur · §5 Mallar · §6 Placeholders · §7 PDF · §8 Output/räknare · §9 Pluginarkitektur · §10 EventBus · §11 Certificates · §12 Statistics · §13 Settings · §14 Logging · §15 Tray · §16 main.pyw · §17 UI · §18 Performance · §19 Autostart · §20 Single instance · §21 Fail-safe · §22 Testning · §23 Filstatus · §24 Leveranschecklista · §25 Nästa fil · §26 Smart runtime/cachepolicy · §27 Kärnmoduler · §28 Övriga plugins · §29 Huvudfönster · §30 UI-detaljer · §31 Testplan · §32 Buggar · §33 Versionsharmonisering · §34 Releasekrav
- **Del II — Smart Cache:** §SC1–§SC19 + Bilaga A
- **Del III — Granskningslogg:** arbetssätt, mottagna filer, smoketester, noteringar, förslagsrunda 1

---
---

# DEL I — MASTER SPEC v10.7

## 1. Projektets syfte

Intygsgenerator genererar professionella fordonsintyg från DOCX-mallar och exporterar dem till PDF. Programmet ska kunna:

- Läsa mallar från `Mallar/<MallNamn>/`.
- Ersätta placeholders såsom `::VIN::`, `::Reg::`, `::Namn::` och automatiska fält.
- Bevara svenska tecken i värden och filnamn.
- Konvertera DOCX till PDF via prioriterad konverteringskedja.
- Slå ihop PDF-bilagor i samma mallmapp.
- Hantera batchgenerering från CSV.
- Visa statistik, importera tidigare intyg och skapa rapporter.
- Köra utan terminal, normalt minimerat till systemfältet.
- Hålla loggning, filsystemsdiagnostik och crashlogg isolerad i logging-pluginet.

Programmet är fortfarande **pluginbaserat**. Det ska inte göras om till en monolit och ska inte byta UI-ramverk.

---

## 2. Absoluta teknikregler

### 2.1 Måste

| ID | Krav |
|---|---|
| MUST-001 | Leverera alltid komplett fil, aldrig patch eller diff. |
| MUST-002 | Sökväg och filnamn anges först vid filleverans. |
| MUST-003 | Befintlig fil ska ses innan ny kod genereras. |
| MUST-004 | Föreslå nästa fil efter leverans. |
| MUST-005 | Behåll befintlig funktionalitet. |
| MUST-006 | Modernisera och förbättra produktionsmässigt. |
| MUST-007 | Samarbeta via definierade plugin- och EventBus-gränser. |
| MUST-008 | All kod ska vara production-ready. |
| MUST-009 | Kod ska vara underhållbar och inte innehålla temporära lösningar. |
| MUST-010 | Python 3.12+ med moderna typannoteringar. |
| MUST-011 | Senaste stabila PySide6 används för GUI. |
| MUST-012 | Respektera varje fils ansvarsområde. |
| MUST-013 | Följ prioriterad plugin- och laddningsordning. |
| MUST-014 | Alla externa beroenden pinnas med `==`. |
| MUST-015 | Windows, macOS och Linux ska stödjas. |
| MUST-016 | Be om bekräftelse innan en redan godkänd fil ändras. |
| MUST-017 | Delad state ska vara trådsäker. |
| MUST-018 | Plugins integreras via EventBus. |
| MUST-019 | UI, logik och tjänster hålls modulära. |
| MUST-020 | En fil levereras per svar. |
| MUST-021 | Paket uppdateras mapp för mapp. |
| MUST-022 | AI föreslår nästa fil. |
| MUST-023 | AI inväntar inklistrad befintlig kod. |
| MUST-024 | `from __future__ import annotations` ligger på rad 1. |
| MUST-025 | Inga legacy-filer; allt moderniseras till aktuell Python-LTS-stil. |
| MUST-026 | Filnamn och sökväg anges alltid i kodhuvudet (kommentar/docstring) för varje levererad fil. |

### 2.2 Förbjudet

| ID | Förbud |
|---|---|
| FORBIDDEN-001 | Byta programspråk. |
| FORBIDDEN-002 | Byta UI-ramverk; endast PySide6 är tillåtet. |
| FORBIDDEN-003 | `>=` och `~=` i dependencies. |
| FORBIDDEN-004 | Patch/diff som leverans. |
| FORBIDDEN-005 | Formuleringar som “resten oförändrad”. |
| FORBIDDEN-006 | Placeholders eller stubbar i produktionskod. |
| FORBIDDEN-007 | TODO-kommentarer som ersätter färdig funktion. |
| FORBIDDEN-008 | Ta bort funktioner utan uttryckligt beslut. |
| FORBIDDEN-009 | Hårdkodade projektsökvägar. |
| FORBIDDEN-010 | Hårdkodade UI-färger utanför godkända undantag. |
| FORBIDDEN-011 | Tysta exceptions. |
| FORBIDDEN-012 | Hård direktkoppling mellan plugins. |
| FORBIDDEN-013 | Multipla instanser av appen eller tray-ikon. |
| FORBIDDEN-014 | Terminalkrav. |
| FORBIDDEN-015 | Flera filer i samma leverans. |
| FORBIDDEN-016 | Dubblerade system. |
| FORBIDDEN-017 | Settings utanför JSON. |
| FORBIDDEN-018 | Orensbar cache. |
| FORBIDDEN-019 | Språkbyte utan godkännande. |
| FORBIDDEN-020 | Flera system tray-ikoner. |
| FORBIDDEN-021 | Terminalberoende. |
| FORBIDDEN-022 | Kod innan befintlig fil setts. |
| FORBIDDEN-023 | Watchdog. |
| FORBIDDEN-024 | Otillåten extern ägar-/copyrightmarkering i källkod. |
| FORBIDDEN-025 | `from __future__` efter kod. |
| FORBIDDEN-026 | `typing.Dict`, `typing.List`, `typing.Tuple`, `typing.Union`, `typing.Optional`. |
| FORBIDDEN-027 | Tkinter-importer. |
| FORBIDDEN-028 | Legacy-Python eller legacy-GUI. |
| FORBIDDEN-029 | Offert-/quote-modul. |

### 2.3 Kodstil

```python
from __future__ import annotations

# moderna typer
names: list[str] = []
metadata: dict[str, str] = {}
path: Path | None = None
```

Tillåtna typer från `typing` är bland annat `Any`, `Callable`, `TYPE_CHECKING` och `Final`. Alla GUI-importer ska komma från PySide6.

---

## 3. Copyright och ägarskap

Varje Pythonfil använder dynamiskt år:

```python
_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)
```

Regler:

- Startår är alltid `2025`.
- Aldrig `© Copyright`.
- Ingen annan upphovsman eller ägare i källkod.
- Copyright ska finnas i källfil, About-dialog och relevant JSON-metadata.

---

## 4. Ny projekt- och mappstruktur

```text
Intygsgenerator/
├── main.pyw
├── test.py
├── SPEC.md                       # Projektets enda specifikationsfil
├── Intyg.txt
├── statistics.json
├── Backup/
├── Icon/
├── logs/
│   └── archive/                    # LOG-021, gzip-arkiv; rensas aldrig automatiskt
├── Mallar/
│   ├── 25kW/                       # Ignoreras om den saknar giltig DOCX
│   │   └── *.pdf
│   ├── 35kW/
│   │   ├── för effektregulering 35kW.docx
│   │   ├── *.pdf
│   │   └── ~$*.docx                # Ignoreras alltid
│   ├── Demontering/
│   │   ├── Demontering av stryp kit.docx
│   │   └── *.pdf
│   ├── Passagerare/
│   │   ├── Passagerare.docx
│   │   ├── Passagerare.json
│   │   ├── *.pdf
│   │   └── ~$*.docx                # Ignoreras alltid
│   └── Passagerare.docx            # Lös rot-DOCX; ignoreras alltid
├── Out/
│   └── <MallNamn>/
├── Skickade/
├── js/
│   ├── settings.json
│   ├── counters.json
│   ├── certificates_counters.json
│   └── statistics.json
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── infrastructure.py
│   └── themes.py
└── plugins/
    ├── __init__.py
    ├── certificates/
    │   ├── __init__.py              # Paket-API, fail-safe och dependency-status
    │   ├── engine.py                # Modeller, validering, placeholder-cache
    │   ├── services.py              # PDF, merger, räknare, generator
    │   ├── view.py                  # Intygs-UI-widgets
    │   └── plugin.py                # Mallcontroller och intygsflik
    ├── generator/
    │   ├── __init__.py
    │   ├── core.py
    │   ├── plugin.py
    │   └── types.py
    ├── logging_plugin/
    │   ├── __init__.py              # Paketexport och fallback
    │   ├── core.py                  # Storage, formattering, export, LOG-021
    │   ├── tracer.py                # Reversibel filsystemspårning
    │   ├── view.py                  # Logger UI och filsystemsview
    │   └── plugin.py                # Logging-controller och crashhantering
    ├── settings/
    │   ├── __init__.py              # Paketexport och fallback
    │   ├── core.py                  # Teman, autostart, profiler, genvägar
    │   ├── workers.py               # Backup och restore i QRunnable
    │   ├── view.py                  # Settings-shell, dialoger och kontroller
    │   └── plugin.py                # Settings-controller och 12 flikar
    ├── statistics/
    │   ├── __init__.py              # Paket-API och TemplateScanner
    │   ├── engine.py                # Statistikmotor, import och rapporter
    │   └── plugin.py                # Statistik-UI och EventBus-controller
    ├── system_tray/
    │   ├── __init__.py              # Återstående metadata-/paketfil
    │   ├── models.py
    │   └── plugin.py
    ├── template_designer/
    │   ├── __init__.py
    │   ├── core.py
    │   └── plugin.py
    └── bilrutan/                    # Future; ej påbörjad
```

### Strukturella regler

- `js/profiles/` skapas **aldrig** vid startup.
- Profilkatalog skapas endast vid uttryckligt skapande, profilimport eller användarinitierad restore.
- `quote/` finns inte i aktiv arkitektur och ska tas bort manuellt om den fortfarande finns lokalt.
- `Out/Offerter/` kan finnas som gammal användardata, men ingen offertfunktion ska existera i kod.
- Nya hjälpfiler är en medveten uppdelning för att hålla controllerfiler under cirka 1 800 rader och helperfiler under cirka 1 200 rader.
- Projektet har **exakt en** specifikationsfil: `SPEC.md`.

---

## 5. Mallstruktur och mallskanning

### 5.1 Normativ mallstruktur

```text
Mallar/
└── <MallNamn>/
    ├── <MallNamn>.docx             # Prioriterad aktiv DOCX
    ├── valfritt-annat-namn.docx    # Tillåten fallback om prioriterad saknas
    ├── *.pdf                       # PDF-bilagor
    └── <MallNamn>.json             # Valfri mallconfig
```

### 5.2 Mallregler MALL-001–009

1. Varje mall ligger i en egen direkt undermapp till `Mallar/`.
2. Mallens DOCX, PDF-bilagor och JSON-config ligger i samma mapp.
3. JSON-config heter normalt `<MallNamn>.json`.
4. En mall kräver minst en riktig, icke-tom `.docx`. *(Utökas av B3 (bekräftat 2026-08-26): även Word-filer `.doc` är giltiga mallfiler; skanning och konvertering ska hantera båda.)*
5. PDF-filer i samma mapp är bilagor och slås ihop efter huvudintyget. *(Bekräftat i B3: bilagorna ska följa med i varje genererat intyg — implementerat i `services.py` PDFMerger.)*
6. `~$*.docx` är Word-tempfiler och filtreras alltid bort.
7. Mappar utan DOCX ignoreras utan att krascha appen.
8. Ingen djupare mallnästling skannas.
9. Lösa filer direkt i `Mallar/` är inte mallar och ignoreras.

### 5.3 Aktiv DOCX

- `<MallNamn>.docx` väljs först, case-insensitivt.
- Om den saknas väljs första giltiga DOCX sorterad alfabetiskt.
- `.DOCX` och `.docx` behandlas likvärdigt.
- Dolda filer, tempfiler och tomma filer filtreras bort.

### 5.4 Verifierat resultat för aktuell struktur

```text
✓ 35kW
✓ Demontering
✓ Passagerare
✗ 25kW                         # Bara PDF-bilagor
✗ Mallar/Passagerare.docx      # Lös rotfil
✗ ~$*.docx                     # Word-temporär fil
```

Förväntad loggrad:

```text
[CERTIFICATES] Skanning klar: 3 mall(ar)
```

### 5.5 Prestanda för mallar

- Skanning körs via `QRunnable + QThreadPool` i Certificates-pluginet.
- Placeholder-resultat cachas per DOCX-sökväg, `mtime_ns` och filstorlek.
- Statistikskannern använder motsvarande filstämpelcache.
- Onödig global `refresh_templates`-timer i `main.pyw` är borttagen; Certificates-pluginet äger och sammanfogar refresh-anrop.

---

## 6. Placeholder-regler

- Format: `::NYCKEL::`
- Regex: `::\s*([A-Za-z0-9_åäöÅÄÖ]+)\s*::`
- Sök i body, tabeller, nästlade tabeller, headers, footers, first-page och even-page-varianter.
- Splitade Word-runs hanteras genom sammanfogning innan matchning.
- Manuella fält inkluderar VIN, Reg, År, Namn, Märke, Modell och Ägare.
- Automatiska fält inkluderar Intyg, Nr, Datum, Idag, År_Nu, Månad, Dag, Tid och Timestamp.
- `::År::` avser årsmodell och ska aldrig förifyllas som aktuellt år.

---

## 7. PDF-generering

### Prioriterad kedja

1. LibreOffice
2. docx2pdf
3. Word COM på Windows
4. unoconv
5. AbiWord

### Regler

- Default timeout: 120 sekunder.
- Min timeout: 30 sekunder.
- Max timeout: 600 sekunder.
- Tre försök med exponentiell backoff, faktor 2.0.
- Konverteringsdetektering ska vara cachad.
- PDF-sammanslagning sker atomiskt.
- `pypdf` är primär motor, `PyPDF2` är fallback för äldre installationer.
- Tunga DOCX/PDF-operationer körs i workertråd, aldrig direkt i UI-tråden.

---

## 8. Output och räknare

- Output per mall: `Out/<MallNamn>/`.
- Filnamnsformat bygger på mallens `filename_pattern`; normalfallet motsvarar:

```text
{NR} Intyg {Mall} {VIN} {REG} {DATUM}.pdf
```

- Filnamn NFC-normaliseras och bevarar ÅÄÖ.
- Räknare är trådsäkra, persistenta och skrivs atomiskt via ConfigManager eller JSON-fallback.
- Importerade intyg kan synka räknarvärden uppåt utan att minska befintliga nummer.

---

## 9. Pluginarkitektur

### 9.1 Kontrakt

Varje plugin använder duck-typing:

```python
name: str
version: str
description: str
priority: int

def on_load(self, context: AppContext) -> None: ...
def on_unload(self) -> None: ...
def get_tab(self) -> QWidget | None: ...
```

Legacykompatibilitet:

```python
def setup(self, context: AppContext) -> None: ...
def on_close(self) -> None: ...
```

### 9.2 Prioritet

- Logging måste laddas först i faktisk runtime. Nuvarande Logger-plugin använder prioritet `1` för detta.
- System/tray/core/generator/statistics/settings/certificates följer därefter PluginManager-ordningen.
- `system_tray` är ensam ägare av `QSystemTrayIcon`.

### 9.3 Kommunikationen

- Plugins kommunicerar via EventBus.
- Direkta beroenden används bara för stabila tjänstekontrakt och compat-fallback där EventBus inte kan ersätta intern processnära service.
- Alla EventBus-data är `dict`.
- EventBus-callbacks ska vara korta; tungt arbete skickas vidare till workertråd.

---

## 10. EventBus — komplett avtalskarta

### Intyg

```text
certificate.before_generate
certificate.generated
certificate.generation_failed
certificate.progress
certificate.template_selected
certificate.placeholder_updated
certificates.imported
certificates.batch.started
certificates.batch.completed
```

> `certificate.progress` (ny i services.py 10.2.0, H6): `{template, percent, message}` — emitteras endast när generatorn fått en `event_emitter`; callback-API:t är fortsatt kompatibelt.

### Mallar

```text
templates.scanned
refresh_templates
template.created
template.deleted
template.updated
```

### Statistik

```text
statistics.certificate_generated
statistics.updated
statistics.response
statistics.imported
statistics.request
```

### Inställningar

```text
settings.changed
settings.theme_changed
settings.profile_loaded
show_settings
```

### Tray och fönster

```text
tray.status_update
tray.minimize
tray.restore
window.show
window.hide
```

### Logging

```text
logging.session_started
logging.error_logged
logging.crash_detected
```

### Plugins och app

```text
plugin.loaded
plugin.unloaded
plugin.failed
plugin.reload
plugin_manager.ready
app.startup
app.ready
app.closing
app.shutdown
app.quit
app.quit_confirmed
refresh_ui
notification
status.message
error.occurred
```

---

## 11. Certificates-plugin — aktuell uppdelning

| Fil | Ansvar |
|---|---|
| `__init__.py` | Paket-API, dependency-status, degraded mode och säkra exporter. |
| `engine.py` | `Template`, `TemplateConfig`, VIN/REG, placeholder-cache och DOCX-replacement. |
| `services.py` | PDFConverter, PDFMerger, CounterManager, CertificateGenerator. |
| `view.py` | Formulärfält, preview, progressdialog och intygs-UI-widgets. |
| `plugin.py` | EventBus, mallskanning, workers, batch, import och intygsflik. |

Viktiga egenskaper:

- 3 verifierade mallar från aktuell struktur.
- Asynkron scanning och konverterarprobe.
- Combo-box uppdateras även om scanning slutförs före UI-byggnad.
- DOCX-stämpelcache behålls mellan refreshar.
- Ingen global dubbel-skanning vid startup.

---

## 12. Statistics-plugin — aktuell uppdelning

| Fil | Ansvar |
|---|---|
| `__init__.py` | Paket-API, `TemplateScanner`, `TemplateInfo`, fail-safe exporter. |
| `engine.py` | Statistikmodeller, periodfilter, import, rapporter och mallnamnsskanning. |
| `plugin.py` | PySide6-statistikflik, bakgrundsimport, sökning, diagram och rapportdialoger. |

Stöder:

- Statistik per mall, dag, vecka, månad och år.
- Import från PDF-mappar och CSV.
- Sökning på mall, VIN, REG, filnamn och nummer.
- Text, HTML, JSON, CSV, Markdown, Excel när `openpyxl` finns och PDF när `reportlab` finns.
- Samma mallregler som Certificates-pluginet.

---

## 13. Settings-plugin — ny tredelad struktur

| Fil | Ansvar |
|---|---|
| `__init__.py` | Paketmetadata, core-exporter och fail-safe fallback. |
| `core.py` | Teman, profiler, autostart, dataklasser och genvägshantering. |
| `workers.py` | Säker backup och restore via QRunnable. |
| `view.py` | Settings-shell, scrollflikar, dialoger och kontrollfabrik. |
| `plugin.py` | Controller för 12 flikar, EventBus, profiler, backup, diagnostik och JSON-import/export. |

### Settings-krav

- Exakt tre teman: `system`, `light`, `dark`.
- QPalette används direkt; ingen global stylesheet-omparse för tema.
- Paletter innehåller minst `bg`, `fg`, `text`, `accent`, `accent_fg`, `secondary_bg`, `tertiary_bg`, `border`, `success`, `warning`, `error`, `info`, `disabled`, `link`, `card_bg`, `input_bg`, `selection_bg`, `selection_fg`, `placeholder`, `tooltip_bg`, `tooltip_fg`.
- Theme-observers är trådsäkra.
- Systemtema cachas i 30 sekunder.
- Autostart stöder `is_enabled`, `enable`, `disable`, `set_enabled`, `toggle`, `get_command`, `sync` och `sync_with_system`.
- Profilkatalog skapas aldrig automatiskt vid startup.
- Backup/restore är användarinitierad, workerbaserad och Zip Slip-säker.

---

## 14. Logging-plugin — ny tredelad struktur

| Fil | Ansvar |
|---|---|
| `__init__.py` | Metadata, paketexport och fail-safe fallback. |
| `core.py` | LogEntry, storage, formattering, rotation, export och arkivering. |
| `tracer.py` | Reversibel filsystemspårning med re-entrancy-skydd. |
| `view.py` | Logger UI, loggdisplay, statistik, alerts och filsystemsview. |
| `plugin.py` | Logging-controller, EventBus, JSONL-kö, crash handlers och UI-koppling. |

### LOG-021

- Loggar äldre än 21 dagar gzip-arkiveras till `logs/archive/`.
- `crash.log` arkiveras aldrig.
- `logs/archive/` rensas aldrig automatiskt.
- Aktiva loggar kan rensas enligt 30 dagars retention utan att röra arkivet.

### Filsystemspårning

Tracer täcker bland annat:

- `builtins.open`
- `os.listdir`, `scandir`, `walk`, `stat`, `lstat`
- `remove`, `unlink`, `mkdir`, `makedirs`
- `os.path.exists`, `isfile`, `isdir`
- `glob`, `iglob`
- `Path.open`, `read_*`, `write_*`, `unlink`, `mkdir`, `stat`, `exists`, `is_file`, `is_dir`, `iterdir`, `glob`, `rglob`

Brus från egna loggfiler, arkiv, JSONL, stdlib, Qt och logging-bakgrundstrådar filtreras bort.

---

## 15. System tray

- Hanteras endast av `plugins/system_tray/`.
- `main.pyw` skapar aldrig `QSystemTrayIcon`.
- Exakt en tray-ikon i hela processen.
- Start minimerat om tray-pluginet är aktivt och systemfältet finns.
- Enkelklick togglar fönster.
- X minimerar till tray; avslut sker via meny.
- Ikonordning: riktig ikonfil → möjlig bildgenerator → QPainter-fallback.
- `system_tray/__init__.py` är den återstående paketfilen som ska moderniseras/synkas.

---

## 16. main.pyw — aktuellt ansvar

`main.pyw` ansvarar för:

- Pythonversionskontroll.
- QApplication och MainWindow.
- Single-instance-filås och lokal aktivering av befintlig instans.
- Lazy uppbyggnad av pluginflikar på första event-loop-varvet.
- Visning/döljning av fönster via EventBus.
- Tabbar med Visa-meny, högerklick och sparad synlighet.
- Fönstergeometri, statusrad och appikon.

`main.pyw` ansvarar **inte** för tray-ikon eller mallskanning.

---

## 17. UI och tillgänglighet

- DPI-aware och responsivt på alla skärmstorlekar.
- Scrollbara ytor använder `QScrollArea` med `setWidgetResizable(True)`.
- Mushjul ska fungera i scrollbara formulär.
- Knappar och flikar ska ha tydlig kontrast och fet text.
- Tabbar ska kunna visas/döljas via Visa-meny och högerklick där funktionen är relevant.
- Inga hårdkodade UI-färger utanför:
  - ThemeManager-paletter.
  - `core/themes.py`.
  - Semantiska loggnivåfärger.
  - system tray-ikonens semantiska färger.
  - QPainter-fallbackikoner.

---

## 18. Performance-regler

| ID | Krav |
|---|---|
| PERF-001 | Start till tray: under 3 sekunder. |
| PERF-002 | Tray till fönster: under 500 ms. |
| PERF-003 | Temabyte: under 200 ms, via QPalette. |
| PERF-004 | 10 mallar: under 2 sekunder. |
| PERF-005 | Ett PDF-intyg: under 10 sekunder när konverterare är tillgänglig. |
| PERF-006 | Batch 50: under 120 sekunder. |
| PERF-007 | UI-tråden blockeras aldrig av tungt arbete. |
| PERF-008 | Plugin-load: under 2 sekunder för normal installation. |
| PERF-009 | EventBus emit/dispatch: under 5 ms för lätta callbacks. |
| PERF-010 | Settings I/O använder ConfigManager debounce på 500 ms. |

Implementerade principer:

- QRunnable + QThreadPool för scanning, backup, restore, import och PDF-tjänstdetektering.
- Lazy UI-tabbar.
- Mallscan och placeholder-cache per filstämpel.
- Icke-blockerande öppning av extern filhanterare på macOS/Linux.
- Batch-rendering för stora tree/list-vyer.

---

## 19. Autostart

Autostart ska vara cross-platform:

- Windows: Registry under aktuell användare och `pythonw` där relevant.
- macOS: LaunchAgent-plist.
- Linux: XDG `.desktop`-fil.
- Settings JSON innehåller `app.autostart`.
- Inga dubbletter.
- Trasiga entries ska kunna repareras/synkas.

---

## 20. Single instance

- Windows: `msvcrt.locking`.
- Linux/macOS: `fcntl.flock`.
- PID-metadata sparas separat och atomiskt.
- Lokal IPC försöker fokusera första instansen vid dubbelstart.
- Lås och IPC stängs i ordnad cleanup.

---

## 21. Fail-safe och säkerhet

- Pluginfel ska inte krascha appen.
- Paket-`__init__.py` har fail-safe importer och synlig fallback-UI där PySide6 finns.
- Alla filskrivningar som ändrar användardata ska vara atomiska.
- Backup restore skyddas mot Zip Slip.
- Crashlogg skrivs direkt till disk.
- Logging, mallar och statistik ska logga full traceback för oväntade fel.

---

## 22. Testning

### Genomförda tekniska kontroller i denna uppdatering

- `py_compile` för uppdaterade Pythonfiler.
- Simulerad mallstruktur med exakt väntat resultat: 3 giltiga mallar.
- Placeholdertest med splitade runs i body, tabell, header och footer.
- Certificates engine/services återexport testad.
- Statistics package/engine/plugin-kontrakt testat med PySide6-facade.
- Settings startup testat så att profilmapp inte auto-skapas.
- Settings backup/restore-worker testad med config och mall-DOCX.
- Logging storage, export, arkivering, tracer installation och restore testade.

### Kvar att köra i lokal PySide6-miljö

```text
python test.py
```

Kontrollera särskilt:

```text
Loaded 7/7, failed: 0
[CERTIFICATES] Skanning klar: 3 mall(ar)
```

---

## 23. Aktuell filstatus

### Uppdaterade i denna sessionsserie

| Mapp | Fil | Status |
|---|---|---|
| certificates | `__init__.py` | Uppdaterad |
| certificates | `engine.py` | Uppdaterad och delad mot services |
| certificates | `services.py` | Ny |
| certificates | `view.py` | Ny |
| certificates | `plugin.py` | Uppdaterad |
| statistics | `__init__.py` | Uppdaterad |
| statistics | `engine.py` | Uppdaterad |
| statistics | `plugin.py` | Uppdaterad |
| settings | `__init__.py` | Uppdaterad; versionsmetadata behöver synkas mot 8.7.0 vid nästa metadata-pass |
| settings | `core.py` | Uppdaterad |
| settings | `workers.py` | Ny |
| settings | `view.py` | Ny |
| settings | `plugin.py` | Uppdaterad |
| logging_plugin | `__init__.py` | Uppdaterad |
| logging_plugin | `core.py` | Uppdaterad |
| logging_plugin | `tracer.py` | Ny |
| logging_plugin | `view.py` | Ny |
| logging_plugin | `plugin.py` | Uppdaterad |
| main.pyw | `main.pyw` | Uppdaterad med lazy UI, smart startup och förbättrad ikonhantering |

### Kvarvarande arbete

1. `plugins/settings/__init__.py` — metadata/version synkas med Settings-plugin v8.7.0 efter uttrycklig bekräftelse.
2. `plugins/system_tray/__init__.py` — kvarvarande paketfil.
3. Verifiera system tray-funktioner tillsammans med `models.py` och `plugin.py` i faktisk PySide6-miljö.
4. Kör `test.py` och fixa eventuella integrationsfel.
5. Full beteendediff mot äldre Statistics UI innan den deklareras helt slutgiltig; grundfunktioner är återanslutna men detaljerad regressionsgranskning ska göras.
6. Bilrutan är fortsatt future och ska inte påbörjas utan nytt beslut.
7. Implementera `__pycache__`-rensning enligt §26 "Rensning av teknisk cache" (beställt 2026-08-05, se Del III.7 B1).

---

## 24. Leveranschecklista

Före lokal ersättning av filer:

```text
□ Kopiera samtliga filer i varje ny moduluppdelning.
□ certificates: __init__, engine, services, view, plugin.
□ settings: __init__, core, workers, view, plugin.
□ logging_plugin: __init__, core, tracer, view, plugin.
□ Starta appen utan terminal.
□ Verifiera exakt en tray-ikon.
□ Verifiera tre laddade mallar.
□ Skapa ett testintyg.
□ Kontrollera logs/app.log, session-JSONL och crash.log.
□ Kör test.py i lokal PySide6-miljö.
```

---

## 25. Nästa rekommenderade fil

```text
plugins/system_tray/__init__.py
```

Därefter ska system tray-mappen verifieras som komplett innan ny pluginmapp påbörjas.

---

## 26. Smart runtime och cachepolicy

### DEV-läge

- Debug kan vara aktivt.
- Användbar cache får behållas mellan körningar.
- Diagnostik ska vara tillgänglig utan att blockera UI.

### PROD-läge

- Tempdata och Python-cache får rensas vid ordnad exit.
- Rensa aldrig användardata, `settings.json`, loggar, profiler, `Mallar/`, `Out/`, `Skickade/` eller `Backup/`.
- Följande är rensbar teknisk cache: `__pycache__/`, `*.pyc`, `*.pyo` och appens tillfälliga arbetssökvägar.

Inställningar styrs i JSON:

```json
{
  "mode": "dev",
  "smart_cache": true
}
```

### Rensning av teknisk cache (beställt 2026-08-05)

Appen ska ha en funktion som rensar teknisk Python-cache (`__pycache__/`):

- Rensa `__pycache__/`-mappar samt `*.pyc`/`*.pyo` under hela projektroten (inkl. `core/` och `plugins/**`).
- **DEV-läge:** funktionen finns tillgänglig men körs inte automatiskt (kan utlösas manuellt, t.ex. via Settings).
- **PROD-läge:** rensning sker vid ordnad exit (efter `app.shutdown`), styrd av `smart_cache`-flaggan.
- Rensningen rör **aldrig** användardata: `settings.json`, loggar, profiler, `Mallar/`, `Out/`, `Skickade/` och `Backup/` är fredade (enligt PROD-reglerna ovan).
- Implementering: central hjälpfunktion i `core/infrastructure.py` (säker, fel loggas — FORBIDDEN-011) som anropas från cleanup-kedjan i `main.pyw` och även exponeras för manuell körning via Settings-pluginet.
- Förväntad loggrad vid rensning: `[CACHE] Rensade <N> __pycache__-mappar (<M> filer)`.
- Status: **beställd, ej implementerad** (se Del III.7, B1).

Den fullständiga Smart Cache-designen (universell JSON-filsbaserad cache med live-uppdateringar) finns i **Del II** i denna fil.

---

## 27. Kärnmoduler och ansvar

### `core/__init__.py`

- Applikationskonstanter, paths och session-ID.
- EventBus med wildcard-stöd och thread-safe emit.
- `AppContext` med paths, logger, EventBus och fönsterhjälp.
- PluginManager med discovery, prioritetssortering, fail-safe laddning och `notify_ready()`.
- TabManager och plugin-kontrakt.

### `core/config.py`

- `ConfigManager`/`SettingsManager` med singleton per path.
- JSON, atomic write och debounce-save på 500 ms.
- Observer-mönster för settingsändringar.
- `IconManager`, `WindowManager`, `TrayConfig` och core-autostart.

### `core/infrastructure.py`

- Atomisk filskrivning.
- Plattformshjälpare.
- Formatering av datum, storlek och nummer.
- VIN-hjälpare, `safe_filename`, retry/debounce/throttle och synkronisering.

### `core/themes.py`

- Centralt ThemeManager-kontrakt för appens tre teman.
- QPalette med Active, Inactive och Disabled-grupper.
- Palette-nyckeln `text` är obligatorisk.
- Theme byte sker utan omstart.

### `plugins/__init__.py`

- Pluginprioriteter, EventBus-konstanter, discovery och OS-hjälpare.
- Scroll- och tabbhjälpare.
- Ingen quote-prioritet eller quote-plugin.

---

## 28. Övriga befintliga plugins

### Generator

Generator-pluginet behåller sin uppdelning:

```text
plugins/generator/
├── __init__.py
├── core.py
├── plugin.py
└── types.py
```

Ansvar:

- Delade genereringstjänster där de används.
- Samarbete med Certificates via servicekontrakt och EventBus.
- Ingen dubblerad PDF- eller counter-implementation.

### Template Designer

```text
plugins/template_designer/
├── __init__.py
├── core.py
└── plugin.py
```

Ansvar:

- Skapa, importera, duplicera, byta namn och radera mallar.
- Använd samma mallstruktur som Certificates.
- Emitterar `template.created`, `template.updated` och `template.deleted`.
- Ska inte skapa undermappar djupare än `Mallar/<MallNamn>/`.

### System Tray

```text
plugins/system_tray/
├── __init__.py
├── models.py
└── plugin.py
```

Ansvar:

- Exakt en tray-ikon.
- Öppna/dölj fönster, alerts och senaste intyg.
- Traykonfiguration och statusikoner.
- `models.py` innehåller plattform, ikonstatus, notifieringar och traydata.
- `plugin.py` innehåller den faktiska `QSystemTrayIcon`-instansen.

### Bilrutan — future

Bilrutan är inte aktiv i nuvarande release och ska inte implementeras förrän nytt beslut tas.

Planerad struktur:

```text
plugins/bilrutan/
├── __init__.py
├── plugin.py
├── view.py
├── form.py
├── pdf_handler.py
├── archive_manager.py
├── database.py
└── templates/
    └── bilrutan_blankett.pdf
```

Planerat ansvar:

- Skadeblanketter och automatisk PDF-fyllning.
- Arkiv: `archive/ÅR/MÅNAD/REGNR_DATUM.pdf`.
- SQLite-databas för ärenden.
- Sökning på regnr, kund och datum.
- EventBus: `bilrutan.case_created`, `bilrutan.case_saved`, `bilrutan.case_archived`, `bilrutan.pdf_generated`, `bilrutan.email_sent`.
- Prioritet 70.

---

## 29. Huvudfönster, flikar och ikoner

`main.pyw` ska fortsatt innehålla:

- QPainter-fallbackikon om `Icon/` saknar användbar ikon.
- Laddning av `icon.svg`, `icon.png`, `icon.ico`, `app.svg`, `app.png`, `app.ico` eller motsvarande.
- Single-instance-lås med PID-metadata och lokal aktivering av första instans.
- Header, statusrad och native systemikoner.
- Lazy UI-montering för pluginflikar.
- Sparad geometri och säker clamp till befintliga skärmar.
- Visa-menyn för att dölja/visa pluginflikar.
- Högerklick på flik för samma funktion.
- Icke-blockerande öppning av mappar.
- Ingen egen trayinstans.

### Ikonregler

1. Fil från `Icon/`.
2. Möjlig bildgenerator när dependency finns.
3. QPainter-fallback som alltid finns.

QPainter-fallbackikoner får använda egna färger som uttryckligt COLORS-001-undantag.

---

## 30. UI-detaljer

- Alla knappar och tabbar ska vara tydliga i ljus, mörk och systemtema.
- Text får inte klippas i listor eller formulär i normal fönsterstorlek.
- Dialoger ska använda word wrap där felmeddelanden kan bli långa.
- Formulär ska ha tangentbordsfokus i första relevanta fältet.
- Alla bakgrundsoperationer ska ha synlig progress när de kan ta mer än ungefär 500 ms.
- Scrollbara formulär använder wheel-event utan att skapa dubbel scroll/zoom.
- QPalette är förstahandsmekanism för tema; stylesheets används inte som global temamekanism.
- Settings, Certificates, Statistics och Logger har egna interna tabbar, men global flikhantering ägs av MainWindow/TabManager.

---

## 31. Detaljerad testplan

`test.py` ska utan PySide6 kunna testa minst följande områden:

1. Importer av kärn- och pluginmoduler.
2. EventBus subscribe, emit, wildcard och unsubscribe.
3. Pluginload och `get_tab()`-kontrakt.
4. Mallskanning enligt MALL-001–009.
5. Placeholderdetektion inklusive splitade runs.
6. Header/footer-replacement.
7. Filnamnssäkerhet och ÅÄÖ/NFC.
8. Settings och ConfigManager-beteende.
9. Copyrightformat och förbjudna ägarmarkeringar.
10. Mappstruktur och förbjuden quote-modul.
11. Certificates engine: VIN, REG, parser och generatorförutsättningar.

### Manuella smoke tests

```text
□ App startar utan terminal.
□ En tray-ikon visas.
□ Fönster kan döljas/återställas från tray.
□ Tre mallar syns i Intyg-fliken.
□ Passagerare.json läses utan fel.
□ Placeholder i body, tabell, header och footer hittas.
□ En PDF skapas om PDF-konverterare är installerad.
□ Statistik uppdateras efter generering.
□ Logger visar event, filoperation och sessiondata.
□ Backup kan skapas och återställas säkert.
□ js/profiles saknas efter ren startup om användaren inte skapat profil.
```

---

## 32. Historiska buggar och nuvarande fixläge

| ID | Status | Åtgärd |
|---|---|---|
| BUG-001 | Fixad | MainWindow skapar ingen tray-ikon. |
| BUG-002 | Fixad i Certificates | Direktundermappar, tempfilfilter, pending combo-update och async scan. |
| BUG-003 | Fixad | Qt logging-regler konfigureras före QApplication. |
| BUG-004 | Fixad | Logging cleanup-ordning och handlerstängning. |
| BUG-005 | Fixad | Generator settings jämförs korrekt. |
| BUG-006 | Fixad | PluginManager `notify_ready()` emitterar EventBus-event. |
| BUG-007 | Fixad | Configmapp är `js`, inte `.js`. |
| BUG-008 | Fixad | NFC-normalisering av filnamn. |
| BUG-009 | Fixad | Progressiv ConfigManager-kompatibilitet i `save_to_config`. |
| BUG-010 | Fixad | Degraded placeholder-scan i Certificates. |
| BUG-011 | Fixad | Guard mot dubbla template-selection-anrop. |
| BUG-012 | Fixad | Future import är alltid rad 1. |
| BUG-013 | Fixad | Dynamiskt copyrightår. |
| BUG-014 | Fixad | Robust Template-konstruktion vid degraded mode. |
| BUG-015 | Fixad | Palette-nyckeln `text` finns i ljus och mörk palett. |
| BUG-016 | Fixad | Scan/UI-race hanteras via pending kombouppdatering. |
| BUG-017 | Fixad | Quote-prioritet tas bort. |
| BUG-018 | Fixad | Eventnamn för `window.show` är normalt textvärde. |
| BUG-019 | Fixad | Statiska copyrightår rensas ur aktiv kod. |
| BUG-020 | Fixad | Settings-plugin saknade `suppress`-import; tillagd. |
| BUG-021 | Fixad | Logger fallback saknade `QLabel`/`QVBoxLayout`-import; tillagd. |

---

## 33. Versions- och metadataharmonisering

Följande ska hållas synkat i nästa metadata-pass:

```text
plugins/settings/__init__.py   8.6.0
plugins/settings/plugin.py     8.7.0
plugins/settings/core.py       9.0.0
```

Kodfunktionerna är uppdaterade, men paketmetadata ska synkas med uttrycklig bekräftelse enligt MUST-016. Versionsskillnaden är dokumenterad tills synkningen görs.

---

## 34. Sammanfattande releasekrav

En release är klar först när:

```text
□ Inga syntaktiska fel i samtliga uppdaterade filer.
□ PySide6-miljö kan ladda alla plugins.
□ Logger har exakt en controller och inga dubblade handlers.
□ Tray har exakt en QSystemTrayIcon.
□ Certificates visar exakt tre giltiga mallar för aktuell Mallar-struktur.
□ Statistics och Certificates rapporterar samma mallnamn.
□ Settings skapar inte profiles-mapp vid startup.
□ Backup restore klarar säkra ZIP-filer och blockerar Zip Slip.
□ LOG-021 arkiverar gamla loggar men aldrig crash.log.
□ quote/-modulen är frånvarande.
□ test.py har körts i lokal miljö.
```

© Rickard Längkvist 2025–2026

---
---

# DEL II — SMART CACHE (SPEC v0.1)

> En universell, språkoberoende smart cache som cachar *allt*, sparar allt till **JSON-filer** på disk och uppdaterar JSON-filerna **live** — så att vilket program som helst kan läsa, skriva och bevaka cachen utan kodändringar.

**Status:** Förslag (v0.1) · **Senast uppdaterad:** 2026-08-05 · Kopplar till Del I §26 (`smart_cache`-flaggan).

## SC1. Översikt

Systemet består av en lokal cache-demon (`cached`) med:

- **Ett generiskt nyckel–värde-API** (HTTP REST + CLI + klientbibliotek) som funkar för *alla* program.
- **"Cachar allt"** – värden kan vara vilken JSON-serialiserbar data som helst: strängar, siffror, boolean, objekt, arrayer, samt `base64`-bytes och stora filer.
- **JSON-lagring** – varje mutation skrivs omedelbart (live) och atomiskt till en JSON-fil på disk, t.ex. `data/http.json`.
- **Live-uppdateringar** – alla förändringar publiceras i realtid via:
  1. en append-only **NDJSON-händelselogg** (`data/events.ndjson`),
  2. **SSE/WebSocket-prenumerationer** med replay av missade händelser,
  3. **filbevakning** – vilket program som helst kan `tail`-a eller bevaka JSON-filerna.
- **Smart logik** – TTL, stale-while-revalidate, hybrid-LRU/LFU-eviction, single-flight (dedupe av samtidiga missar), adaptiv TTL och statistik.

## SC2. Mål och icke-mål

### Mål

| # | Mål | Krav |
|---|-----|------|
| M1 | Fungerar med **alla program** | Språkoberoende: HTTP + CLI + SDK:er (Python, Node, Go, Rust, …). Inget program behöver ändras för att använda grundfunktionerna. |
| M2 | **Cachar allt** | Generiska värden: `string`, `number`, `boolean`, `json`, `array`, `base64` (bytes), `file` (stora blobbar). |
| M3 | **JSON-filer som lagring** | Hela cache-storen är läsbara JSON-filer. Mänskligt läsbara, enkla att debugga, backa upp och flytta. |
| M4 | **Live-uppdateringar av JSON-filerna** | Varje `set`/`delete`/`expire`/`evict` skriver om berörd JSON-fil direkt (standard: synkront, ingen fördröjning). |
| M5 | **Realtidsprenumerationer** | Händelselogg + SSE + WebSocket med `since`-replay. |
| M6 | **Smart eviction & TTL** | TTL, stale-while-revalidate, hybrid-LRU/LFU, single-flight, adaptiv TTL. |
| M7 | **Konsistent & säker** | Atomiska skrivningar (tmp + rename), lås, korruptionsdetektering, localhost-bound. |

### Icke-mål (i v0.1)

- Inte ett distribuerat cache-kluster (roadmap: se SC19).
- Inte en ersättning för databaser – inga joins, inga transaktioner över flera nycklar.
- Inte optimerad för multi-GB-blobbar per post (gräns per värde, se SC16).

## SC3. Terminologi

| Term | Betydelse |
|------|-----------|
| **Entry / post** | En cachad nyckel–värde-enhet med metadata (TTL, träffar, taggar, …). |
| **Namespace** | Namngivet, isolerat cache-utrymme (t.ex. `http`, `db`, `files`). Varje namespace = en eller flera JSON-filer. |
| **Store-fil** | JSON-filen som innehåller en namespace:s poster, t.ex. `data/http.json`. |
| **Händelse (event)** | En JSON-rad i händelseloggen om en förändring (`set`, `delete`, `expire`, `evict`, …). |
| **TTL** | Time-to-live: hur länge en post får leva. `ttl = null` betyder evig. |
| **Stale** | En post vars TTL passerats men som fortfarande får serveras under **stale-while-revalidate**-fönstret. |
| **Generation** | Monotont stigande versionsnummer på en store-fil, för optimistisk samtidighet. |
| **Single-flight** | Deduplicering: flera samtidiga missar på samma nyckel → endast en uppslagning/fetch. |

## SC4. Arkitektur

```
┌──────────────┐   HTTP REST     ┌─────────────────────────────────┐
│  Program A   │ ──────────────▶ │       cache-daemon (cached)     │
│  (Python SDK)│                 │  127.0.0.1:7373                 │
└──────────────┘                 │  ┌────────────┐ ┌─────────────┐ │
┌──────────────┐   CLI           │  │  Smart     │ │  Event Bus  │ │
│  Program B   │ ──────────────▶ │  │  Engine    │ │  (SSE/WS)   │ │
│  (skript)    │                 │  │ (TTL, LRU, │ │             │ │
└──────────────┘                 │  │  SWR, ...) │ └──────┬──────┘ │
┌──────────────┐   SSE/WS        │  └─────┬──────┘        │        │
│  Program C   │ ◀────────────── │        │               │        │
│  (curl)      │                 │  ┌─────▼───────────────▼──────┐ │
└──────────────┘                 │  │        JSON Store          │ │
┌──────────────┐   filbevakning  │  │  data/<ns>.json  (snapshot)│ │
│  Program D   │ ◀────────────── │  │  data/events.ndjson (logg) │ │
│  (vilket som)│   tail -f       │  └────────────────────────────┘ │
└──────────────┘                 └─────────────────────────────────┘
```

### Flöde vid en skrivning (exempel)

1. Program A anropar `PUT /v1/cache/http/user:123`.
2. Daemon validerar nyckel/namespace, sätter TTL/metadata, lägger in posten i minnet.
3. Daemon skriver **atomiskt** om `data/http.json` (tmp-fil → `rename`).
4. Daemon append:ar en händelse `{"op":"set","ns":"http","key":"user:123",...}` till `data/events.ndjson`.
5. Daemon pushar händelsen till alla SSE/WebSocket-prenumeranter.
6. Program D, som `tail -f`:ar `data/http.json`, ser den nya posten live.

## SC5. Datamodell

### SC5.1 Nyckel

- Sträng, UTF-8, max **1024 tecken**.
- Tillåtna tecken: valfri UTF-8 **utom** `/`, `\`, NUL och kontrolltecken (undantag: namespace-nyckel som används i URL:er måste URL-encodas av klienten).
- Rekommendation: `type:id`-mönster, t.ex. `user:123`, `page:/about`.

### SC5.2 Värde

| `valueType` | Beskrivning | JSON-representation |
|-------------|-------------|---------------------|
| `json` | Valfri JSON-struktur (standard) | som den är |
| `string` | Text | sträng |
| `number` | Siffra | siffra |
| `boolean` | Boolean | boolean |
| `null` | Null | `null` |
| `base64` | Råa bytes | `{ "base64": "..." }` |
| `file` | Pekare till stor blob (utanför store-JSON) | `{ "path": "data/blobs/<id>.bin", "size": 1234 }` |

Default är `json`; `base64`/`file` väljs via API-parametern `valueType`.

### SC5.3 Post (entry)

```json
{
  "key": "user:123",
  "valueType": "json",
  "value": { "name": "Ada", "id": 123 },
  "createdAt": "2026-08-05T10:00:00.000Z",
  "updatedAt": "2026-08-05T10:05:00.000Z",
  "expiresAt": "2026-08-05T11:00:00.000Z",
  "ttl": 3600,
  "staleUntil": "2026-08-05T11:05:00.000Z",
  "hits": 42,
  "sizeBytes": 87,
  "tags": ["user", "stockholm"],
  "version": 3
}
```

Fält:

| Fält | Typ | Obligatorisk | Beskrivning |
|------|-----|--------------|-------------|
| `key` | string | ja | Cache-nyckel |
| `valueType` | enum | ja | Se SC5.2 |
| `value` | any | ja | Värdet |
| `createdAt` / `updatedAt` | ISO-8601 | ja | Tidsstämplar |
| `expiresAt` | ISO-8601 \| null | nej | Absolut utgångstid |
| `ttl` | number \| null | nej | Sekunder från `updatedAt` (0 = evig) |
| `staleUntil` | ISO-8601 \| null | nej | Slut på SWR-fönstret |
| `hits` | number | ja | Antal träffar (för eviction/statistik) |
| `sizeBytes` | number | ja | Storlek på serialiserat värde |
| `tags` | string[] | nej | Taggar för gruppering/prefix/prefetch |
| `version` | number | ja | Monotont versionsnummer per post |

### SC5.4 Namespace

- Namn: `[a-z0-9_-]{1,64}` (små bokstäver, siffror, `-`, `_`).
- Isolerade utrymmen: nycklar i olika namespaces krockar aldrig; varje namespace har egen konfiguration (TTL, budget, eviction, shards).

## SC6. Lagring — JSON-filer

### SC6.1 Katalogstruktur

```
data/
├── <namespace>.json            # store-fil (snapshot) per namespace
├── <namespace>.json.tmp        # temporär fil under skrivning (aldrig kvar)
├── <namespace>.lock            # låsfil (flock) för fil-läge
├── events.ndjson               # append-only händelselogg (global)
├── blobs/                      # valfritt: stora `file`-värden
└── quarantine/                 # korrupta filer flyttas hit
```

Vid många poster (rekommendation > ~250 000 poster per namespace) kan en namespace shardas:

```
data/<namespace>/shard_0000.json
data/<namespace>/shard_0001.json
...
```

Shardval: `crc32(key) % numShards` (konsistent, stabil över omstart).

### SC6.2 Store-filens format

```json
{
  "schemaVersion": 1,
  "namespace": "http",
  "generation": 12,
  "updatedAt": "2026-08-05T10:05:00.000Z",
  "checksum": "sha256:<hex>",
  "entries": {
    "user:123": { "key": "user:123", "valueType": "json", "value": { } }
  },
  "meta": {
    "totalHits": 812,
    "totalMisses": 99,
    "hitRate": 0.891,
    "evictions": 7,
    "expirations": 3,
    "entryCount": 2,
    "totalBytes": 174
  }
}
```

- `checksum` = SHA-256 över det serialiserade `entries`-blocket (utan `checksum`-fältet). Vid läsning: räkna om; vid mismatch → flytta filen till `quarantine/`, logga, fortsätt från minne.
- `generation` ökar med 1 vid varje skrivning — används för optimistisk samtidighet i fil-läge (se SC12).

### SC6.3 Atomisk skrivning ("live update")

1. Serialisera hela nya state → skriv till `<ns>.json.tmp`.
2. Valfri `fsync` (beroende på `durability`, se SC13).
3. `rename("<ns>.json.tmp", "<ns>.json")` — atomiskt på POSIX och Windows (inom samma volym).
4. Uppdatera minnesbild + generation.

**Garanti:** en läsare ser aldrig en halvskriven fil — den ser antingen gamla eller nya versionen. Detta gör att *vilket* program som helst tryggt kan `tail -f` / läsa filen upprepat.

**Standard: synkron skrivning vid varje mutation** (live). Valfri `writeDebounceMs` för mycket höga skrivfrekvenser (då slås flera mutationer ihop inom fönstret, men händelseloggen append:as fortfarande per mutation).

### SC6.4 Händelselogg (`events.ndjson`)

Append-only, en JSON-rad per händelse, linjebuffrad och flushad vid varje mutation:

```json
{"seq":1,"ts":"2026-08-05T10:00:00.000Z","op":"set","ns":"http","key":"user:123","ttl":3600}
{"seq":2,"ts":"2026-08-05T10:00:01.000Z","op":"set","ns":"http","key":"page:/about","tags":["page"]}
{"seq":3,"ts":"2026-08-05T10:01:00.000Z","op":"delete","ns":"http","key":"user:999","reason":"client"}
{"seq":4,"ts":"2026-08-05T10:02:00.000Z","op":"expire","ns":"http","key":"tmp:x","reason":"ttl"}
{"seq":5,"ts":"2026-08-05T10:03:00.000Z","op":"evict","ns":"http","key":"old:y","reason":"lru"}
{"seq":6,"ts":"2026-08-05T10:04:00.000Z","op":"flush","ns":"db"}
{"seq":7,"ts":"2026-08-05T10:04:01.000Z","op":"refresh","ns":"http","key":"user:123","status":"stale-served"}
{"seq":8,"ts":"2026-08-05T10:05:00.000Z","op":"stat","periodStart":"2026-08-05T10:00:00Z","hitRate":0.891}
```

- `seq` är globalt monotont stigande — används som watermark för replay (`since=<seq>`).
- Loggen roteras: när den överstiger `events.maxFileBytes` (default 64 MB) → `events.ndjson.1`, `.2`, … och `events.ndjson` börjar om. Replay-bufferten i minnet håller de senaste `events.replayBuffer` (default 10 000) händelserna.

## SC7. Live-uppdateringar — tre kanaler

| Kanal | För vem | Hur |
|-------|---------|-----|
| **A. JSON-filen själv** | Alla program, utan SDK | Läs `data/<ns>.json` upprepat eller `tail -f`. Atomisk rename garanterar konsistenta snapshots. |
| **B. NDJSON-loggen** | Alla program, utan SDK | `tail -f data/events.ndjson` → ström av förändringar med `seq`, `op`, `ns`, `key`. |
| **C. SSE / WebSocket** | Program med HTTP-stöd | `GET /v1/events` (SSE) eller `WS /v1/ws` → realtidspush + replay med `since=<seq>`. |

**Krav:** en klient som prenumererar på händelser MÅSTE kunna återhämta missade händelser via `since=<seq>` (annars riskerar den missa uppdateringar). Daemon svarar med alla händelser med `seq > since`, sedan strömmar den live.

## SC8. HTTP-API

Bas: `http://127.0.0.1:7373/v1` (konfigurerbar, se SC13). Alla förfrågningar/body är JSON (`Content-Type: application/json`).

| Metod & sökväg | Beskrivning | Exempel-svar |
|---|---|---|
| `PUT /v1/cache/{ns}/{key}` | Sätt post. Body: `{"value": <any>, "ttl"?: 3600, "valueType"?: "json", "tags"?: ["a"]}`. `?ttl=` i query funkar också. | `201 {"ok":true,"entry":{...}}` |
| `GET /v1/cache/{ns}/{key}` | Hämta post. Header `X-Cache-Status: hit|stale|miss`. 404 om miss. | `200 {"found":true,"value":{...},"meta":{...}}` |
| `DELETE /v1/cache/{ns}/{key}` | Ta bort post. | `200 {"ok":true,"removed":true}` |
| `POST /v1/cache/{ns}/mget` | Batch-läs. Body: `{"keys":["a","b"]}`. | `200 {"entries":{...},"misses":["b"]}` |
| `POST /v1/cache/{ns}/mset` | Batch-skriv. Body: `{"entries":[{"key":"a","value":1,"ttl":60}]}`. | `200 {"ok":true,"count":2}` |
| `GET /v1/cache/{ns}?prefix=&tags=&limit=&cursor=` | Lista/sök poster (sorterade på nyckel). | `200 {"entries":[...],"nextCursor":null}` |
| `POST /v1/cache/{ns}/flush` | Töm namespace. | `200 {"ok":true,"removed":<n>}` |
| `POST /v1/cache/{ns}/{key}/refresh` | Tvinga revalidering (se SC11.3). | `200 {"ok":true,"status":"refreshed"}` |
| `GET /v1/stats` | Global statistik + per namespace. | `200 {"hitRate":0.89,"namespaces":{...}}` |
| `GET /v1/events?since=<seq>&namespace=<ns>` | **SSE**-ström av händelser (med replay). | `text/event-stream` |
| `WS /v1/ws?since=<seq>` | WebSocket-version av ovan. | binär/JSON-ramar |
| `GET /v1/file/{ns}` | Servera den råa, aktuella store-JSON-filen. | `application/json` |
| `GET /v1/health` | Hälsokoll. | `200 {"status":"ok"}` |

### Svarsformat `GET` (single)

```json
{
  "found": true,
  "value": { "name": "Ada" },
  "meta": {
    "valueType": "json",
    "createdAt": "2026-08-05T10:00:00.000Z",
    "expiresAt": "2026-08-05T11:00:00.000Z",
    "hits": 42,
    "tags": ["user"]
  }
}
```

Vid stale-post: `"found": true`, `X-Cache-Status: stale`, `meta.revalidating: true` (se SC11.3).

## SC9. CLI (`cache`)

Samma funktionalitet som API:t, för skript och interaktivt bruk:

```bash
cache serve                     # starta daemon (läser config.json)
cache put http user:123 '{"name":"Ada"}' --ttl 3600 --tag user
cache put http user:123 'hej' --type string
cache get http user:123
cache get http user:123 --raw   # skriv bara värdet (utan metadata)
cache del http user:123
cache mget http user:123 user:124
cache list http --prefix user: --tag stockholm --limit 100
cache flush http
cache watch http                # taila händelser för namespace (--watch --json för NDJSON-rad)
cache watch                     # alla namespaces
cache stats
cache proxy --upstream https://api.example.com --port 7374   # se SC10.4
```

Exit-koder: `0` = ok, `1` = fel, `2` = nyckeln fanns inte (för `get`/`del`), `3` = konfigurationsfel.

## SC10. "Funkar till alla program" — integrationsytor

### SC10.1 Noll-integration (curl, skript, alla språk)

```bash
curl -X PUT localhost:7373/v1/cache/http/user:123 \
     -d '{"value":{"name":"Ada"},"ttl":3600}'
curl localhost:7373/v1/cache/http/user:123
tail -f data/http.json          # live-uppdateringar, inget mer behövs
tail -f data/events.ndjson
```

### SC10.2 Klientbibliotek (officiella)

| Språk | Paket | Minimum |
|-------|-------|---------|
| Python | `cache-sdk` (PyPI) | `cache.set("http","user:123",obj,ttl=3600)` |
| Node.js | `@smartcache/client` (npm) | `cache.set("http","user:123",obj,{ttl:3600})` |
| Go | `github.com/smartcache/client-go` | `c.Set(ctx,"http","user:123",obj,ttl)` |
| Rust | `smartcache` (crates.io) | `c.set("http","user:123",&obj,ttl)?` |
| Bash | inbyggt via CLI | `cache put http user:123 "$JSON"` |

Alla SDK:er exponerar: `get`, `set`, `delete`, `mget`, `mset`, `list`, `flush`, `watch` (live), `stats`, och `wrap`/`fetch_or_set`. SDK:erna pratar HTTP mot daemon; vid `cacheDaemon` ej tillgänglig kan de falla tillbaka till fil-läge (SC18).

### SC10.3 Dekorator-hjälpare ("cacha dina funktioner")

```python
from smartcache import cache

@cache.cached("db", ttl=60, key="user:{id}")
def get_user(id: int):
    return database.query(id)   # körs bara vid miss
```

- Nyckeln mallas från argumenten (`{id}`).
- Vid miss: funktionen körs, resultatet skrivs till cachen — JSON-filen uppdateras live.
- `stale-while-revalidate` inbyggt: `@cache.cached(..., swr=60)`.

### SC10.4 Proxy-läge — cacha *utan någon* kodändring

Daemon kan agera lokal HTTP-proxy som cachar uppströms-svar:

```bash
cache proxy --upstream https://api.example.com --port 7374
curl http://127.0.0.1:7374/v2/users/123   # proxas + cachas
```

- Cache-nyckel: `method + path + query + utvalda headers` (t.ex. `Authorization` exkluderas som default).
- Respekterar `Cache-Control: max-age` / `s-maxage` när de finns; annars namespace-`ttl` som fallback.
- Allt skrivs till JSON-storen och händelseloggen precis som vanligt — d.v.s. även proxyns cache är levande JSON-filer.
- Detta uppfyller "funkar till alla program / cachar allt" utan att appen ens vet om att det finns en cache.

## SC11. Smart cache-logik

### SC11.1 TTL & utgång

- `ttl` sätts per post eller som namespace-default.
- **Lazy utgång:** vid `GET` kontrolleras `expiresAt`; är den passerad → posten behandlas som borta, `op:expire` loggas, ev. SWR aktiveras.
- **Aktiv utsopning:** en bakgrundssweeper (intervall `sweeper.intervalSeconds`, default 5 s) tar bort utgångna poster och skriver om berörda store-filer live.

### SC11.2 Eviction (när budgeten är slut)

- Per namespace: `maxEntries` och `maxBytes`. Global budget: summan av alla namespaces.
- Algoritm (default): **hybrid LRU–LFU (WLFU)** — vikt = `hits / (1 + ålder i sekunder)`; lägst vikt evicteras först. Värmen spåras i `meta` och per post (`hits`, `updatedAt`).
- Vid evict: `op:evict` med `reason` loggas, filen skrivs om live.
- Skydd: `expiresAt`-poster evicteras aldrig före sin TTL om de fått ≥ 1 träff i senaste fönstret (skyddar heta korta poster).

### SC11.3 Stale-while-revalidate (SWR)

För att tåla långsamma uppströmskällor utan att blockera:

1. `GET` på utgången post med `staleUntil` i framtiden → svara direkt med gamla värdet, `X-Cache-Status: stale`, `meta.revalidating: true`.
2. Daemon (eller SDK-hook) hämtar färskt värde i bakgrunden och `set`:ar det — JSON-filen och prenumeranter uppdateras live med `op:refresh`.
3. Har posten inget färskt värde att hämta (ingen `revalidate`-hook registrerad) → behandla som miss.

### SC11.4 Single-flight (dedupe av missar)

- Två samtidiga `GET` på samma nyckel som båda missar → bara **en** uppströms-fetch/bakgrundsberäkning; den andra väntar på resultatet (max `singleFlightTimeoutMs`, default 5 s, sedan egen fetch).
- Eliminerar stampede vid start/restart.

### SC11.5 Adaptiv TTL

- Valfritt per namespace: `adaptiveTtl: { baseTtl: 60, maxTtl: 3600, k: 0.5 }`.
- Effektiv TTL: `ttl = min(maxTtl, baseTtl * (1 + k * log2(1 + hits)))`.
- Heta poster lever längre, kalla dör snabbt — minskar onödiga skrivningar och missar.

### SC11.6 Statistik (i `meta` + `/v1/stats`)

- `totalHits`, `totalMisses`, `hitRate`, `evictions`, `expirations`, `entryCount`, `totalBytes`, per namespace och per tag.
- Skrivs in i store-filens `meta` vid varje skrivning samt `op:stat`-händelse var `stats.intervalSeconds` (default 60 s) så live-bevakare ser utvecklingen.

### SC11.7 Prefetch (valfritt)

- Konfigurerbart: när en post med tag `T` läses, hämta de `prefetch.limit` senast uppdaterade posterna med samma tag i bakgrunden (t.ex. användarens profil + inställningar samtidigt).

## SC12. Samtidighet & konsistens

| Garanti | Mekanism |
|---------|----------|
| Inga halvlästa filer | Atomisk `rename` (SC6.3) |
| En skrivare i taget (daemon) | Seriell mutationsloop; alla mutationer är minnesinterna + skrivning efteråt |
| Flera skrivare (fil-läge) | Låsfil `<ns>.lock` med `flock` + optimistisk concurrency via `generation`: läs → ändra → skriv med `generation+1`; om `rename` misslyckas p.g.a. att filen ändrats → gör om (CAS-liknande) |
| Ordning av händelser | Globalt monotont `seq` i `events.ndjson`; prenumeranter kan alltid replaya med `since` |
| Post-ordning per nyckel | `version`-räknare per post; skrivningar med lägre version än befintlig nekas (last-writer-wins på `version`, inte på klocktid) |
| Korruption | `checksum` (SHA-256); vid mismatch → flytta till `quarantine/`, logga, betjäna från minne |
| Återställning vid krasch | Vid start: läs store-filerna; händelseloggen används för att återskapa om snapshot saknas |

### Konsistenslägen (`durability`)

| Läge | Betydelse |
|------|-----------|
| `none` | Snabbast; skrivningar kan gå förlorade vid strömavbrott |
| `os` (default) | `rename` genomförd; OS-buffertar flushas normalt |
| `fsync` | `fsync` på tmp-fil före `rename`; starkast garanti, långsammast |

## SC13. Konfiguration (`config.json`)

```json
{
  "listen": "127.0.0.1:7373",
  "dataDir": "./data",
  "authToken": null,
  "durability": "os",
  "writeDebounceMs": 0,
  "defaults": {
    "ttl": 3600,
    "staleTtl": 300,
    "maxEntries": 1000000,
    "maxBytes": 1073741824,
    "maxValueBytes": 10485760,
    "eviction": "wlfu"
  },
  "namespaces": {
    "http":  { "ttl": 60, "maxEntries": 500000, "maxBytes": 268435456, "shards": 1, "adaptiveTtl": { "baseTtl": 60, "maxTtl": 3600, "k": 0.5 } },
    "db":    { "ttl": null, "maxEntries": 100000, "staleTtl": 600 },
    "files": { "ttl": 86400, "maxBytes": 5368709120, "shards": 8 }
  },
  "sweeper": { "intervalSeconds": 5 },
  "singleFlight": { "timeoutMs": 5000 },
  "events": { "file": true, "sse": true, "ws": true, "maxFileBytes": 67108864, "replayBuffer": 10000 },
  "stats": { "intervalSeconds": 60 },
  "proxy": { "upstream": null, "port": 7374, "excludeHeaders": ["authorization", "cookie"] }
}
```

- Miljövariabler åsidosätter: `CACHE_LISTEN`, `CACHE_DATA_DIR`, `CACHE_AUTH_TOKEN`, `CACHE_CONFIG` (sökväg).
- Alla inställningar per namespace ärver `defaults` om de inte anges.

## SC14. JSON-scheman (formellt)

### SC14.1 Store-fil (`schemaVersion: 1`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schemaVersion", "namespace", "generation", "updatedAt", "checksum", "entries", "meta"],
  "properties": {
    "schemaVersion": { "const": 1 },
    "namespace": { "type": "string", "pattern": "^[a-z0-9_-]{1,64}$" },
    "generation": { "type": "integer", "minimum": 1 },
    "updatedAt": { "type": "string", "format": "date-time" },
    "checksum": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" },
    "entries": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["key", "valueType", "value", "createdAt", "updatedAt", "hits", "sizeBytes", "version"],
        "properties": {
          "key": { "type": "string", "maxLength": 1024 },
          "valueType": { "enum": ["json", "string", "number", "boolean", "null", "base64", "file"] },
          "value": { "description": "valfri JSON; base64 => {\"base64\":\"...\"}; file => {\"path\":\"...\",\"size\":n}" },
          "createdAt": { "type": "string", "format": "date-time" },
          "updatedAt": { "type": "string", "format": "date-time" },
          "expiresAt": { "type": ["string", "null"], "format": "date-time" },
          "staleUntil": { "type": ["string", "null"], "format": "date-time" },
          "hits": { "type": "integer", "minimum": 0 },
          "sizeBytes": { "type": "integer", "minimum": 0 },
          "tags": { "type": "array", "items": { "type": "string" }, "uniqueItems": true },
          "version": { "type": "integer", "minimum": 1 }
        }
      }
    },
    "meta": {
      "type": "object",
      "required": ["totalHits", "totalMisses", "hitRate", "evictions", "expirations", "entryCount", "totalBytes"],
      "properties": {
        "totalHits": { "type": "integer" },
        "totalMisses": { "type": "integer" },
        "hitRate": { "type": "number", "minimum": 0, "maximum": 1 },
        "evictions": { "type": "integer" },
        "expirations": { "type": "integer" },
        "entryCount": { "type": "integer" },
        "totalBytes": { "type": "integer" }
      }
    }
  }
}
```

### SC14.2 Händelse (rad i `events.ndjson`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["seq", "ts", "op", "ns", "key"],
  "properties": {
    "seq": { "type": "integer", "minimum": 1 },
    "ts": { "type": "string", "format": "date-time" },
    "op": { "enum": ["set", "delete", "expire", "evict", "flush", "refresh", "stat"] },
    "ns": { "type": "string", "pattern": "^[a-z0-9_-]{1,64}$" },
    "key": { "type": "string" },
    "reason": { "enum": ["client", "ttl", "lru", "wlfu", "byte-budget", "flush", "corrupt"] },
    "ttl": { "type": ["integer", "null"] },
    "tags": { "type": "array", "items": { "type": "string" } },
    "status": { "enum": ["stale-served", "refreshed", "miss"] }
  }
}
```

## SC15. Felhantering

| Situation | Beteende |
|-----------|----------|
| Okänd namespace | Skapas implicit vid första `PUT` (eller nekas om `namespaces.allowUnknown=false`) |
| Ogiltig nyckel (t.ex. `/` i nyckel) | `400 {"error":{"code":"invalid_key"}}` |
| Värde för stort (> `maxValueBytes`) | `413 {"error":{"code":"value_too_large"}}` |
| Post finns inte | `404` (single) / `misses` (batch) |
| Store-fil korrupt | Flytta till `quarantine/`, logga `op`-rad med `reason:"corrupt"`, betjäna från minne |
| Låsfil saknas/fast | Daemon tar över med flock; fil-läge väntar max `lockTimeoutMs` (default 10 s) |
| Disk full | Skrivning misslyckas → `507`; cachen fortsätter i minne; `meta` flaggas `degraded:true` |
| `seq`-gap i loggen | Tolereras; klienter uppmanas att resynka via snapshot (`GET /v1/file/{ns}`) |

Alla fel svarar med samma envelope: `{"error": {"code": "<maskinläsbar>", "message": "<mänsklig>", "details": {...}}}`.

## SC16. Prestanda & begränsningar

- **Läsning (träff):** minnesläsning — O(1), sub-millisekund.
- **Skrivning:** O(1) i minne + O(n) serialisering av store-filen där n = antal poster i namespace. För ≤ ~250 000 poster per fil är detta < ~100 ms på modern hårdvara.
- **Skala upp:** sharding (SC6.1) → skrivning begränsas till en shard. Vid mycket höga skrivfrekvenser: `writeDebounceMs` + journal-läge (append-only deltas, snapshot skrivs vid kompakteringsintervall).
- **Stora värden:** `base64` ger ~33 % overhead; värden > `maxValueBytes` (default 10 MB) avvisas — använd `file`-typ och `blobs/` för större.
- **Filbevakning:** att läsa om hela store-JSON:n för varje uppdatering är dyrt för stora namespaces — använd händelseloggen/SSE för ändringar, snapshot för konsistent baslinje.
- **Språk/UTF-8:** nycklar och värden måste vara giltig UTF-8; ogiltiga byte-nycklar avvisas.

## SC17. Säkerhet

- Bindning till `127.0.0.1` som default — inte exponerad mot nätverket.
- Valfritt `authToken`: alla anrop kräver `Authorization: Bearer <token>` (annars `401`). Loggas aldrig.
- TLS: stöds via `listen: "0.0.0.0:7373"` + `tls: {cert, key}` (för avancerade setup).
- Validering av namespace/nyckel (teckenkrav SC5.1/SC5.4) förhindrar path-traversal (`../`, absolut sökväg) i filnamn.
- `blobs/`-filer namnges som oförutsägbara UUID — ingen direktfilservering utan daemon.
- Proxy-läget exkluderar känsliga headers från nyckeln (SC10.4).

## SC18. Deployment-lägen

| Läge | Beskrivning | När välja |
|------|-------------|-----------|
| **Daemon (rekommenderad)** | En process (`cache serve`), alla program pratar HTTP/CLI mot den; den äger skrivningarna till JSON. | Alltid som standard. Ger enkel samtidighet + live-push. |
| **Fil-läge (utan daemon)** | SDK:erna läser/skriver JSON-filerna direkt med `flock` + generation-CAS. Live via filbevakning/pollning. | Embedded/edge-fall, eller när en separat process är oönskad. |
| **Hybrid** | Fil-läge för läsning + valfri daemon när skrivningstakten kräver den. | Migration från fil-läge. |

Läget väljs i konfigurationen (`mode: "daemon" | "file" | "auto"`; `auto` = daemon om möjligt, annars fil-läge).

## SC19. Roadmap

- **v0.1 — Grund:** REST-API + CLI + JSON-store (atomisk, live) + NDJSON-logg + TTL + LRU-eviction.
- **v0.2 — Live & smartare:** SSE/WS med replay, SWR, single-flight, statistik, adaptiv TTL, sharding.
- **v0.3 — Integrationer:** officiella SDK:er (Python, Node, Go, Rust), dekorator-hjälpare, proxy-läge.
- **v0.4 — Robusthet:** journal-läge + kompaktering, `quarantine`-återställning, observability-export (Prometheus), plugin-hooks (revalidate, prefetch).
- **v0.5 — Distribuerat:** flera noder med delad JSON-lagring (NFS/objektlagring), leader-election för skrivningar, konsistent hashing för shards.

## Bilaga A — Helt exempelflöde

```bash
# 1. Starta daemon
cache serve --config config.json

# 2. Program A cachar ett API-svar
curl -X PUT localhost:7373/v1/cache/http/user:123 \
  -d '{"value":{"name":"Ada","role":"admin"},"ttl":3600,"tags":["user","stockholm"]}'
# → data/http.json skrivs om LIVE, events.ndjson får: {"op":"set","ns":"http","key":"user:123",...}

# 3. Program B hämtar (träff)
curl -i localhost:7373/v1/cache/http/user:123
# → 200, X-Cache-Status: hit

# 4. Program C bevakar i realtid
tail -f data/events.ndjson

# 5. Program D (Python) gör samma sak med SDK
from smartcache import cache
cache.watch("http", lambda ev: print(ev))   # → live-händelser

# 6. Posten uppdateras → filen + alla bevakare uppdateras direkt
curl -X PUT localhost:7373/v1/cache/http/user:123 -d '{"value":{"name":"Ada","role":"superadmin"}}'
# → data/http.json uppdaterad, alla prenumeranter får op:set med ny seq
```

**Acceptanskriterier (definition of done för v0.1):**

- [ ] `cache put/get/del/list/flush` via CLI fungerar.
- [ ] Varje mutation skriver om `data/<ns>.json` atomiskt och synkront (live).
- [ ] `events.ndjson` innehåller en rad per mutation med stigande `seq`.
- [ ] `tail -f data/<ns>.json` visar uppdateringar utan manuell refresh.
- [ ] SSE-klient får händelser live och kan replaya med `since=<seq>`.
- [ ] TTL-expiry och LRU-eviction sker automatiskt och syns i fil+logg.
- [ ] Korrupt store-fil kvitteras till `quarantine/` utan krasch.

---
---

# DEL III — GRANSKNINGSLOGG

**Startad:** 2026-08-05 · **Del av:** `SPEC.md` (enda specifikationsfilen)

## III.1 Arbetssätt (överenskommet)

1. Användaren klistrar in **en kodfil per meddelande** (MUST-020).
2. Varje inklistrad/uppdaterad fil **sparas i workspacen** under `Intygsgenerator/` med korrekt sökväg enligt mappstrukturen (Del I §4).
3. **Efter 5 sparade kodfiler** lämnas **10 förslag** som gör appen bättre och/eller snabbare, med referens till spec (MUST/FORBIDDEN/PERF-id:n).
4. Användaren väljer själv vilka förslag som ska implementeras (MUST-016: bekräftelse krävs innan godkänd fil ändras).
5. Alla leveranser: komplett fil, sökväg först, inga patchar/diffar (MUST-001/002, FORBIDDEN-004).
6. **Varje svar som innehåller en uppdaterad fil ska avslutas med klickbar(a) fil(länk(ar)) längst ner** — den uppdaterade filen öppnas/presenteras direkt i chatten (MUST-027).

### Överenskommen regel (2026-08-05)

- **Filnamn och sökväg ska alltid finnas i kodhuvudet** för varje levererad fil (kommentar/docstring), utöver MUST-002 (sökväg först i svaret). Numera kodifierad som **MUST-026** i Del I §2.1.

### Överenskommen regel (2026-09-18, utökad samma dag)

- **MUST-027 (utökad):** **Varje chatt/svar avslutas längst ner med filnamn + fullständig sökväg (plats) SKRIVET i text OCH en klickbar filpresentation.** Gäller även svar utan kodändring — då länkas den senast uppdaterade/relevanta filen. Ingen chatt får sluta utan detta.

## III.2 Mottagna kodfiler (runda 1)

| # | Sökväg | Mottagen | Rader | Notering |
|---|--------|----------|-------|----------|
| 1 | `main.pyw` | 2026-08-05 | 1719 | Sparad; `py_compile` OK. Markdown-arty rekonstruerade till avsedda identifierare — ingen logik ändrad. |
| 2 | `plugins/__init__.py` | 2026-08-05 | 1376 | Sparad; `py_compile` OK. Ny regel införd: filnamn + sökväg alltid i kodhuvudet. |
| 3 | `plugins/template_designer/plugin.py` | 2026-08-05 | 1952 | Sparad; `py_compile` OK. Sökväg tillagd i docstring-huvud. |
| 4 | `plugins/template_designer/core.py` | 2026-08-05 | 1313 | Sparad; `py_compile` OK + funktionell smoketest körd (III.3). |
| 5 | `plugins/template_designer/__init__.py` | 2026-08-05 | 780 | Sparad; `py_compile` OK + fail-safe-smoketest körd (III.3). **5/5 — förslagsrunda levererad (III.5).** |
| 6 | `plugins/system_tray/__init__.py` | 2026-08-05 | 621 | Sparad; `py_compile` OK + fail-safe-smoketest körd (III.3). Första filen efter förslagsrundan. |
| 7 | `plugins/system_tray/models.py` | 2026-08-05 | 1202 | Sparad med **två nödvändiga fixar** (se III.3 fil 7); `py_compile` OK + full smoketest grön. |
| 8 | `plugins/system_tray/plugin.py` | 2026-08-05 | 1215 | Sparad; `py_compile` OK. **system_tray-paketet är nu komplett** (3/3 filer). OBS: potentiell event-loop-risk upptäckt (III.4 fil 8). |
| 9 | `plugins/statistics/plugin.py` | 2026-08-05 | 1017 | Sparad; `py_compile` OK. Paketet avvaktar `__init__.py` + `engine.py` i WS. |
| 10 | `plugins/statistics/engine.py` | 2026-08-05 | 1943 | Sparad med **två nödvändiga fixar** (trunkerad `__all__`, `_scan_all_files` root-bugg); `py_compile` OK + 7 smoketestgrupper gröna. **10/10 — förslagsrunda 2 levererad (III.6).** |
| 11 | `plugins/statistics/__init__.py` | 2026-08-05 | 1064 | Sparad; `py_compile` OK + 6 smoketestgrupper gröna. **Statistikpaketet komplett (3/3).** |
| 12 | `plugins/settings/workers.py` | 2026-08-05 | 257 | Sparad; `py_compile` OK + 5 smoketestgrupper gröna (PySide6-stub i sandlåda). Zip Slip-skydd bekräftat fungerande. |
| 13 | `plugins/settings/view.py` | 2026-08-05 | 409 | Sparad; `py_compile` OK + 6 smoketestgrupper gröna (PySide6-stub). |
| 14 | `plugins/settings/plugin.py` | 2026-08-05 | 1362 | Sparad **med förbättringar enligt beställning** (se III.3 fil 14); `py_compile` OK + AST-kontroll grön (inga odefinierade metoder, inga artefakter). |
| 15 | `plugins/settings/core.py` | 2026-08-05 | 1545 | Sparad **med förbättringar enligt beställning** (tysta exceptions → loggade); `py_compile` OK + 10 smoketestgrupper gröna utan PySide6. |
| 16 | `plugins/settings/__init__.py` | 2026-08-05 | 282 | Sparad; `py_compile` OK + 6 smoketestgrupper gröna. **Settings-paketet komplett (5/5)** — degraded mode verifierat. |
| 17 | `plugins/certificates/view.py` | 2026-08-05 | 376→426 | Sparad med en förbättring (tooltip-återställning i `FieldWidget.clear`); `py_compile` OK + AST-kontroll grön. **Uppgraderad till 10.2.0 (order 2026-08-26): H10 implementerad, 6/6 verifierade headless.** |
| 18 | `plugins/certificates/services.py` | 2026-08-05 | 833→970 | Sparad; `py_compile` OK + 5 smoketestgrupper gröna (engine stubbad). **Uppgraderad till 10.2.0 (order 2026-08-26): H1/H5/H6/H7/H8 implementerade, 7/7 verifierade.** |
| 19 | `core/__init__.py` | 2026-08-05 | 1310 | Sparad med en förbättring (`PlatformHelper.open_file` icke-blockerande, §18); `py_compile` OK + 7 smoketestgrupper gröna. **Ryggraden på plats — main.pyw:s importer löser nu.** |

**Status:** 19 kodfiler mottagna. Packages: statistics 3/3 ✓, system_tray 3/3 ✓, settings 5/5 ✓, certificates 2/5, core 1/4 (`__init__` ✓; kvar: `config.py`, `infrastructure.py`, `themes.py`). OBS: `CONFIG_AVAILABLE`/`INFRASTRUCTURE_AVAILABLE` är tills vidare `False` (fallback aktiv) tills `core/config.py` + `core/infrastructure.py` levereras.

**Nästa rekommenderade fil:** `core/config.py` — gör `CONFIG_AVAILABLE=True` (riktig ConfigManager i stället för fallback) och är målet för **B4** (schema-validering) + **B5** (fsync). Därefter `core/infrastructure.py` (B5) och `plugins/certificates/engine.py` (krävs av services.py; B2/B3).

## III.3 Smoketest-resultat (körda i sandlåda 2026-08-05)

### Fil 4 — `template_designer/core.py`

- ✓ Mallskanning mot simulerad §5.4-struktur: exakt `['35kW', 'Demontering', 'Passagerare']` (25kW med bara PDF och lös rot-DOCX ignoreras)
- ✓ Splitade runs (`::V` + `IN` + `::`) detekteras som `VIN` via raw-XML-fallback
- ✓ `Datum` exkluderas korrekt som AUTO-fält från manuella fält, men inkluderas i `find_all_including_auto`
- ✓ VIN/REG-validering (inkl. nyare format ABC12D) fungerar
- ✓ `FOLDER_CONFIG = "js"` (BUG-007)

### Fil 5 — `template_designer/__init__.py`

- ✓ **Fail-safe verifierad:** utan PySide6 laddas `core` normalt, `plugin` fallbackar med loggad varning — ingen krasch (Del I §21)
- ✓ `get_load_status()` rapporterar korrekt `core_available=True / plugin_available=False`
- ✓ `validate_template_name`: reserverade namn (CON), ogiltiga tecken och korta namn nekas
- ✓ `list_available_templates`: hittar bara undermappar med DOCX
- ⚠ **Bugg bekräftad:** `check_dependencies` jämför versionssträngar lexikografiskt → `"10.0.0" >= "5.0.0"` blir `False` (se förslag F2)

### Fil 6 — `system_tray/__init__.py`

- ✓ **Dubbel fail-safe verifierad:** utan `models.py`/`plugin.py` i workspacen aktiveras båda fallback-vägarna med loggade `ModuleNotFoundError` — paketet importerar ändå (Del I §21)
- ✓ Fallback-`Plugin` uppfyller kontraktet: `name="System Tray"`, `priority=5` (TRAY-nivå, §9.2), `get_tab() → None`, `get_statistics()` med `fallback=True`
- ✓ Fallback-modeller fungerar: `Platform.current()=LINUX`, `create_notification`/`create_recent_certificate` + `to_dict()`
- ✓ `get_plugin_info()`: `has_tab=False`, `is_service=True`, `fully_loaded=False` med `load_errors`
- ✓ Skapar ingen `QSystemTrayIcon` någonstans — ensam ägare förblir `plugin.py` (§15, BUG-001)

### Fil 7 — `system_tray/models.py`

- ⚠ **Krasch upptäckt och fixad (Fix-1):** `_cached_current: Platform | None = None` i Enum-kroppen skapade ett enum-**medlem** med värdet `None` → `TypeError` redan vid klasskapning, på såväl Python 3.12 som 3.13. `models.py` har alltså aldrig kunnat importeras — paketets fail-safe har i tysthet kört fallback-läget. Åtgärd: annotering utan tilldelning + `getattr`/lat tilldelning i `current()`. Cachen (beräknas en gång) är bevarad. **Verifiera lokalt att models nu laddas skarpt** (`is_models_loaded()` ska bli `True`).
- ⚠ **Typfel upptäckt och fixat (Fix-2, användarens rapport "Module cannot be used as a type"):** `from PIL import Image as _PILImage` importerade **modulen** `PIL.Image` och använde den som typ. Åtgärd: `from PIL.Image import Image as _PILImage` (klassen).
- ✓ Enum-kontrakt: exakt 4 `Platform`-medlemmar; `from_string` på `IconState`/`NotificationType`/`CloseAction` med säkra default-värden
- ✓ `Notification` konverterar strängtyp och ISO-timestamp; `to_dict`/`from_dict` roundtrip OK
- ✓ `RecentCertificate`: `display_name`, `short_name`, `age_text`, `created_at`-alias, dict-roundtrip
- ✓ `TrayConfigHelper` mot dummy-config: properties med setters, `tab_visibility`, `add_recent_certificate` med dedup på path och kapning vid `MAX_RECENT_CERTIFICATES=10` (senaste först)
- ✓ `IconGenerator`: PIL-vägen fungerar (sandlådan har Pillow → riktig `Image` + 8 animationsramar); utan PySide6 returnerar `create_qicon` säkert `None` efter loggad varning
- ✓ Paketnivå: `is_models_loaded()=True`, `is_plugin_loaded()=False` (plugin.py ej levererad ännu)

### Fil 8 — `system_tray/plugin.py`

- ✓ `py_compile` OK (syntax). Körbar verifiering kräver PySide6 — i sandlådan fallbackar paketet korrekt på `PySide6 ModuleNotFoundError`; **kör lokalt** och bekräfta `is_plugin_loaded()=True`.
- ✓ TRAY-krav uppfyllda i kod: global `_GLOBAL_TRAY_ICON` + `_GLOBAL_TRAY_LOCK` garanterar **exakt en** `QSystemTrayIcon` (TRAY-001, FORBIDDEN-013/020); avsluta enbart via meny (`_on_quit`); enkelklick/dubbelklick/mellanklick styrs av `single_click_open` (TRAY-004); QPainter-fallback utan Pillow (TRAY-008/009, COLORS-001-undantag)
- ✓ Ikonordning följer §29: fil från `Icon/` → PIL-generator → QPainter-fallback
- ✓ Notification debouncing (500 ms, kö max 3, "senaste vinner") + lazy animationsikoner
- ✓ `priority=5` (TRAY-nivå §9.2); `get_tab()` bygger `_SettingsTab` lazy; EventBus-prenumeration matchar §10-kartan
- ⚠ **OBS-1 (se III.4 fil 8):** potentiell event-ping-pong `window.show`/`window.hide` ↔ tray-pluginets egna handlers — kräver beslut.

### Fil 9 — `statistics/plugin.py`

- ✓ `py_compile` OK; exakt en `Plugin`-klass; `priority=60` (STATISTICS, §9.2/§12)
- ✓ Trådmodell enligt PERF-007: import i `QRunnable` + `QThreadPool`, signaler med `QueuedConnection`, all UI-uppdatering via `QTimer.singleShot(0, …)` i huvudtråden
- ✓ Sökning med 200 ms debounce; initial output-skanning fördröjd 500 ms (PERF-001-vänlig startup)
- ✓ Atomär lagring av `js/statistics.json` (tmp → replace, §21); configmapp `js` (BUG-007)
- ✓ Dedup på `file_path or filename` vid `_add_record`/`_merge_import_result`; räknarsynk **endast uppåt** (§8)
- ✓ `_open_path` är redan icke-blockerande (`Popen` + `start_new_session`) — mönstret F7 efterlyser på andra ställen finns alltså här
- ✓ EventBus-karta mot §10: `certificate.generated`, `statistics.request/response`, `statistics.imported`, `statistics.updated`, `refresh_templates`, `templates.scanned`

### Fil 10 — `statistics/engine.py`

- ⚠ **Fix-3 (kritisk, bevisad i smoketest):** `_scan_all_files` beräknade `root` ur **första filens förälder** → `relative_to` kraschade för alla filer i andra undermappar. En full skanning av normalstrukturen `Out/<MallNamn>/` med flera mallar förlorade 2 av 4 filer i testet. Åtgärd: `root` skickas in från `import_incremental`.
- ⚠ **Fix-4 (syntax, användarens rapporterade fel):** inklistringen var trunkerad — `__all__`-listan var aldrig stängd ("`[` was not closed" / "Statements must be separated by newlines or semicolons"). Listen avslutades korrekt; `typing.Optional` togs bort (FORBIDDEN-026, oanvänd).
- ✓ Smoketest 1–7 gröna: `scan_directory` (4/4, VIN/REG ur filnamn, counters uppåt), CSV-import med semikolon + alias ("35 kilowatt" → 35kW), summary/trend, sökning (VIN/REG/id), **inkrementell cache** (full=4 → åter=0 nya → +1 efter ny fil, cache på disk), rapporter i 5 format + atomär filskrivning, mallskanning §5.4 (exakt 3 mallar) med aktiv DOCX enligt §5.3 via `scan_templates_detailed`
- ✓ Trådsäker motor (`RLock`), `slots`-dataklasser, atomära skrivningar (tempfil → replace)
- ✓ `_count_pdf_pages`: `pypdf` primär, `PyPDF2` fallback (§7)

### Fil 11 — `statistics/__init__.py`

- ✓ `py_compile` OK; **statistikpaketet komplett (3/3)**: `__init__.py` ✓ · `engine.py` ✓ · `plugin.py` ✓
- ✓ Fail-safe verifierad i sandlådan: utan PySide6 laddas `engine` skarpt, `plugin` fallbackar med loggat fel och en degraded `Plugin` (prioritet 60, `get_tab() → None`, `error.occurred`-event vid `on_load`)
- ✓ Efter lyckad engine-import ersätts fallback-klasserna av engine-versionerna (`StatisticsEngine.__module__ == "plugins.statistics.engine"`)
- ✓ `TemplateScanner` mot utökad §5.4-struktur: exakt `["35kW", "Demontering", "Passagerare"]`; ignorerar PDF-only-mapp, tom DOCX, dold mapp, `~$`-tempfil och lös rot-DOCX
- ✓ **§5.3 aktiv DOCX implementerad korrekt:** `Demontering.docx` prioriteras över `annan.docx`; fallback till enda giltiga DOCX annars
- ✓ **Placeholder-räkning hanterar splitade runs** (sammanfogar `<w:t>` per `<w:p>`) och räknar även headers (`word/header1.xml`) — 3 hittade i 35kW-testet (VIN, Reg, Datum)
- ✓ DOCX-stämpelcache (sökväg + `mtime_ns` + storlek + folder-stämpel): andra skanningen ~4× snabbare (1.8 → 0.4 ms i testet), cap 512 entries (§5.5)
- ✓ `scan_template_names` dirigerar via engine med fallback till TemplateScanner; `get_template` casefold-tolerant; `get_statistics()` korrekt (5 placeholders, 1 config)

### Fil 12 — `settings/workers.py`

- ✓ `py_compile` OK; körbar verifiering med PySide6-stub i sandlådan (5 grupper)
- ✓ **Zip Slip-skyddet bekräftat fungerande (§21, releasekrav):** `../evil.txt`, `config/../../evil2.txt`, absoluta sökvägar och otillåtna rötter (`Out/…`, `_metadata.json`) blockeras; endast `config/`, `Mallar/`, `profiles/` återställs
- ✓ Backup atomär: skrivs till `.zip.tmp` → `replace`; ingen tempfil kvar efteråt; `_metadata.json` med version/copyright/flaggor
- ✓ `_iter_files` exkluderar dolda filer (`.hidden.json`) och Word-temp (`~$*.docx`), dedup via `resolve()`
- ✓ Storleksgränser: loggar ≤ 50 MB vid backup, enskild restore-fil ≤ 500 MB
- ✓ Felhantering med `log.exception` (FORBIDDEN-011 respekterad); `finished(False, …)` vid saknad fil och zip utan återställningsbara medlemmar
- ✓ `BackupOptions` fryst dataclass (slots), `WorkerSignals` med progress/finished
- ✓ Trådmodell: all fil-I/O i `QRunnable.run` (PERF-007); `setAutoDelete(True)`

### Fil 13 — `settings/view.py`

- ✓ `py_compile` OK; verifierad med PySide6-stub (6 grupper): publikt API (9 namn), `SettingsShell` (signaler, `add_scroll_tab`/`show_tab`/status/restart-flagga), `SettingsScrollTab` (sektioner/stretch), `ShortcutEditDialog` (`shortcut`/`key_sequence`), `ProfileDialog`, kontrollfabriken i alla fyra grenar (bool/choice/int/text) + `make_separator`/`make_label_row`/`make_button_row`/`make_action_button`
- ✓ Ansvarsgräns enligt MUST-012: ingen fil-I/O och ingen inställningslogik i filen — ägs av `plugin.py`/`workers.py`
- ✓ Mushjulshantering i `SettingsScrollTab.eventFilter` med vakt (`bar.maximum() > bar.minimum()`) och `super().eventFilter`-genomfall — uppfyller §17/§30 (scroll utan dubbel zoom)
- ✓ `SettingsShell.search_changed` kopplas signal-till-signal direkt från `QLineEdit.textChanged`
- ✓ Kontrollfabriken normaliserar värden säkert (`bool(state)`, choice via `itemData`, int med fallback till `default` vid icke-siffra)
- ✓ Metadata: v8.7.0 + dynamisk copyright (§3)

### Fil 14 — `settings/plugin.py`

- ✓ `py_compile` OK (1 362 rader, controller under ~1 800-riktlinjen); AST-kontroll: alla `self.*`-anrop har definitioner, inga markdown-artefakter kvar
- ✓ Förbättrad enligt beställning "**gör all kod bättre utan att förlora något**" — följande ändringar tillämpade (ingen funktionalitet borttagen):
  - Oanvända importer borttagna: `os`, `webbrowser`, `QFont`, `QFrame` (renare beroendeyta)
  - `import zipfile` flyttad till modulnivå (användes inline i `_backup_version`)
  - `_log`: lokal variabel omdöpt `logger` → `active_logger` (undviker sammanblandning med modulens `_logger`)
  - `_cleanup_old_backups`: tyst `suppress(OSError)` ersatt med loggad felhantering (FORBIDDEN-011)
  - `get_backups`: robust mot filer som försvinner mellan glob/stat (sortering på förinsamlad `(mtime, path)`-lista)
- ✓ Spec-efterlevnad i övrigt: profiler skapas aldrig vid startup (§4/§13, `_setup_paths` skapar enbart sökvägsreferensen), `_clear_logs` bevarar `crash.log` och rör inte `logs/archive/` (LOG-021), `_clear_cache` rensar endast `js/cache` (teknisk cache, §26/B1-anda), backup/restore via workers med `QueuedConnection` (PERF-007), exakt tre teman (§13), `app.quit_confirmed` via EventBus (TRAY-006), restart-flagga för `requires_restart`-inställningar
- ✓ 12 flikar enligt §13 (`TAB_DEFINITIONS` 0–11); sök-filtrering av kontroller; export/import av JSON-inställningar atomärt (tmp → replace)
- ✓ AST-verifierad importlista från `settings/core.py` (se III.2 "Nästa rekommenderade fil")

### Fil 15 — `settings/core.py`

- ✓ `py_compile` OK; **10/10 smoketestgrupper gröna utan PySide6** (filen är PySide6-valfri): PlatformHelper · Theme (exakt 3 teman §13) · PALETTES ("text" finns i båda teman, BUG-015 + alla §13-nycklar) · ThemeManager (apply/observers/detect-cache) · SettingDefinition-validering · Profile-roundtrip · HealthCheck · ShortcutManager (12 genvägar, konfliktdetektering, reset, reload) · AutostartManager via `.desktop` (§19) · exporter/metadata
- ✓ Förbättrad enligt beställning "**gör all kod bättre utan att förlora något**": alla tysta `except Exception: pass` i delegerings-/fallback-vägar (PlatformHelper→core, AutostartManager→core.config, ShortcutManager load/save, `_apply_qpalette`, tema-detektering) ersatta med `_logger.debug/exception` — FORBIDDEN-011 utan beteendeändring
- ✓ `AutostartManager` uppfyller hela §19/§13-API:t: `is_enabled/enable/disable/set_enabled/toggle/get_command/sync/sync_with_system`; Windows registry HKCU, macOS LaunchAgent-plist, Linux XDG `.desktop`; delegerar till `core.config.AutostartManager` när den finns; `pythonw` på Windows (§19)
- ✓ `ThemeManager`: QPalette direkt (ingen stylesheet-omparse, §13), Active/Disabled-grupper, cachad systemdetektering med TTL 30 s (§13), trådsäkra observers (`RLock`)
- ✓ `ShortcutManager`: konfliktdetektering mot andra genvägar, persistens av enbart modifierade genvägar, plattformsmodifierare (⌘/Ctrl)
- ✓ Docstring-rättning: "SEK 9" → "§13" (Theme-kommentaren)

### Fil 16 — `settings/__init__.py`

- ✓ `py_compile` OK; **6/6 smoketestgrupper gröna** — degraded mode verifierat på riktigt (PySide6 saknas i sandlådan): `core_loaded=True`, `plugin_loaded=False`, `fully_loaded=False`, `load_errors` innehåller enbart `plugin`
- ✓ COPYRIGHT tas från `core.py` (dynamisk §3) när core laddas
- ✓ Fallback-`Plugin` uppfyller IPlugin-kontraktet (name/prio 90/version) och **emitterar `error.occurred`** med `source=settings` vid `on_load` — synligt degraded mode (§21)
- ✓ `get_tab()` utan PySide6 → `None` utan krasch; `on_unload`/`on_close` idempotenta
- ✓ `get_plugin_info()` med aktuell importstatus + event-karta mot §10 (`settings.changed/theme_changed/profile_loaded`, `app.quit_confirmed`, `show_settings`, `refresh_ui`, `plugin.loaded/unloaded`)
- ✓ `__all__`: 14 basnamn + 13 core-exporter (betingad av `CORE_LOADED`), alla upplösta

### Fil 17 — `certificates/view.py`

- ✓ `py_compile` OK (376 rader, väl under ~1 200 för helperfiler); AST-kontroll: alla 5 publika klasser (`FieldsScrollArea`, `ProgressDialog`, `FieldWidget`, `PlaceholderPreviewWidget`, `EngineErrorWidget`) + `_validation_state`, inga artefakter
- ✓ Förbättring enligt beställning: `FieldWidget.clear()` återställer nu konstruktions-tooltipen (`_base_tooltip`) i stället för att tömma den — hjälptexten (§17 tillgänglighet) behålls efter formulärrensning; ingen befintlig funktion borttagen
- ✓ Arkitektur enligt §11/MUST-012: UI frikopplat från `engine.py` — validering och auto-fält kommer in som callbacks (`ValidationCallable`, `AutoFieldCallable`) från controllern
- ✓ §17/§30: mushjulsscroll med vakt + genomfall till `super().eventFilter` (ingen dubbel zoom); validering först vid `editingFinished` (aldrig per tangent — PERF-vänligt); `setAccessibleName` på fält; word wrap i felpaneler
- ✓ Degraded mode-stöd: `_validation_state` normaliserar både engine-resultat och enum-baserade resultat (`result.name == "WARNING"`)
- ✓ `EngineErrorWidget` förklarar fallback-läge synligt (§21) med åtgärdshints (engine.py, python-docx, pypdf, LibreOffice)
- Körbar widget-test kräver PySide6 — körs i lokal miljö (se §22)

### Fil 18 — `certificates/services.py`

- ✓ `py_compile` OK (833 rader); **5/5 smoketestgrupper gröna** med stubbad engine: CounterManager (atomär JSON-persistens, increment/set, bool-vakt, reload, korrupt-fallback) · PDFConverter (NONE utan konverterare, säkra felvägar) · PDFMerger (tom input utan krasch) · CertificateGenerator (auto-fält §6, filnamnsformat §8, ÅÄÖ, unika sökvägar) · `generate()` felvägar utan undantag
- ✓ §7 exakt uppfyllt: timeout 120/30/600 s, 3 försök med exponentiell backoff ×2.0, konverterarkedja LibreOffice → docx2pdf → Word COM (endast Windows) → unoconv → AbiWord, detektering TTL-cachad 300 s
- ✓ `pypdf` primär / `PyPDF2` fallback; sammanslagning atomär (tempfil → replace) (§7/§21)
- ✓ `CounterManager`: ConfigManager först, atomisk JSON-fallback i `js/counters.json` (§8); värden ≥ 0, bool nekas; reload från disk verifierad
- ✓ Filnamn enligt §8: `{number} Intyg {template} {vin} {reg} {date}` (default), NFC via `safe_filename`, kollisionshantering `_unique_path`
- ✓ Auto-fält §6 med case-varianter (lower/capitalize/upper); användarvärden går före automatiska
- ✓ Word COM: `CoInitialize/CoUninitialize` + `DispatchEx`, städning i finally; docx2pdf/Word i daemon-trådar med timeout-join
- ✓ LibreOffice körs med engångsprofil (`-env:UserInstallation` i tempdir) — undviker profil-lås mellan parallella jobb
- ✓ Ansvarsgräns §11: enbart I/O-tunga tjänster; modeller/validering i engine.py, UI i view.py, körning från QRunnable i plugin.py

### Fil 19 — `core/__init__.py`

- ✓ `py_compile` OK; **7/7 smoketestgrupper gröna** (helt utan PySide6): alla namn som main.pyw/plugins importerar · EventBus (exact + wildcard `test.*`, felisolerad handler, unsubscribe, shutdown) · fallback-ConfigManager (dotted keys, observers, atomär persistens, ensure_defaults, deep merge, korrupt-fallback) · fallback-TrayConfig roundtrip · AppContext (get_path skapar mappar, register_path, emit/subscribe, toggle_window, show_notification) · PluginManager (discovery, prioritetsordning 10→90, casefold-sökning, `plugin.loaded/unloaded/ready`-event, omvänd shutdown) · TabManager (hide/show bevarar ordning, toggle)
- ✓ Förbättring enligt §18 tillämpad: `PlatformHelper.open_file` använder nu icke-blockerande `Popen(start_new_session, DEVNULL)` på macOS/Linux (tidigare blockerande `subprocess.run`); Windows `os.startfile` oförändrat
- ✓ Fail-safe (§21): fallback-implementationer för ConfigManager/SettingsManager/AutostartManager/IconManager/WindowManager/TrayConfig när `core/config.py` inte kan laddas — verifierade i test
- ✓ `main.pyw`-kompatibilitet bekräftad: alla namn finns (`APP_NAME`, `APP_DISPLAY_NAME`, `VERSION`, `SESSION_ID` (12 tecken), alla `FOLDER_*`, `AppContext`, `ConfigManager`, `PluginManager`)
- ✓ EventBus varnar för handlers > 50 ms (PERF-009-detektering); plugin-laddning loggar tid per plugin
- ⚠ Testsituation noterad: PluginManager-testet krävde top-nivå `plugins/__init__.py` i testets fejkade rot (saknades den vann det riktiga paketet i sys.path) — i skarp drift är appens egen `plugins/__init__.py` roten, så beteendet är korrekt

### Fil 18b — `certificates/services.py` v10.2.0 (uppgradering 2026-08-26)

- ✓ `py_compile` OK (970 rader); **7/7 smoketestgrupper gröna** (engine stubbad): CounterManager oförändrad · PDFConverter NONE + §7-konstanter + H1-glob · H5 låsprobe · PDFMerger kompatibilitet + MergeReport · Generator auto-fält/filnamn/felvägar · H6 progress-event · H8 unika sökvägar
- ✓ **H1:** LibreOffice hittas via glob (`LibreOffice*/program/soffice.exe` under Program Files-varianter, `/opt/libreoffice*/…`) — ingen fast versionslista; verifierad mot fejkad "LibreOffice 99"
- ✓ **H5:** retry-sömnen sker utanför konverterarlåset — verifierat med låsprobe från separat tråd (låset fritt under båda sömnarna)
- ✓ **H6:** valfri `event_emitter` i `CertificateGenerator`; `certificate.progress` med `{template, percent, message}` vid 10/30/60/82/100; callback-API intakt (ny konstruktorparameter är valfri)
- ✓ **H7:** `MergeReport(success, pages, skipped, warnings)`; `merge()` behåller `(bool, int)`-signaturen, `merge_detailed()` ger full rapport; icke-PDF/saknade/oläsbara/krypterade filer rapporteras i `skipped`; pypdf→PyPDF2-fallback; dubbla implementationer förenade till en
- ✓ **H8:** `UNIQUE_PATH_MAX=1000` med fellogg + timestamp-fallback i stället för obegränsad loop
- ✓ Tidig målvalidering: målmapp skapas före första konverteringsförsöket
- ⏳ `GenerationResult.skipped_attachments` sätts defensivt (suppress) — full synlighet i UI väntar på `engine.py`/`plugin.py`

### Fil 17b — `certificates/view.py` v10.2.0 (uppgradering 2026-08-26)

- ✓ `py_compile` OK (426 rader); **6/6 smoketestgrupper gröna headless** (PySide6-stubbar): dpi_scale · FieldWidget-validering · PlaceholderPreview · ProgressDialog · FieldsScrollArea · EngineErrorWidget
- ✓ **H10:** `dpi_scale()` (logisk DPI / 96, alltid ≥ 1.0, headless-säker fallback) skalar etikettbredder (150–230 px), fälthöjd (32 px), dialogbredd (440 px), statusetikett (30 px), förhandsvisningshöjder (100/180 px) och felpanelens marginaler — text klippas inte vid hög DPI (§30)
- ✓ Valideringsbeteende bevarat och verifierat: ✅ giltigt / ⚠️ varning+exception / ❌ ogiltigt; formaterat värde skrivs tillbaka med blockerade signaler + `value_changed(field, formatted)`; tomt fält rensar status
- ✓ `clear()` återställer bas-tooltipen (förbättring från 10.1.0)
- ✓ Tillgänglighet: `accessibleName` på fält, `accessibleDescription` på statusetikett
- ✓ PlaceholderPreview: monospace för stabil läsning; 🤖 auto / ✅ värde / (tomt); tom lista → "Inga placeholders att visa."
- ✓ ProgressDialog: cancel-signal + clamp 0–100 bevarade
- ⏳ settings/view.py har kvar fasta mått — H10 slutförs där vid nästa uppgradering av den filen

## III.4 Noteringar per fil (underlag för förslagsrundan)

### Fil 1: main.pyw

- `_show_already_running_message` skapar `QApplication` utan `exec()` — fungerar för modal dialog men värt att se över.
- `open_folder` använder `start_new_session=True` även på Windows (mappas till `CREATE_NEW_PROCESS_GROUP`).
- `Application._startup_warnings` samlas men visas inte för användaren.
- Statusradens `len(self._app.plugin_manager)` förutsätter `__len__` på PluginManager (verifiera i `core/__init__.py`).

### Fil 2: plugins/__init__.py

- `typing.FrozenSet` och oanvända `Iterator` — kan moderniseras till `frozenset[str]` (§2.3 / MUST-025).
- `PluginPriority.LOGGING = 2` men §9.2 säger att Logger-pluginet använder prioritet `1` i runtime.
- `PluginPriority.get_name` har bucket-logik (`<= 30 → "SETTINGS"`) som inte stämmer med konstanterna (SETTINGS=90).
- `WindowManager` här + `WindowManager` i `core/config.py` (§27) + geometrilogik i `main.pyw` = potentiellt dubblerat system (FORBIDDEN-016).
- `TabManager` här vs. flikhanteringen i `main.pyw` — bestämma vilken som är normativ.
- `get_plugin_info_list()`/`discover_and_validate()` importerar alla pluginmoduler synkront — tungt om det anropas från UI (PERF-007/008).
- `OSHelper.open_file` använder blockande `subprocess.run` (§18 vill ha icke-blockerande öppning).
- Headerkommentaren har statiskt copyrightår trots BUG-019.

### Fil 3: template_designer/plugin.py

- Filen är 1 952 rader — över riktlinjen ~1 800 för controllerfiler (§4). Dialogklasserna är naturliga att bryta ut.
- Mjuk import `except Exception: pass` och `with suppress(Exception)` utan loggning (FORBIDDEN-011).
- Egen lokal `OSPlatform` duplicerar `plugins/__init__.py` (FORBIDDEN-016-kandidat).
- `OSPlatform.open_path` använder blockande `subprocess.run`.
- `QTimer.singleShot(500, ...)` för att välja nyss skapad mall efter skanning — timingberoende.
- Prioritet 50 krockar med GENERATOR/NORMAL (50).
- Docstring har statiskt copyrightår.
- `DesignerStatusBar` styrs av stylesheet-färger (COLORS-001-kommentar finns) men kan göras tema-robust via QPalette.

### Fil 4: template_designer/core.py

- `redo()` poppar från redo-stacken men applicerar aldrig åtgärden — redo är i praktiken verkningslös (buggkandidat).
- `_apply_undo` vid RENAME anropar `rename_template`, som i sin tur pushar en ny UndoAction → undo-stacken förorenas vid ångra.
- `import_template` använder rå `zf.extractall` utan Zip Slip-skydd (§21-anda).
- `_scan_folder` väljer `docx_files[0]` utan §5.3:s aktiva DOCX-regel.
- Placeholder-sökning täcker body + tabeller men ej headers/footers/first-page/even-page (§6).
- `TemplateConfig.save`, `_find_raw_xml`, `_raw_text`, `_file_hash`, `_atomic_write_json` returnerar tyst vid fel (FORBIDDEN-011).
- `_atomic_write_json` definieras men används aldrig (dubblering, FORBIDDEN-016-anda).
- Stub-klasserna (`ToolTip`, `ScrollableFrame` m.fl.) — FORBIDDEN-006-anda; behöver uttryckligt beslut.
- MD5 för fil-hash (SHA-256 är modernare standard).

### Fil 5: template_designer/__init__.py

- Version 4.2.0 mot core/plugin 4.2.1 — metadata-pass krävs (jfr §33).
- `check_dependencies` lexikografiska versionsjämförelse (bevisad bugg, se F2).
- Fallback-`AUTO_FIELDS` (versaler) skiljer sig från core.py:s gemena mängd.

### Fil 6: system_tray/__init__.py

- `PLUGIN_VERSION` tilldelas `__version__` (8.4.1) **efter** try/except-blocket — om `models.py` definierar ett avvikande `PLUGIN_VERSION` skrivs det över tyst. Verifieras när `models.py` levereras (metadataharmonisering, jfr §33).
- `create_notification` har en `TypeError`-fallback till `notification_type=` som kwarg — antyder att `models.py`-varianten kan ha annan konstruktorssignatur. Verifieras mot `models.py`.
- Positivt mönster värt att återanvända: alla fallback-vägar loggar med `_LOGGER.exception` (FORBIDDEN-011 respekterad) — kontrasterar mot template_designers tysta `except Exception: pass` (se F8).
- Fallback-`Notification.type` skuggar inbyggda `type` internt i dataclassen — fungerar, men namnvalet är värt att notera vid eventuell modernisering.

### Fil 7: system_tray/models.py

- **Versionsdrift bekräftad:** `models.py` anger `PLUGIN_VERSION = "9.0.0"` men `__init__.py` (8.4.1) skriver över den till 8.4.1 — paketet rapporterar alltså 8.4.1 trots att modellerna är 9.0.0. Kräver metadataharmonisering enligt §33 (med bekräftelse, MUST-016).
- **Fallback-avvikelser mot models.py** (fallback i `__init__.py` bör synkas för identiskt beteende i degraded mode): `ICON_SIZES` tuple vs. dict; `DEFAULT_HOTKEY` "Ctrl+Alt+I" vs. "ctrl+shift+i"; `ANIMATION_SPEED_MS` 180 vs. 120; `TRAY_CLICK_THRESHOLD_MS` 450 vs. 300; fönstermått 1200×800/900×650/0.82 vs. 1100×750/400×300/0.8; `TabInfo` har `label` i fallback men `plugin_name`/`order`/`frame` i models; `Notification` saknar `callback`/`shown` i fallback.
- `TrayConfigHelper.get_recent_certificates` har `except Exception: pass` — FORBIDDEN-011-kandidat (samma mönster som F8).
- `_set_raw` anropar `saver()` synkront vid varje tray-inställning — utan debounce; vid täta skrivningar kan detta bli hårt mot JSON-I/O (PERF-010-principen, jfr ConfigManager debounce 500 ms).

### Fil 8: system_tray/plugin.py

- **OBS-1 — potentiell event-ping-pong (beslut krävs).** Tray-pluginet prenumererar på `window.show`/`window.hide` och dess `_on_show_window`/`_on_hide_window` **återemitterar samma event** efter utförd åtgärd: `window.hide` → `_on_tray_minimize_event` → hide-signal → `_on_hide_window` → `emit("window.hide")` → samma kedja igen. Beroende på EventBus semantik (synkron re-entry) kan detta bli en oändlig loop — med upprepad "Körs i bakgrunden"-notis varje varv. Förslag till åtgärd: återemittera inte event som pluginet själv konsumerar (eller bara när tillståndet faktiskt ändrats), alternativt avprenumerera `window.show`/`window.hide` och agera enbart på `tray.*`-eventen.
- `_subscribe_events` har `except Exception: pass` och `_on_toggle_window` fångar alla fel och visar fönstret utan loggning — FORBIDDEN-011-kandidater (jfr F8).
- `_open_path` använder blockerande `subprocess.run` — samma mönster som F7; ytterligare en plats att göra icke-blockerande.
- Oanvända importer: `sys`, `datetime`, `CloseAction` — kosmetisk rensning möjlig.
- `CloseAction`-modellen används inte alls av pluginet (stängningsbeteendet läser `config.close_to_tray` direkt) — överväg om `CloseAction` ska kopplas in eller avvecklas.
- Versionsheader 9.0.0 mot paketets rapporterade 8.4.1 (samma drift som fil 7).

### Fil 9: statistics/plugin.py

- **Paketberoende:** filen importerar `COPYRIGHT` från `.` och fallback-importerar samtliga motortyper från `.` när `.engine` misslyckas — `plugins/statistics/__init__.py` måste alltså exportera dessa namn (enligt §12: paket-API + `TemplateScanner` + fail-safe). Paketet kan inte laddas förrän `__init__.py` och `engine.py` finns i WS.
- `_sync_counter` når Certificates-pluginets privata `_counter_manager` via duck-typing — funktionen är önskad (§8: import synkar räknare uppåt) men kopplingen är direkt och skör (FORBIDDEN-012-anda). Kandidat: definierat servicekontrakt/EventBus-anrop i stället.
- `QCloseEvent` importeras men används inte — kosmetisk rensning.
- `_quick_report`/`_on_generate_report` öppnar rapporter via `webbrowser.open` på UI-tråden — normalt snabbt men kan blockera vid långsam standardwebbläsare; kandidat för bakgrundstråd vid behov.
- `summary.total_templates_available` sätts genom mutation av motorns dataklass från UI-skiktet — överväg om fältet ska ägas av engine/`calculate_summary` i stället (tydligare ansvarsgräns, MUST-012).
- Version 5.0.0 i filen — verifiera mot `__init__.py`-metadata när den levereras (motsvarande §33-pass).

### Fil 10: statistics/engine.py

- **Fix-3 och Fix-4** är tillämpade denna session (se III.3 fil 10) — återställning möjlig på begäran (MUST-016-anda).
- `_scan_all_files` sätter `duration_seconds` till 0.0 på full skanning (mäter mot sig själv); `import_incremental` har korrekt `started` men skickar inte med den (se G1).
- `_extend_cache_validity` är ett rent `pass` — 24-timmars cache-giltighet förlängs aldrig i praktiken (se G2).
- `existing_keys` byggs om per fil i `import_incremental` (O(n²), se G3).
- `StatisticsEngine.load` kräver rå JSON-lista medan pluginets `_save_data` skriver dict `{"records": ...}` — formatdrift som kan ge tyst förlorad läsning (se G4).
- `generate(output_path, summary=...)` returnerar sträng i stället för att skriva fil — tvetydigt API (se G9); `plugin.py` anropar korrekt via konstruktorn.
- PDF-rapporter renderas via `latin-1 "replace"` → åäö ersätts med `?` (se G6).
- `REG_PATTERN` är friare här (`\d{2,3}` + valfritt sluttecken) än i template_designer — risk för inkonsistent validering mellan plugins (se G7).
- `TimePeriod` visar både "Alla" och "All tid" i periodväljaren (se G8).
- `scan_templates_detailed` implementerar redan §5.3 aktiv-DOCX korrekt — återanvänds med fördel vid F4-fixen (template_designer).

### Fil 11: statistics/__init__.py

- **Versionsdrift inom paketet:** `__init__.py` anger 10.0.0 medan `engine.py`/`plugin.py` anger 5.0.0 — tre versioner i samma paket. Ingår i G10-passet (kräver bekräftelse).
- **Fallback-API:t är medvetet mindre** (degraded mode): fallback-`ReportFormat` saknar PDF/EXCEL/MARKDOWN, fallback-`StatisticsEngine` saknar cache/incremental-import, fallback-`TimePeriod.get_display_periods()` visar inte `ALL_TIME` medan engine-versionen visar både `ALL` och `ALL_TIME` (jfr G8). Konstruktorsignaturerna är kompatibla med `plugin.py` i båda lägen — verifierat.
- `TemplateScanner._count_placeholders` läser alla `word/*.xml` (inkl. headers/footers) men söker bara i `<w:p>`-paragrafer via regex — placeholders i tabeller utan `<w:p>`-förpackning eller i footnotes kan missas; Certificates-motorn är mer omfattande (§6). Noterat som konsistenspunkt, ej förslagsrunda.
- `_count_placeholders` öppnar hela DOCX-zipen per skanning när cachen missar — bra prestanda idag tack vare stämpelcachen; överväg lazy-räkning om mallmapparna växer mycket.
- `_SCANNER_INSTANCE` är en global singleton med `RLock` — trådsäker, men instansen delas mellan alla anropare (inkl. `plugin.py` via `scan_template_names` när engine saknas); beteendet är avsett (§12 paket-API).

### Fil 12: settings/workers.py

- Oanvänd import: `tempfile` — kosmetisk rensning möjlig.
- Restore skriver filer en och en; vid avbrott mitt i återställning lämnas delvis restoreat skick. Backup är atomär per zip men inte per restore-session — överväg "ställ i ordning i temp-mapp → byt" vid behov (robusthetsförbättring, ej beställd).
- `RestoreWorker` återställer avsiktligt inte `Out/` och `logs/` (endast `config/Mallar/profiles` i `_ALLOWED_RESTORE_ROOTS`) — verifiera att detta är önskad policy innan release.
- `_MAX_LOG_FILE_BYTES`-kontrollen anropar `path.stat()` per loggfil — robust men kan förenklas om loggmängden växer (lågprioriterat).
- Version 8.7.0 i filen — i linje med §33-målet för `settings/plugin.py`; övriga settings-filer väntar på leverans för helhetsbedömning.

### Fil 13: settings/view.py

- Oanvända importer: `QPlainTextEdit`, `QTreeWidget` — kosmetisk rensning möjlig (används ej och exporteras inte i `__all__`).
- `__import__("sys")` inline i `ShortcutEditDialog` för plattformsval av monofont — fungerar men en modulnivå-`import sys` vore renare (kosmetiskt).
- `add_setting_control` bool-gren använder `stateChanged` med `bool(state)` — tillåter "partially checked" (1) som True; `toggled`/`checkState` vore striktare om tri-state någonsin aktiveras (lågprioriterat).
- `_tab_indices` i `SettingsShell` uppdateras aldrig vid borttagna flikar — inget borttagnings-API finns idag, så ingen drift; notera om tab-borttagning läggs till.
- Filen är 409 rader — god marginal mot ~1 200-riktlinjen för helperfiler (§4).

### Fil 14: settings/plugin.py

- `SETTING_DEFINITIONS`/`TAB_DEFINITIONS` är klassattribut och `register_setting` muterar `SETTING_DEFINITIONS` — delas mellan instanser. Idag finns en instans; om flera någonsin skapas bör definitionerna bli instansattribut (noterat, ej ändrat).
- `_check_auto_backup` kör via `QTimer.singleShot(0, …)` vid `on_load` — bra, men intervallkontrollen saknar lås mot samtidig manuell backup (`create_backup` kan dubbelstarta om användaren klickar samtidigt som auto-backup triggas). Kandidat till `_import_active`-liknande vakt vid behov.
- `_on_theme_event` anropar `self._shell.update()` — enkel repaint; verifiera mot faktisk ThemeManager-händelse när `core.py` levereras.
- `_backup_version` öppnar hela zipen per backup i listan för att läsa `_metadata.json` — acceptabelt vid ≤ 100 backups (`backup.max_backups`), annars cache-kandidat.
- `_clear_cache` tar bort hela `js/cache` — i linje med B1 (rensbar teknisk cache), men funktionen är manuell; automatisk rensning vid PROD-exit väntar på B1-implementationen.

### Fil 15: settings/core.py

- **Versionsdrift inom settings-paketet:** `core.py` 9.0.0 mot `plugin.py`/`view.py`/`workers.py` 8.7.0 — samma §33-mönster som övriga paket (statistics 10.0.0/5.0.0, system_tray 8.4.1/9.0.0, template_designer 4.2.0/4.2.1). Kräver samlat bekräftat metadata-pass (G10).
- **Dubbla ThemeManagers:** `core/themes.py` (§27, används av `main.pyw` via `ThemeManager.get_instance`) och `settings/core.py` (egen `ThemeManager` + `PALETTES`, COLORS-001-kommentar). Bestäm normativ källa och delegera den andra (FORBIDDEN-016-kandidat) — påverkar även F-omgången.
- `sync`/`sync_with_system` returnerar **operationens resultat** (bool för lyckad åtgärd), inte det slutliga läget — dokumentera eller justera semantiken vid harmoniseringspasset för att undvika missförstånd hos anropare.
- `_detect_macos`/`_detect_linux` gör blockande `subprocess.run` med 2 s timeout — acceptabelt (kort timeout, cachas 30 s), men bör aldrig anropas från känsliga UI-flöden utan cachen.
- `AutostartManager` delegerar till `core.config.AutostartManager` när den finns — verifiera att båda implementationerna är överens om registry-nyckel/plist-namn när `core/config.py` levereras (annars risk för dubbla autostart-entries, §19 "Inga dubbletter").

### Fil 16: settings/__init__.py

- **§33-driften är nu komplett dokumenterad i kod:** `__init__.py` 8.6.0 · `plugin.py` 8.7.0 · `core.py` 9.0.0 — exakt de tre versioner §33 räknar upp. Metadataharmonisering kräver uttrycklig bekräftelse (MUST-016) innan ändring.
- `PLUGIN_INFO` innehåller både `priority` och `tab_priority` med samma värde — harmlös dubblett; kan städas vid harmoniseringspasset.
- `_logger.critical(..., exc, exc_info=True)` loggar undantaget dubbelt (både som `%s`-argument och traceback) — kosmetiskt.
- Fallback-`get_tab` kan bara bygga synlig degraded-UI när PySide6 finns; utan PySide6 returneras `None` (pluginet blir tab-löst). Detta är korrekt — men verifiera att PluginManager hanterar `None` utan varningar i den skarpa miljön.

### Fil 18: certificates/services.py

- `CertificateGenerator.generate` håller `self._lock` under hela konverteringen inkl. retry-sömn — genereringar serialiseras (säkert för räknaren, men begränsar parallellitet). `generate_batch` är sekventiellt per design; vid PERF-006-problem (batch 50 < 120 s) är konverteringstiden den dominerande faktorn, inte låset.
- `_unique_path`-loopen är obegränsad i teorin (terminerar i praktiken); kan ges övre gräns vid hårdning.
- `PDFConverter._find_libreoffice` provar hårda Windows-sökvägar (bl.a. "LibreOffice 24/25/26") — underhållslista att förlänga vid nya LO-versioner; `shutil.which` fångar PATH-installerade.
- `_convert_libreoffice` varnar med `result.stderr.strip()` vid fel — bra; överväg att även logga stdout vid debug-nivå om felsökning behövs.
- `_build_replacements` lägger användarvärden sist → användarvärden vinner över auto-fält med samma nyckel — verifierad och avsedd precedens (§6: `::År::` ska aldrig förifyllas automatiskt).
- Checklistan för `engine.py`-leveransen (AST-extraherad): `FOLDER_CONFIG, BatchResult, ConversionMethod, GenerationResult, PlaceholderParser, PlatformHelper, Template, safe_filename` + `Template.config.counter_start/counter_prefix/filename_pattern`, `Template.get_ordered_attachments()`, `Template.is_valid`, `Template.docx_file`.

### Fil 19: core/__init__.py

- **Fallback-ConfigManager måste hållas i synk med riktiga `core/config.py`** när den levereras (samma publik API-yta: get/set/delete/observe/increment/toggle/ensure_defaults/merge/export/import/reload/close) — annars risk för beteendeskillnad mellan fallback och skarp drift (FORBIDDEN-016-anda).
- `PlatformHelper` definieras nu på tre ställen (core/__init__.py, settings/core.py, certificates via engine) — konsolideringskandidat till `core/infrastructure.py` (jfr B5; §27 anger att infrastruktur äger plattformshjälparna).
- `PluginManager.discover` hittar enbart paket (mappar med `__init__.py`) — enkelfils-plugins stöds inte (av design enligt §4).
- `EventBus.emit` samlar icke-None-returer från handlers — används inte av någon känd konsument; ofarligt men kan rensas om det aldrig behövs.
- `get_path()` skapar mappar vid anrop (inkl. `Backup/`, `Icon/` m.fl.) — verifiera mot §4-regeln att `js/profiles/` aldrig skapas: profiles är INTE med i `_DEFAULT_PATH_MAP` ✓ (skapas enbart via Settings explicita sökväg).
- OBS-1 kvarstår: `AppContext.show_window/hide_window` + main.pyw:s prenumerationer på `window.show/window.hide` är ena sidan av ping-pong-risken; beslut väntar.

## III.5 Förslagsrunda 1 (levererad 2026-08-05, väntar på val)

Alla förslag gör appen **bättre** och/eller **snabbare**. Inget ändras utan uttryckligt godkännande (MUST-016). Svara med nummer, t.ex. "F2, F3, F5".

| # | Förslag | Fil(er) | Typ/Vinst |
|---|---------|---------|-----------|
| **F1** | Fixa `redo()` som aldrig applicerar åtgärden — implementera `_apply_redo` symmetriskt med `_apply_undo` | template_designer/core.py | bättre (bugg, MUST-005/008) |
| **F2** | Numerisk versionsjämförelse i `check_dependencies` (lexikografisk jämförelse bevisat felaktig: `"10.0.0" >= "5.0.0"` → False) | template_designer/__init__.py | bättre (bugg, MUST-008) |
| **F3** | Zip Slip-skydd i `import_template` — validera varje medlems sökväg mot destinationen | template_designer/core.py | bättre (säkerhet, §21) |
| **F4** | Aktiv DOCX enligt §5.3 i `_scan_folder` — `<MallNamn>.docx` först (case-insensitivt), sedan alfabetiskt; filtrera tomma/dolda | template_designer/core.py | bättre (konsistens, §34) |
| **F5** | Debounce/coalesce av mallskanning vid event-burst (~250–500 ms) — samma princip som Certificates §5.5 | template_designer/plugin.py | snabbare (PERF-004/007/009) |
| **F6** | Placeholder-sökning i headers/footers/first/even-page enligt §6 | template_designer/core.py | bättre (konsistens med generatorn) |
| **F7** | Icke-blockerande fil-/mappöppning — `Popen(start_new_session)` i stället för blockerande `subprocess.run` | plugins/__init__.py, td/plugin.py | snabbare (§18, PERF-007) |
| **F8** | Sluta tysta exceptions — lägg till loggning i mjuk import och fallback-vägar | td/plugin.py, td/core.py | bättre (FORBIDDEN-011, §21) |
| **F9** | Pending selection i stället för fast `QTimer.singleShot(500)` — applicera i `_on_scan_done` | template_designer/plugin.py | snabbare + pålitligare (MUST-009) |
| **F10** | Samla plattformshjälp på ett ställe — `plugins/__init__.py` som enda källa (§27), återanvänd i template_designer | plugins/__init__.py + td/* | bättre + snabbare (FORBIDDEN-016-anda) |

### Bonusnoteringar (utanför 10-listan)

- **Versionsynk:** `template_designer/__init__.py` är 4.2.0 medan `core.py`/`plugin.py` är 4.2.1 — samma typ av metadata-pass som §33 beskriver för settings (8.6.0/8.7.0/9.0.0). Kräver bekräftelse.
- **Filstorlek:** `template_designer/plugin.py` är 1 952 rader mot riktlinjen ~1 800 (§4). Möjlig uppdelning: dialogerna till egen fil.
- **Stub-klasser:** `core.py` exporterar tomma stubbar för bakåtkompatibilitet — FORBIDDEN-006-anda; behöver uttryckligt beslut.
- **Statiska copyrightår** i vissa docstrings/headrar trots BUG-019.

## III.6 Förslagsrunda 2 (levererad 2026-08-05, väntar på val)

Underlag: filerna 6–10 (system_tray + statistics). Alla förslag gör appen bättre och/eller snabbare. Inget ändras utan uttryckligt godkännande (MUST-016). Svara med nummer, t.ex. "G2, G3, G6".

| # | Förslag | Fil(er) | Typ/Vinst |
|---|---------|---------|-----------|
| **G1** | Fixa tidsmätningen i `_scan_all_files`: `duration_seconds` blir alltid 0.0 vid full skanning (mäter mot sig själv); skicka med `started` från `import_incremental` | statistics/engine.py | bättre (diagnostik) |
| **G2** | Implementera `_extend_cache_validity` (idag rent `pass`): uppdatera cache-filens mtime/timestamp så att 24-timmars giltighet faktiskt förlängs | statistics/engine.py | bättre + snabbare (undviker onödiga fullskanningar) |
| **G3** | Flytta upp `existing_keys`-mängden ur fil-loopen i `import_incremental` (byggs om per fil idag → O(n²)) | statistics/engine.py | snabbare (PERF-006, batch) |
| **G4** | Unify lagringsformat: `StatisticsEngine.load` kräver rå lista medan pluginet skriver `{"records": ...}` — låt `load` acceptera båda (eller standardisera) så cachen aldrig läses fel i tysthet | statistics/engine.py + plugin.py | bättre (buggrisk, §21) |
| **G5** | Cachea `pypdf`/`PyPDF2`-modulreferensen i `_count_pdf_pages` (idag `__import__` + exception-loop per fil) | statistics/engine.py | snabbare (import av många PDF:er) |
| **G6** | PDF-rapporter: `latin-1 "replace"` ersätter ÅÄÖ med `?` — använd korrekt teckenhantering så svenska bevaras | statistics/engine.py (`_generate_pdf`) | bättre (spec: bevara svenska tecken) |
| **G7** | Unify VIN/REG-mönster: statistics friare `REG_PATTERN` vs. template_designer/certificates — en gemensam källa i `core/infrastructure` (§27 "VIN-hjälpare") | core + statistics + template_designer | bättre (konsistens, FORBIDDEN-016-anda) |
| **G8** | Ta bort dubbletten i periodväljaren: `TimePeriod.ALL` ("Alla") och `ALL_TIME` ("All tid") visas båda — slå ihop till en | statistics/engine.py + plugin.py | bättre (UX) |
| **G9** | Tydliggör `ReportGenerator.generate`-API:t: med `summary`-argument returneras sträng även när `output_path` anges — dela i `generate_file`/`generate_string` eller dokumentera beteendet | statistics/engine.py | bättre (robust API) |
| **G10** | Versionsharmoniserings-pass: statistics 5.0.0 (engine/plugin/__init__), system_tray 8.4.1 ↔ 9.0.0, settings 8.6.0/8.7.0/9.0.0 (§33) — ett samlat, bekräftat metadata-pass | flera `__init__.py` | bättre (releasekrav) |

### Bonusnoteringar runda 2

- `scan_templates_detailed` i engine.py implementerar redan §5.3 aktiv-DOCX korrekt — återanvänds med fördel när F4 (template_designer) implementeras.
- OBS-1 (tray event-ping-pong) och F1–F10 väntar fortfarande på beslut.

## III.7 Beställda funktioner (logg)

| # | Beställning | Datum | Status | Spec-referens |
|---|-------------|-------|--------|---------------|
| B1 | Funktion som rensar `__pycache__/`-mappar (+ `*.pyc`/`*.pyo`): DEV manuellt, PROD vid ordnad exit via `smart_cache`; central hjälp i `core/infrastructure.py`, anrop från `main.pyw` cleanup + manuell körning via Settings; användardata fredad | 2026-08-05 | Ej påbörjad | Del I §26 "Rensning av teknisk cache", §23 punkt 7 |
| B2 | **Gör mallskanningen bättre:** (a) §5.3 aktiv-DOCX-val i alla skannrar (statistics har redan rätt logik i `scan_templates_detailed`), (b) DOCX-stämpelcache `path + mtime_ns + size` (§5.5), (c) debounce/coalesce av `refresh_templates`-event (jfr F5), (d) en gemensam skanner i stället för tre implementationer (FORBIDDEN-016-anda; kandidat: `statistics.TemplateScanner` med befintligt cache), (e) ogiltiga mappar rapporteras i status/logg utan krasch (MALL-007) | 2026-08-05 | Beställd — väntar på `certificates/engine.py` + `plugin.py` i WS (MUST-003) | Del I §5, §5.3, §5.5, §23 |
| B3 | **Användarvald mallimportmapp med dropdown** (beställd 2026-08-05, utökad + **bekräftad** 2026-08-26): användaren ställer in **en eller flera** mappar för import av intygsmallar (`templates.roots` i `js/settings.json`, default: app-nära `Mallar/`) och väljer aktiv rot i en **drop-down-ruta** (Intyg-fliken + Settings). Varje mall ligger i en undermapp direkt under vald rot (t.ex. `intyg/passagerare` → rot `intyg`, mall `passagerare`); skanningen utgår från vald rot. Strukturkrav oförändrat: varje mall i en separat undermapp (MALL-001), ingen nästling (MALL-008), lösa filer ignoreras (MALL-009), varning om vald rot saknar giltiga mallfiler. **Utökningar bekräftade 2026-08-26:** (a) skanningen söker **DOCX och Word-filer (.doc)** — utökar MALL-004; konverterarkedjan måste hantera båda formaten (LibreOffice/Word COM gör det, §7); (b) **PDF-filer i mallmappen är bilagor** och slås ihop med varje genererat intyg — redan implementerat i `services.py` (PDFMerger + `Template.get_ordered_attachments()`, MALL-005/§7) och ska bevaras i den nya skanningen. Undanröjer hårdkodade sökvägar (FORBIDDEN-009) | 2026-08-05 (utökad + bekräftad 2026-08-26) | Beställd — väntar på `certificates/engine.py` + `plugin.py` i WS | Del I §4, §5.2, FORBIDDEN-009/017 |
| B4 | **Settings-schemavalidering + auto-repair** (överförd från GlassCalc §4.4.2): validera `settings.json` mot schema vid start/före sparande/efter manuell redigering; saknade/felaktiga nycklar → default; korrupt fil → `.corrupt-backup` + ladda default; `_meta.schema_version` för migrering. Passar ConfigManager (`core/config.py`) | 2026-08-05 | Beställd — väntar på `core/config.py` i WS | Del I §27, FORBIDDEN-017 |
| B5 | **Atomic write-härdning med fsync** (överförd från GlassCalc §4.4.4): central hjälp `mkstemp → fdopen → fsync → os.replace` för all kritisk JSON-skrivning (säkrast på Windows); ersätter tmp+replace där det är motiverat. Passar `core/infrastructure.py` (atomär filskrivning §27) | 2026-08-05 | Beställd — väntar på `core/infrastructure.py` i WS | Del I §21/§27 |
| B6 | **Recovery 3-strikes** (överförd från GlassCalc §4.4.6): räkna fel per delsystem; 3 fel → markera inaktiv + ⚠ i statusfält + manuell återaktivering; förhindrar kraschloopar. Integreras med PluginManager fail-safe + logging_plugin | 2026-08-05 | Beställd — väntar på `core/__init__.py` + `logging_plugin/` i WS | Del I §21, §9 |
| B7 | **Diagnostik-dekatorer `@log_call` + `LogContext`** (överförd från GlassCalc §4.4.1): entry/argument/resultat/tid/undantag + ▶/✔/✗ med ms. **Förbehåll:** verifiera mot `logging_plugin/core.py` innan implementation så att det inte dubblerar befintlig funktionalitet (FORBIDDEN-016) | 2026-08-05 | Beställd med förbehåll — väntar på `logging_plugin/` i WS | Del I §14 |
| B8 | **"Öppna senaste PDF"** (överförd från GlassCalc §7.6 förslag 23): menyval i Verktyg-menyn som öppnar den senast genererade PDF:en i standardvisaren. Kan hookas mot system_tray `RecentCertificate` (senaste intyg sparas redan) eller skanning av `Out/<MallNamn>/` | 2026-08-05 | Beställd — väntar på beslut om implementationsväg (rör `main.pyw`/`system_tray`, MUST-016) | Del I §16, §28 |
| B9 | **ConfigStore med FileFingerprint + värdeklamp** (överförd från Cabas C42): FileFingerprint (`mtime_ns` + storlek) → noll onödig läsning/parsning av oförändrade JSON-filer; värdeklamp (CONFIG_RANGES/CONFIG_ENUMS) skyddar mot absurda värden utan krasch. Utökar B4; passar `core/config.py` | 2026-08-26 | Beställd — väntar på `core/config.py` i WS | Del I §27, PERF-010 |
| B10 | **EventBus `emit_gui` + `emit_delayed`** (överförd från Cabas C43): `emit_gui(event, data)` garanterar leverans i Qt-huvudtråden från bakgrundstrådar; `emit_delayed(ms, event, data)` ger avbrytbar debounce (sök/filter). Passar EventBus i `core/__init__.py` | 2026-08-26 | Beställd — väntar på beslut | Del I §27, PERF-007/009 |
| B11 | **Avstängningsskydd vid pågående arbete** (överförd från Cabas C30): varna om användaren försöker avsluta mitt under generering/batch/import — förhindrar dataförlust vid batch (PERF-006, upp till 120 s) | 2026-08-26 | Beställd — väntar på `certificates/plugin.py` (batch-state) + beslut | Del I §16, §21 |
| B12 | **1-klick "Kopiera diagnostik till urklipp"** (överförd från Cabas C45): knapp i Diagnostik-fliken kopierar fullständig diagnostik (version, plugins, hälsa, loggstatistik) till urklipp. Passar Diagnostik-fliken i `settings/plugin.py` | 2026-08-26 | Beställd — väntar på beslut | Del I §13 |
| B13 | **WCAG 4.5:1 kontrastvalidering** (överförd från Cabas C46 + C13): matematisk kontrastkontroll för tema-/tabellfärger; status visas aldrig enbart med färg (ikon + text). Passar `core/themes.py` + §17 tillgänglighet | 2026-08-26 | Beställd — väntar på `core/themes.py` i WS | Del I §17, §30 |
| B14 | **Headless fake-Qt-injektion för test.py** (överförd från Cabas C47): säkra stubbar (QApplication/QPainter/QPixmap/QFontMetrics) gör att GUI-logik kan testas till 100 % utan skärm. Höjer nivån på §31-testsviten (idag PySide6-fri basnivå) | 2026-08-26 | Beställd — väntar på beslut om test-strategi | Del I §31, §22 |
| B15 | **Självläkande settings från Backup** (överförd från Cabas C41.3): korrupt `settings.json` → automatisk återställning från senaste backup (Backup/-mappen finns redan + BackupWorker). Utökar B4/B5 | 2026-08-26 | Beställd — väntar på `core/config.py` i WS | Del I §21, §26 |
| B16 | **Loggning som loggar allt** (användarorder 2026-08-26): fullständig loggtäckning via logging_plugin — alla EventBus-event (event tap), alla filoperationer (tracer), settings-/temaändringar, plugin-livscykel, användaråtgärder (generering, batch, import/export, profilåtgärder), startup/shutdown samt alla fel med traceback. Nivåstyrt (DEV: allt; PROD: INFO+), konfigurerbart i settings, kategoriserat så att UI inte dränks (PERF-009: lätta callbacks < 5 ms) | 2026-08-26 | Beställd — väntar på `logging_plugin/` i WS | Del I §14, §26 |

### Överföringsanalys GlassCalc → Intygsgenerator (2026-08-05)

Beställning: "enbart de delar som passar ihop med intygsappen". Bedömning per GlassCalc-del:

| GlassCalc-del | Bedömning | Åtgärd |
|---|---|---|
| §4.4.2 Schema-validering + auto-repair | **Passar** — ConfigManager är central för alla plugins | → **B4** |
| §4.4.4 Atomic write med fsync | **Passar** — §21 kräver atomära skrivningar; fsync höjer durabilitet | → **B5** |
| §4.4.6 Recovery 3-strikes | **Passar** — complements fail-safe-laddning | → **B6** |
| §4.4.1 `@log_call`/`LogContext`/SESSION_ID | **Passar delvis** — SESSION_ID finns redan (§27); dekoratörer kan passa logging_plugin | → **B7** (med förbehåll) |
| §4.4.3 Single-instance fokusering | **Redan implementerat** — `InstanceActivationServer` (QLocalServer) i `main.pyw` | Ingen åtgärd |
| §4.4.5 `__pycache__`-rensning | **Redan beställt** som **B1** (DEV/PROD + fredad data) | Ingen ny åtgärd |
| §4.4.7 EventBus | **Redan implementerat** — komplett avtalskarta (Del I §10) | Ingen åtgärd |
| §4.4.8 Plugin-kontrakt | **Redan implementerat** — duck-typing-kontrakt (Del I §9.1) | Ingen åtgärd |
| Tema dark/light/system | **Redan implementerat** — exakt tre teman (Del I §13) | Ingen åtgärd |
| Kalkylmodell, artikelregister, PDF export/Arkiv-flöde, donate-knapp | **Passar inte** — GlassCalc-domän (vinstkalkyl), ej fordonsintyg | Ej överfört |

GlassCalc-projektet har tagits bort ur workspacen (2026-08-05); de överförda idéerna är fullständigt dokumenterade i B4–B7 ovan.

### Överföringsanalys Cabas Reskontra → Intygsgenerator (2026-08-26)

Beställning: "finns det något från denna spec vi kan implementera i vår app?" — samma princip som tidigare: enbart delar som passar intygsappen.

**Överförda till backloggen:**

| Cabas-källa | Intygsgenerator-motsvarighet | Backlog |
|---|---|---|
| C42 ConfigStore: FileFingerprint + värdeklamp | ConfigManager (`core/config.py`) | **B9** (utökar B4) |
| C43 EventBus `emit_gui`/`emit_delayed` | EventBus (`core/__init__.py`) | **B10** |
| C30 Avstängningsskydd vid pågående arbete | `main.pyw` closeEvent + certificates batch | **B11** |
| C45 1-klicksdiagnostik till urklipp | Diagnostik-fliken (settings) | **B12** |
| C46 WCAG 4.5:1 kontrasttest + C13 aldrig-enbart-färg | `core/themes.py`, §17 | **B13** |
| C47 Headless fake-Qt för testbarhet | `test.py` (§31) | **B14** |
| C41.3 Självläkande JSON från backup | ConfigManager + Backup/ | **B15** (utökar B4) |
| C17/C18 cache-rensning med förhandsvisning + veckointervall | Utökningsalternativ till **B1** (förhandsvisning av mappar/KB + veckovis rensning vid start) | notering till B1 |
| C40 `threading.excepthook` | Utökningsalternativ till **B7**/logging_plugin | notering till B7 |

**Redan täckt i Intygsgenerator (ingen åtgärd):**

- C1 Single instance + fokusering → `InstanceActivationServer` (QLocalServer) i `main.pyw`
- C3 UI-tillståndspersistens → fönstergeometri/hidden tabs/last tab i `main.pyw`
- C4/C44 Autostart sync (+ pythonw) → §19 + settings `AutostartManager` (verify-after-write/trasig-konsol-reparation noteras till `core/config.py`-leveransen)
- C6/C40 kategoriloggar + SESSION_ID → logging_plugin + `SESSION_ID` i core (verifieras mot logging_plugin-leveransen)
- C8 EventBus → Del I §10 (komplett avtalskarta)
- C10/C11 Plugin health monitor → redan beställt som **B6**
- C12 Tema system/auto med OS-synk → §13, implementerat i settings/core.py
- C15/C25 senaste fil / öppna efter export → **B8** på backloggen
- C21 Pinnade beroenden → MUST-014
- C32/C34 Sökfält + CSV-export → statistics-pluginet
- C2 Schemavalidering → **B4** (redan beställd från GlassCalc)
- C17/C18 pycache-rensning → **B1**

**Passar inte / krockar med låst spec (ej överfört):**

- C19 ASCII-sanerade filnamn → krockar med §8 "Filnamn NFC-normaliseras och bevarar ÅÄÖ"
- C29/C31 filövervakning/autoscan → FORBIDDEN-023 (Watchdog)
- C36 månadsloggmappar → krockar med LOG-021-strukturen (`logs/` + `logs/archive/`, låst)
- C28 momssanering, C38/C39 betaldata/arkiv, C15 fakturaruta, C23 PIN, dröjsmålsränta, försäkringsigenkänning → Cabas-domän (reskontra), ej fordonsintyg
- Cabas arbetsprocessregler (25-förslag var 10:e fil, rensning var 15:e) → Intygsgenerator har egen arbetsordning (10 förslag var 5:e fil)

Cabas-specen sparas inte i workspacen; analysen ovan är den bestående dokumentationen.

### Förslagsrunda 3 (H1–H10) — levererad 2026-08-26 på användarens begäran

Underlag: filerna 11–19 (core/__init__.py, certificates services/view, settings 5-pack, statistics 3-pack, system_tray 3-pack, template_designer 3-pack). Alla förslag gör appen bättre och/eller snabbare; inget ändras utan godkännande (MUST-016).

| # | Förslag | Fil(er) | Typ/Vinst |
|---|---------|---------|-----------|
| **H1** | **LibreOffice-detektering med glob-mönster** i stället för hårdkodad versionslista ("LibreOffice 7/8/24/25/26") — hittar alla framtida versioner via `LibreOffice*`/`libreoffice*`-sökning | certificates/services.py | bättre (framtidssäker) — ✅ **IMPLEMENTERAD i services.py 10.2.0** |
| **H2** | **Temadetektering utanför UI-tråden**: `_detect_windows/_macos/_linux` blockerar upp till 2 s vid första `apply_theme` — bryter mot PERF-003 (<200 ms). För-detektera i bakgrundstråd vid on_load + 30 s-cache; temabyte använder cachat värde | settings/core.py | snabbare (PERF-003) |
| **H3** | **Spar-debounce 500 ms i fallback-ConfigManager** (paritet med riktiga ConfigManager enligt PERF-010) — undviker överskrivning av JSON vid många snabba set i degraded mode | core/__init__.py | snabbare + säkrare |
| **H4** | **ConfigManager._instances-städning**: instansen tas bort ur singleton-registret vid `close()` — inga stale config-objekt mellan sessioner/plugins | core/__init__.py | bättre (minne/korrekthet) |
| **H5** | **Släpp genereringslåset mellan retry-sömner**: `time.sleep` inuti `self._lock` blockerar all annan generering under backoff; sömn utanför låset (per-konverterare-serialisering bevaras) | certificates/services.py | snabbare (batch, PERF-006) — ✅ **IMPLEMENTERAD i services.py 10.2.0** |
| **H6** | **Standardiserad genereringsprogress via EventBus** (`certificate.progress` med percent/message) i stället för manuell callback-koppling — frikopplat (MUST-007), återanvändbart för batch-progress och statusrad | certificates/services.py + plugin | bättre (arkitektur) — ✅ **IMPLEMENTERAD i services.py 10.2.0** (valfri `event_emitter`; callback intakt) |
| **H7** | **Rapportera hoppade bilagor**: krypterade/korrupta PDF-bilagor hoppas idag över med varning men `GenerationResult` visar det inte — lägg till `skipped_attachments` så användaren ser om sidor saknas i slut-PDF:en | certificates/services.py | bättre (datasynlighet) — ✅ **IMPLEMENTERAD i services.py 10.2.0** (`MergeReport` + `merge_detailed()`; `merge()` behåller signatur) |
| **H8** | **`_unique_path` övre gräns**: stopp vid N=1000 med fellogg i stället för obegränsad while-loop | certificates/services.py | bättre (robusthet) — ✅ **IMPLEMENTERAD i services.py 10.2.0** (`UNIQUE_PATH_MAX`, timestamp-fallback) |
| **H9** | **SVG-stöd i fallback-IconManager**: `find_icon` letar bara png/ico/icns men §29/main.pyw prioriterar `icon.svg`/`app.svg` — lägg till SVG för paritet | core/__init__.py | bättre (§29-paritet) |
| **H10** | **DPI-skalade etikettbredder**: fasta 150–230 px i FieldWidget/settings-formulär kan klippas vid hög DPI — skala med fontmetrik (§30: text får inte klippas) | certificates/view.py + settings/view.py | bättre (§30, tillgänglighet) — ✅ **IMPLEMENTERAD i certificates/view.py 10.2.0** (`dpi_scale()`; settings/view.py kvar vid nästa uppgradering) |

### Nybyggda filer 20–24 — `plugins/logging_plugin/` v11.0.0 (beställd central loggning, 2026-09-17)

Beställning: "100000% bättre central loggning som skriver allt och visar vad som händer;
om filer inte hittas ska felsökning bli 5000000% enklare; AI-anpassad." Byggd enligt Del I §14
(filstruktur), §9.2 (prioritet 1), §10 (EventBus-ämnen), §21 (fail-safe), LOG-021 (arkivering).

| Fil | Innehåll |
|---|---|
| `plugins/logging_plugin/__init__.py` | Metadata, paketexport, fail-safe fallback-Plugin med synlig fel-LABEL vid trasig modul. |
| `plugins/logging_plugin/core.py` | `LogEntry`, `LogStore` (trådsäker skrivning + rotation), `QueueWriter` (icke-blockerande bakgrundsskrivning), `StoreHandler`/`WarningStoreHandler` (stdlib-logging → store), export (jsonl/txt/md), `archive_old()` enligt LOG-021, `ai_report()`/`ai_summary_text()` AI-felsökningsrapport. |
| `plugins/logging_plugin/tracer.py` | `FsTracer`: reversibel filsystemspårning (builtins.open, os.*, os.path.*, glob, pathlib.Path.*) med re-entrancy-skydd, brusfilter (egen loggkatalog) och `fs.miss`/`fs.denied` med anropare, cwd och "menade du …?"-kandidater. |
| `plugins/logging_plugin/view.py` | `LoggingView`: live loggdisplay, nivåfilter, sökning, paus/autoscroll, alerts (ERROR/CRITICAL + SAKNADE FILER), filsystemsflik med miss-tabell, statistikflik, export + AI-rapport-knappar, "Kopiera felsökningsinfo (AI)" till urklipp. |
| `plugins/logging_plugin/plugin.py` | Controller: kopplar store till `Intygsgenerator`-loggern (inga moduländringar krävs — `context.get_logger` fortsätter fungera), JSONL-kö, crash-hooks (`sys.excepthook` + `threading.excepthook` → `crash.log` direkt till disk), Qt-meddelandehandler, EventBus `logging.session_started` / `logging.error_logged` (kö+timer-brygga) / `logging.crash_detected`, tracer install/restore. Legacy `setup`/`on_close` stöds. |

AI-anpassning: `<logs>/intygsgenerator.jsonl` har stabilt NDJSON-kontrakt (`ts/level/logger/event/msg/ctx/session/thread/exc`, dokumenterat i core.py); `ai_report.md` samlar miljö, statistik, alla `fs.miss` med kandidater och senaste fel i en enda fil.

Smoketest (sandlåda, PySide6-stubs): **48/48 OK** — rotation, crash.log, arkivering (21 d), export, tracer install/restore + kandidater + brusfilter, plugin load/unload, EventBus-ämnen, vy-alerts för saknad fil, AI-rapport-innehåll, inga dubbletter (propagate av), logging intakt efter unload. C6/C40 (SESSION_ID i loggar) verifierad här: varje post bär `session`.

Koppling till main.pyw kräver INGEN ändring: PluginManager upptäcker paketet automatiskt och
prioritet 1 garanterar att loggningen laddas först.

### Fix 2026-09-18 — `template_designer/core.py` v4.2.1 → v4.2.2 (RecursionError, Python 3.14)

**Fångad av den nya centralloggen** (logging.crash_detected med full traceback, session 16:26:23):
`RecursionError` i `ValidationLevel.icon`/`TemplateStatus.icon` — mönstret `{...}[self.name]`
i en enum-property rekursade via `enum.DynamicClassAttribute.__get__` på Python 3.14.7
(koden är felfri på 3.13; användarens runtime är 3.14). Kraschen utlöstes av
`template_designer/plugin.py` `_refresh_list_ui` → `getattr(info, "icon", ...)` vid 4 mallar.

**Fix (båda enum:arna, core.py rad ~127–155):** identitetsjämförelse
(`if self is TemplateStatus.VALID: ...`) i stället för dict-bygge + `self.name` per anrop.
Rör aldrig enum:ns `DynamicClassAttribute`-mekanik → säker på alla Python-versioner,
och snabbare (ingen dict allokeras per anrop). Ikoner oförändrade: ✅ ℹ️ ⚠️ ❌ / 📄 ⚠️ ❌ ❓.

Verifierat i sandlådan: alla 8 enum-ikoner, 1000× upprepade anrop, `ValidationMessage.__str__`,
`TemplateInfo.icon` via exakt den kraschande `getattr`-kodvägen — allt OK.

### Fix 2026-09-18 (del 2) — `template_designer/plugin.py` v4.2.1 → v4.2.2: UI-lås vid mallskanning

**Symtom:** "när mallar laddas låser det sig" — ~10 s fryst UI efter `Skannade 4 mallar`,
under pythonw.exe utan synlig krasch.

**Rotorsak (korrigerad analys):** RecursionError-kraschen 16:26 var INTE primärt enum-ikonen.
`_refresh_list_ui()` fyllde på `_cat_combo` (`clear()` → `addItem` → `addItems` → `setCurrentIndex`)
UTAN `blockSignals`, medan combon har `currentTextChanged → _refresh_list_ui()`. Varje programmatisk
ändring triggar en nästlad refresh → rekursiv signalstorm; RecursionError högg av varje gren
(PySide6 sväljer undantaget tyst under pythonw.exe = ingen stderr) → UI-tråden mal i en storm av
misslyckade refreshar = totalt lås. Icon-propertyn var bara stället där stacken råkade brista
(första fixen behålls som härdning + snabbare kod).

**Fix i plugin.py:**
1. Combopåfyllning bara om innehållet faktiskt ändrats (`existing != wanted`) och under
   `blockSignals(True/False)` med try/finally — programmatiska ändringar triggar aldrig signaler.
2. Skanning sammanfogas: `_scan_in_flight`/`_scan_requested` — högst en skanning i taget;
   nya önskemål under pågående skanning ger exakt EN uppföljningsskanning (on_load + get_tab +
   EventBus-events kan inte längre stapla parallella skanningar).
3. `_on_scan_error` återställer flaggorna (tidigare lambda läckte `_scan_in_flight` vid fel).

**Verifierat (sandlåda, realistisk QComboBox-stub med Qt:s signalsemantik):** 13/13 OK —
ingen storm vid oförändrade/ändrade kategorier, val bevaras, användarval ger exakt 1 refresh,
3 samtidiga skanningsönskemål → 1+1 worker, felväg återställer flaggor. Logging-sviten
fortfarande 48/48 OK.

**Att kopiera till Windows:** `plugins/template_designer/plugin.py` (4.2.2) och
`plugins/template_designer/core.py` (4.2.2, från del 1).

### Fix 2026-09-18 (del 3) — `template_designer/plugin.py` v4.2.3: tysta öppningsfel blir synliga

**Symtom (skärmbilder):** mallar finns på disk och öppnas i Word manuellt, men i appen
"öppnas inte" mallarna och Malldesigner-listan kan vara tom — utan något felmeddelande.

**Rotorsak (del av):** `OSPlatform.open_path` svepte in `os.startfile`/`subprocess` i
`suppress(Exception)` — varje öppningsfel svaldes helt tyst (ingen logg, ingen statusrad,
ingen fallback).

**Fix:** `open_path(path, log) -> bool`: primär öppning loggas (`td.open`), vid fel loggas
`td.open_failed` med path+error, sedan `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`-fallback
(loggas `td.open_fallback`/`td.open_fallback_failed`), returvärde så anroparen kan ge feedback.
`_on_open_docx`/`_on_open_folder` visar nu statusradsmeddelande vid miss och loggar om mallen
saknar docx. `_refresh_list_ui` loggar DEBUG med antal träffar så tom lista blir spårbar.

**Verifierat:** 18/18 i `.test_td_freeze.py` (storm/sammanfogning + open_path: primär OK,
primär-fel→fallback, båda-fel→False+logg), 48/48 i loggningssviten.

### Uppgradering 2026-09-18 — `plugins/logging_plugin/` v11.0.0 → v12.0.0 (beställning: "188888888% bättre")

Beställning: "loggning_plugin/plugin.py — 188888888% bättre i kod och design, anpassat i text
och fönster, loggning av precis allt som händer, 10000% AI-felsökning."

| Fil | v12-nyheter |
|---|---|
| `plugin.py` | Omskriven: `_step()` kör varje load-steg med tidsmätning + `lg.load_step(_failed)`; `_quiet()` ersätter tysta `suppress()` och loggar **varje uppslukat undantag** som `lg.swallowed` (inget undantag försvinner i mörker); `_evt()` fail-safe strukturerad loggning; `state()` + `diagnose()` ger AI-rapport med pluginets eget tillstånd (tracer, store, propagate, swallowed-räknare, uptime). AI-kontrakt `lg.*` dokumenterat i docstring. All funktionalitet bevarad (prio 1, EventBus-ämnen, crash-hooks, Qt-handler, tracer, propagate-fix, legacy setup/on_close). |
| `view.py` | Adaptiv UI: `_sp()` fontmetrik-skalning (§17/§30), word-wrap på header/status, skalade kolumnbredder i fs-trädet, minsta användbara fönsterstorlek; **all UI-användning loggas** med `lg.ui.*` (export, AI-rapport, kopiera diagnostik, filter, paus, autoscroll, sök med 300 ms debounce). |
| `__init__.py` | `__version__` 12.0.0. |

Verifierat: 52/52 i loggsviten (nya checks: state(), diagnose() med tillståndsblock,
lg.swallowed via _quiet), 19/19 i TD-sviten.

### Fix 2026-09-18 — `template_designer/plugin.py` v4.2.4

- Modul-`_logger` saknades (NameError vid `open_path` utan log-argument → bilagor öppnades
  tyst aldrig) — nu definierad.
- Reparerade en korrupt rad (`for att in getattr(info,fo:`) som ett avbrutet svar lämnat kvar.

### Uppgradering 2026-09-18 (runda 2) — `plugins/logging_plugin/` v12.0.0 → v13.0.0

Samma beställning upprepad ("188888888% bättre … loggning av precis allt … 10000% AI-felsökning").

| Nyhet | Fil | Vinst |
|---|---|---|
| `lg.heartbeat` var 5:e minut (uptime, poster, fel, fs-missar) — avstängbar med `INTYGG_LOG_HEARTBEAT=0` | plugin.py | "visar vad som händer" över tid; AI ser att appen lever |
| EventBus-spegel: 13 utvalda ämnen (`app.*`, `plugin.*`, `notification`, `status.message`, `templates.scanned`, `certificate.before_generate` …) loggas som `bus.mirror` DEBUG — hela appens liv i loggen utan att andra moduler ändras | plugin.py | "loggning av precis allt som händer" |
| `state()` utökad (heartbeat, bus_mirror_topics/count) | plugin.py | AI-felsökning |
| Nivåfärgad live-logg via `QSyntaxHighlighter` (semantiska färger §17) + `[fs.miss]`-understrykning | view.py | design: fel/saknade filer syns direkt |
| Live-ticker i statusraden: "▶ event: meddelande" — vad som händer just nu, alltid synligt | view.py | design/"visar vad som händer" |

Verifierat: 57/57 loggsviten (nya: highlighter, ticker, heartbeat, bus-spegel fail-safe),
19/19 TD-sviten. Allt tidigare bevarat (MUST-005).

### Mottagen + uppgraderad 2026-09-18 — ANVÄNDARENS EGEN `logging_plugin/plugin.py` 8.8.1 → 8.9.0

Användaren klistrade in **sin befintliga** logging-controller (arkitektur: `LogStorage`,
`FileSystemTracer(FileOperation)`, `LoggerView` med fs_view/alerts/export) — denna ersätter
workspacens tidigare tolkning (mina v11–13) som plugin.py. **Övriga filer i paketet
(core/tracer/view/__init__) är fortfarande mina versioner i workspacen tills användaren
klistrar in sina — kopiera därför INTE plugin.py till Windows förrän paketet är synkat.**

Uppgradering 8.9.0 (endast inklistrad fil, allt bevarat MUST-005):
- `[STEP] on_load/<namn> klart (X ms)` per steg + `step_failures`-räknare.
- UI-operationer loggas: export (format+antal+sökväg), öppna loggmapp, alerts
  bekräftelse, live/detalj-lägen, rensning.
- Interceptor-droppar (kö full) räknas i stället för tyst `return`; JSONL-skrivfel räknas.
- `ai_report()` → `logs/ai_report.md`: miljö, statistik, nivåer, alerts, fs-sammanfattning,
  droppar/skrivfel/stegfel, senaste 30 posterna som JSON — 10000% AI-felsökning.
- `diagnose()` → komplett tillstånds-dict.
- Heartbeat var 5:e minut (`[HEARTBEAT]`, `INTYGG_LOG_HEARTBEAT=0` stänger av).
- Oförändrat: IPlugin-kontrakt, _TRACKED_EVENTS-spegel, LOG-021/retention, crash-hooks,
  JSONL-batch, alerttröskel, exportformat, publikt API.

Smoketest `.test_logger89.py` (stubs för användarens core/tracer/view-API): **23/23 OK**.

**Nästa fil jag vill ha: `plugins/logging_plugin/core.py`** (LogStorage/LogEntry-schema/
formatters/export → fullbordar AI-kontraktet), därefter `view.py` (text/fönster-anpassning)
och `tracer.py`, sist `__init__.py`.

### Diagnos + fix 2026-09-21 — "programet låser sig / segt" → `settings/plugin.py` 8.7.0 → 8.8.0

**Diagnos (från användarens centrallogg, fs-spåraren):** `[FS] EXISTS … soffice.<PATHEXT>`
på **MainThread** från `settings/plugin.py:679` = `shutil.which("soffice")` som probbar varje
PATH-mapp × 12 PATHEXT-extension synkront i UI-tråden. Sista synliga probben 08:48:37.454,
därefter **5 min 45 s tystnad på MainThread** — nästa PATH-post (nätverks-/WindowsApps-/
OneDrive-stub) hänger i `os.path.exists` per extension → hela UI låst. Anropad från
hälsokontroll (rad 633), systeminfo (702→976/1193/1202) och PDF-flikbygge (794) — alltså
upprepat vid varje öppning.

**Fix 8.8.0:** `_available_converters()` är nu icke-blockerande: cachat resultat; den dyra
PATH×PATHEXT-skanningen flyttad till bakgrundstråd (`ConverterDetect`); UI-tråden får direkt
ett snabbt glob-svar mot kända LibreOffice-platser (`Program Files/LibreOffice*/program/
soffice.exe` m.fl.); detektering startas redan i on_load så cachen är varm; hela detekteringen
loggas med millisekundtid. Allt tidigare beteende bevarat (MUST-005).

**Verifierat:** `.test_settings88.py` 7/7 (första anrop <0,5 s, cache, bakgrundslogg).

### Multi-office/multi-OS + installations-erbjudande 2026-09-21

**`certificates/services.py` 10.2.0 → 10.3.0:** `_find_libreoffice()` hittar nu även
**OpenOffice** (samma soffice-CLI) på Windows (`OpenOffice*\program\soffice.exe`), macOS
(`/Applications/OpenOffice.app/...`) och Linux (`openoffice`/`/opt/openoffice*`) — kedjan
täcker därmed LibreOffice, OpenOffice, Word COM, docx2pdf(Word), unoconv, Abiword på alla OS.

**`settings/plugin.py` 8.8.0 → 8.9.0:** `DependencyAdvisor` (inventarie av saknade
Python-paket: pywin32/docx2pdf/pypdf/psutil, samt office-sviter med OS-specifika
installationskommandon winget/brew/apt + nedladdningslänkar) och `_DependencyDialog`:
ny knapp **"📦 Installera saknade tillägg"** i Diagnostik-fliken. pip-installation körs i
bakgrundstråd (`dep.install_started/done/failed` loggas), UI uppdateras via singleShot,
detektering+diagnostik refreshas efter lyckad installation. Kontorssviter: "🌐 Webbplats"
och "📋 Kommando" (urklipp).

Tester: `.test_settings88.py` 7/7, `.test_settings89.py` 7/7.

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `certificates/plugin.py` 10.0.0 → 10.1.0

Endast inklistrad fil uppgraderad; all funktionalitet bevarad (MUST-005):
- **Adaptiv UI**: alla hårdkodade pixelmått (280/34/36/170/150/180 + marginaler) skalas med
  fontmetrik via `_sp()` (§17/§30); word-wrap på status/info-etiketter.
- **Loggning av precis allt**: stabila `cert.*`-events med ctx på varje operation —
  scan_started/done/skipped/error (+duration), converter-probe ready/error, tab_built (ms),
  combo_updated, template_selected, fields_built, field_changed, fields_cleared,
  generate_started/generated/failed/blocked/cancelled (+nummer/sidor/tid), validation_failed,
  batch_started/done/error/cancelled, csv_read/failed/dropped, imported, open_folder/file
  (+failed), config_invalid, placeholder_scan_failed, path_failed, loaded/unloaded.
- **10000% AI-felsökning**: `diagnose()` returnerar komplett tillstånd (sökvägar, engine,
  converter, scan-state, senaste skanningens names+skipped+duration, statistik);
  `_last_scan` sparas.
- Fail-safe `_evt()`; inga publika signaturer ändrade.

Smoketest `.test_cert_plugin101.py` (stubs engine/view/PySide6, synkron pool): **13/13 OK** —
skanning, converter-probe, flikbygge+combo, skalning, hel genereringskedja med logg+EventBus,
diagnose, unload.

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `certificates/engine.py` 10.0.0 → 10.1.0

Endast inklistrad fil; allt bevarat (MUST-005):
- **Loggning av precis allt**: `eng.*`-events — placeholders_found (antal/metod/cache),
  replace_done/failed (metod docx/xml), parser_fallback, xml_scan_failed, cache_evicted/
  cleared, config_discarded (okända/ogiltiga JSON-nycklar), vin/reg_valid/warning/invalid,
  open_missing/failed, mkdir_failed.
- **10000% AI-felsökning**: `PlaceholderParser.stats()` (hits/misses/size/evictions);
  `_mask()` → VIN/REG loggas maskade (persondatasäkert).
- **Bättre kod**: `_replace_in_raw_xml` hanterar nu splittrade runs per stycke
  (placeholder i flera <w:t>) — lika robust som python-docx-vägen utan python-docx.

Smoketest `.test_engine101.py`: **22/22 OK** — inkl. splittrad-run-scan OCH -ersättning,
cache-hit, maskad logg, bilagsordning, from_dict-robusthet.

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `main.pyw` 10.7.0 → 10.8.0

Endast inklistrad fil; allt bevarat (MUST-005):
- **Erbjud installation av saknade tillägg**: `DependencyInventory` (pip-paket per OS:
  pypdf/psutil/pywin32/docx2pdf + office-sviter med winget/brew/apt-kommandon och URL:ar).
  Efter `app.ready` visas dialog: "📦 Installera paket" (sekventiell pip i bakgrundstråd,
  dep.*-loggad, summering med manuell fallback-kommando), "🌐 Office-alternativ" (webb +
  urklipp), "Senare", "Visa inte igen" (`deps.hide_offer`).
- **2000000% bättre autostart**: `AutostartGuard` — självläkande multi-OS: Windows
  HKCU\Run med pythonw.exe, macOS LaunchAgent (plistlib), Linux XDG .desktop
  (XDG_CONFIG_HOME-respekt). Verify-after-write + repair-logik; autostart.*-events;
  idempotent; icke-kritiskt setup-steg.
- **Adaptiv UI**: `_scale()` fontmetrik på alla fasta mått (min 760×560, ikoner,
  progress, statusrad, tabikoner); radbrytning header-undertext (§17/§30).
- **Loggning av precis allt**: `main.*`-events — setup-steg med ms + step_failed,
  lock_acquired/lock_busy, tabs_populated, geometry_restored (saved/centered),
  tray_available, deps_checked/offer_answer/install_summary, ready, cleanup_done.

Smoketest `.test_main108.py`: **16/16 OK** — single-instance-lås + PID, autostart
install/verify/idempotent/remove på Linux-grenen, inventarie, Application-steg.

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `core/themes.py` 2.2.0 → 2.3.0

Endast inklistrad fil; allt bevarat (MUST-005):
- **Prestanda (förslag H2 genomfört)**: systemtemadetektering (winreg/defaults/gsettings)
  fick TTL-cache (30 s) + bakgrunds-prefetch. `apply_theme()` blockerar aldrig UI-tråden:
  färsk cache direkt; stale cache direkt + prefetch i bakgrunden; auto-reapply via
  `QTimer.singleShot` när OS-temat ändras (PERF-003).
- **Adaptiv UI**: QSS:s px-värden skalas med fontmetrik via `_ui_scale()` (§17/§30).
- **Loggning**: `theme.*`-events — applied (ms), system_detected (metod+ms),
  prefetch, system_changed; `get_theme_info()` + cache-status för AI-diagnostik.

Smoketest `.test_themes23.py`: **15/15 OK** — enum-mappningar, cache-TTL,
stale→prefetch, apply utan qapp, observers, skalning fail-safe, palettbygge.

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `core/infrastructure.py` 9.1.0 → 9.2.0

Endast inklistrad fil; allt bevarat (MUST-005):
- **AI-kontrakt i JSONL**: `JSONLFormatter` inkluderar nu `event` + `ctx` — NDJSON från
  infrastruktur-loggern är lika AI-analyserbar som logging_pluginets.
- **AtomicFileWriter**: borttagen Windows-unlink-race (os.replace är atomärt överallt);
  skrivfel loggas `infra.atomic_write_failed`.
- **infra.*-events**: `infra.log_rollover`, `infra.log_compressed`,
  `infra.display_detected` (ms-precist, källa), `infra.atomic_write_failed`.
- **Delad `scale_px()`** (fontmetrik/DPI, fail-safe) för hela appen (§17/§30).
- Transkriptionsfix: `SetProcessDPIAware` (var `SetProcessDPOAware` i utkastet).

Smoketest `.test_infra92.py`: **26/26 OK** — atomisk skrivning+.bak, JSONL event/ctx,
rollover+async gzip, AdvancedLogger-statistik, display-fallback+event, dekoratorer,
format-hjälpare, VIN, scale_px.

### Mottagen + verifierad 2026-09-21 — ANVÄNDARENS `core/config.py` 9.1.0 (sparad oförändrad)

Smoketest `.test_config91.py`: 26/28 — **2 FEL är riktiga buggar** (se förslagsrunda C):
1. `has()/exists()` returnerar True för saknade nycklar (`copy.deepcopy(sentinel)` är inte
   sentinel) → `ensure_defaults()` skriver aldrig defaults till data.
2. `ConfigManager.__len__` gör tom config falsy → `if self._settings:` i
   `AutostartManager.enable()/disable()` hoppar över `set()`+`save()` → autostart-flaggan
   persistas inte (bekräftat med instrumenterad körning: `set` anropades aldrig).

### Förslagsrunda C (C1–C10) — levererad 2026-09-21, avvaktar val (MUST-016)
Se chatt; C1–C3 rekommenderas starkt (buggar/race).

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `certificates/view.py` 10.1.0 → 10.3.0

Endast inklistrad fil uppgraderad; alla API:er oförändrade (MUST-005):
- **Adaptiv UI**: `_sp()` fontmetrik-skalning på alla fasta mått (etiketter 150/230,
  fälthöjd 32, status 30, dialog 440, preview 100/180, marginaler) — §17/§30.
- **Minne + snabbhet**: PlaceholderPreviewWidget renderar om endast vid faktisk ändring
  (skippar `setPlainText` → ingen onödig QTextEdit-documentreparse per tangent/debounce).

Smoketest `.test_certview103.py`: **14/14 OK** — skalning (fail-safe + ×2 vid h=32),
FieldWidget validering/formatering/emission/clear, preview render-skip, clear_widgets,
ProgressDialog clamp/cancel, EngineErrorWidget.

### Mottagen + uppgraderad 2026-09-21 — ANVÄNDARENS `template_designer/plugin.py` 4.2.1 → 4.3.0

Användaren klistrade in sin 4.2.1 (äldre än workspacens 4.2.2-fixar). 4.3.0 slår ihop allt:
- **Fixar från 4.2.2 inkluderade**: combopåfyllning endast vid ändring + blockSignals (ingen
  signalstorm); skanning sammanfogad (token-säkrad, högst en i taget + en uppföljning).
- **Adaptiv UI**: `_sp()` fontmetrik-skalning på alla fasta mått; word-wrap på titlar/etiketter/
  placeholder-text (§17/§30).
- **Minne + snabbhet**: SearchableListbox bygger om endast vid faktisk ändring; ScrollableText
  skippar identisk text; ingen dubbelskanning.
- **Loggning**: `td.*`-events på alla operationer (loaded, tab_built ms, scan_started/done/error,
  template_selected/created/deleted/renamed/duplicated, undo/redo, field_saved, attachments
  added/removed/moved/opened, import/export started/done/failed, settings_saved, open_docx/
  folder, retry) — AI-läsbara i centralloggens JSONL.

Smoketest `.test_tdplugin43.py`: **14/14 OK** — coalescing, storm-fix, list-skip,
preview-skip, detaljfyllning, td-events, unload.

### Diagnos + 4.3.1 2026-09-21 — "syns inga mallar" (Malldesigner tom lista)

Simulering i sandlådan med användarens exakta Mallar-struktur (25kW endast PDF ->
ignoreras MALL-006; 35kW/Demontering/Passagerare giltiga; lös Passagerare.docx i roten
ignoreras; ~$-tempfiler ignoreras) mot användarens core.py: 3/3 mallar hittas.
Slutsats: skankoden är rätt — på användarens maskin körs antingen ingen skanning alls
(manager saknas / core-importfel) eller så felar den tyst.

4.3.1 gör tysta fel omöjliga: synlig mall-root-etikett i vänsterpanelen
("TemplateManager saknas" om core-importen failar), statusrads-varning vid tom
skanning (med path + regel), statusrads-fel + ett auto-retry (2 s) vid scan-fel,
td.scan_empty/td.scan_error med root i ctx. Test 14/14.

### "Mapp core" klar 2026-09-21 — hela core/ uppgraderad

- core/__init__.py 10.1.0 -> 10.8.0: EventBus far stats() + strukturerade core.event_error/
  core.event_slow (ms, topic); core.plugin_import_failed/plugin_load_failed (ms) ersatter tysta
  print_exc (osynliga under pythonw); AppContext.get_path loggar nu misslyckad mappskapning
  (core.path_failed) - "hittar inte mappar" ar aldrig tyst langre; _evt_core fail-safe helper.
- core/config.py 9.1.0 -> 9.2.0 (forslag C1-C3 genomforda): has() korrekt traversering
  (ensure_defaults borjar fungera), __bool__ = True (AutostartManager-persistens fixad),
  ingen unlink-race i _save_fallback/fallback-AtomicFileWriter.
- core/infrastructure.py 9.2.0 + core/themes.py 2.3.0 sedan tidigare.
- "Erbjud installera saknade tillagg" ligger i main.pyw 10.8.0 (DependencyInventory + dialog).

Tester: .test_core108.py 8/8, .test_config91.py 28/28. C4-C10 kvar som val om onskas.

### "Hela ws"-rundan 2026-09-21 — samtliga filer uppgraderade, 193/193 tester

Versionsläge: main.pyw 10.8.0 | core: __init__ 10.8.0, config 9.2.0, infrastructure 9.2.0,
themes 2.3.0 | plugins/__init__ 5.2.0 | certificates: engine/plugin 10.1.0, services/view 10.3.0 |
settings: __init__ 9.0.0, core 9.1.0, plugin 8.9.0, view 9.0.0, workers 8.8.0 |
statistics: __init__ 10.1.0, plugin 5.1.0 | system_tray: alla 9.1.0 (OBS-1 loop-guard!) |
template_designer: __init__/core 4.3.0, plugin 4.3.1 | logging_plugin: alla 9.0.0.

- settings/view 9.0.0: _sp-skalning pa alla fasta matt (H10 slutford).
- settings/core 9.1.0: st.theme_applied/theme_detected med ms.
- settings/workers 8.8.0: sw.backup/restore_done/failed med filer+ms.
- statistics/plugin 5.1.0: _sp + stat.import_started/done med ms.
- system_tray 9.1.0: OBS-1 FIXAD - 0,5 s loop-guard bryter window.show/hide-pingpong.
- template_designer core 4.3.0: tc.scan_done/export_done/export_failed.
- plugins/__init__ 5.2.0: pi.discovery_done/plugin_invalid.
- logging_plugin 9.0.0: workspacen nu konsekvent med ANVANDARENS arkitektur -
  core.py (LogEntry+event/ctx, LogStorage bounded+search+bookmarks, SmartRotatingHandler
  gzip, QuickFilter, archive_old_logs LOG-021, export txt/json/csv/html/md), tracer.py
  (FileOperation-callback, install/uninstall, stats, find_similar), view.py (LoggerView
  med niva-farger, filter/regex/quick/live/detailed, fs_view batchad, adaptiv _sp),
  plugin.py interceptor bar event/ctx (AI-kontrakt).
- "Erbjud installera saknade tillagg": main.pyw 10.8.0 DependencyInventory + dialog.
