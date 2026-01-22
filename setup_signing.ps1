# setup_signing.ps1
$PkgUrl = "https://www.nuget.org/api/v2/package/Microsoft.Trusted.Signing.Client/1.0.60"
$ZipFile = "signing_tools.zip"
$ExtractPath = ".\SigningTools"

# 1. Download Azure Signing Client (Nuget Package is just a Zip)
Write-Host "Downloading Azure Trusted Signing Client..."
Invoke-WebRequest -Uri $PkgUrl -OutFile $ZipFile

# 2. Extract
Expand-Archive -Path $ZipFile -DestinationPath $ExtractPath -Force

# 3. Locate the Dlib (x64)
$DlibPath = "$ExtractPath\bin\x64\Azure.CodeSigning.Dlib.dll"
if (Test-Path $DlibPath) {
    Write-Host "SUCCESS: Dlib found at: $DlibPath" -ForegroundColor Green
} else {
    Write-Error "Could not find Azure.CodeSigning.Dlib.dll"
}

# Cleanup
Remove-Item $ZipFile