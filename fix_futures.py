from pathlib import Path

ROOT = Path(__file__).parent
FUTURE_LINE = "from __future__ import annotations\n"


def fix_file(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8-sig")
        lines   = content.splitlines(keepends=True)

        future_indices = [
            i for i, ln in enumerate(lines)
            if ln.strip() == "from __future__ import annotations"
        ]
        if not future_indices:
            return False

        first_future = future_indices[0]

        allowed_before: list[str] = []
        for ln in lines[:first_future]:
            stripped = ln.strip()
            if stripped.startswith("#!"):
                allowed_before.append(ln)

        if first_future == len(allowed_before):
            return False

        remaining = [
            ln for i, ln in enumerate(lines)
            if i not in future_indices
        ]

        new_lines: list[str] = list(allowed_before)
        new_lines.append(FUTURE_LINE)
        new_lines.extend(remaining)

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(new_lines), encoding="utf-8")
        tmp.replace(path)

        print(f"  FIXAD: {path.relative_to(ROOT)}")
        return True

    except Exception as exc:
        print(f"  FEL ({path.name}): {exc}")
        return False


def main() -> None:
    print(f"Skannar: {ROOT}\n")
    fixed   = 0
    skipped = 0

    for ext in ("*.py", "*.pyw"):
        for path in sorted(ROOT.rglob(ext)):
            if "__pycache__" in path.parts:
                continue
            if fix_file(path):
                fixed += 1
            else:
                skipped += 1

    print(f"\nKlart: {fixed} fixade, {skipped} redan korrekta")
    print("Kör nu: python main.pyw")


if __name__ == "__main__":
    main()