@echo off
echo Packaging main.dist into a Single Executable...

:: 1. Define paths
set SEVENZIP="C:\Program Files\7-Zip\7z.exe"

:: 2. Create the configuration file for the SFX
echo ;!@Install@!UTF-8! > config.txt
echo Title="Syncron-E Clinical Logger" >> config.txt
echo Progress="yes" >> config.txt
echo RunProgram="main.exe" >> config.txt
echo ;!@InstallEnd@! >> config.txt

:: 3. Zip the contents of main.dist
cd main.dist
%SEVENZIP% a -t7z ..\payload.7z *
cd ..

:: 4. Merge the SFX Module + Config + Archive into one EXE
:: CHANGED: Now looks for 7zSD.sfx in the current folder, not Program Files
if not exist 7zSD.sfx (
    echo ERROR: 7zSD.sfx not found in current directory!
    pause
    exit /b
)

copy /b 7zSD.sfx + config.txt + payload.7z "Syncron-E_Logger.exe"

:: 5. Cleanup
del config.txt
del payload.7z

echo.
echo DONE! Created Syncron-E_Logger.exe
pause