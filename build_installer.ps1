Write-Host "=== STARTING SYNCRONE-E BUILD PROCESS (SPACES + ICON FIX) ===" -ForegroundColor Cyan

# --- CONFIGURATION ---
$7z = "C:\Program Files\7-Zip\7z.exe"
$ResHacker = ".\ResourceHacker.exe"
$SfxModule = ".\7zSD.sfx"
$IconFile = ".\icon.ico"
# CHANGED: Now using spaces
$OutputExe = "Syncron-E CD Logger.exe"
$TempSfx = "loader_branded.sfx"
$ManifestFile = "uac.manifest"

# --- CHECKS ---
if (-not (Test-Path $7z)) { Write-Error "7-Zip not found!"; exit }
if (-not (Test-Path $SfxModule)) { Write-Error "7zSD.sfx not found!"; exit }
if (-not (Test-Path $ResHacker)) { Write-Error "ResourceHacker.exe not found!"; exit }
if (-not (Test-Path "main.dist\main.exe")) { Write-Error "CRITICAL: main.exe not found! Run Nuitka first."; exit }

# --- STEP 1: PREPARE FILES ---
Write-Host "1. Preparing Files..." -ForegroundColor Yellow
Copy-Item $SfxModule $TempSfx -Force

$manifestContent = @"
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="1.0.0.0" processorArchitecture="*" name="SyncronE" type="win32"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>
"@
Set-Content -Path $ManifestFile -Value $manifestContent

# --- STEP 2: NUKE OLD ICONS AND INJECT NEW ONE ---
Write-Host "2. Nuking Icon ID 1 and Injecting New Icon..." -ForegroundColor Yellow

# Pass 1: DELETE generic icon (ID 1)
Start-Process -FilePath $ResHacker -ArgumentList "-open `"$TempSfx`" -save `"$TempSfx`" -action delete -mask ICONGROUP,1," -Wait

# Pass 2: DELETE ID 101
Start-Process -FilePath $ResHacker -ArgumentList "-open `"$TempSfx`" -save `"$TempSfx`" -action delete -mask ICONGROUP,101," -Wait

# Pass 3: ADD icon as ID 1
$procIcon = Start-Process -FilePath $ResHacker -ArgumentList "-open `"$TempSfx`" -save `"$TempSfx`" -action addoverwrite -res `"$IconFile`" -mask ICONGROUP,1," -Wait -PassThru

# Pass 4: Overwrite Manifest
$procMan = Start-Process -FilePath $ResHacker -ArgumentList "-open `"$TempSfx`" -save `"$TempSfx`" -action addoverwrite -res `"$ManifestFile`" -mask 24,1," -Wait -PassThru

if ($procIcon.ExitCode -ne 0) { Write-Error "Failed to add Icon."; exit }

# --- STEP 3: CONFIGURATION (UTF-8 NO BOM) ---
Write-Host "3. Creating SFX Config..." -ForegroundColor Yellow
$configContent = ";!@Install@!UTF-8!`r`nTitle=`"Syncron-E CD Logger`"`r`nProgress=`"yes`"`r`nRunProgram=`"main.exe`"`r`n;!@InstallEnd@!`r`n"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$PWD\config.txt", $configContent, $utf8NoBom)

# --- STEP 4: PAYLOAD ---
Write-Host "4. Compressing Payload..." -ForegroundColor Yellow
if (Test-Path "payload.7z") { Remove-Item "payload.7z" }
Push-Location main.dist
& $7z a -t7z -mx9 ..\payload.7z * | Out-Null
Pop-Location

# --- STEP 5: MERGE ---
Write-Host "5. Merging Components..." -ForegroundColor Yellow
# FIX: Added explicit quotes around filenames for CMD
cmd /c "copy /b `"$TempSfx`" + config.txt + payload.7z `"$OutputExe`""

# --- CLEANUP ---
Remove-Item config.txt
Remove-Item payload.7z
Remove-Item $TempSfx
Remove-Item $ManifestFile
Remove-Item "${TempSfx}_original" -ErrorAction SilentlyContinue

Write-Host "==========================================" -ForegroundColor Green
Write-Host "SUCCESS! Created: $OutputExe" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green