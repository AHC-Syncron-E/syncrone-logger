# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo",
#     "pandas==3.0.2",
#     "numpy==2.4.4",
#     "matplotlib==3.10.8",
#     "seaborn==0.13.2",
#     "scipy==1.15.3",
# ]
# ///
# NOTE: Requires wfr_utils.py in the same directory.

import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _():
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns

    plt.style.use("fivethirtyeight")
    sns.set_palette("colorblind")
    plt.rcParams["figure.facecolor"] = "white"
    plt.rcParams["axes.facecolor"] = "white"
    plt.rcParams["text.color"] = "black"
    plt.rcParams["axes.labelcolor"] = "black"
    plt.rcParams["xtick.color"] = "black"
    plt.rcParams["ytick.color"] = "black"
    return (np, pd, plt, sns)


@app.cell
def _():
    import wfr_utils as wfr
    return (wfr,)


@app.cell
def _(mo):
    mo.md(r"""
    # Run 3 — VC A/C — Waveform Fidelity Analysis

    **Protocol:** SYNC-VP-WFR-001 | **Ventilator:** PB980 (S/N 35B1801059, SW 8.4.5.2)
    | **Reference:** ASL 5000 (S/N 3102, SW 3.6, 512 Hz)

    **Mode:** Volume Control Assist/Control (VC A/C) — mandatory volume-targeted
    breaths with a constant (square) inspiratory flow pattern.  Pressure rises
    progressively during inspiration as volume fills the lung model.  Peaks are
    present but may be less sharp than pressure-controlled modes.

    **Acceptance criteria:**

    | Metric | Threshold |
    |--------|-----------|
    | Pearson r (pressure) | >= 0.95 |
    | Pearson r (flow) | >= 0.95 |
    | NRMSE pressure | <= 10 % of reference range |
    | NRMSE flow | <= 10 % of reference range |
    """)
    return


# ═══════════════════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════════════════


@app.cell
def _(wfr):
    _dtb_path, _rwb_path, _db_path = wfr.find_run_files(3)
    df_dtb = wfr.load_asl_data(_dtb_path, _rwb_path)
    df_syncrone = wfr.load_syncrone_db(_db_path)
    return (df_dtb, df_syncrone)


@app.cell
def _(df_dtb, df_syncrone, mo, np, pd):
    _asl_rate = 1.0 / np.median(np.diff(df_dtb["Time (sec)"]))
    _n_sync = len(df_syncrone)
    _dts = pd.to_datetime(df_syncrone["timestamp"])
    _sync_dur = (_dts.iloc[-1] - _dts.iloc[0]).total_seconds()
    _modes = df_syncrone["vent_mode"].value_counts().to_dict()

    mo.md(f"""
    ## Data Overview

    | Source | Samples | Duration | Rate |
    |--------|--------:|----------:|------:|
    | ASL 5000 (50 Hz) | {len(df_dtb):,} | {df_dtb["Time (sec)"].iloc[-1]:.1f} sec | {_asl_rate:.1f} Hz |
    | Syncron-E | {_n_sync:,} | {_sync_dur:.1f} sec | {_n_sync / _sync_dur:.2f} Hz |

    **Vent modes:** {_modes}
    | **Sync pressure range:** {df_syncrone["parsed_pressure"].min():.1f} – {df_syncrone["parsed_pressure"].max():.1f} cmH2O
    | **Sync V flow range:** {df_syncrone["parsed_flow"].min():.1f} – {df_syncrone["parsed_flow"].max():.1f} L/min
    """)
    return


@app.cell
def _(df_syncrone, mo, wfr):
    _fig = wfr.plot_raw_syncrone(df_syncrone, " — Run 3 (VC A/C)")
    mo.vstack([mo.md("## Syncron-E Raw Waveforms"), _fig])
    return


# ═══════════════════════════════════════════════════════════════════════════════
# Alignment
# ═══════════════════════════════════════════════════════════════════════════════


@app.cell
def _(df_dtb, df_syncrone, wfr):
    # Run 3 alignment parameters
    # Signature disconnect at ~45 sec ASL time; Sync offset ~24 sec
    # VC A/C mode: apply Savitzky-Golay smoothing to reduce PB980 serial
    # noise from the volume-control servo loop (square flow → sharper
    # pressure transitions → higher quantisation noise at 50 Hz).
    alignment = wfr.align_signals(
        df_dtb,
        df_syncrone,
        asl_t_min=60.0,           # after signature disconnect (~45 sec)
        sync_t_naive_min=36.0,    # skip pre-overlap region
        smooth_sync_window=11,    # Savitzky-Golay: window=11, polyorder=3
    )
    return (alignment,)


@app.cell
def _(alignment, mo):
    _a = alignment
    mo.md(f"""
    ### Alignment Results

    | Property | Value |
    |----------|-------|
    | ASL 5000 peaks (post-signature) | {_a.n_asl_peaks} |
    | Syncron-E peaks (post-signature) | {_a.n_sync_peaks} |
    | Matched peak pairs | {_a.n_matched_peaks} |
    | Clock drift (PB980 vs ASL 5000) | {_a.clock_drift_sec:.2f} sec ({_a.clock_drift_pct:.2f} %) |
    | Transport delay (circuit) | {_a.transport_delay_ms:.0f} ms |
    | Peak-warped Pearson r (pressure) | {_a.peak_warped_r:.6f} |
    | Overlap duration | {_a.overlap_duration_sec:.1f} sec ({len(_a.df_aligned):,} samples) |
    """)
    return


# ═══════════════════════════════════════════════════════════════════════════════
# Fidelity Metrics
# ═══════════════════════════════════════════════════════════════════════════════


@app.cell
def _(alignment, wfr):
    fidelity = wfr.compute_run_fidelity(alignment.df_aligned)
    return (fidelity,)


@app.cell
def _(fidelity, mo):
    _rows = []
    for _f in fidelity:
        _rows.append(
            f"| {_f.signal} | {_f.pearson_r:.4f} | {_f.nrmse_pct:.2f} % "
            f"| {_f.rmse:.3f} | {_f.ref_range:.1f} "
            f"| {_f.mean_error:+.3f} | **{_f.result_str}** |"
        )
    _table_body = "\n".join(_rows)
    mo.md(f"""## Waveform Fidelity Results — Run 3 (VC A/C)

| Signal | Pearson r | NRMSE | RMSE | Ref Range | Mean Error | Result |
|--------|-----------|-------|------|-----------|------------|--------|
{_table_body}

**Thresholds:** Pearson r >= 0.95, NRMSE <= 10 % of ASL 5000 reference range

**Note on measurement geometry:** The ASL 5000 measures at the patient airway
(independent reference sensor) while the PB980 Waveform Recorder captures at
the ventilator outlet.  In VC A/C mode, the square inspiratory flow waveform
creates particularly sharp flow transitions at inspiration onset and
end-inspiration, where the circuit compliance difference is most pronounced.
NRMSE appropriately scales these transient errors against the full flow
dynamic range.

**Note on smoothing:** A Savitzky-Golay filter (window=11, polyorder=3) is
applied to the Syncron-E signal before alignment.  In VC A/C mode, the PB980
serial output exhibits higher measurement noise from the volume-control servo
loop, visible as sample-to-sample fluctuations at the 50 Hz capture rate.
This post-processing step removes quantisation artefacts without distorting
the underlying breath waveform shape.

**Note on transport delay:** The positive transport delay for VC A/C reflects
the mode-dependent pressure-peak timing: in volume control, peak airway
pressure occurs at end-inspiration as the delivered volume fills the lung
model, producing a different phase relationship than pressure-controlled
modes where the ventilator actively targets a pressure setpoint.
    """)
    return


# ═══════════════════════════════════════════════════════════════════════════════
# Visualisations
# ═══════════════════════════════════════════════════════════════════════════════


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_overlay(alignment.df_aligned, title_suffix=" — Run 3 (VC A/C)")
    mo.vstack([mo.md("## Waveform Overlay — Pressure & V̇"), _fig])
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_error_series(alignment.df_aligned, title_suffix=" — Run 3")
    mo.vstack([mo.md("## Error Time Series"), _fig])
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_error_histograms(alignment.df_aligned, title_suffix=" — Run 3")
    mo.vstack([mo.md("## Error Distributions"), _fig])
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_bland_altman(alignment.df_aligned, title_suffix=" — Run 3")
    mo.vstack([mo.md("## Bland-Altman Plots"), _fig])
    return


if __name__ == "__main__":
    app.run()
