# IntygsDrive

## Starta appen

Använd startfilen för operativsystemet:

- Windows: `Starta IntygsDrive.bat`
- macOS: dubbelklicka `Starta IntygsDrive.command`
- Linux: kör `./Starta IntygsDrive.sh` från terminalen

Startfilen letar tyst efter en körbar Python-installation. Finns Python startar
IntygsDrive direkt och ingen Python-installationsfråga visas. Saknas Python
frågar startfilen om den ska öppna Pythons officiella nedladdningssida. Den
installerar eller uppdaterar inte Python i bakgrunden; användaren väljer och
kör själv installationsprogrammet. Efter installationen trycker användaren
Enter i startfönstret så kontrollen och appstarten görs om.

Python uppdateras inte automatiskt och ingen schemalagd Python-uppdatering
skapas. Den fristående filen `Installera senaste Python.bat` ingår inte.
