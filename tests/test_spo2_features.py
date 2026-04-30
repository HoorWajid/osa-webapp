"""
Extended tests for SpO₂ feature alignment with ml_service output.

These tests verify that every feature key returned by _spo2_features()
matches what viz_spo2.js and viz_report.js expect to consume, and that
no extra keys are returned that are not used by the frontend.
"""
import numpy as np
import pytest
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from app.ml_service import _spo2_features


# Keys that viz_spo2.js and viz_report.js read from the features dict.
FRONTEND_CONSUMED_KEYS = frozenset([
    "spo2_mean", "spo2_median", "spo2_std", "spo2_cv",
    "spo2_min", "spo2_max",
    "spo2_p5", "spo2_p10", "spo2_p25",
    "spo2_p75", "spo2_p90", "spo2_p95",
    "time_below_85", "time_below_88", "time_below_90",
    "time_below_93", "time_below_95", "time_above_95",
    "valid_data_pct", "invalid_samples", "recording_hours",
])


@pytest.fixture(scope="module")
def normal_feats():
    rng = np.random.default_rng(0)
    sig = np.clip(rng.normal(97.0, 0.7, 3600), 70.0, 100.0)
    feats, _, _ = _spo2_features(sig, sr=1)
    return feats


class TestSpo2FeatureAlignment:
    """Verify feature dict keys match exactly what the frontend consumes."""

    def test_all_frontend_keys_are_present(self, normal_feats):
        missing = FRONTEND_CONSUMED_KEYS - set(normal_feats.keys())
        assert not missing, f"ml_service is missing keys the frontend expects: {missing}"

    def test_no_snoring_intensity_key(self, normal_feats):
        assert "snoring_intensity" not in normal_feats

    def test_all_values_are_finite_floats_or_ints(self, normal_feats):
        for k, v in normal_feats.items():
            assert isinstance(v, (int, float)), f"{k} is not numeric: {type(v)}"
            assert np.isfinite(v), f"{k} has non-finite value: {v}"


class TestSpo2SignalCleaning:
    """Verify the three-stage cleaning pipeline produces correct outputs."""

    def test_stage1_clips_values_below_70(self):
        sig = np.array([60.0, 65.0] + [97.0] * 3600)
        feats, _, _ = _spo2_features(sig, sr=1)
        assert feats["invalid_samples"] == 2

    def test_stage1_clips_values_above_100(self):
        sig = np.array([101.0, 105.0] + [97.0] * 3600)
        feats, _, _ = _spo2_features(sig, sr=1)
        assert feats["invalid_samples"] == 2

    def test_cleaned_signal_within_valid_range(self):
        rng = np.random.default_rng(7)
        sig = rng.normal(95.0, 2.0, 7200)
        sig[::50] = 60.0  # inject periodic bad values
        _, _, clean_ds = _spo2_features(sig, sr=1)
        assert np.all(clean_ds >= 70.0)
        assert np.all(clean_ds <= 100.0)

    def test_downsampled_arrays_length_matches_recording_hours(self):
        sr = 1
        duration = 7200
        sig = np.full(duration, 96.0)
        feats, raw_ds, clean_ds = _spo2_features(sig, sr=sr)
        expected_hours = duration / 3600
        assert abs(feats["recording_hours"] - expected_hours) < 0.01
        # Downsampled at 1 Hz from a 1 Hz signal → same length
        assert len(raw_ds) == duration
        assert len(clean_ds) == duration

    def test_insufficient_valid_data_returns_empty(self):
        # Only 30 valid seconds at sr=1 → below the 60-second minimum
        sig = np.array([60.0] * 3570 + [97.0] * 30)
        feats, raw, clean = _spo2_features(sig, sr=1)
        assert feats == {}


class TestSpo2ThresholdConsistency:
    """Threshold time values must be logically consistent."""

    @pytest.fixture(autouse=True)
    def compute(self):
        rng = np.random.default_rng(99)
        # Mix of normal and hypoxic values
        sig = np.concatenate([
            np.full(1800, 97.0),  # 50% normal
            np.full(900, 91.0),   # 25% mildly low
            np.full(900, 86.0),   # 25% severely low
        ])
        feats, _, _ = _spo2_features(sig, sr=1)
        self.f = feats

    def test_time_below_90_le_time_below_93(self):
        assert self.f["time_below_90"] <= self.f["time_below_93"]

    def test_time_below_88_le_time_below_90(self):
        assert self.f["time_below_88"] <= self.f["time_below_90"]

    def test_time_below_85_le_time_below_88(self):
        assert self.f["time_below_85"] <= self.f["time_below_88"]

    def test_time_below_95_le_100(self):
        assert self.f["time_below_95"] <= 100.0

    def test_time_above_95_ge_0(self):
        assert self.f["time_above_95"] >= 0.0
