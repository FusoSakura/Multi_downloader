@echo off
REM ============================================================
REM  FETCH 서버 실행기 (Windows)
REM  - UTF-8 코드페이지로 한글 출력 정상화
REM  - 스크립트 위치로 자동 이동 (더블클릭 대응)
REM  - 종료 후 pause로 에러 메시지 확인 가능
REM ============================================================

chcp 65001 > nul
cd /d "%~dp0"

echo ============================================
echo  FETCH server starting...
echo  Working dir: %CD%
echo ============================================
echo.

python app.py

echo.
echo ============================================
echo  Process exited with code %errorlevel%
echo ============================================
pause
