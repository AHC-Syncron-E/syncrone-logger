"""Waveform Recorder analysis utilities.

Shared loaders, alignment, metrics, and plotting functions for
Syncron-E Waveform Recorder verification against ASL 5000 reference data.

Used by ``analyze_run{1..5}.py`` marimo notebooks.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, resample_poly, savgol_filter
from scipy.stats import pearsonr


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

# Base directory for validation analysis data files.
# Set SYNCRONE_VALIDATION_DATA environment variable to override.
BASE_DIR = Path(
    os.environ.get(
        "SYNCRONE_VALIDATION_DATA",
        str(Path(__file__).resolve().parent.parent / "validation_data"),
    )
)

_HEADER_SIZE = 500
_DTB_NUM_COLS = 16
_RWB_NUM_COLS = 13

DTB_COLUMNS: list[str] = [
    "Breath Number",
    "Compressed Volume (mL)",
    "Airway Pressure (cmH2O)",
    "Muscle Pressure (cmH2O)",
    "Total Volume (mL)",
    "Total Flow (L/min)",
    "Chamber 1 Pressure (cmH2O)",
    "Chamber 2 Pressure (cmH2O)",
    "Chamber 1 Volume (mL)",
    "Chamber 2 Volume (mL)",
    "Chamber 1 Flow (L/min)",
    "Chamber 2 Flow (L/min)",
    "Avg Chamber Pressure (cmH2O)",
    "Total Volume (mL) [dup]",
    "Total Flow (L/min) [dup]",
    "Airway Pressure (cmH2O) [dup]",
]

RWB_COLUMNS: list[str] = [
    "Time (sec)",
    "Airway Pressure (cmH2O)",
    "Muscle Pressure (cmH2O)",
    "Tracheal Pressure (cmH2O)",
    "Chamber 1 Volume (L)",
    "Chamber 2 Volume (L)",
    "Total Volume (L)",
    "Chamber 1 Pressure (cmH2O)",
    "Chamber 2 Pressure (cmH2O)",
    "Breath File Number (#)",
    "Aux 1 (V)",
    "Aux 2 (V)",
    "Oxygen Sensor (V)",
]

_NEAREST_COLS = {"Breath Number", "Breath File Number (#)"}


# ═══════════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class AlignmentResult:
    """Results from the three-step alignment procedure."""

    df_aligned: pd.DataFrame
    n_asl_peaks: int
    n_sync_peaks: int
    n_matched_peaks: int
    clock_drift_sec: float
    clock_drift_pct: float
    transport_delay_ms: float
    peak_warped_r: float
    overlap_duration_sec: float


@dataclass
class FidelityResult:
    """Waveform fidelity metrics for a single signal."""

    signal: str
    pearson_r: float
    p_value: float
    rmse: float
    nrmse_pct: float
    ref_range: float
    mean_error: float
    max_abs_error: float
    r_threshold: float
    nrmse_threshold_pct: float

    @property
    def r_pass(self) -> bool:
        return self.pearson_r >= self.r_threshold

    @property
    def nrmse_pass(self) -> bool:
        return self.nrmse_pct <= self.nrmse_threshold_pct

    @property
    def overall_pass(self) -> bool:
        return self.r_pass and self.nrmse_pass

    @property
    def result_str(self) -> str:
        return "PASS" if self.overall_pass else "FAIL"


# ═══════════════════════════════════════════════════════════════════════════════
# File discovery
# ═══════════════════════════════════════════════════════════════════════════════


def find_run_files(
    run_number: int,
    base_dir: Path = BASE_DIR,
) -> tuple[Path, Path, Path]:
    """Locate ASL .dtb, .rwb, and Syncron-E .db files for a given run.

    Parameters
    ----------
    run_number : int
        Run number (1–5).
    base_dir : Path
        Root of the SYNC-VP-WFR-001 directory.

    Returns
    -------
    tuple[Path, Path, Path]
        (dtb_path, rwb_path, db_path)

    Raises
    ------
    FileNotFoundError
        If any required file is missing.
    """
    asl_dir = base_dir / "ASL" / f"run{run_number}"
    dtb_path = asl_dir / f"run{run_number}.dtb"
    rwb_path = asl_dir / f"run{run_number}.rwb"

    if not dtb_path.exists():
        raise FileNotFoundError(f"DTB file not found: {dtb_path}")
    if not rwb_path.exists():
        raise FileNotFoundError(f"RWB file not found: {rwb_path}")

    wr_dir = base_dir / "WaveformRecorder"
    dbs = sorted(wr_dir.glob(f"syncrone_run{run_number}_*.db"))
    dbs = [d for d in dbs if d.name.endswith(".db")]
    if not dbs:
        raise FileNotFoundError(
            f"No Syncron-E DB found for run {run_number} in {wr_dir}"
        )

    return dtb_path, rwb_path, dbs[0]


# ═══════════════════════════════════════════════════════════════════════════════
# ASL 5000 loaders
# ═══════════════════════════════════════════════════════════════════════════════


def load_dtb(filepath: str | Path) -> pd.DataFrame:
    """Load an ASL 5000 .dtb binary file into a DataFrame.

    Parameters
    ----------
    filepath : str | Path
        Path to the .dtb file.

    Returns
    -------
    pd.DataFrame
        Waveform data with 16 columns at 512 Hz (no time column).
    """
    raw = Path(filepath).read_bytes()[_HEADER_SIZE:]
    n_rows = len(raw) // (4 * _DTB_NUM_COLS)
    arr = (
        np.frombuffer(raw[: n_rows * _DTB_NUM_COLS * 4], dtype=">f4")
        .astype(np.float64)
        .reshape(n_rows, _DTB_NUM_COLS)
    )
    return pd.DataFrame(arr, columns=DTB_COLUMNS)


def load_rwb(filepath: str | Path) -> pd.DataFrame:
    """Load an ASL 5000 .rwb binary file into a DataFrame.

    Parameters
    ----------
    filepath : str | Path
        Path to the .rwb file.

    Returns
    -------
    pd.DataFrame
        Waveform data with 13 columns at 512 Hz.
    """
    raw = Path(filepath).read_bytes()[_HEADER_SIZE:]
    n_rows = len(raw) // (4 * _RWB_NUM_COLS)
    arr = (
        np.frombuffer(raw[: n_rows * _RWB_NUM_COLS * 4], dtype=">f4")
        .astype(np.float64)
        .reshape(n_rows, _RWB_NUM_COLS)
    )
    return pd.DataFrame(arr, columns=RWB_COLUMNS)


def load_asl_data(
    dtb_path: str | Path,
    rwb_path: str | Path,
) -> pd.DataFrame:
    """Load and downsample ASL 5000 data from .dtb and .rwb files.

    Loads both files, attaches the .rwb time vector to the .dtb data,
    and downsamples from 512 Hz to 50 Hz.

    Parameters
    ----------
    dtb_path : str | Path
        Path to the .dtb file.
    rwb_path : str | Path
        Path to the .rwb file.

    Returns
    -------
    pd.DataFrame
        Downsampled (50 Hz) DTB data with time column prepended.
    """
    df_dtb = load_dtb(dtb_path)
    df_rwb = load_rwb(rwb_path)
    assert len(df_dtb) == len(df_rwb), (
        f"Row count mismatch: dtb={len(df_dtb)}, rwb={len(df_rwb)}"
    )
    df_dtb.insert(0, "Time (sec)", df_rwb["Time (sec)"].values)
    return downsample_512_to_50(df_dtb)


# ═══════════════════════════════════════════════════════════════════════════════
# Downsampling
# ═══════════════════════════════════════════════════════════════════════════════


def downsample_512_to_50(
    df: pd.DataFrame,
    time_col: str = "Time (sec)",
) -> pd.DataFrame:
    """Downsample a 512 Hz DataFrame to 50 Hz via polyphase resampling.

    Uses ``scipy.signal.resample_poly(x, up=25, down=256)`` with
    built-in anti-aliasing FIR filter.  Categorical columns use
    nearest-neighbor mapping.

    Parameters
    ----------
    df : pd.DataFrame
        Input at 512 Hz with a time column.
    time_col : str
        Name of the time column.

    Returns
    -------
    pd.DataFrame
        Resampled data at 50 Hz.
    """
    up, down = 25, 256
    n_in = len(df)
    n_out = int(np.ceil(n_in * up / down))
    t_start = df[time_col].iloc[0]
    t_end = df[time_col].iloc[-1]
    time_50 = np.linspace(t_start, t_end, n_out)

    result: dict[str, np.ndarray] = {time_col: time_50}
    for col in df.columns:
        if col == time_col:
            continue
        vals = df[col].to_numpy()
        if col in _NEAREST_COLS:
            idx = np.round(np.linspace(0, n_in - 1, n_out)).astype(int)
            result[col] = vals[idx]
        else:
            resampled = resample_poly(vals, up, down)
            if len(resampled) >= n_out:
                result[col] = resampled[:n_out]
            else:
                result[col] = np.pad(
                    resampled, (0, n_out - len(resampled)), mode="edge"
                )
    return pd.DataFrame(result)


# ═══════════════════════════════════════════════════════════════════════════════
# Syncron-E loader
# ═══════════════════════════════════════════════════════════════════════════════


def load_syncrone_db(filepath: str | Path) -> pd.DataFrame:
    """Load Syncron-E Waveform Recorder data from SQLite in ID order.

    The PB980 serial port sends packets whose timestamps overlap between
    packets; sorting by timestamp corrupts the waveform.  Row ID order
    preserves the correct sample sequence.

    Parameters
    ----------
    filepath : str | Path
        Path to the .db file.

    Returns
    -------
    pd.DataFrame
        Waveform data in ID order.
    """
    conn = sqlite3.connect(str(filepath))
    df = pd.read_sql_query(
        "SELECT id, session_id, timestamp, parsed_pressure, parsed_flow, "
        "vent_mode, breath_index FROM waveforms ORDER BY id",
        conn,
    )
    conn.close()
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# Alignment
# ═══════════════════════════════════════════════════════════════════════════════


def align_signals(
    df_dtb: pd.DataFrame,
    df_syncrone: pd.DataFrame,
    *,
    asl_peak_height: float = 10.0,
    asl_peak_distance: int = 100,
    asl_peak_prominence: float = 3.0,
    sync_peak_height: float = 10.0,
    sync_peak_distance: int = 60,
    sync_peak_prominence: float = 3.0,
    asl_t_min: float = 170.0,
    sync_t_naive_min: float = 58.0,
    t_end_margin: float = 10.0,
    edge_trim_start: int = 2,
    edge_trim_end: int = 3,
    delay_scan_range_ms: float = 300.0,
    delay_scan_step_ms: float = 2.0,
    smooth_sync_window: int = 0,
    smooth_sync_polyorder: int = 3,
) -> AlignmentResult:
    """Three-step alignment: peak warp + transport delay + overlap extraction.

    1. **Peak-to-peak time warping** — Detect pressure peaks in both signals,
       match 1:1, and build a piecewise-linear time map from Syncron-E sample
       index to ASL elapsed time.
    2. **Transport delay correction** — Scan ±delay_scan_range_ms to find the
       global sub-sample shift that maximises Pearson r.
    3. **Overlap extraction** — Interpolate both signals onto a common 50 Hz
       grid over the aligned overlap region.

    Parameters
    ----------
    df_dtb : pd.DataFrame
        ASL 5000 data at 50 Hz (must have ``Time (sec)`` and
        ``Airway Pressure (cmH2O)`` columns).
    df_syncrone : pd.DataFrame
        Syncron-E data in ID order (must have ``parsed_pressure`` and
        ``parsed_flow``).
    asl_peak_height, asl_peak_distance, asl_peak_prominence
        Peak detection parameters for the ASL pressure signal.
    sync_peak_height, sync_peak_distance, sync_peak_prominence
        Peak detection parameters for the Syncron-E pressure signal.
    asl_t_min : float
        Minimum ASL time (sec) for peak matching — skip signature events.
    sync_t_naive_min : float
        Minimum naive Syncron-E time (sec) for peak matching.
    t_end_margin : float
        Seconds to exclude from end of both signals.
    edge_trim_start : int
        Number of matched peaks to skip at the start of the overlap.
    edge_trim_end : int
        Number of matched peaks to skip at the end of the overlap.
    delay_scan_range_ms : float
        Transport delay scan half-range in ms (±).
    delay_scan_step_ms : float
        Transport delay scan step in ms.
    smooth_sync_window : int
        If > 0, apply Savitzky-Golay smoothing to the Syncron-E pressure and
        flow signals before alignment.  Useful for VC and VC+ modes where the
        PB980 serial output has higher measurement noise from the control loop.
        Must be odd.  Typical value: 11.
    smooth_sync_polyorder : int
        Polynomial order for Savitzky-Golay filter.  Default 3.

    Returns
    -------
    AlignmentResult
        Aligned data and metadata.
    """
    sync_p = df_syncrone["parsed_pressure"].values.copy()
    sync_f = df_syncrone["parsed_flow"].values.copy()

    if smooth_sync_window > 0:
        sync_p = savgol_filter(sync_p, smooth_sync_window, smooth_sync_polyorder)
        sync_f = savgol_filter(sync_f, smooth_sync_window, smooth_sync_polyorder)

    sync_idx = np.arange(len(sync_p))
    sync_t_naive = sync_idx * 0.02

    asl_t = df_dtb["Time (sec)"].values
    asl_p = df_dtb["Airway Pressure (cmH2O)"].values
    asl_f = df_dtb["Total Flow (L/min)"].values

    # ── Step 1: Detect pressure peaks ────────────────────────────────────
    asl_peaks, _ = find_peaks(
        asl_p,
        height=asl_peak_height,
        distance=asl_peak_distance,
        prominence=asl_peak_prominence,
    )
    asl_peaks = asl_peaks[
        (asl_t[asl_peaks] > asl_t_min)
        & (asl_t[asl_peaks] < asl_t[-1] - t_end_margin)
    ]

    sync_peaks, _ = find_peaks(
        sync_p,
        height=sync_peak_height,
        distance=sync_peak_distance,
        prominence=sync_peak_prominence,
    )
    sync_peaks = sync_peaks[
        (sync_t_naive[sync_peaks] > sync_t_naive_min)
        & (sync_t_naive[sync_peaks] < sync_t_naive[-1] - t_end_margin)
    ]

    n_common = min(len(asl_peaks), len(sync_peaks))
    if n_common < 5:
        raise ValueError(
            f"Too few matched peaks ({n_common}). "
            f"ASL peaks: {len(asl_peaks)}, Sync peaks: {len(sync_peaks)}. "
            "Check asl_t_min / sync_t_naive_min or peak detection parameters."
        )

    # ── Step 2: Peak-to-peak time warping ────────────────────────────────
    sync_warped_t = np.interp(
        sync_idx,
        sync_peaks[:n_common],
        asl_t[asl_peaks[:n_common]],
    )

    # Measure clock drift
    naive_peak_t = sync_t_naive[sync_peaks[:n_common]]
    asl_peak_t = asl_t[asl_peaks[:n_common]]
    offsets = asl_peak_t - naive_peak_t
    clock_drift_sec = float(offsets[-1] - offsets[0])
    clock_drift_pct = clock_drift_sec / (asl_peak_t[-1] - asl_peak_t[0]) * 100

    # ── Step 3: Transport delay optimisation ─────────────────────────────
    overlap_start = asl_t[asl_peaks[edge_trim_start]]
    overlap_end = asl_t[asl_peaks[n_common - edge_trim_end]]
    n_ov = int((overlap_end - overlap_start) * 50) + 1
    ct = np.linspace(overlap_start, overlap_end, n_ov)
    api = np.interp(ct, asl_t, asl_p)

    best_shift_ms = 0.0
    best_r = -1.0
    for shift_ms in np.arange(
        -delay_scan_range_ms, delay_scan_range_ms, delay_scan_step_ms
    ):
        spi = np.interp(ct, sync_warped_t + shift_ms / 1000.0, sync_p)
        r, _ = pearsonr(api, spi)
        if r > best_r:
            best_r = r
            best_shift_ms = float(shift_ms)

    transport_delay_sec = best_shift_ms / 1000.0
    sync_aligned_t = sync_warped_t + transport_delay_sec

    # ── Build aligned DataFrame ──────────────────────────────────────────
    api_final = np.interp(ct, asl_t, asl_p)
    afi = np.interp(ct, asl_t, asl_f)
    spi = np.interp(ct, sync_aligned_t, sync_p)
    sfi = np.interp(ct, sync_aligned_t, sync_f)

    df_aligned = pd.DataFrame(
        {
            "time_sec": ct,
            "asl_pressure": api_final,
            "asl_flow": afi,
            "sync_pressure": spi,
            "sync_flow": sfi,
            "pressure_error": spi - api_final,
            "flow_error": sfi - afi,
        }
    )

    return AlignmentResult(
        df_aligned=df_aligned,
        n_asl_peaks=len(asl_peaks),
        n_sync_peaks=len(sync_peaks),
        n_matched_peaks=n_common,
        clock_drift_sec=clock_drift_sec,
        clock_drift_pct=clock_drift_pct,
        transport_delay_ms=best_shift_ms,
        peak_warped_r=best_r,
        overlap_duration_sec=float(ct[-1] - ct[0]),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Fidelity metrics
# ═══════════════════════════════════════════════════════════════════════════════


def compute_fidelity(
    reference: np.ndarray,
    test: np.ndarray,
    signal_label: str,
    r_threshold: float = 0.95,
    nrmse_threshold_pct: float = 10.0,
) -> FidelityResult:
    """Compute Pearson r, RMSE, and NRMSE between reference and test signals.

    NRMSE is normalised to the reference signal's range
    (max − min), expressed as a percentage.

    Parameters
    ----------
    reference : np.ndarray
        Reference signal (ASL 5000).
    test : np.ndarray
        Test signal (Syncron-E).
    signal_label : str
        Human-readable signal name.
    r_threshold : float
        Minimum acceptable Pearson r.
    nrmse_threshold_pct : float
        Maximum acceptable NRMSE as a percentage of the reference range.

    Returns
    -------
    FidelityResult
    """
    r, p = pearsonr(reference, test)
    err = test - reference
    rmse = float(np.sqrt(np.mean(err**2)))
    ref_range = float(reference.max() - reference.min())
    nrmse_pct = (rmse / ref_range * 100) if ref_range > 0 else float("inf")
    return FidelityResult(
        signal=signal_label,
        pearson_r=float(r),
        p_value=float(p),
        rmse=rmse,
        nrmse_pct=nrmse_pct,
        ref_range=ref_range,
        mean_error=float(np.mean(err)),
        max_abs_error=float(np.max(np.abs(err))),
        r_threshold=r_threshold,
        nrmse_threshold_pct=nrmse_threshold_pct,
    )


def compute_run_fidelity(
    df_aligned: pd.DataFrame,
    r_min: float = 0.95,
    nrmse_max_pressure_pct: float = 10.0,
    nrmse_max_flow_pct: float = 10.0,
) -> list[FidelityResult]:
    """Compute fidelity metrics for both pressure and flow.

    Parameters
    ----------
    df_aligned : pd.DataFrame
        Aligned data from ``align_signals``.
    r_min : float
        Minimum Pearson r threshold for both signals.
    nrmse_max_pressure_pct : float
        Maximum NRMSE threshold for pressure (% of reference range).
    nrmse_max_flow_pct : float
        Maximum NRMSE threshold for flow (% of reference range).

    Returns
    -------
    list[FidelityResult]
        [pressure_result, flow_result]
    """
    return [
        compute_fidelity(
            df_aligned["asl_pressure"].values,
            df_aligned["sync_pressure"].values,
            "Pressure (cmH₂O)",
            r_threshold=r_min,
            nrmse_threshold_pct=nrmse_max_pressure_pct,
        ),
        compute_fidelity(
            df_aligned["asl_flow"].values,
            df_aligned["sync_flow"].values,
            "V̇ (L/min)",
            r_threshold=r_min,
            nrmse_threshold_pct=nrmse_max_flow_pct,
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Plotting
# ═══════════════════════════════════════════════════════════════════════════════


def plot_raw_syncrone(
    df_syncrone: pd.DataFrame,
    title_suffix: str = "",
) -> plt.Figure:
    """Quick preview of raw Syncron-E waveforms in ID order.

    Parameters
    ----------
    df_syncrone : pd.DataFrame
        Syncron-E data in ID order.
    title_suffix : str
        Appended to plot titles.

    Returns
    -------
    plt.Figure
    """
    n = len(df_syncrone)
    t_naive = np.arange(n) * 0.02

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    ax1.plot(t_naive, df_syncrone["parsed_pressure"].values, linewidth=0.5, color="C0")
    ax1.set_ylabel("Pressure (cmH₂O)")
    ax1.set_title(f"Syncron-E Raw Waveforms (ID order){title_suffix}")
    ax1.grid(True, alpha=0.3)

    ax2.plot(t_naive, df_syncrone["parsed_flow"].values, linewidth=0.5, color="C1")
    ax2.set_ylabel("V̇ (L/min)")
    ax2.set_xlabel("Approximate elapsed time (sec)")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def plot_overlay(
    df_aligned: pd.DataFrame,
    duration_sec: float = 30.0,
    title_suffix: str = "",
) -> plt.Figure:
    """Overlay plot of pressure and flow for the first N seconds.

    Parameters
    ----------
    df_aligned : pd.DataFrame
        Aligned data.
    duration_sec : float
        How many seconds to show from the start of the overlap.
    title_suffix : str
        Appended to plot titles.

    Returns
    -------
    plt.Figure
    """
    t0 = df_aligned["time_sec"].iloc[0]
    t_end = min(t0 + duration_sec, df_aligned["time_sec"].iloc[-1])
    mask = df_aligned["time_sec"] <= t_end
    t = df_aligned.loc[mask, "time_sec"].values - t0

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    axes[0].plot(
        t, df_aligned.loc[mask, "asl_pressure"].values,
        linewidth=0.8, label="ASL 5000 (patient side)", alpha=0.9,
    )
    axes[0].plot(
        t, df_aligned.loc[mask, "sync_pressure"].values,
        linewidth=0.8, label="Syncron-E (ventilator side)",
        alpha=0.9, linestyle="--",
    )
    axes[0].set_ylabel("Pressure (cmH₂O)")
    axes[0].legend(loc="upper right", fontsize=9)
    axes[0].set_title(f"Pressure Overlay{title_suffix}")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(
        t, df_aligned.loc[mask, "asl_flow"].values,
        linewidth=0.8, label="ASL 5000 (patient side)", alpha=0.9,
    )
    axes[1].plot(
        t, df_aligned.loc[mask, "sync_flow"].values,
        linewidth=0.8, label="Syncron-E (ventilator side)",
        alpha=0.9, linestyle="--",
    )
    axes[1].set_ylabel("V̇ (L/min)")
    axes[1].set_xlabel("Time from overlap start (sec)")
    axes[1].legend(loc="upper right", fontsize=9)
    axes[1].set_title(f"V̇ Overlay{title_suffix}")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def plot_error_series(
    df_aligned: pd.DataFrame,
    title_suffix: str = "",
) -> plt.Figure:
    """Full-duration error time series for pressure and flow.

    Parameters
    ----------
    df_aligned : pd.DataFrame
        Aligned data.
    title_suffix : str
        Appended to plot titles.

    Returns
    -------
    plt.Figure
    """
    t = df_aligned["time_sec"].values
    t_rel = t - t[0]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)

    ax1.plot(
        t_rel, df_aligned["pressure_error"].values,
        linewidth=0.4, color="C3", alpha=0.7,
    )
    ax1.axhline(0, color="black", linewidth=0.5, linestyle=":")
    ax1.axhline(
        2.0, color="red", linewidth=0.8, linestyle="--",
        alpha=0.5, label="RMSE threshold (2.0 cmH₂O)",
    )
    ax1.axhline(-2.0, color="red", linewidth=0.8, linestyle="--", alpha=0.5)
    ax1.set_ylabel("ΔP (cmH₂O)")
    ax1.set_title(f"Pressure Error (Syncron-E − ASL 5000){title_suffix}")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    ax2.plot(
        t_rel, df_aligned["flow_error"].values,
        linewidth=0.4, color="C4", alpha=0.7,
    )
    ax2.axhline(0, color="black", linewidth=0.5, linestyle=":")
    ax2.axhline(
        3.0, color="red", linewidth=0.8, linestyle="--",
        alpha=0.5, label="RMSE threshold (3.0 L/min)",
    )
    ax2.axhline(-3.0, color="red", linewidth=0.8, linestyle="--", alpha=0.5)
    ax2.set_ylabel("ΔV̇ (L/min)")
    ax2.set_xlabel("Time from overlap start (sec)")
    ax2.set_title(f"V̇ Error (Syncron-E − ASL 5000){title_suffix}")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def plot_error_histograms(
    df_aligned: pd.DataFrame,
    title_suffix: str = "",
) -> plt.Figure:
    """Error distribution histograms for pressure and flow.

    Parameters
    ----------
    df_aligned : pd.DataFrame
        Aligned data.
    title_suffix : str
        Appended to plot titles.

    Returns
    -------
    plt.Figure
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    pe = df_aligned["pressure_error"].values
    fe = df_aligned["flow_error"].values

    ax1.hist(pe, bins=80, color="C3", alpha=0.7, edgecolor="white", linewidth=0.3)
    ax1.axvline(0, color="black", linewidth=0.8)
    ax1.axvline(
        np.mean(pe), color="C0", linewidth=1, linestyle="--",
        label=f"Mean: {np.mean(pe):.3f}",
    )
    ax1.set_xlabel("ΔP (cmH₂O)")
    ax1.set_ylabel("Count")
    ax1.set_title(f"Pressure Error Distribution{title_suffix}")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    ax2.hist(fe, bins=80, color="C4", alpha=0.7, edgecolor="white", linewidth=0.3)
    ax2.axvline(0, color="black", linewidth=0.8)
    ax2.axvline(
        np.mean(fe), color="C0", linewidth=1, linestyle="--",
        label=f"Mean: {np.mean(fe):.3f}",
    )
    ax2.set_xlabel("ΔV̇ (L/min)")
    ax2.set_ylabel("Count")
    ax2.set_title(f"V̇ Error Distribution{title_suffix}")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig


def plot_bland_altman(
    df_aligned: pd.DataFrame,
    title_suffix: str = "",
) -> plt.Figure:
    """Bland-Altman plots for pressure and flow.

    Parameters
    ----------
    df_aligned : pd.DataFrame
        Aligned data.
    title_suffix : str
        Appended to plot titles.

    Returns
    -------
    plt.Figure
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Pressure
    p_mean = (
        df_aligned["asl_pressure"].values + df_aligned["sync_pressure"].values
    ) / 2
    p_diff = df_aligned["pressure_error"].values
    p_mean_err = np.mean(p_diff)
    p_std_err = np.std(p_diff)

    ax1.scatter(p_mean, p_diff, s=1, alpha=0.15, color="C3", rasterized=True)
    ax1.axhline(p_mean_err, color="C0", linewidth=1, label=f"Mean: {p_mean_err:.3f}")
    ax1.axhline(
        p_mean_err + 1.96 * p_std_err, color="red", linewidth=0.8,
        linestyle="--", label=f"+1.96 SD: {p_mean_err + 1.96 * p_std_err:.3f}",
    )
    ax1.axhline(
        p_mean_err - 1.96 * p_std_err, color="red", linewidth=0.8,
        linestyle="--", label=f"\u22121.96 SD: {p_mean_err - 1.96 * p_std_err:.3f}",
    )
    ax1.set_xlabel("Mean Pressure (cmH₂O)")
    ax1.set_ylabel("ΔP (Sync − ASL)")
    ax1.set_title(f"Bland-Altman: Pressure{title_suffix}")
    ax1.legend(fontsize=7, loc="upper left")
    ax1.grid(True, alpha=0.3)

    # Flow
    f_mean = (
        df_aligned["asl_flow"].values + df_aligned["sync_flow"].values
    ) / 2
    f_diff = df_aligned["flow_error"].values
    f_mean_err = np.mean(f_diff)
    f_std_err = np.std(f_diff)

    ax2.scatter(f_mean, f_diff, s=1, alpha=0.15, color="C4", rasterized=True)
    ax2.axhline(f_mean_err, color="C0", linewidth=1, label=f"Mean: {f_mean_err:.3f}")
    ax2.axhline(
        f_mean_err + 1.96 * f_std_err, color="red", linewidth=0.8,
        linestyle="--", label=f"+1.96 SD: {f_mean_err + 1.96 * f_std_err:.3f}",
    )
    ax2.axhline(
        f_mean_err - 1.96 * f_std_err, color="red", linewidth=0.8,
        linestyle="--", label=f"\u22121.96 SD: {f_mean_err - 1.96 * f_std_err:.3f}",
    )
    ax2.set_xlabel("Mean V̇ (L/min)")
    ax2.set_ylabel("ΔV̇ (Sync − ASL)")
    ax2.set_title(f"Bland-Altman: V̇{title_suffix}")
    ax2.legend(fontsize=7, loc="upper left")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    return fig
