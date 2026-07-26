@echo off
echo Opening Windows Firewall port 8765 for Label Studio...
powershell -Command "New-NetFirewallRule -DisplayName 'Label Studio Server (Port 8765)' -Direction Inbound -LocalPort 8765 -Protocol TCP -Action Allow"
if %errorlevel% neq 0 (
    echo.
    echo ERROR: You must right-click this file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)
echo.
echo Success! Your team can now connect to http://192.168.1.83:8765
pause
