# Waveform Fidelity Validation -- Analysis Scripts

This directory contains the reproducible analysis scripts used to generate the waveform fidelity validation results for the Syncron-E Waveform Recorder under protocol **SYNC-VP-WFR-001**.

## Scripts

| Script | Description |
|--------|-------------|
| `wfr_utils.py` | Shared utility module: ASL5000 binary loaders (.dtb/.rwb), Syncron-E SQLite loader, three-step time alignment (signature-event detection, peak-to-peak clock drift correction, transport delay optimization), fidelity metrics (Pearson r, NRMSE, RMSE, Bland-Altman), and publication-quality plotting. |
| `analyze_run1.py` | Run 1 (SPONT PS / PSV) -- marimo notebook. Loads Run 1 data, performs alignment, computes per-channel fidelity metrics, generates diagnostic plots. |
| `analyze_run2.py` | Run 2 (A/C PC / PCV) -- marimo notebook. Same workflow as Run 1 for pressure-controlled ventilation. |
| `analyze_run3.py` | Run 3 (A/C VC / VCV) -- marimo notebook. Includes Savitzky-Golay smoothing (window=11, polyorder=3) for PB980 servo-loop quantisation noise in volume-controlled modes. |
| `analyze_summary.py` | Aggregate summary -- marimo notebook. Loads Runs 1-3, produces combined fidelity table, pooled Bland-Altman plots, error histograms, and auto-generated pass/fail verdicts. |

## Running the Analysis

These scripts are [marimo](https://marimo.io/) reactive notebooks. To run them:

1. Install marimo: `pip install marimo`
2. Install dependencies: `pip install numpy scipy pandas matplotlib edfio`
3. Place the raw data files in the expected directory (see below).
4. Run a notebook: `marimo run analyze_run1.py`

Or open in the marimo editor: `marimo edit analyze_run1.py`

## Raw Data

The analysis scripts require raw reference data files that are not included in this repository due to their size (binary waveform files). The required data includes:

- **ASL5000 reference files:** `.dtb` (pressure) and `.rwb` (flow) binary files for each run, recorded at 512 Hz by the ASL5000 Breathing Simulator (S/N 3102).
- **Syncron-E Waveform Recorder databases:** SQLite `.db` files for each run, containing the PB980 serial port data captured by the Waveform Recorder (v1.0.0.61).

**To request raw data for independent verification**, contact Autonomous Healthcare Inc. at support@autonomoushealthcare.com. Please reference protocol SYNC-VP-WFR-001.

## Equipment Identification

| Equipment | Model | Serial Number | Notes |
|-----------|-------|---------------|-------|
| Ventilator | Puritan Bennett 980 | 35B1801059 | Waveform output via serial port (38400 bps, ASCII, 50 Hz) |
| Breathing Simulator | Ingmar Medical ASL5000 | 3102 | Calibration date: 2024-06-06. Internal sensors at 512 Hz. |
| USB-to-Serial Adapter | Prolific chipset | -- | Connected to PB980 rear serial port |
