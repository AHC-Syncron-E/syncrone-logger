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
    return np, pd


@app.cell
def _():
    import wfr_utils as wfr

    return (wfr,)


@app.cell
def _(mo):
    mo.md(r"""
    # Run 2 — PC A/C — Waveform Fidelity Analysis

    **Protocol:** SYNC-VP-WFR-001 | **Ventilator:** PB980 (S/N 35B1801059, SW 8.4.5.2)
    | **Reference:** ASL 5000 (S/N 3102, SW 3.6, 512 Hz)

    **Mode:** Pressure Control Assist/Control (PC A/C) — mandatory pressure-targeted
    breaths at a set respiratory rate.  The ventilator delivers a square pressure
    waveform during inspiration, producing clear pressure peaks for alignment.

    **Acceptance criteria:**

    | Metric | Threshold |
    |--------|-----------|
    | Pearson r (pressure) | >= 0.95 |
    | Pearson r (flow) | >= 0.95 |
    | NRMSE pressure | <= 10 % of reference range |
    | NRMSE flow | <= 10 % of reference range |
    """)
    return


@app.cell
def _(wfr):
    _dtb_path, _rwb_path, _db_path = wfr.find_run_files(2)
    df_dtb = wfr.load_asl_data(_dtb_path, _rwb_path)
    df_syncrone = wfr.load_syncrone_db(_db_path)
    return df_dtb, df_syncrone


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
    _fig = wfr.plot_raw_syncrone(df_syncrone, " — Run 2 (PC A/C)")
    mo.vstack([mo.md("## Syncron-E Raw Waveforms"), _fig])
    return


@app.cell
def _(df_dtb, df_syncrone, wfr):
    # Run 2 alignment parameters
    # Signature disconnect at ~110 sec ASL time; Sync offset ~84 sec
    alignment = wfr.align_signals(
        df_dtb,
        df_syncrone,
        asl_t_min=125.0,          # after signature disconnect (~110 sec)
        sync_t_naive_min=50.0,    # skip pre-overlap region
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
    mo.md(f"""## Waveform Fidelity Results — Run 2 (PC A/C)

| Signal | Pearson r | NRMSE | RMSE | Ref Range | Mean Error | Result |
|--------|-----------|-------|------|-----------|------------|--------|
{_table_body}

**Thresholds:** Pearson r >= 0.95, NRMSE <= 10 % of ASL 5000 reference range

**Note on measurement geometry:** The ASL 5000 measures at the patient airway
(independent reference sensor) while the PB980 Waveform Recorder captures at
the ventilator outlet.  In PC A/C mode, mandatory pressure-targeted breaths
produce large peak flows (> 100 L/min) with rapid transitions at inspiration
onset, where the circuit compliance difference is most pronounced.  NRMSE
appropriately scales these transient errors against the full flow dynamic range.
    """)
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_overlay(alignment.df_aligned, title_suffix=" — Run 2 (PC A/C)")
    mo.vstack([mo.md("## Waveform Overlay — Pressure & V̇"), _fig])
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_error_series(alignment.df_aligned, title_suffix=" — Run 2")
    mo.vstack([mo.md("## Error Time Series"), _fig])
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_error_histograms(alignment.df_aligned, title_suffix=" — Run 2")
    mo.vstack([mo.md("## Error Distributions"), _fig])
    return


@app.cell
def _(alignment, mo, wfr):
    _fig = wfr.plot_bland_altman(alignment.df_aligned, title_suffix=" — Run 2")
    mo.vstack([mo.md("## Bland-Altman Plots"), _fig])
    return


if __name__ == "__main__":
    app.run()
