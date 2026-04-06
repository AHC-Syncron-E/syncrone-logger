# Syncron-E Waveform Recorder

**Syncron-E Waveform Recorder** is a high-fidelity medical ventilator waveform recorder designed for long-duration (7+ days) clinical studies. It captures serial data from Puritan Bennett 980 ventilators, visualizes it in real-time, and generates 1-hour EDF snapshots.

![License](https://img.shields.io/badge/license-Proprietary-red)
![Platform](https://img.shields.io/badge/platform-Windows_11_(MSIX)-blue)
![Python](https://img.shields.io/badge/python-3.13-yellow)

https://github.com/user-attachments/assets/35190ce7-5202-4cdf-be44-c5994cd247a7

---

## Waveform Fidelity Validation

The Syncron-E Waveform Recorder has been validated for waveform fidelity 
under formal protocol **SYNC-VP-WFR-001** using a PB980 ventilator (S/N 
35B1801059) connected to an Ingmar Medical ASL5000 Breathing Simulator (S/N 
3102, calibration date: 2025-11-03) as the independent reference standard. 
The ASL5000 provides airway-proximal pressure and flow measurements at 512 Hz through internal sensors independent of the PB980's measurement system.

**Waveform Recorder version tested:** v1.0.0.61

**Acceptance criterion:** NRMSE <= 10% for all channels across all modes.

### Fidelity Results (Runs 1-3)

| Run | Mode | Pearson r (Pressure) | Pearson r (Flow) | NRMSE (Pressure) | NRMSE (Flow) | Verdict |
|-----|------|---------------------|-----------------|------------------|-------------|---------|
| 1 | PSV (SPONT PS) | ~0.999 | ~0.992 | ~6.2% | ~3.3% | **PASS** |
| 2 | PCV (A/C PC) | ~0.999 | ~0.993 | ~5.0% | ~2.3% | **PASS** |
| 3 | VCV (A/C VC) | ~0.978 | ~0.990 | ~6.4% | ~2.9% | **PASS** |

All channels across all three supported ventilation modes pass the acceptance criterion, with actual NRMSE values ranging from 2.3% to 6.4%.

### NRMSE Acceptance Criterion Rationale

The 10% NRMSE threshold is conservative engineering judgment. NRMSE normalizes the root mean square error against the signal's full dynamic range. Relevant ineffective effort detection features (e.g., flow deflection >= 5.0 L/min per Chen et al. 2008) are substantially larger than the noise floor implied by the achieved NRMSE values within typical ventilator flow ranges. The actual values (2-7%) provide additional margin.

### Reproducible Analysis

The validation analysis scripts are available in the [`validation/`](validation/) directory. These marimo notebooks load the raw reference data and reproduce the fidelity metrics, alignment diagnostics, and summary plots. See [`validation/README.md`](validation/README.md) for instructions on running the analysis and requesting raw data.

---

## EDF Output Format Specification

The Syncron-E Waveform Recorder produces **EDF+** (European Data Format) files with the following structure:

| Property | Value |
|----------|-------|
| **Format** | EDF+ |
| **Sampling rate** | 50 Hz (derived from PB980 20 ms sample interval) |
| **Signal 1** | Pressure -- physical dimension: cmH2O |
| **Signal 2** | Flow -- physical dimension: L/min |
| **Annotations** | Breath boundaries (BS/BE markers with sequence numbers and ventilation mode strings) |

Files are generated as rolling 1-hour snapshots during recording. Each file is self-contained and can be loaded directly into Syncron-E for analysis.

The CSV format specification for Syncron-E input files is documented in the Syncron-E Instructions for Use, Appendix 1.

---

## PB840/PB980 Compatibility

Waveform fidelity validation was performed on the **PB980** ventilator. The PB840 uses a similar serial communication protocol with the same BS/BE breath-delimited ASCII waveform payload format at 50 Hz. PB840 compatibility is expected based on the shared serial protocol, and is supported by the successful use of PB840 serial port waveform data in published research (Adams et al., Scientific Reports, 2017;7:14980) .

---

## Deployment & Installation

The application is deployed as a **Signed MSIX Package**, ensuring clean installs/uninstalls and containment within the Windows App container.

### For Clinical Users
1. Download `Syncron-E_x.x.x.msix` from the **Releases** tab.
2. Double-click the file to install via Windows App Installer.
   * *Note:* Requires the "Autonomous Healthcare" Trusted Signing certificate to be present on the machine if not installed from the Store/Intune.

---

## Development Setup

### Prerequisites
* **Windows 11** (Recommended for Nuitka/MSIX compatibility)
* **Python 3.13**
* **1Password CLI (`op`)** (Required for Telemetry/Watchdog)

### Local Environment
1. Clone the repository:
   ```bash
   git clone https://github.com/AHC-Syncron-E/syncrone-logger.git
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

## Build Architecture

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

## Features & Status

| Feature | Status | Implementation Details |
| --- | --- | --- |
| **Serial Capture** | Done | Dual-port (Waveform @ 38400, Settings @ 9600) |
| **Visualization** | Done | Real-time PyQtGraph (Software Rasterization) |
| **Storage (Raw)** | Done | SQLite Batch Insert (One row per sample) |
| **Storage (Export)** | Done | Rolling 1-Hour EDF Files (edfio) |
| **Breath Detection** | Done | Automatic sequence tracking & duration calc |
| **Telemetry** | Done | Out-of-process Sidecar (`watchdog.py`) |
| **Crash Safety** | Done | Auto-restart & SQLite WAL journal persistence |

## Data Output Convention

Session data is stored in:
`Desktop\Syncron-E Data\Session_Databases\syncrone_{PatientID}_{Timestamp}.db`
