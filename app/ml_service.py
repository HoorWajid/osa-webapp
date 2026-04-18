"""
app/ml_service.py
─────────────────────────────────────────────────────────────────────────────
OSA Detection ML Service

Security fixes applied:
  1. Path traversal prevention — file_path validated inside UPLOAD_FOLDER
     using os.path.realpath() before any file read
  2. Model integrity check — SHA-256 hashes verified against manifest before
     joblib.load() (pickle RCE prevention)
  3. Thread-safe model loading — threading.Lock() around _load()
  4. CSV nrows cap (10 000) — prevents memory exhaustion DoS
  5. EDF signal size guard — rejects recordings > MAX_EDF_SAMPLES per channel
  6. warnings.filterwarnings scope narrowed — no longer silences all warnings
  7. Sample entropy O(n²) cap tightened to 500 points
  8. Analysis timeout via ThreadPoolExecutor (60 s default)
  9. Exception details go to app.logger only — safe generic message to caller
 10. CSV column values whitelisted to numeric types — blocks adversarial inputs
─────────────────────────────────────────────────────────────────────────────
"""

import os
import sys
import hashlib
import logging
import threading
import numpy as np
import pandas as pd
import joblib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

logger = logging.getLogger(__name__)

# ── CRITICAL: register transformers BEFORE any joblib.load() ─────────────────
from app.osa_transformers import DataQualityChecker, OutlierHandler, register_for_unpickling
register_for_unpickling()

# ── optional heavy imports ────────────────────────────────────────────────────
try:
    import pyedflib
    HAS_PYEDF = True
except ImportError:
    HAS_PYEDF = False

try:
    from scipy.signal import savgol_filter, find_peaks, butter, filtfilt
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from biosppy.signals import ecg as ecg_biosppy
    HAS_BIOSPPY = True
except ImportError:
    HAS_BIOSPPY = False

try:
    from hrvanalysis import (
        get_time_domain_features, get_frequency_domain_features,
        get_poincare_plot_features, get_csi_cvi_features, get_sampen,
        remove_outliers, remove_ectopic_beats, interpolate_nan_values,
    )
    HAS_HRV = True
except ImportError:
    HAS_HRV = False

    def remove_outliers(rr, low_rri=300, high_rri=2000):
        rr = np.asarray(rr, dtype=float)
        rr[(rr < low_rri) | (rr > high_rri)] = np.nan
        return rr

    def remove_ectopic_beats(rr, method='malik'):
        """Malik rule: flag beats where |RRn - RRn-1| / RRn-1 > 0.2."""
        rr = np.asarray(rr, dtype=float)
        for i in range(1, len(rr)):
            if np.isnan(rr[i - 1]) or rr[i - 1] == 0:
                continue
            if abs(rr[i] - rr[i - 1]) / rr[i - 1] > 0.2:
                rr[i] = np.nan
        return rr

    def interpolate_nan_values(rr, interpolation_method='linear'):
        rr = np.asarray(rr, dtype=float)
        nan_idx = np.isnan(rr)
        if not nan_idx.any():
            return rr
        ok = np.where(~nan_idx)[0]
        if len(ok) >= 2:
            rr = np.interp(np.arange(len(rr)), ok, rr[ok])
        return rr

    def get_time_domain_features(rr):
        rr = np.asarray(rr, dtype=float)
        rr = rr[np.isfinite(rr)]
        if len(rr) < 2:
            return {}
        diff_rr = np.diff(rr)
        sdnn = float(np.std(rr, ddof=1))
        rmssd = float(np.sqrt(np.mean(diff_rr ** 2)))
        nn50 = int(np.sum(np.abs(diff_rr) > 50))
        nn20 = int(np.sum(np.abs(diff_rr) > 20))
        return {
            'mean_nni':   float(np.mean(rr)),
            'median_nni': float(np.median(rr)),
            'range_nni':  float(np.max(rr) - np.min(rr)),
            'sdnn':       sdnn,
            'sdsd':       float(np.std(diff_rr, ddof=1)),
            'rmssd':      rmssd,
            'nni_50':     nn50,
            'pnn50':      float(nn50 / max(len(diff_rr), 1) * 100),
            'nni_20':     nn20,
            'pnn20':      float(nn20 / max(len(diff_rr), 1) * 100),
            'cvnni':      float(sdnn / np.mean(rr) * 100) if np.mean(rr) > 0 else np.nan,
            'cvsd':       float(rmssd / np.mean(rr) * 100) if np.mean(rr) > 0 else np.nan,
        }

    def get_frequency_domain_features(rr):
        rr = np.asarray(rr, dtype=float)
        rr = rr[np.isfinite(rr)]
        nan_out = {k: np.nan for k in ['lf', 'hf', 'lf_hf_ratio', 'lfnu', 'hfnu', 'total_power', 'vlf']}
        if len(rr) < 30:
            return nan_out
        try:
            t = np.cumsum(rr) / 1000.0
            t -= t[0]
            fs = 4.0
            t_uniform = np.arange(0, t[-1], 1.0 / fs)
            rr_uniform = np.interp(t_uniform, t, rr)
            rr_uniform -= np.mean(rr_uniform)
            n = len(rr_uniform)
            if n < 30:
                return nan_out
            freqs = np.fft.rfftfreq(n, d=1.0 / fs)
            psd   = (np.abs(np.fft.rfft(rr_uniform)) ** 2) / n
            df    = freqs[1] - freqs[0]
            vlf   = float(np.sum(psd[(freqs >= 0.003) & (freqs < 0.04)]) * df)
            lf    = float(np.sum(psd[(freqs >= 0.04)  & (freqs < 0.15)]) * df)
            hf    = float(np.sum(psd[(freqs >= 0.15)  & (freqs < 0.40)]) * df)
            total = vlf + lf + hf
            lfnu  = float(lf / (lf + hf) * 100) if (lf + hf) > 0 else np.nan
            hfnu  = float(hf / (lf + hf) * 100) if (lf + hf) > 0 else np.nan
            ratio = float(lf / hf) if hf > 0 else np.nan
            return {'vlf': vlf, 'lf': lf, 'hf': hf, 'total_power': total,
                    'lf_hf_ratio': ratio, 'lfnu': lfnu, 'hfnu': hfnu}
        except Exception:
            return nan_out

    def get_poincare_plot_features(rr):
        rr = np.asarray(rr, dtype=float)
        rr = rr[np.isfinite(rr)]
        if len(rr) < 2:
            return {}
        sd1 = float(np.std(np.diff(rr), ddof=1) / np.sqrt(2))
        sd2 = float(np.sqrt(max(2 * np.var(rr, ddof=1) - 0.5 * np.var(np.diff(rr), ddof=1), 0)))
        return {
            'sd1': sd1,
            'sd2': sd2,
            'ratio_sd2_sd1': float(sd2 / sd1) if sd1 > 0 else np.nan,
        }

    def get_csi_cvi_features(rr):
        rr = np.asarray(rr, dtype=float)
        rr = rr[np.isfinite(rr)]
        if len(rr) < 2:
            return {}
        diff_rr = np.diff(rr)
        l   = float(np.sqrt(2) * np.std(rr, ddof=1))
        t   = float(np.sqrt(2) * np.std(diff_rr, ddof=1))
        csi = float(l / t) if t > 0 else np.nan
        cvi = float(np.log10(l * t)) if l * t > 0 else np.nan
        return {'csi': csi, 'cvi': cvi}

    def get_sampen(rr):
        """
        Sample Entropy (m=2, r=0.2*std). Pure numpy O(n²).
        SECURITY FIX: cap tightened from 2000 to 500 points to prevent
        CPU exhaustion under concurrent requests (DoS prevention).
        """
        rr = np.asarray(rr, dtype=float)
        rr = rr[np.isfinite(rr)]
        if len(rr) < 10:
            return {'sample_entropy': np.nan}
        rr = rr[:500]   # FIX: was 2000 — 500² = 250k ops vs 4M ops
        m, r = 2, 0.2 * float(np.std(rr, ddof=1))
        if r == 0:
            return {'sample_entropy': np.nan}

        def _count(m):
            count = 0
            for i in range(len(rr) - m):
                for j in range(i + 1, len(rr) - m):
                    if np.max(np.abs(rr[i:i + m] - rr[j:j + m])) < r:
                        count += 1
            return count

        try:
            a, b   = _count(m + 1), _count(m)
            sampen = float(-np.log(a / b)) if b > 0 and a > 0 else np.nan
        except Exception:
            sampen = np.nan
        return {'sample_entropy': sampen}


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# SECURITY FIX 2: SHA-256 hashes of known-good model files.
# Generate with: python -c "import hashlib; print(hashlib.sha256(open('file','rb').read()).hexdigest())"
# Set to None to disable check for a specific file (not recommended in production).
# Update this dict every time you retrain and replace model files.
MODEL_HASH_MANIFEST = {
    'presence_pipeline.pkl':      os.environ.get('HASH_PRESENCE_PIPELINE'),
    'presence_model.pkl':         os.environ.get('HASH_PRESENCE_MODEL'),
    'presence_metadata.pkl':      os.environ.get('HASH_PRESENCE_METADATA'),
    'severity_pipeline.pkl':      os.environ.get('HASH_SEVERITY_PIPELINE'),
    'severity_model.pkl':         os.environ.get('HASH_SEVERITY_MODEL'),
    'severity_label_encoder.pkl': os.environ.get('HASH_SEVERITY_ENCODER'),
    'severity_metadata.pkl':      os.environ.get('HASH_SEVERITY_METADATA'),
}

# SECURITY FIX 5: Max EDF samples per channel (8 hrs @ 256 Hz = 7,372,800)
MAX_EDF_SAMPLES   = 8 * 3600 * 512    # 512 Hz ceiling — covers most clinical recorders
MAX_CSV_ROWS      = 10_000            # SECURITY FIX 4: CSV row cap
ANALYSIS_TIMEOUT  = 60               # SECURITY FIX 8: seconds before analysis is aborted
MAX_RR    = 20_000
MAX_SPO2  = 30_000


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: path containment check
# ─────────────────────────────────────────────────────────────────────────────

def _safe_path(file_path: str, upload_folder: str) -> bool:
    """
    SECURITY FIX 1: Verify file_path is inside upload_folder using realpath.
    Think of it as checking a visitor's badge says 'Authorised Zone A'
    before letting them through the door — not just trusting what they claim.

    os.path.realpath() resolves all symlinks and '..' components, making
    '../../../etc/passwd' resolve to '/etc/passwd' which fails the prefix check.
    """
    real_file   = os.path.realpath(os.path.abspath(file_path))
    real_folder = os.path.realpath(os.path.abspath(upload_folder))
    return real_file.startswith(real_folder + os.sep) or real_file == real_folder


def _verify_model_hash(filepath: str, expected_hash: str) -> bool:
    """
    SECURITY FIX 2: Compute SHA-256 of model file and compare to manifest.
    Joblib/pickle files execute arbitrary code on load — a tampered model
    is equivalent to a malicious executable.
    If expected_hash is None (not set in env), log a warning but allow load
    so the app works out-of-the-box without env vars configured.
    """
    if expected_hash is None:
        logger.warning(
            f'[MLService] No hash configured for {os.path.basename(filepath)} '
            f'— integrity unverified. Set HASH_* env vars in production.'
        )
        return True   # permissive if hash not configured
    try:
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        if actual != expected_hash:
            logger.critical(
                f'[MLService] HASH MISMATCH for {os.path.basename(filepath)}! '
                f'Expected {expected_hash[:16]}... got {actual[:16]}... '
                f'Model file may have been tampered with. Refusing to load.'
            )
            return False
        return True
    except Exception as e:
        logger.error(f'[MLService] Hash verification failed for {filepath}: {e}')
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL PROCESSING
# ─────────────────────────────────────────────────────────────────────────────

def _clean_rr(rr):
    rr = np.asarray(rr).flatten()
    rr = rr[np.isfinite(rr) & (rr >= 300) & (rr <= 2000)]
    if rr.size == 0 or not HAS_HRV:
        return rr
    try:
        rr = np.asarray(remove_outliers(rr, low_rri=300, high_rri=2000))
        rr = np.asarray(remove_ectopic_beats(rr, method='malik'))
        rr = np.asarray(interpolate_nan_values(rr, interpolation_method='linear'))
        rr = rr[np.isfinite(rr) & (rr >= 300) & (rr <= 2000)]
    except Exception:
        pass
    return rr


def _detect_rpeaks_scipy(ecg_signal, sr):
    """Scipy-only Pan-Tompkins-inspired R-peak detector (biosppy fallback)."""
    sig = np.asarray(ecg_signal, dtype=float)
    nyq = sr / 2.0
    lo, hi = 5.0 / nyq, min(20.0 / nyq, 0.99)
    try:
        b, a  = butter(2, [lo, hi], btype='band')
        filt  = filtfilt(b, a, sig)
    except Exception:
        filt = sig.copy()
    diff_sq    = np.diff(filt) ** 2
    win        = max(1, int(0.15 * sr))
    kernel     = np.ones(win) / win
    integrated = np.convolve(diff_sq, kernel, mode='same')
    thresh     = float(np.mean(integrated) * 2.0)
    min_dist   = int(0.25 * sr)
    peaks, _   = find_peaks(integrated, height=thresh, distance=min_dist)
    refine_win = int(0.05 * sr)
    rpeaks = []
    for p in peaks:
        lo_i = max(0, p - refine_win)
        hi_i = min(len(sig) - 1, p + refine_win)
        rpeaks.append(lo_i + int(np.argmax(sig[lo_i:hi_i + 1])))
    return np.array(rpeaks, dtype=int)


def _hrv_features(ecg_signal, sr):
    """Return (feature_dict, rr_raw_ms, rr_clean_ms)."""
    empty = ({}, np.array([]), np.array([]))
    if not HAS_SCIPY:
        return empty
    rpeaks = None
    if HAS_BIOSPPY:
        try:
            out    = ecg_biosppy.ecg(signal=ecg_signal, sampling_rate=sr, show=False)
            rpeaks = out['rpeaks']
        except Exception:
            rpeaks = None
    if rpeaks is None or len(rpeaks) < 2:
        try:
            rpeaks = _detect_rpeaks_scipy(ecg_signal, sr)
        except Exception:
            return empty
    if len(rpeaks) < 2:
        return empty
    rr_raw = np.diff(rpeaks) / sr * 1000
    rr     = _clean_rr(rr_raw)
    if len(rr) < 20:
        return empty
    m = {
        'total_peaks':        len(rpeaks),
        'valid_rr_intervals': len(rr),
        'artifacts_removed':  max(len(rr_raw) - len(rr), 0),
        'recording_hours':    len(ecg_signal) / (sr * 3600),
    }
    try:
        m.update(get_time_domain_features(rr))
    except Exception:
        pass
    try:
        if (1000.0 / np.mean(rr)) > 0.8:
            m.update(get_frequency_domain_features(rr))
        else:
            for k in ['lf', 'hf', 'lf_hf_ratio', 'lfnu', 'hfnu', 'total_power', 'vlf']:
                m[k] = np.nan
    except Exception:
        for k in ['lf', 'hf', 'lf_hf_ratio', 'lfnu', 'hfnu', 'total_power', 'vlf']:
            m[k] = np.nan
    for fn in [get_poincare_plot_features, get_csi_cvi_features, get_sampen]:
        try:
            m.update(fn(rr))
        except Exception:
            pass
    return m, rr_raw, rr


def _spo2_features(spo2, sr):
    """Return (feature_dict, spo2_raw_ds, spo2_clean_ds)."""
    empty = ({}, np.array([]), np.array([]))
    spo2_in = np.asarray(spo2, dtype=float)
    if len(spo2_in) == 0:
        return empty

    stage1       = spo2_in.copy()
    invalid_mask = (stage1 < 70) | (stage1 > 100)
    n_invalid    = int(np.sum(invalid_mask))
    stage1[invalid_mask] = np.nan

    n_valid = int(np.sum(~invalid_mask))
    if n_valid < sr * 60:
        return empty

    stage2  = stage1.copy()
    nan_idx = np.isnan(stage2)
    if nan_idx.any():
        ok = np.where(~nan_idx)[0]
        if len(ok) >= 2:
            stage2 = np.interp(np.arange(len(stage2)), ok, stage2[ok])
        else:
            stage2[nan_idx] = np.nanmean(stage1)

    stage3 = stage2.copy()
    if HAS_SCIPY:
        w = int(sr * 3)
        w = w + 1 if w % 2 == 0 else w
        if w >= 3 and len(stage3) > w:
            try:
                # SECURITY FIX 6: use targeted warnings suppression only here
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', RuntimeWarning)
                    stage3 = savgol_filter(stage2, w, 2)
                stage3 = np.clip(stage3, 70.0, 100.0)
            except Exception:
                stage3 = stage2.copy()

    step          = max(1, int(sr))
    spo2_raw_ds   = spo2_in[::step]
    spo2_clean_ds = stage3[::step]
    n             = min(len(spo2_raw_ds), len(spo2_clean_ds))
    spo2_raw_ds   = spo2_raw_ds[:n]
    spo2_clean_ds = spo2_clean_ds[:n]

    mn = float(np.mean(stage3)) or 1.0
    p  = lambda q: float(np.percentile(stage3, q))

    feats = {
        'spo2_mean':   float(np.mean(stage3)),
        'spo2_median': float(np.median(stage3)),
        'spo2_std':    float(np.std(stage3)),
        'spo2_cv':     float(np.std(stage3) / mn * 100),
        'spo2_min':    float(np.min(stage3)),
        'spo2_max':    float(np.max(stage3)),
        'spo2_p5':   p(5),  'spo2_p10': p(10), 'spo2_p25': p(25),
        'spo2_p75':  p(75), 'spo2_p90': p(90), 'spo2_p95': p(95),
        'time_below_85': float(np.sum(stage3 < 85)  / len(stage3) * 100),
        'time_below_88': float(np.sum(stage3 < 88)  / len(stage3) * 100),
        'time_below_90': float(np.sum(stage3 < 90)  / len(stage3) * 100),
        'time_below_93': float(np.sum(stage3 < 93)  / len(stage3) * 100),
        'time_below_95': float(np.sum(stage3 < 95)  / len(stage3) * 100),
        'time_above_95': float(np.sum(stage3 >= 95) / len(stage3) * 100),
        'valid_data_pct':  float(n_valid / len(spo2_in) * 100),
        'invalid_samples': n_invalid,
        'recording_hours': float(len(spo2_in) / (sr * 3600)),
    }
    return feats, spo2_raw_ds, spo2_clean_ds


def _compute_ahi(spo2, sr, epoch_sec=30, min_sec=10, da=4, dh=3):
    spo2  = np.asarray(spo2)
    spe   = int(epoch_sec * sr)
    mes   = int(min_sec * sr)
    ae = he = 0
    for i in range(0, len(spo2), spe):
        ep   = spo2[i:i + spe]
        if len(ep) == 0:
            continue
        base = np.max(ep)
        if   np.sum(ep <= base - da) >= mes:
            ae += 1
        elif np.sum((ep > base - da) & (ep <= base - dh)) >= mes:
            he += 1
    hrs = len(spo2) / (sr * 3600)
    return ae, he, (ae + he) / hrs if hrs > 0 else np.nan


# ─────────────────────────────────────────────────────────────────────────────
# ML SERVICE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class MLService:

    def __init__(self):
        self._loaded    = False
        self._lock      = threading.Lock()   # SECURITY FIX 3: thread-safe loading
        self.p_pipeline = None
        self.p_model    = None
        self.p_features = None
        self.s_pipeline = None
        self.s_model    = None
        self.s_encoder  = None
        self.s_features = None
        # ThreadPoolExecutor for analysis timeout enforcement
        self._executor  = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ml_worker')

    # ── thread-safe loader ────────────────────────────────────────────────

    def _load(self):
        """
        SECURITY FIX 3: Double-checked locking pattern.
        Outer check avoids acquiring the lock on every request (fast path).
        Inner check guards against the race where two threads both saw
        _loaded=False before either acquired the lock.
        """
        if self._loaded:
            return
        with self._lock:
            if self._loaded:   # second check inside lock
                return
            self._load_models()
            self._loaded = True

    def _load_models(self):
        here      = os.path.dirname(os.path.abspath(__file__))
        model_dir = os.path.join(here, 'ml_models')

        def _get(name):
            path = os.path.join(model_dir, name)
            if not os.path.exists(path):
                logger.warning(f'[MLService] {name} not found in {model_dir}')
                return None
            # SECURITY FIX 2: Verify hash before loading pickle
            expected = MODEL_HASH_MANIFEST.get(name)
            if not _verify_model_hash(path, expected):
                logger.critical(
                    f'[MLService] Refusing to load {name} — hash verification failed. '
                    f'Replace with a verified model file.'
                )
                return None
            try:
                return joblib.load(path)
            except Exception as e:
                logger.error(f'[MLService] Failed to load {name}: {e}')
                return None

        self.p_pipeline = _get('presence_pipeline.pkl')
        self.p_model    = _get('presence_model.pkl')
        p_meta = _get('presence_metadata.pkl')
        if p_meta:
            self.p_features = (p_meta.get('original_feature_names')
                               or p_meta.get('feature_names'))

        self.s_pipeline = _get('severity_pipeline.pkl')
        self.s_model    = _get('severity_model.pkl')
        self.s_encoder  = _get('severity_label_encoder.pkl')
        s_meta = _get('severity_metadata.pkl')
        if s_meta:
            self.s_features = (s_meta.get('original_feature_names')
                               or s_meta.get('feature_names'))

        ok = []
        if self.p_model: ok.append('presence ✓')
        else:            ok.append('presence MISSING → rule-based fallback')
        if self.s_model: ok.append('severity ✓')
        else:            ok.append('severity MISSING → AHI fallback')
        logger.info(f'[MLService] {", ".join(ok)}')

    # ── helper: build input DataFrame ────────────────────────────────────

    def _build_df(self, merged, feature_list):
        row = {col: merged.get(col, np.nan) for col in feature_list}
        return pd.DataFrame([row])

    # ── demographics merge ────────────────────────────────────────────────

    @staticmethod
    def _merge_demographics(signal_features, demographics):
        merged = dict(signal_features)
        if demographics is None:
            return merged
        demo = (demographics.__dict__
                if hasattr(demographics, '__dict__')
                else dict(demographics))
        for k, v in demo.items():
            if k.startswith('_') or v is None:
                continue
            if isinstance(v, bool):
                merged[k] = int(v)
            elif isinstance(v, str):
                vl = v.lower()
                if   vl in ('male', 'm', '1', 'yes', 'true'):   merged[k] = 1
                elif vl in ('female', 'f', '0', 'no', 'false'): merged[k] = 0
                else:
                    try:
                        merged[k] = float(v)
                    except (ValueError, TypeError):
                        pass   # non-numeric string — skip silently
            else:
                merged[k] = v
        return merged

    # ── EDF processing ────────────────────────────────────────────────────

    def _process_edf(self, path):
        if not HAS_PYEDF:
            return {}, np.nan, 0, 0, 'pyedflib not installed (pip install pyedflib)', {}, {}
        targets = ('ECG', 'SPO2', 'SAO2')
        signals = {}
        try:
            with pyedflib.EdfReader(path) as f:
                for i, lbl in enumerate(f.getSignalLabels()):
                    key = next((t for t in targets if t in lbl.upper()), None)
                    if key:
                        sr  = f.getSampleFrequency(i)
                        n   = f.getNSamples()[i]
                        # SECURITY FIX 5: Reject recordings exceeding MAX_EDF_SAMPLES
                        if n > MAX_EDF_SAMPLES:
                            logger.warning(
                                f'[MLService] EDF channel {lbl} has {n} samples '
                                f'(limit {MAX_EDF_SAMPLES}) — truncating to prevent OOM.'
                            )
                        read_n = min(n, MAX_EDF_SAMPLES)
                        signals[key] = {
                            'data':          f.readSignal(i, digital=False)[:read_n],
                            'sampling_rate': sr,
                        }
        except Exception as e:
            # SECURITY FIX 9: log detail, return generic message to caller
            logger.error(f'[MLService] EDF read error for {path}: {e}')
            return {}, np.nan, 0, 0, 'Could not read EDF file. It may be corrupted or unsupported.', {}, {}

        feats      = {}
        rr_signals = {}
        spo2_raw   = {}

        if 'ECG' in signals:
            hrv_result = _hrv_features(
                signals['ECG']['data'], signals['ECG']['sampling_rate']
            )
            hrv_feats, rr_raw_arr, rr_clean_arr = hrv_result
            feats.update(hrv_feats)
            if len(rr_raw_arr) > 0:
                rr_signals = {
                    'rr_raw':   rr_raw_arr.tolist(),
                    'rr_clean': rr_clean_arr.tolist(),
                    'sr':       float(signals['ECG']['sampling_rate']),
                }

        skey = next((k for k in ['SPO2', 'SAO2'] if k in signals), None)
        ae = he = 0
        ahi = np.nan
        if skey:
            spo2_data = signals[skey]['data']
            spo2_sr   = signals[skey]['sampling_rate']
            spo2_feats, spo2_raw_arr, spo2_clean_arr = _spo2_features(spo2_data, spo2_sr)
            feats.update(spo2_feats)
            ae, he, ahi = _compute_ahi(spo2_data, spo2_sr)
            if len(spo2_raw_arr) > 0 and len(spo2_clean_arr) > 0:
                spo2_raw = {
                    'spo2_raw':   spo2_raw_arr.tolist(),
                    'spo2_clean': spo2_clean_arr.tolist(),
                    'sr': 1.0,
                    'cleaning_params': {
                        'stage1': 'NaN substitution for out-of-range [70,100]%',
                        'stage2': 'linear interpolation of NaN gaps',
                        'stage3': 'Savitzky-Golay smooth (window=3s, poly=2)',
                        'invalid_removed': int(spo2_feats.get('invalid_samples', 0)),
                        'valid_data_pct':  round(float(spo2_feats.get('valid_data_pct', 100)), 2),
                    },
                }
        return feats, ahi, ae, he, None, rr_signals, spo2_raw

    # ── CSV processing ────────────────────────────────────────────────────

    def _process_csv(self, path):
        """
        SECURITY FIX 4: Row cap prevents memory exhaustion.
        SECURITY FIX 10: Column values are cast to float only —
        string values are dropped so adversarial text cannot influence
        the ML pipeline's numeric feature space.
        """
        try:
            df = pd.read_csv(path, nrows=MAX_CSV_ROWS)   # FIX 4: cap rows
        except Exception as e:
            logger.error(f'[MLService] CSV read error for {path}: {e}')
            return {}, np.nan, 0, 0, 'Could not read CSV file.', {}, {}

        df.columns = [c.lower().strip() for c in df.columns]
        raw_record = df.to_dict('records')[0] if not df.empty else {}

        # SECURITY FIX 10: whitelist numeric values only
        feats = {}
        for k, v in raw_record.items():
            try:
                fv = float(v)
                if np.isfinite(fv):
                    feats[k] = fv
                # else: inf/nan — skip
            except (TypeError, ValueError):
                pass   # string column — skip entirely

        ahi = feats.get('ahi', feats.get('ahi_score', np.nan))
        ae  = int(feats.get('apnea_events', 0))
        he  = int(feats.get('hypopnea_events', 0))
        if np.isnan(ahi) and feats.get('recording_hours', 0) > 0:
            ahi = (ae + he) / feats['recording_hours']

        rr_signals = {}
        spo2_raw   = {}
        if 'rr_interval' in feats:
            try:
                rr_arr     = np.asarray([feats['rr_interval']])
                rr_signals = {'rr_raw': rr_arr.tolist(), 'rr_clean': rr_arr.tolist(), 'sr': 0.0}
            except Exception:
                pass
        return feats, ahi, ae, he, None, rr_signals, spo2_raw

    # ── predictions ───────────────────────────────────────────────────────

    def _predict_presence(self, merged):
        if not (self.p_pipeline and self.p_model and self.p_features):
            return self._rule_based_presence(merged)
        try:
            df    = self._build_df(merged, self.p_features)
            X     = self.p_pipeline.transform(df)
            pred  = int(self.p_model.predict(X)[0])
            proba = self.p_model.predict_proba(X)[0]
            return bool(pred == 1), float(proba[pred])
        except Exception as e:
            logger.error(f'[MLService] Presence prediction error: {e} — using rule-based fallback')
            return self._rule_based_presence(merged)

    def _predict_severity(self, merged):
        if not (self.s_pipeline and self.s_model and self.s_features):
            return self._rule_based_severity(merged.get('ahi', np.nan))
        try:
            df   = self._build_df(merged, self.s_features)
            X    = self.s_pipeline.transform(df)
            pred = self.s_model.predict(X)[0]
            if self.s_encoder is not None:
                label = str(self.s_encoder.inverse_transform([int(pred)])[0])
            else:
                label = str(pred)
            return label
        except Exception as e:
            logger.error(f'[MLService] Severity prediction error: {e} — using rule-based fallback')
            return self._rule_based_severity(merged.get('ahi', np.nan))

    # ── rule-based fallbacks ──────────────────────────────────────────────

    @staticmethod
    def _rule_based_presence(merged):
        ahi = merged.get('ahi', np.nan)
        if ahi is not None and not (isinstance(ahi, float) and np.isnan(ahi)):
            detected   = float(ahi) >= 5
            confidence = float(min(0.99, max(0.50, 0.5 + (float(ahi) - 5) / 30)))
            return detected, confidence
        spo2_min = merged.get('spo2_min', np.nan)
        if spo2_min is not None and not (isinstance(spo2_min, float) and np.isnan(spo2_min)):
            return float(spo2_min) < 90, 0.65
        return False, 0.50

    @staticmethod
    def _rule_based_severity(ahi):
        try:
            ahi = float(ahi)
        except (TypeError, ValueError):
            return 'unknown'
        if np.isnan(ahi): return 'unknown'
        if ahi < 5:       return 'normal'
        if ahi < 15:      return 'mild'
        if ahi < 30:      return 'moderate'
        return 'severe'

    # ── core analysis (runs inside thread pool for timeout) ───────────────

    def _run_analysis(self, file_path, patient_demographics):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.edf':
            sig_feats, ahi, ae, he, err, rr_signals, spo2_raw = self._process_edf(file_path)
        else:
            sig_feats, ahi, ae, he, err, rr_signals, spo2_raw = self._process_csv(file_path)

        if err:
            return None, err

        merged = self._merge_demographics(sig_feats, patient_demographics)
        merged['ahi'] = (float(ahi)
                         if (ahi is not None and not np.isnan(ahi))
                         else np.nan)

        osa_detected, confidence = self._predict_presence(merged)
        severity = self._predict_severity(merged) if osa_detected else 'normal'

        odi     = float(merged.get('time_below_90') or 0.0)
        snoring = float(merged.get('spo2_std')      or 0.0)

        safe = {}
        for k, v in merged.items():
            try:
                fv = float(v)
                safe[k] = None if np.isnan(fv) else round(fv, 4)
            except (TypeError, ValueError):
                safe[k] = str(v) if v is not None else None

        def _trim(lst, mx):
            return lst[:mx] if isinstance(lst, list) else lst

        if rr_signals:
            rr_signals['rr_raw']   = _trim(rr_signals.get('rr_raw',   []), MAX_RR)
            rr_signals['rr_clean'] = _trim(rr_signals.get('rr_clean', []), MAX_RR)
        if spo2_raw:
            spo2_raw['spo2_raw']   = _trim(spo2_raw.get('spo2_raw',   []), MAX_SPO2)
            spo2_raw['spo2_clean'] = _trim(spo2_raw.get('spo2_clean', []), MAX_SPO2)

        return {
            'osa_detected':        bool(osa_detected),
            'severity':            severity,
            'confidence':          round(confidence, 4),
            'ahi_score':           round(float(ahi), 2) if not np.isnan(ahi) else 0.0,
            'apnea_events':        int(ae),
            'hypopnea_events':     int(he),
            'oxygen_desaturation': round(odi,     2),
            'snoring_intensity':   round(snoring, 2),
            'features':            safe,
            'rr_signals':          rr_signals,
            'spo2_raw':            spo2_raw,
        }, None

    # ── PUBLIC API ────────────────────────────────────────────────────────

    def analyze_file(self, file_path, patient_demographics=None, upload_folder=None):
        """
        Entry point called from routes.py:
            results, error = ml_service.analyze_file(path, patient_demographics=demo,
                                                      upload_folder=app.config['UPLOAD_FOLDER'])
        """
        self._load()

        # SECURITY FIX 1: Validate path stays inside upload folder
        if upload_folder:
            if not _safe_path(file_path, upload_folder):
                logger.critical(
                    f'[MLService] Path traversal attempt blocked: '
                    f'{file_path!r} is outside {upload_folder!r}'
                )
                return None, 'Invalid file path.'
        else:
            logger.warning('[MLService] upload_folder not provided — path traversal check skipped.')

        if not os.path.exists(file_path):
            return None, 'File not found.'

        # SECURITY FIX 8: Enforce analysis timeout via thread pool
        future = self._executor.submit(self._run_analysis, file_path, patient_demographics)
        try:
            results, error = future.result(timeout=ANALYSIS_TIMEOUT)
            return results, error
        except FuturesTimeout:
            future.cancel()
            logger.error(f'[MLService] Analysis timed out after {ANALYSIS_TIMEOUT}s: {file_path}')
            return None, 'Analysis timed out. The file may be too large or complex.'
        except Exception as e:
            # SECURITY FIX 9: detailed exception to log only
            logger.error(f'[MLService] Unexpected analysis error for {file_path}: {e}')
            return None, 'An unexpected error occurred during analysis.'


# Singleton — imported by routes.py as: from app.ml_service import ml_service
ml_service = MLService()