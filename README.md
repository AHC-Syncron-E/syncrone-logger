# Syncron-E Waveform Recorder

**Syncron-E Waveform Recorder** is a high-fidelity medical ventilator waveform recorder designed for long-duration (7+ days) clinical studies. It captures serial data from Puritan Bennett 980 ventilators, visualizes it in real-time, and generates 1-hour EDF snapshots.

![License](https://img.shields.io/badge/license-Proprietary-red)
![Platform](https://img.shields.io/badge/platform-Windows_11_(MSIX)-blue)
![Python](https://img.shields.io/badge/python-3.13-yellow)

https://github.com/user-attachments/assets/35190ce7-5202-4cdf-be44-c5994cd247a7

## 🚀 Deployment & Installation

The application is deployed as a **Signed MSIX Package**, ensuring clean installs/uninstalls and containment within the Windows App container.

### For Clinical Users
1. Download `Syncron-E_x.x.x.msix` from the **Releases** tab.
2. Double-click the file to install via Windows App Installer.
   * *Note:* Requires the "Autonomous Healthcare" Trusted Signing certificate to be present on the machine if not installed from the Store/Intune.

---

## 🛠️ Development Setup

### Prerequisites
* **Windows 11** (Recommended for Nuitka/MSIX compatibility)
* **Python 3.13**
* **1Password CLI (`op`)** (Required for Telemetry/Watchdog)

### Local Environment
1. Clone the repository:
   ```bash
   git clone [https://github.com/AutonomousHealthcare/syncrone-logger.git](https://github.com/AutonomousHealthcare/syncrone-logger.git)
   cd syncrone-logger

   ```

2. Install dependencies (Strict Pinned Versions):
   ```bash
   pip install -r requirements.txt
   
   ```

   *Note: We strictly pin `PySide6==6.10.1` and `Nuitka==2.8.9` to avoid memory leak regressions.*

3. Run the Recorder (Main App):
   ```bash
   python main.py
   
   ```


4. Run the Watchdog (Telemetry):
*Requires 1Password CLI configured for the `REDACTED_PROJECT` project.*
   ```powershell
   # Injects WANDB_API_KEY environment variable at runtime
   op run -- python watchdog.py
   
   ```



---

## 📦 Build Architecture

We do not use `pyinstaller` or generic wrappers. We use **Nuitka** for AOT compilation followed by **MSIX** packaging.

### 1. Compile (Nuitka)

Compiles Python to a standalone folder (`main.dist`) with aggressive optimization and no bytecode.

* **Config:** `main.py` (Header handles stdout/stderr redirection for frozen state).
* **Memory Safety:** Object pooling is enforced to prevent Nuitka+Shiboken wrapper leaks.

### 2. Package (MSIX)

Wraps the compiled binaries into a Windows Container.

* **Manifest:** Generates `AppxManifest.xml` with `runFullTrust` capability.
* **Workflow:** `.github/workflows/build_msix.yml`

### 3. Sign (Azure Trusted Signing)

All binaries (`.exe`, `.dll`, `.pyd`) and the final `.msix` are recursively signed via Azure Trusted Signing in the CI/CD pipeline.

---

## ✨ Features & Status

| Feature | Status | Implementation Details |
| --- | --- | --- |
| **Serial Capture** | ✅ | Dual-port (Waveform @ 38400, Settings @ 9600) |
| **Visualization** | ✅ | Real-time PyQtGraph (Software Rasterization) |
| **Storage (Raw)** | ✅ | SQLite Batch Insert (One row per sample) |
| **Storage (Export)** | ✅ | Rolling 1-Hour EDF Files (edfio) |
| **Breath Detection** | ✅ | Automatic sequence tracking & duration calc |
| **Telemetry** | ✅ | Out-of-process Sidecar (`watchdog.py`) |
| **Crash Safety** | ✅ | Auto-restart & SQLite WAL journal persistence |

## 📂 Data Output Convention

Session data is stored in:
`Desktop\Syncron-E Data\Session_Databases\syncrone_{PatientID}_{Timestamp}.db`

