@echo off
REM Activa el entorno virtual del proyecto abriendo una consola con el venv en PATH.

setlocal

set PROJECT_ROOT=%USERPROFILE%\workspace\testrail_sync
set VENV_SCRIPTS=%PROJECT_ROOT%\python_scripts\.venv\Scripts

if not exist "%VENV_SCRIPTS%\python.exe" (
    echo [ERROR] No se encontro "%VENV_SCRIPTS%\python.exe".
    echo Crea primero el virtualenv en python_scripts\.venv, por ejemplo:
    echo   cd %%PROJECT_ROOT%%\python_scripts
    echo   python -m venv .venv
    pause
    exit /b 1
)

REM Poner el venv al inicio del PATH solo para esta nueva consola
set PATH=%VENV_SCRIPTS%;%PATH%

REM Ir a la carpeta de scripts del proyecto
cd /d %PROJECT_ROOT%\python_scripts

echo ==========================================
echo Entorno virtual activado para testrail_sync
echo Ruta del venv: %VENV_SCRIPTS%
echo Ahora puedes usar:
echo   python ...   (usa el python del venv)
echo   pip ...      (pip del venv)
echo ==========================================

REM Abrir una nueva shell CMD con este PATH
cmd /k

endlocal
