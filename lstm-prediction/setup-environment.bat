@echo off
chcp 65001 >nul
echo ========================================
echo LSTM预测服务 - Python环境设置
echo ========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] 检测到Python环境
python --version
echo.

REM 检查是否在lstm-prediction目录
if not exist "server.py" (
    echo [错误] 请在lstm-prediction目录中运行此脚本
    pause
    exit /b 1
)

echo [OK] 当前目录正确
echo.

REM 创建虚拟环境
if not exist "venv" (
    echo [1/4] 正在创建Python虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo [OK] 虚拟环境创建成功
) else (
    echo [1/4] 虚拟环境已存在，跳过创建
)
echo.

REM 激活虚拟环境
echo [2/4] 正在激活虚拟环境...
call venv\Scripts\activate
if errorlevel 1 (
    echo [错误] 虚拟环境激活失败
    pause
    exit /b 1
)
echo [OK] 虚拟环境激活成功
echo.

REM 升级pip
echo [3/4] 正在升级pip...
python -m pip install --upgrade pip
echo.

REM 安装依赖
echo [4/4] 正在安装Python依赖包...
echo 这可能需要几分钟时间...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo [OK] 依赖安装成功
echo.

echo ========================================
echo 环境设置完成！
echo ========================================
echo.
echo 启动LSTM预测服务:
echo   python server.py
echo.
echo 或使用conda (如果有):
echo   conda run -n base python server.py
echo.
echo 如需退出虚拟环境，请输入: deactivate
echo ========================================
pause
