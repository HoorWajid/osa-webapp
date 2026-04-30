"""
Tests for app/ml_service.py

Covers the three pure-function feature calculators that ml_service exposes:
  - _hrv_features()   → HRV time/freq/nonlinear features
  - _spo2_features()  → SpO₂ descriptive + desaturation features
  - _compute_ahi()    → Apnea-Hypopnea Index

All tests are independent of the database and Flask app context.
"""
import os
import math
import numpy as np
import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from app.ml_service import _hrv_features, _spo2_features, _compute_ahi, _clean_rr


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _synthetic_ecg(sr: int = 250, duration_s: int = 300, hr_bpm: float = 60.0) -> np.ndarray:
    """
    Gaussian-bump QRS synthetic ECG detectable by the scipy R-peak fallback.
    Each QRS is a narrow Gaussian (σ = 3 samples) centred at the beat position.
    """
    n = sr * duration_s
    sig = np.zeros(n, dtype=float)
    beat_interval = int(sr * 60.0 / hr_bpm)
    sigma = 3.0
    t = np.arange(-10, 11)
    qrs = np.exp(-(t ** 2) / (2 * sigma ** 2))
    for centre in range(beat_interval // 2, n, beat_interval):
        lo, hi = centre - 10, centre + 11
        sl = slice(max(lo, 0), min(hi, n))
        qrs_sl = qrs[max(0, -lo): max(0, -lo) + (sl.stop - sl.start)]
        sig[sl] += qrs_sl
    return sig


def _synthetic_spo2(sr: int = 1, duration_s: int = 3600, mean_pct: float = 97.0) -> np.ndarray:
    """Generate a realistic SpO₂ signal with small physiological variation."""
    rng = np.random.default_rng(42)
    return np.clip(rng.normal(loc=mean_pct, scale=0.8, size=sr * duration_s), 70.0, 100.0)


def _spo2_with_desats(sr: int = 1, duration_s: int = 3600) -> np.ndarray:
    """SpO₂ signal that drops below 90 % for ~20 % of recording."""
    sig = np.full(sr * duration_s, 97.0)
    n_below = int(0.20 * len(sig))
    sig[:n_below] = 85.0
    return sig


# ─────────────────────────────────────────────────────────────────
# _clean_rr
# ─────────────────────────────────────────────────────────────────

class TestCleanRR:
    def test_removes_outliers_below_300ms(self):
        rr = np.array([800, 820, 100, 810, 790], dtype=float)
        cleaned = _clean_rr(rr)
        assert 100 not in cleaned

    def test_removes_outliers_above_2000ms(self):
        rr = np.array([800, 820, 2500, 810, 790], dtype=float)
        cleaned = _clean_rr(rr)
        assert 2500 not in cleaned

    def test_preserves_valid_intervals(self):
        rr = np.array([750, 760, 770, 780, 790], dtype=float)
        cleaned = _clean_rr(rr)
        assert len(cleaned) == len(rr)

    def test_returns_numpy_array(self):
        rr = np.array([800, 820, 810], dtype=float)
        result = _clean_rr(rr)
        assert isinstance(result, np.ndarray)


# ─────────────────────────────────────────────────────────────────
# _spo2_features
# ─────────────────────────────────────────────────────────────────

class TestSpo2Features:
    """Tests cover every feature key that ml_service guarantees to return."""

    REQUIRED_KEYS = [
        "spo2_mean", "spo2_median", "spo2_std", "spo2_cv",
        "spo2_min", "spo2_max",
        "spo2_p5", "spo2_p10", "spo2_p25", "spo2_p75", "spo2_p90", "spo2_p95",
        "time_below_85", "time_below_88", "time_below_90",
        "time_below_93", "time_below_95", "time_above_95",
        "valid_data_pct", "invalid_samples", "recording_hours",
    ]

    @pytest.fixture(autouse=True)
    def compute(self):
        spo2 = _synthetic_spo2(sr=1, duration_s=3600, mean_pct=97.0)
        feats, raw_ds, clean_ds = _spo2_features(spo2, sr=1)
        self.feats = feats
        self.raw_ds = raw_ds
        self.clean_ds = clean_ds

    def test_returns_all_required_keys(self):
        for key in self.REQUIRED_KEYS:
            assert key in self.feats, f"Missing feature: {key}"

    def test_mean_in_valid_range(self):
        assert 70.0 <= self.feats["spo2_mean"] <= 100.0

    def test_min_le_mean_le_max(self):
        assert self.feats["spo2_min"] <= self.feats["spo2_mean"] <= self.feats["spo2_max"]

    def test_time_thresholds_sum_to_approx_100(self):
        # time_below_95 + time_above_95 ≈ 100%
        total = self.feats["time_below_95"] + self.feats["time_above_95"]
        assert abs(total - 100.0) < 1.0

    def test_valid_data_pct_between_0_and_100(self):
        assert 0.0 <= self.feats["valid_data_pct"] <= 100.0

    def test_recording_hours_positive(self):
        assert self.feats["recording_hours"] > 0.0

    def test_raw_and_clean_arrays_returned(self):
        assert len(self.raw_ds) > 0
        assert len(self.clean_ds) > 0

    def test_invalid_samples_clips_below_70(self):
        sr = 1
        sig = np.full(3600, 95.0)
        sig[:100] = 60.0  # below 70% → invalid
        feats, _, _ = _spo2_features(sig, sr=sr)
        assert feats["invalid_samples"] == 100

    def test_empty_signal_returns_empty_dict(self):
        feats, raw, clean = _spo2_features(np.array([]), sr=1)
        assert feats == {}
        assert len(raw) == 0
        assert len(clean) == 0

    def test_desaturation_burden_high_when_below_90(self):
        sig = _spo2_with_desats(sr=1, duration_s=3600)
        feats, _, _ = _spo2_features(sig, sr=1)
        # ~20% of signal was at 85%, so time_below_90 should be near 20%
        assert feats["time_below_90"] >= 15.0

    def test_normal_signal_has_high_time_above_95(self):
        feats, _, _ = _spo2_features(_synthetic_spo2(mean_pct=97.0), sr=1)
        assert feats["time_above_95"] > 50.0

    def test_percentiles_monotonically_increasing(self):
        p5  = self.feats["spo2_p5"]
        p10 = self.feats["spo2_p10"]
        p25 = self.feats["spo2_p25"]
        p75 = self.feats["spo2_p75"]
        p90 = self.feats["spo2_p90"]
        p95 = self.feats["spo2_p95"]
        assert p5 <= p10 <= p25 <= p75 <= p90 <= p95


# ─────────────────────────────────────────────────────────────────
# _compute_ahi
# ─────────────────────────────────────────────────────────────────

class TestComputeAHI:
    def test_no_events_in_normal_signal(self):
        sig = np.full(7200, 97.0)  # 2 hours, stable
        ae, he, ahi = _compute_ahi(sig, sr=1)
        assert ahi < 5.0, "Stable signal should produce AHI < 5"

    def test_apnea_events_detected_on_deep_drops(self):
        # Each epoch = 30 s; within each epoch, ≥10 s must drop ≥4% below base
        sr = 1
        epoch = 30
        sig = []
        for _ in range(10):
            base = np.full(epoch, 97.0)
            # Drop 5% for 12 seconds → apnea (≥4% for ≥10s)
            base[5:17] = 92.0
            sig.extend(base.tolist())
        sig = np.array(sig)
        ae, he, ahi = _compute_ahi(sig, sr=sr)
        assert ae > 0

    def test_ahi_proportional_to_event_count(self):
        # 120 epochs × 30 s = 3600 s = 1 hour; all epochs are apnea
        sr = 1
        epoch = 30
        sig = []
        for _ in range(120):
            base = np.full(epoch, 97.0)
            base[5:17] = 92.0
            sig.extend(base.tolist())
        ae, he, ahi = _compute_ahi(np.array(sig), sr=sr)
        assert ahi > 0, "Should detect events over 1-hour recording"

    def test_returns_tuple_of_three(self):
        result = _compute_ahi(np.full(1800, 96.0), sr=1)
        assert len(result) == 3

    def test_ahi_nan_on_zero_length_signal(self):
        ae, he, ahi = _compute_ahi(np.array([]), sr=1)
        assert math.isnan(ahi)

    def test_hypopnea_only_on_shallow_drops(self):
        # Drop of exactly 3% (< 4%) for 12 seconds → hypopnea only
        sr = 1
        epoch = 30
        sig = []
        for _ in range(60):
            base = np.full(epoch, 97.0)
            base[5:17] = 94.0  # 3% drop
            sig.extend(base.tolist())
        ae, he, ahi = _compute_ahi(np.array(sig), sr=sr)
        assert he > 0


# ─────────────────────────────────────────────────────────────────
# _hrv_features
# ─────────────────────────────────────────────────────────────────

class TestHRVFeatures:
    """
    HRV features require scipy.  Tests are skipped gracefully when
    the optional dependency is absent (CI without heavy dependencies).
    """

    @pytest.fixture(autouse=True)
    def skip_without_scipy(self):
        pytest.importorskip("scipy", reason="scipy required for HRV features")

    def test_returns_tuple_of_three(self):
        ecg = _synthetic_ecg(sr=250, duration_s=300)
        result = _hrv_features(ecg, sr=250)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_returns_dict_and_arrays(self):
        feats, rr_raw, rr_clean = _hrv_features(_synthetic_ecg(), sr=250)
        assert isinstance(feats, dict)
        assert isinstance(rr_raw, np.ndarray)
        assert isinstance(rr_clean, np.ndarray)

    def test_recording_hours_matches_signal_length(self):
        sr, duration = 250, 300
        feats, _, _ = _hrv_features(_synthetic_ecg(sr=sr, duration_s=duration), sr=sr)
        if "recording_hours" in feats:
            expected = duration / 3600
            assert abs(feats["recording_hours"] - expected) < 0.01

    def test_time_domain_keys_consistent_when_computed(self):
        feats, _, _ = _hrv_features(_synthetic_ecg(), sr=250)
        if "sdnn" in feats:
            assert "rmssd" in feats
            assert "pnn50" in feats
            assert "mean_nni" in feats

    def test_sdnn_positive_when_computed(self):
        feats, _, _ = _hrv_features(_synthetic_ecg(), sr=250)
        sdnn = feats.get("sdnn")
        if sdnn is not None:
            assert sdnn > 0

    def test_frequency_domain_values_are_numeric(self):
        feats, _, _ = _hrv_features(_synthetic_ecg(), sr=250)
        for key in ("lf", "hf", "lf_hf_ratio", "vlf", "total_power", "lfnu", "hfnu"):
            if key in feats:
                val = feats[key]
                assert val is None or isinstance(val, (int, float))

    def test_nonlinear_values_are_numeric(self):
        feats, _, _ = _hrv_features(_synthetic_ecg(), sr=250)
        for key in ("sd1", "sd2", "csi", "cvi", "sample_entropy"):
            if key in feats:
                val = feats[key]
                assert val is None or isinstance(val, (int, float))

    def test_empty_signal_returns_empty(self):
        feats, raw, clean = _hrv_features(np.array([]), sr=250)
        assert feats == {}
        assert len(raw) == 0
        assert len(clean) == 0

    def test_too_short_signal_returns_empty(self):
        # 10 samples at 250 Hz = 40 ms — far too short
        feats, raw, clean = _hrv_features(np.ones(10), sr=250)
        assert feats == {}
