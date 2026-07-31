@echo off
echo ========================================
echo STEP 1: Cleaning Old Environment
echo ========================================
echo.
echo This will remove the local conda environment at backend/Credit_assessment\.conda
echo (or the package-root .conda folder).
echo.
echo Press any key to continue or Ctrl+C to cancel...
pause > nul

cd /d "%~dp0..\.."

echo.
echo Removing conda env at %CD%\.conda ...
conda env remove -p "%CD%\.conda" -y

if %errorlevel% neq 0 (
    echo.
    echo WARNING: conda env remove reported an error (env may not exist).
)

echo.
echo Cleaning conda cache...
conda clean --all -y

echo.
echo ========================================
echo Clean complete. Run install_env.bat next.
echo ========================================
pause
