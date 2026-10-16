@echo off
rem ===========================================================================
rem  Construit les programmes Windows de Suricate-tracker :
rem    dist\SuricateTracker.exe          version SANS fenetre (utilisation normale, demarrage auto)
rem    dist\SuricateTracker-console.exe  version avec console (diagnostic : --debug, messages en direct)
rem
rem  A lancer sur un PC Windows ayant Python 3.10+ (python.org ; gardez "tcl/tk and IDLE"
rem  coche a l'installation, la fenetre de configuration en a besoin).
rem  Double-cliquez sur ce fichier, ou lancez-le depuis une invite de commandes.
rem  (L'option "nopause" evite la pause finale : utilisee par build_installer.bat.)
rem ===========================================================================
cd /d "%~dp0"

echo [1/4] Environnement de construction...
python -m venv build\venv || goto :error
call build\venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
python -m pip install --upgrade pyinstaller requests || goto :error

echo [2/4] Verification de tkinter...
python -c "import tkinter" || (echo tkinter est absent : reinstallez Python avec l'option "tcl/tk and IDLE". & goto :error)

rem Video de fond de la page du navigateur : embarquee dans les .exe si le fichier existe.
set "ADDDATA="
if exist "%~dp0assets\background.mp4" set ADDDATA=--add-data "%~dp0assets\background.mp4;assets"
if not defined ADDDATA echo ATTENTION : assets\background.mp4 est absent : la page du navigateur n'aura pas de video de fond.

echo [3/4] Construction de SuricateTracker.exe (sans fenetre)...
pyinstaller --noconfirm --clean --onefile --noconsole --name SuricateTracker %ADDDATA% ^
  --specpath build --workpath build\work-gui --distpath dist "%~dp0client.py" || goto :error

echo [4/4] Construction de SuricateTracker-console.exe (diagnostic)...
pyinstaller --noconfirm --onefile --console --name SuricateTracker-console %ADDDATA% ^
  --specpath build --workpath build\work-console --distpath dist "%~dp0client.py" || goto :error

echo.
echo Termine. Programmes crees dans : %~dp0dist
echo   SuricateTracker.exe          (double-clic : fenetre de configuration au premier lancement)
echo   SuricateTracker-console.exe  (diagnostic)
if /I not "%~1"=="nopause" pause
exit /b 0

:error
echo.
echo ERREUR : la construction a echoue (voir les messages ci-dessus).
if /I not "%~1"=="nopause" pause
exit /b 1
