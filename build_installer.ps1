Write-Host "=== STARTING SYNCRONE-E BUILD PROCESS (FINAL) ===" -ForegroundColor Cyan

# --- CONFIGURATION ---
$7z = "C:\Program Files\7-Zip\7z.exe"
$ResHacker = ".\ResourceHacker.exe"
$SfxModule = ".\7zSD.sfx"
$IconFile = ".\icon.ico"
$OutputExe = "Syncron-E_Logger.exe"
$TempSfx = "loader_branded.sfx"
$ManifestFile = "uac.manifest"

# --- CHECKS ---
if (-not (Test-Path $7z)) { Write-Error "7-Zip not found!"; exit }
if (-not (Test-Path $SfxModule)) { Write-Error "7zSD.sfx not found!"; exit }
if (-not (Test-Path $ResHacker)) { Write-Error "ResourceHacker.exe not found!"; exit }
if (-not (Test-Path "main.dist\main.exe")) { Write-Error "CRITICAL: main.exe not found inside main.dist folder!"; exit }

# --- STEP 1: MANIFEST (Non-Admin Fix) ---
Write-Host "1. Generating Non-Admin Manifest..." -ForegroundColor Yellow
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

# --- STEP 2: BRANDING (Icon + Manifest) ---
Write-Host "2. Injecting Icon and Manifest..." -ForegroundColor Yellow
Copy-Item $SfxModule $TempSfx -Force
# Overwrite Icon (ICONGROUP) and Manifest (24,1)
$args = "-open $TempSfx -save $TempSfx -action addoverwrite -res $IconFile -mask ICONGROUP,MAINICON, -res $ManifestFile -mask 24,1,"
$proc = Start-Process -FilePath $ResHacker -ArgumentList $args -Wait -PassThru
if ($proc.ExitCode -ne 0) { Write-Error "Resource Hacker Failed."; exit }

# --- STEP 3: CONFIGURATION (UTF-8 NO BOM FIX) ---
Write-Host "3. Creating SFX Config (UTF-8 No BOM)..." -ForegroundColor Yellow

# Note: We use 'RunProgram' which is standard for launching EXEs from SFX
$configContent = ";!@Install@!UTF-8!`r`nTitle=`"Syncron-E Clinical Logger`"`r`nProgress=`"yes`"`r`nRunProgram=`"main.exe`"`r`n;!@InstallEnd@!`r`n"

# Use .NET to write strictly without the Byte Order Mark (BOM)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$PWD\config.txt", $configContent, $utf8NoBom)

# --- STEP 4: PAYLOAD ---
Write-Host "4. Compressing Payload..." -ForegroundColor Yellow
if (Test-Path "payload.7z") { Remove-Item "payload.7z" }

Push-Location main.dist
# Ensure we are zipping the contents, not the folder
& $7z a -t7z -mx9 ..\payload.7z * | Out-Null
Pop-Location

# --- STEP 5: MERGE ---
Write-Host "5. Merging Components..." -ForegroundColor Yellow
cmd /c "copy /b $TempSfx + config.txt + payload.7z $OutputExe"

# --- CLEANUP ---
Remove-Item config.txt
Remove-Item payload.7z
Remove-Item $TempSfx
Remove-Item $ManifestFile
Remove-Item "${TempSfx}_original" -ErrorAction SilentlyContinue

Write-Host "==========================================" -ForegroundColor Green
Write-Host "SUCCESS! Created: $OutputExe" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green