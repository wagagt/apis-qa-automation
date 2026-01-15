@echo off
REM ============================================================
REM  Dry-run de la sincronización Newman -> TestRail (SOLO PREVIEW)
REM  No crea ni modifica nada en TestRail.
REM ============================================================

setlocal

REM PROJECT_ROOT = carpeta donde vive este .bat
set "PROJECT_ROOT=%~dp0"

REM Normalizar (por si hay espacios, dejamos siempre comillas al usar)
set "PY_ENV=%PROJECT_ROOT%python_scripts\.venv\Scripts\python.exe"
set "SCRIPT=%PROJECT_ROOT%python_scripts\sync_newman_to_testrail.py"
set "COLLECTION=%PROJECT_ROOT%newman\BPXAPI.postman_collection.json"
set "CONFIG=%PROJECT_ROOT%config.json"

REM --- Checks básicos -----------------------------------------

if not exist "%PY_ENV%" (
    echo [ERROR] No se encontro el Python del entorno virtual:
    echo        "%PY_ENV%"
    echo Asegurate de haber creado el virtualenv en:
    echo        %PROJECT_ROOT%python_scripts\.venv
    echo Ejemplo:
    echo   cd %PROJECT_ROOT%python_scripts
    echo   python -m venv .venv
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] No se encontro el script de sincronizacion:
    echo        "%SCRIPT%"
    exit /b 1
)

if not exist "%COLLECTION%" (
    echo [ERROR] No se encontro la Postman collection:
    echo        "%COLLECTION%"
    exit /b 1
)

if not exist "%CONFIG%" (
    echo [ERROR] No se encontro config.json:
    echo        "%CONFIG%"
    echo Crea config.json (puedes partir de config.sample.json)
    echo con tus credenciales de TestRail y project_id.
    exit /b 1
)

REM --- Ejecutar DRY-RUN ---------------------------------------

echo ===========================================================
echo  Ejecutando DRY-RUN de sync_newman_to_testrail.py
echo  Collection : "%COLLECTION%"
echo  Config     : "%CONFIG%"
echo  Script     : "%SCRIPT%"
echo ===========================================================
echo.

"%PY_ENV%" "%SCRIPT%" --collection "%COLLECTION%" --config "%CONFIG%" --dry-run
set "EXITCODE=%ERRORLEVEL%"

echo.
echo -----------------------------------------------------------
if not "%EXITCODE%"=="0" (
    echo [ERROR] El script devolvio codigo %EXITCODE%.
    echo Revisa la salida anterior para mas detalles.
) else (
    echo [OK] DRY-RUN completado sin errores.
    echo Revisa el listado de secciones/folders/casos que se mostraron.
)
echo -----------------------------------------------------------

endlocal & exit /b %EXITCODE%
