@echo off
echo ========================================
echo STEP 1: Cleaning Old Environment
echo ========================================
echo.
echo This will remove the corrupted conda environment
echo Location: C:\Users\gopik\Downloads\agri_credit_pipeline\.conda
echo.
echo Press any key to continue or Ctrl+C to cancel...
pause > nul

echo.
echo Deactivating any active conda environment...
call conda deactivate 2>nul

echo.
echo Removing old environment (this may take a few minutes)...
conda env remove -p C:\Users\gopik\Downloads\agri_credit_pipeline\.conda -y

echo.
echo ========================================
echo Environment Cleanup Complete!
echo ========================================
echo.
echo Next step: Run install_env.bat to create fresh environment
echo.
pause
