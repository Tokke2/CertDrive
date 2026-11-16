#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)

while :; do
    PYTHON_BIN=
    for candidate in python3 python; do
        candidate_path=$(command -v "$candidate" 2>/dev/null || true)
        if [ -n "$candidate_path" ] && "$candidate_path" -c 'import sys' >/dev/null 2>&1; then
            PYTHON_BIN=$candidate_path
            break
        fi
    done

    if [ -n "$PYTHON_BIN" ]; then
        break
    fi

    printf '%s\n' 'Python hittades inte. IntygsDrive behöver Python för att starta.'
    printf '%s' 'Vill du öppna den officiella sidan för senaste Python? [j/N] '
    IFS= read -r answer || exit 0
    case "$answer" in
        j|J|ja|JA|Ja|y|Y|yes|YES|Yes) ;;
        *) exit 0 ;;
    esac

    case "$(uname -s 2>/dev/null || printf unknown)" in
        Darwin)
            DOWNLOAD_URL='https://www.python.org/downloads/macos/'
            if command -v open >/dev/null 2>&1; then
                open "$DOWNLOAD_URL" || printf 'Öppna sidan manuellt: %s\n' "$DOWNLOAD_URL"
            else
                printf 'Öppna sidan manuellt: %s\n' "$DOWNLOAD_URL"
            fi
            ;;
        *)
            DOWNLOAD_URL='https://www.python.org/downloads/'
            if command -v xdg-open >/dev/null 2>&1; then
                xdg-open "$DOWNLOAD_URL" >/dev/null 2>&1 || printf 'Öppna sidan manuellt: %s\n' "$DOWNLOAD_URL"
            elif command -v gio >/dev/null 2>&1; then
                gio open "$DOWNLOAD_URL" >/dev/null 2>&1 || printf 'Öppna sidan manuellt: %s\n' "$DOWNLOAD_URL"
            else
                printf 'Öppna sidan manuellt: %s\n' "$DOWNLOAD_URL"
            fi
            ;;
    esac

    printf '%s' 'Installera Python och tryck sedan Enter för att försöka igen (Ctrl+C avslutar): '
    IFS= read -r _ || exit 0
done

exec "$PYTHON_BIN" "$SCRIPT_DIR/main.pyw" "$@"
