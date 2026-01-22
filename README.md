nuitka --onefile --standalone --enable-plugin=pyside6 --windows-disable-console main.py


nuitka --onefile --standalone --enable-plugin=pyside6 --include-module=PySide6.QtOpenGL --include-module=PySide6.QtOpenGLWidgets --windows-icon-from-ico=icon.ico --include-data-file="C:\Users\tsphan-g6\PycharmProjects\syncrone-logger\vcruntime140.dll=vcruntime140.dll" --include-data-file="C:\Users\tsphan-g6\PycharmProjects\syncrone-logger\msvcp140.dll=msvcp140.dll" --include-data-file="C:\Users\tsphan-g6\PycharmProjects\syncrone-logger\vcruntime140_1.dll=vcruntime140_1.dll" main.py

nuitka --onefile --standalone --enable-plugin=pyside6 --include-module=PySide6.QtOpenGL --include-module=PySide6.QtOpenGLWidgets --windows-icon-from-ico=icon.ico --windows-disable-console --include-data-file="vcruntime140_1.dll=vcruntime140_1.dll" main.py


$env:AZURE_CLIENT_SECRET = "0s.8Q~vCfT12PJ8HV08Lot9BY6nXAzOQjq2Pka_e"
$env:AZURE_TENANT_ID = "4db11efe-d3c7-455c-a10e-9ff4a40c6e01"
$env:AZURE_CLIENT_ID = "7b724bf7-7f63-491b-81cc-96fecedb206b"

syncrone-logger/
├── 7zSD.sfx
├── README.md
├── ResourceHacker.exe
├── ResourceHacker.ini
├── build_installer.ps1
├── icon.ico
├── metadata.json
├── msvcp140.dll
├── package_sfx.bat
├── patch_uac.py
├── setup_signing.ps1
├── vcruntime140.dll
└── vcruntime140_1.dll
