# Syncron-E Clinical Data Logger

**Syncron-E Clinical Data Logger** is a PySide6-based application for logging clinical data from PB 980 ventilators. It is packaged as a self-extracting executable designed for restricted hospital IT environments (Windows 10/11).

![License](https://img.shields.io/badge/license-Proprietary-red)
![Platform](https://img.shields.io/badge/platform-Windows_x64-blue)

## 🚀 Quick Start (For Users)
1. Download `Syncron-E Waveform Recorder.exe` from the **Releases** tab.
2. Double-click to run.
   * *Note:* No Administrator privileges are required.
   * *Note:* The app extracts momentarily (displaying a progress bar) before launching.

---

## 🛠️ Development Setup

### Prerequisites
* **Windows 10/11**
* **Python 3.13** (Anaconda recommended)
* **7-Zip** (Installed in default path)
* **Visual C++ Redistributable** (Latest x64)

### Installation
1. Clone the repository:
    ```bash
    git clone [https://github.com/AutonomousHealthcare/syncrone-logger.git](https://github.com/AutonomousHealthcare/syncrone-logger.git)
    cd syncrone-logger
    
    ```

2. Create the environment:
    ```bash
    conda env create -f environment.yml
    conda activate syncrone-logger
    
    ```


3. Run locally:
    ```bash
    python main.py
    
    ```



---

## 📦 Build & Release

We use a 3-stage build process to ensure reliability on "Fresh" Windows installs.

### 1. Compile (Nuitka)

Compiles Python to a standalone folder (`main.dist`) containing all DLLs.

```powershell
# Run Nuitka manually or via CI
python -m nuitka --standalone `
  --enable-plugin=pyside6 `
  --include-module=PySide6.QtOpenGL `
  --include-module=PySide6.QtOpenGLWidgets `
  --windows-icon-from-ico=icon.ico `
  --windows-console-mode=disable `
  main.py

```

### 2. Package (SFX)

Wraps the `main.dist` folder into a single `.exe` using 7-Zip SFX and fixes UAC permissions.

```powershell
.\build_installer.ps1

```

* **Requires:** `7zSD.sfx`, `ResourceHacker.exe`, `icon.ico`.
* **Output:** `Syncron-E Waveform Recorder.exe`

### 3. Sign (Azure)

Signs the binary for Windows security trust.

* **Local:** Use `setup_signing.ps1` to download tools, then run `signtool`.
* **CI:** Handled automatically by GitHub Actions.

---

## ✅ TODO / Status

* [x] Create Non-Admin Self-Extracting Archive
* [x] Implement Azure Trusted Signing
* [ ] Wwaveform parsing and plotting
* [ ] Settings request, parsing, and displaying ventilation mode
* [ ] Auto-detection of waveform cable and settings cable
* [ ] Dedicated folder for 1-hr EDF file dump at 5-minute update intervals
* [ ] SQLite database for continuous storage of waveform and settings payloads
