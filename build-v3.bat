@echo off
REM V3 카카오 매니저 — onedir + zip (자동 업데이트용)
setlocal

for /f "tokens=2 delims==" %%a in ('findstr /B "__version__" version.py') do set RAW_VER=%%a
set VER=%RAW_VER:"=%
set VER=%VER: =%

set EXENAME=KakaoManager-V3_v%VER%
set DISTDIR=dist\%EXENAME%
set BUILDDIR=build\%EXENAME%

if exist "%DISTDIR%" (
  echo Cleaning %DISTDIR% ...
  rmdir /s /q "%DISTDIR%"
)
if exist "%BUILDDIR%" (
  echo Cleaning %BUILDDIR% ...
  rmdir /s /q "%BUILDDIR%"
)

echo Building %EXENAME% (onedir)...
pyinstaller --noconfirm KakaoManager-V3.spec
if errorlevel 1 (
  echo 빌드 실패
  exit /b 1
)

set ZIPNAME=%EXENAME%.zip
echo Creating %ZIPNAME% ...
if exist "%ZIPNAME%" del "%ZIPNAME%"
python -c "import shutil; shutil.make_archive('%EXENAME%', 'zip', 'dist/%EXENAME%')"

echo.
if exist "%DISTDIR%\%EXENAME%.exe" (
  echo 빌드 완료: %DISTDIR%\%EXENAME%.exe
  echo zip: %ZIPNAME%
) else (
  echo 빌드 실패 — exe 없음
  exit /b 1
)
