@echo off
chcp 936 >nul
title Ship Fire Simulation
color 0A

echo.
echo  ========================================
echo    Ship Fire Simulation - Quick Start
echo  ========================================
echo.

set "ROOT=%~dp0"

:: ====== Check Environment ======
echo [Check] Node.js ...
where node >nul 2>&1
if errorlevel 1 (echo [Error] Node.js not found & pause & exit /b 1)

echo [Check] pnpm ...
where pnpm >nul 2>&1
if errorlevel 1 (echo [Error] pnpm not found & pause & exit /b 1)

set "HAS_PYTHON=0"
set "PYTHON_CMD="
REM Check Anaconda Python first
if exist "D:\Anaconda\python.exe" (
    set "HAS_PYTHON=1"
    set "PYTHON_CMD=D:\Anaconda\python.exe"
    echo [Check] Python found: D:\Anaconda\python.exe
    goto PYTHON_FOUND
)
REM Try system python
where python >nul 2>&1
if not errorlevel 1 (
    set "HAS_PYTHON=1"
    set "PYTHON_CMD=python"
    echo [Check] Python found
    goto PYTHON_FOUND
)
echo [Warn] Python not found, LSTM will be skipped
:PYTHON_FOUND

echo.

:: ====== Install Dependencies ======
if not exist "%ROOT%node_modules\" (
    echo [1/3] Installing frontend dependencies...
    cd /d "%ROOT%"
    pnpm install
    echo.
) else (
    echo [1/3] Frontend dependencies OK
)

if not exist "%ROOT%backend\node_modules\" (
    echo [2/3] Installing backend dependencies...
    cd /d "%ROOT%backend"
    pnpm install
    echo.
) else (
    echo [2/3] Backend dependencies OK
)

if "%HAS_PYTHON%"=="1" (
    echo [Check] LSTM dependencies...
    if "%PYTHON_CMD%"=="" set "PYTHON_CMD=python"
    "%PYTHON_CMD%" -c "import torch; import flask; import numpy" >nul 2>&1
    if errorlevel 1 (
        echo [3/3] Installing LSTM dependencies...
        cd /d "%ROOT%lstm-prediction"
        "%PYTHON_CMD%" -m pip install -r requirements.txt -q
        echo.
    ) else (
        echo [OK] LSTM dependencies OK
    )
)
if "%HAS_PYTHON%"=="0" echo [3/3] Skip LSTM check

echo.
echo  ----------------------------------------
echo    Starting services...
echo  ----------------------------------------
echo.

:: Kill existing processes on ports
echo [Clean] Checking for existing services...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3001') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5173') do taskkill /F /PID %%a >nul 2>&1
timeout /t 1 >nul

:: Start MySQL
echo [Check] MySQL service ...
sc query wampmysqld >nul 2>&1
if errorlevel 1 goto MYSQL_DIRECT_START
sc query wampmysqld | find "RUNNING" >nul
if errorlevel 1 goto MYSQL_START_SERVICE
echo [OK] MySQL already running
goto MYSQL_CONNECTION_CHECK

:MYSQL_DIRECT_START
echo [Warn] wampmysqld service not found, trying direct start...
start "" /min "C:\mysql-5.7.30-win32\bin\mysqld.exe" --console
timeout /t 3 >nul
goto MYSQL_CONNECTION_CHECK

:MYSQL_START_SERVICE
echo [Start] MySQL service (wampmysqld) ...
net start wampmysqld >nul 2>&1
if errorlevel 1 goto MYSQL_DIRECT_START
echo [OK] MySQL service started

:MYSQL_CONNECTION_CHECK

:: Check MySQL connection
timeout /t 2 >nul
"C:\mysql-5.7.30-win32\bin\mysql.exe" -uroot -e "SELECT 1" >nul 2>&1
if errorlevel 1 (
    echo [Error] MySQL connection failed! Please check MySQL configuration.
    pause
    exit /b 1
)
echo [OK] MySQL connection verified

:: Check database exists
"C:\mysql-5.7.30-win32\bin\mysql.exe" -uroot -e "USE boat_fire_system" >nul 2>&1
if errorlevel 1 (
    echo [Init] Database not found, initializing...
    cd /d "%ROOT%backend"
    call npm run init-db
    if errorlevel 1 (
        echo [Error] Database initialization failed!
        pause
        exit /b 1
    )
    echo [OK] Database initialized
) else (
    echo [OK] Database exists
)
echo.

:: Start Backend
echo [Start] Backend API on port 3001 ...
cd /d "%ROOT%backend"
start "Backend-3001" cmd /k "cd /d %ROOT%backend & pnpm run dev"

:: Start LSTM
if "%HAS_PYTHON%"=="1" (
    if "%PYTHON_CMD%"=="" set "PYTHON_CMD=python"
    echo [Start] LSTM Prediction on port 5000 ...
    if "%PYTHON_CMD%"=="python" (
        start "LSTM-5000" cmd /k "cd /d %ROOT%lstm-prediction & python server.py"
    ) else (
        start "LSTM-5000" cmd /k "cd /d %ROOT%lstm-prediction & %PYTHON_CMD% server.py"
    )
)

:: Wait
timeout /t 3 >nul

:: Start Frontend
echo [Start] Frontend on port 5173 ...
start "Frontend-5173" cmd /k "cd /d %ROOT% & pnpm run dev"

echo.
echo  ========================================
echo    All services started!
echo  ========================================
echo.
echo    Frontend:  http://localhost:5173
echo    Backend:   http://localhost:3001
echo    LSTM:      http://localhost:5000
echo.
echo    Close each window to stop service.
echo.
echo  Opening browser in 5s ...
timeout /t 5 >nul
start http://localhost:5173
echo.
pause
