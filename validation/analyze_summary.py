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
    return (pd,)


@app.cell
def _():
    import wfr_utils as wfr

    return (wfr,)


@app.cell
def _(mo):
    mo.md(r"""
    # Aggregate Waveform Fidelity — Runs 1–3

    **Protocol:** SYNC-VP-WFR-001 | **Ventilator:** PB980 (S/N 35B1801059, SW 8.4.5.2)
    | **Reference:** ASL 5000 (S/N 3102, SW 3.6, 512 Hz)

    Combines the three core ventilation modes to assess overall recording fidelity
    of the Syncron-E Waveform Recorder v1.0.0.61 serial-port capture path:

    | Run | Mode | Paradigm |
    |-----|------|----------|
    | 1 | SPONT PS | Pressure Support Ventilation (PSV) |
    | 2 | PC A/C | Pressure-Controlled Ventilation (PCV) |
    | 3 | VC A/C | Volume-Controlled Ventilation (VCV) |

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
    # Run 1 — SPONT PS
    _dtb1, _rwb1, _db1 = wfr.find_run_files(1)
    _df_asl1 = wfr.load_asl_data(_dtb1, _rwb1)
    _df_sync1 = wfr.load_syncrone_db(_db1)
    alignment_r1 = wfr.align_signals(
        _df_asl1, _df_sync1,
        asl_t_min=170.0, sync_t_naive_min=58.0,
    )
    return (alignment_r1,)


@app.cell
def _(wfr):
    # Run 2 — PC A/C
    _dtb2, _rwb2, _db2 = wfr.find_run_files(2)
    _df_asl2 = wfr.load_asl_data(_dtb2, _rwb2)
    _df_sync2 = wfr.load_syncrone_db(_db2)
    alignment_r2 = wfr.align_signals(
        _df_asl2, _df_sync2,
        asl_t_min=125.0, sync_t_naive_min=50.0,
    )
    return (alignment_r2,)


@app.cell
def _(wfr):
    # Run 3 — VC A/C (with Savitzky-Golay smoothing for servo noise)
    _dtb3, _rwb3, _db3 = wfr.find_run_files(3)
    _df_asl3 = wfr.load_asl_data(_dtb3, _rwb3)
    _df_sync3 = wfr.load_syncrone_db(_db3)
    alignment_r3 = wfr.align_signals(
        _df_asl3, _df_sync3,
        asl_t_min=60.0, sync_t_naive_min=36.0,
        smooth_sync_window=11,
    )
    return (alignment_r3,)


@app.cell
def _(alignment_r1, alignment_r2, alignment_r3, wfr):
    fidelity_r1 = wfr.compute_run_fidelity(alignment_r1.df_aligned)
    fidelity_r2 = wfr.compute_run_fidelity(alignment_r2.df_aligned)
    fidelity_r3 = wfr.compute_run_fidelity(alignment_r3.df_aligned)
    return fidelity_r1, fidelity_r2, fidelity_r3


@app.cell
def _(alignment_r1, alignment_r2, alignment_r3, mo):
    _runs = [
        ("Run 1 — SPONT PS", alignment_r1),
        ("Run 2 — PC A/C", alignment_r2),
        ("Run 3 — VC A/C", alignment_r3),
    ]
    _rows = []
    for _label, _a in _runs:
        _rows.append(
            f"| {_label} | {_a.n_matched_peaks} | "
            f"{_a.clock_drift_pct:+.2f} % | "
            f"{_a.transport_delay_ms:+.0f} ms | "
            f"{_a.peak_warped_r:.4f} | "
            f"{_a.overlap_duration_sec:.0f} sec ({len(_a.df_aligned):,} samples) |"
        )
    _table_body = "\n".join(_rows)
    mo.md(f"""## Alignment Summary

    | Run | Matched Peaks | Clock Drift | Transport Delay | Warped r | Overlap |
    |-----|---------------|-------------|-----------------|----------|---------|
    {_table_body}
    """)
    return


@app.cell
def _(fidelity_r1, fidelity_r2, fidelity_r3, mo):
    _run_fidelities = [
        ("Run 1 — SPONT PS", fidelity_r1),
        ("Run 2 — PC A/C", fidelity_r2),
        ("Run 3 — VC A/C", fidelity_r3),
    ]
    _rows = []
    for _label, _fid in _run_fidelities:
        for _f in _fid:
            _rows.append(
                f"| {_label} | {_f.signal} | {_f.pearson_r:.4f} | {_f.nrmse_pct:.2f} % "
                f"| {_f.rmse:.3f} | {_f.ref_range:.1f} "
                f"| {_f.mean_error:+.3f} | **{_f.result_str}** |"
            )
    _table_body = "\n".join(_rows)
    mo.md(f"""## Per-Run Fidelity

    | Run | Signal | Pearson r | NRMSE | RMSE | Ref Range | Mean Error | Result |
    |-----|--------|-----------|-------|------|-----------|------------|--------|
    {_table_body}

    **Thresholds:** Pearson r >= 0.95, NRMSE <= 10 % of ASL 5000 reference range
    """)
    return


@app.cell
def _(alignment_r1, alignment_r2, alignment_r3, pd):
    df_pooled = pd.concat(
        [alignment_r1.df_aligned, alignment_r2.df_aligned, alignment_r3.df_aligned],
        ignore_index=True,
    )
    return (df_pooled,)


@app.cell
def _(df_pooled, wfr):
    fidelity_pooled = wfr.compute_run_fidelity(df_pooled)
    return (fidelity_pooled,)


@app.cell
def _(
    alignment_r1,
    alignment_r2,
    alignment_r3,
    df_pooled,
    fidelity_pooled,
    mo,
):
    _n_total = len(df_pooled)
    _n1 = len(alignment_r1.df_aligned)
    _n2 = len(alignment_r2.df_aligned)
    _n3 = len(alignment_r3.df_aligned)
    _rows = []
    for _f in fidelity_pooled:
        _rows.append(
            f"| {_f.signal} | {_f.pearson_r:.4f} | {_f.nrmse_pct:.2f} % "
            f"| {_f.rmse:.3f} | {_f.ref_range:.1f} "
            f"| {_f.mean_error:+.3f} | **{_f.result_str}** |"
        )
    _table_body = "\n".join(_rows)
    mo.md(f"""## Pooled Fidelity — All Modes Combined

    Concatenated aligned samples from all three runs:
    {_n1:,} (Run 1) + {_n2:,} (Run 2) + {_n3:,} (Run 3) = **{_n_total:,} total samples**

    | Signal | Pearson r | NRMSE | RMSE | Ref Range | Mean Error | Result |
    |--------|-----------|-------|------|-----------|------------|--------|
    {_table_body}

    **Thresholds:** Pearson r >= 0.95, NRMSE <= 10 % of ASL 5000 reference range

    The pooled reference range reflects the widest dynamic range seen across all
    three ventilation modes.  Because NRMSE normalises against this combined range,
    the pooled NRMSE may differ from the arithmetic mean of per-run NRMSEs.
    """)
    return


@app.cell
def _(alignment_r1, mo, wfr):
    _fig = wfr.plot_overlay(alignment_r1.df_aligned, title_suffix=" — Run 1 (SPONT PS)")
    mo.vstack([mo.md("### Run 1 — SPONT PS"), _fig])
    return


@app.cell
def _(alignment_r2, mo, wfr):
    _fig = wfr.plot_overlay(alignment_r2.df_aligned, title_suffix=" — Run 2 (PC A/C)")
    mo.vstack([mo.md("### Run 2 — PC A/C"), _fig])
    return


@app.cell
def _(alignment_r3, mo, wfr):
    _fig = wfr.plot_overlay(alignment_r3.df_aligned, title_suffix=" — Run 3 (VC A/C)")
    mo.vstack([mo.md("### Run 3 — VC A/C"), _fig])
    return


@app.cell
def _(df_pooled, mo, wfr):
    _fig = wfr.plot_bland_altman(df_pooled, title_suffix=" — Pooled (Runs 1–3)")
    mo.vstack([mo.md("## Bland-Altman — Pooled"), _fig])
    return


@app.cell
def _(df_pooled, mo, wfr):
    _fig = wfr.plot_error_histograms(df_pooled, title_suffix=" — Pooled (Runs 1–3)")
    mo.vstack([mo.md("## Error Distributions — Pooled"), _fig])
    return


@app.cell
def _(fidelity_pooled, fidelity_r1, fidelity_r2, fidelity_r3, mo):
    _all_results = fidelity_r1 + fidelity_r2 + fidelity_r3
    _all_pass = all(_f.overall_pass for _f in _all_results)
    _pooled_pass = all(_f.overall_pass for _f in fidelity_pooled)

    if _all_pass and _pooled_pass:
        _verdict = (
            "All per-run **and** pooled fidelity metrics meet acceptance criteria.  "
            "The Syncron-E Waveform Recorder serial-port capture path provides "
            "high-fidelity waveform data suitable for asynchrony analysis across "
            "all three core ventilation paradigms (PSV, PCV, VCV)."
        )
    else:
        _failing = [
            f"{_f.signal}" for _f in _all_results + fidelity_pooled
            if not _f.overall_pass
        ]
        _verdict = (
            f"**Attention:** The following metrics did not meet thresholds: "
            f"{', '.join(_failing)}.  Review the per-run results above for details."
        )

    mo.md(f"""## Conclusion

    {_verdict}
    """)
    return


if __name__ == "__main__":
    app.run()
