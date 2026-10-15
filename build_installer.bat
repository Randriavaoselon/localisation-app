@echo off
rem ===========================================================================
rem  Fabrique l'assistant d'installation : installers\WifiTracker-Setup.exe
rem
rem  Prerequis sur ce PC Windows :
rem    - Python 3.10+ (python.org, avec "tcl/tk and IDLE" coche)
rem    - Inno Setup 6 (gratuit : https://jrsoftware.org/isdl.php)
rem
rem  Usage :  build_installer.bat https://mon-serveur.onrender.com
rem           (2e argument facultatif "nopause" : pas de pause finale, pour GitHub Actions)
rem           (l'adresse est pre-remplie dans l'assistant ; l'utilisateur peut la modifier)
rem ===========================================================================
cd /d "%~dp0"

set "SERVER_URL=%~1"
if "%SERVER_URL%"=="" set /p SERVER_URL=Adresse de votre serveur (ex. https://mon-serveur.onrender.com) : 
if "%SERVER_URL%"=="" goto :nourl

echo.
echo === Etape 1 : construction de WifiTracker.exe ===
call build_windows.bat nopause || goto :error

echo.
echo === Etape 2 : recherche d'Inno Setup ===
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" goto :noinno

echo.
echo === Etape 3 : construction de l'assistant d'installation ===
"%ISCC%" "/DServerUrl=%SERVER_URL%" installer.iss || goto :error

echo.
echo Termine : %~dp0installers\WifiTracker-Setup.exe
echo Etape suivante : publiez ce fichier avec le serveur (voir le README), par exemple :
echo     git add installers/WifiTracker-Setup.exe ^&^& git commit -m "Installateur Windows" ^&^& git push
if /I not "%~2"=="nopause" pause
exit /b 0

:nourl
echo Adresse du serveur manquante. Exemple : build_installer.bat https://mon-serveur.onrender.com
if /I not "%~2"=="nopause" pause
exit /b 1

:noinno
echo Inno Setup 6 est introuvable. Installez-le (https://jrsoftware.org/isdl.php) puis relancez ce script.
if /I not "%~2"=="nopause" pause
exit /b 1

:error
echo.
echo ERREUR : la fabrication a echoue (voir les messages ci-dessus).
if /I not "%~2"=="nopause" pause
exit /b 1
