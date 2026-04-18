"""
app/osa_transformers.py
Security fixes applied:
  - No changes to transformer logic (must match training exactly)
  - register_for_unpickling() unchanged (required for joblib compat)
  - Added module-level docstring clarifying the re-pickle requirement
"""

import sys
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin


class DataQualityChecker(BaseEstimator, TransformerMixin):
    """Check and report data quality issues — identical to training version."""

    def __init__(self, missing_threshold=0.5, variance_threshold=0.01):
        self.missing_threshold   = missing_threshold
        self.variance_threshold  = variance_threshold
        self.columns_to_drop     = []
        self.data_quality_report = {}
        self.feature_names_      = None

    def fit(self, X, y=None):
        X_df = pd.DataFrame(X) if not isinstance(X, pd.DataFrame) else X
        self.feature_names_ = X_df.columns.tolist()

        missing_pct  = X_df.isnull().sum() / len(X_df)
        high_missing = missing_pct[missing_pct > self.missing_threshold].index.tolist()

        zero_var      = X_df.var() == 0
        zero_var_cols = zero_var[zero_var].index.tolist()

        normalized_var = X_df.var() / (X_df.max() - X_df.min() + 1e-10)
        low_var        = normalized_var[normalized_var < self.variance_threshold].index.tolist()

        duplicate_cols = []
        for i, col1 in enumerate(X_df.columns):
            for col2 in X_df.columns[i+1:]:
                if X_df[col1].equals(X_df[col2]):
                    duplicate_cols.append(col2)

        self.columns_to_drop = list(set(
            high_missing + zero_var_cols + low_var + duplicate_cols
        ))
        self.data_quality_report = {
            'high_missing':  high_missing,
            'zero_variance': zero_var_cols,
            'low_variance':  low_var,
            'duplicates':    duplicate_cols,
            'total_dropped': len(self.columns_to_drop),
        }
        return self

    def transform(self, X, y=None):
        X_df = pd.DataFrame(X) if not isinstance(X, pd.DataFrame) else X
        keep = [c for c in X_df.columns if c not in self.columns_to_drop]
        self.feature_names_ = keep
        return X_df[keep]

    def get_feature_names_out(self, input_features=None):
        return np.array(self.feature_names_)

    def get_report(self):
        return self.data_quality_report


class OutlierHandler(BaseEstimator, TransformerMixin):
    """Handle outliers using IQR or Z-score — identical to training version."""

    def __init__(self, method='iqr', threshold=3, cap=True):
        self.method       = method
        self.threshold    = threshold
        self.cap          = cap
        self.lower_bounds = {}
        self.upper_bounds = {}
        self.feature_names_ = None

    def fit(self, X, y=None):
        X_df = pd.DataFrame(X) if not isinstance(X, pd.DataFrame) else X
        self.feature_names_ = X_df.columns.tolist()

        for col in X_df.columns:
            if self.method == 'iqr':
                Q1  = X_df[col].quantile(0.25)
                Q3  = X_df[col].quantile(0.75)
                IQR = Q3 - Q1
                self.lower_bounds[col] = Q1 - 1.5 * IQR
                self.upper_bounds[col] = Q3 + 1.5 * IQR
            elif self.method == 'zscore':
                mean = X_df[col].mean()
                std  = X_df[col].std()
                self.lower_bounds[col] = mean - self.threshold * std
                self.upper_bounds[col] = mean + self.threshold * std
        return self

    def transform(self, X, y=None):
        X_df = (pd.DataFrame(X) if not isinstance(X, pd.DataFrame) else X).copy()
        X_transformed = X_df.copy()

        if self.cap:
            for col in X_transformed.columns:
                if col in self.lower_bounds:
                    X_transformed[col] = X_transformed[col].clip(
                        lower=self.lower_bounds[col],
                        upper=self.upper_bounds[col],
                    )
        else:
            for col in X_transformed.columns:
                if col in self.lower_bounds:
                    mask = (
                        (X_transformed[col] < self.lower_bounds[col]) |
                        (X_transformed[col] > self.upper_bounds[col])
                    )
                    X_transformed.loc[mask, col] = np.nan

        return X_transformed

    def get_feature_names_out(self, input_features=None):
        return np.array(self.feature_names_)


def register_for_unpickling():
    """
    Injects DataQualityChecker and OutlierHandler into sys.modules['__main__']
    so that joblib.load() can find them when the pkl references '__main__.*'.

    NOTE: This is a temporary compatibility shim. Before production deployment,
    re-save all pkl files with proper module paths using re_pickle.py so this
    global side-effect is no longer needed.

    Call this BEFORE any joblib.load() of your pipelines.
    """
    main_module = sys.modules.get('__main__')
    if main_module is not None:
        if not hasattr(main_module, 'DataQualityChecker'):
            main_module.DataQualityChecker = DataQualityChecker
        if not hasattr(main_module, 'OutlierHandler'):
            main_module.OutlierHandler = OutlierHandler


# Auto-register on import
register_for_unpickling()