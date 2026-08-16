"""
Data Processing Utilities
==========================
Common data processing functions used across the pipeline.

Functions for:
- Array statistics
- Time-series processing
- Data validation
- Index calculations (optical + SAR)               [Pillar 1]
- Time-series smoothing (Whittaker / Savitzky-Golay) [Pillar 1]
- Optical-SAR fusion + per-bin quality weighting     [Pillar 1]

Backward compatibility: every public method that existed prior to the v5
re-architecture is preserved verbatim (calculate_ndvi / calculate_evi /
calculate_ndmi / align_arrays / calculate_coefficient_of_variation, etc.).
New Pillar-1 helpers are additive.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Optional scipy acceleration. All Pillar-1 smoothers degrade gracefully to
# numpy-only implementations when scipy is unavailable, so this module never
# hard-fails on import.
try:
    import scipy.sparse as _sp
    import scipy.sparse.linalg as _spla
    from scipy.signal import savgol_filter as _savgol_filter
    _SCIPY_OK = True
except Exception:  # pragma: no cover - environment dependent
    _SCIPY_OK = False


# Numerical guard used across index math
_EPS = 1e-10


class DataProcessor:
    """Common data processing utilities"""

    # =====================================================================
    # ORIGINAL API  (preserved verbatim — do not change signatures)
    # =====================================================================

    @staticmethod
    def calculate_array_stats(arr: np.ndarray) -> Dict[str, float]:
        """
        Calculate basic statistics for an array.

        Args:
            arr: Input numpy array

        Returns:
            Dictionary with mean, std, min, max, percentiles
        """
        valid = arr[~np.isnan(arr)]

        if len(valid) == 0:
            return {
                'mean': 0.0,
                'std': 0.0,
                'min': 0.0,
                'max': 0.0,
                'p10': 0.0,
                'p50': 0.0,
                'p90': 0.0,
                'count': 0
            }

        return {
            'mean': float(np.mean(valid)),
            'std': float(np.std(valid)),
            'min': float(np.min(valid)),
            'max': float(np.max(valid)),
            'p10': float(np.percentile(valid, 10)),
            'p50': float(np.percentile(valid, 50)),
            'p90': float(np.percentile(valid, 90)),
            'count': int(len(valid))
        }

    @staticmethod
    def align_arrays(arrays: List[np.ndarray]) -> List[np.ndarray]:
        """
        Align multiple arrays to the same shape (minimum dimensions).

        Args:
            arrays: List of 2D numpy arrays

        Returns:
            List of aligned arrays
        """
        if not arrays:
            return arrays

        min_height = min(arr.shape[0] for arr in arrays)
        min_width = min(arr.shape[1] for arr in arrays)

        aligned = [arr[:min_height, :min_width] for arr in arrays]

        return aligned

    @staticmethod
    def calculate_ndvi(
        nir: np.ndarray,
        red: np.ndarray
    ) -> np.ndarray:
        """
        Calculate NDVI (Normalized Difference Vegetation Index).

        NDVI = (NIR - RED) / (NIR + RED)

        Returns:
            NDVI array, clipped to [-1, 1]
        """
        with np.errstate(divide='ignore', invalid='ignore'):
            ndvi = (nir - red) / (nir + red + _EPS)
            ndvi = np.where(np.isfinite(ndvi), np.clip(ndvi, -1, 1), np.nan)

        return ndvi

    @staticmethod
    def calculate_evi(
        nir: np.ndarray,
        red: np.ndarray,
        blue: np.ndarray,
        G: float = 2.5,
        C1: float = 6.0,
        C2: float = 7.5,
        L: float = 1.0
    ) -> np.ndarray:
        """
        Calculate EVI (Enhanced Vegetation Index).

        EVI = G * ((NIR - RED) / (NIR + C1*RED - C2*BLUE + L))

        Returns:
            EVI array, clipped to [-1, 3]
        """
        with np.errstate(divide='ignore', invalid='ignore'):
            evi = G * (nir - red) / (nir + C1 * red - C2 * blue + L + _EPS)
            evi = np.where(np.isfinite(evi), np.clip(evi, -1, 3), np.nan)

        return evi

    @staticmethod
    def calculate_ndmi(
        nir: np.ndarray,
        swir: np.ndarray
    ) -> np.ndarray:
        """
        Calculate NDMI (Normalized Difference Moisture Index).

        NDMI = (NIR - SWIR) / (NIR + SWIR)

        Returns:
            NDMI array, clipped to [-1, 1]
        """
        with np.errstate(divide='ignore', invalid='ignore'):
            ndmi = (nir - swir) / (nir + swir + _EPS)
            ndmi = np.where(np.isfinite(ndmi), np.clip(ndmi, -1, 1), np.nan)

        return ndmi

    @staticmethod
    def validate_band_data(
        data: np.ndarray,
        min_valid_ratio: float = 0.15
    ) -> bool:
        """
        Validate satellite band data quality.

        Returns:
            True if data has at least ``min_valid_ratio`` finite pixels.
        """
        if data is None or data.size == 0:
            return False

        valid_ratio = np.sum(~np.isnan(data)) / data.size

        return valid_ratio >= min_valid_ratio

    @staticmethod
    def clean_satellite_data(
        data: np.ndarray,
        nodata_value: Optional[float] = None,
        min_value: float = 0.0,
        max_value: float = 10000.0
    ) -> np.ndarray:
        """
        Clean satellite band data (nodata + outlier removal -> NaN).
        """
        cleaned = data.copy()

        if nodata_value is not None:
            cleaned[cleaned == nodata_value] = np.nan

        cleaned[cleaned < min_value] = np.nan
        cleaned[cleaned > max_value] = np.nan

        return cleaned

    @staticmethod
    def find_consecutive_periods(
        boolean_series: pd.Series,
        min_length: int = 3
    ) -> List[Tuple[int, int]]:
        """
        Find consecutive True periods in a boolean series.

        Returns:
            List of (start_idx, end_idx) tuples.
        """
        periods = []
        start_idx = None

        for i, val in enumerate(boolean_series):
            if val and start_idx is None:
                start_idx = i
            elif not val and start_idx is not None:
                if i - start_idx >= min_length:
                    periods.append((start_idx, i - 1))
                start_idx = None

        if start_idx is not None and len(boolean_series) - start_idx >= min_length:
            periods.append((start_idx, len(boolean_series) - 1))

        return periods

    @staticmethod
    def parse_date_flexible(
        date_input: Any
    ) -> Optional[datetime]:
        """Parse date from various formats -> datetime or None."""
        if isinstance(date_input, datetime):
            return date_input

        if isinstance(date_input, pd.Timestamp):
            return date_input.to_pydatetime()

        if isinstance(date_input, str):
            try:
                return pd.to_datetime(date_input)
            except Exception:
                pass

        return None

    @staticmethod
    def interpolate_time_series(
        dates: List[datetime],
        values: List[float],
        target_dates: List[datetime]
    ) -> np.ndarray:
        """Interpolate time-series values to target dates (linear)."""
        x = np.array([d.toordinal() for d in dates])
        y = np.array(values)
        x_target = np.array([d.toordinal() for d in target_dates])

        y_interp = np.interp(x_target, x, y)

        return y_interp

    @staticmethod
    def calculate_cumulative_integral(
        x: np.ndarray,
        y: np.ndarray
    ) -> float:
        """Area under curve using trapezoidal integration."""
        return float(np.trapz(y, x))

    @staticmethod
    def normalize_to_range(
        values: np.ndarray,
        target_min: float = 0.0,
        target_max: float = 1.0
    ) -> np.ndarray:
        """
        Min-max normalize values to a target range, using the series' OWN extremes.

        ⚠ NOT suitable for building a cross-parcel comparable signal. Because the
        bounds come from the data, every input series is stretched to span the
        full target range regardless of its actual magnitude — a barren plot
        oscillating between 0.02 and 0.05 comes out looking identical to a
        thriving double-cropped field. Use ``normalize_fixed_range`` for anything
        that will be compared between parcels or against an absolute threshold.

        Retained for legacy/diagnostic use only.
        """
        v_min = np.nanmin(values)
        v_max = np.nanmax(values)

        if v_max == v_min:
            return np.full_like(values, target_min)

        normalized = (values - v_min) / (v_max - v_min)
        normalized = normalized * (target_max - target_min) + target_min

        return normalized

    @staticmethod
    def normalize_fixed_range(
        values: np.ndarray,
        lo: float,
        hi: float,
        clip: bool = True,
    ) -> np.ndarray:
        """
        Normalize to [0, 1] against FIXED physical bounds, not the data's own.

        This is what makes a vegetation signal comparable across parcels: the
        same reflectance always maps to the same output value, so an absolute
        threshold ("this looks like a crop canopy") means the same thing
        everywhere.

        Values outside [lo, hi] are clipped rather than dropped — an NDVI of
        -0.4 over open water is a real observation, and clamping it to 0 says
        "no vegetation", which is correct. NaN propagates as NaN.

        Args:
            values: raw index series (may contain NaN)
            lo, hi: physical bounds for this index (see PipelineConfig.INDEX_PHYSICAL_RANGES)
            clip:   clamp to [0, 1]; disable only for diagnostics
        """
        span = float(hi) - float(lo)
        if span <= 0:
            raise ValueError(f"normalize_fixed_range: invalid bounds lo={lo} hi={hi}")

        out = (np.asarray(values, dtype=float) - float(lo)) / span
        if clip:
            # np.clip preserves NaN, which is what we want — a missing
            # observation must stay missing, not become 0.0.
            out = np.clip(out, 0.0, 1.0)
        return out

    @staticmethod
    def calculate_coefficient_of_variation(
        values: np.ndarray
    ) -> float:
        """Coefficient of variation (CV = std / mean), NaN-safe."""
        valid = values[~np.isnan(values)]

        if len(valid) == 0 or np.mean(valid) == 0:
            return 0.0

        return float(np.std(valid) / np.mean(valid))

    @staticmethod
    def resample_to_resolution(
        data: np.ndarray,
        current_resolution: int,
        target_resolution: int
    ) -> Tuple[int, int]:
        """Output (height, width) for resampling to a target resolution."""
        scale = current_resolution / target_resolution

        out_height = max(10, int(data.shape[0] * scale))
        out_width = max(10, int(data.shape[1] * scale))

        return out_height, out_width

    # =====================================================================
    # PILLAR 1 — ADDITIONAL VEGETATION INDICES
    # All operate elementwise on reflectance arrays (or scalars) and are
    # NaN-safe. Reflectances are expected in [0, 1] (surface reflectance).
    # =====================================================================

    @staticmethod
    def calculate_savi(nir, red, L: float = 0.5):
        """
        SAVI (Soil-Adjusted Vegetation Index).

        SAVI = (1 + L) * (NIR - RED) / (NIR + RED + L)

        L in [0,1] dampens soil brightness; L=0.5 is the canonical default,
        good for partial canopy / early season on bare-ish soils.
        """
        nir = np.asarray(nir, dtype=float)
        red = np.asarray(red, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            savi = (1.0 + L) * (nir - red) / (nir + red + L + _EPS)
            savi = np.where(np.isfinite(savi), np.clip(savi, -1, 1.5), np.nan)
        return savi

    @staticmethod
    def calculate_msavi2(nir, red):
        """
        MSAVI2 (Modified Soil-Adjusted Vegetation Index, self-calibrating L).

        MSAVI2 = (2*NIR + 1 - sqrt((2*NIR + 1)^2 - 8*(NIR - RED))) / 2

        Best early-season / sowing signal: robust where soil dominates and
        NDVI is noisy. Primary index for detecting green-up onset.
        """
        nir = np.asarray(nir, dtype=float)
        red = np.asarray(red, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            term = (2.0 * nir + 1.0) ** 2 - 8.0 * (nir - red)
            term = np.where(term < 0, np.nan, term)
            msavi2 = (2.0 * nir + 1.0 - np.sqrt(term)) / 2.0
            msavi2 = np.where(np.isfinite(msavi2), np.clip(msavi2, -1, 1.5), np.nan)
        return msavi2

    @staticmethod
    def calculate_nirv(nir, red):
        """
        NIRv (Near-Infrared reflectance of vegetation) = NIR * NDVI.

        Strong GPP / biomass proxy; the backbone for the crop-agnostic
        *yield-potential* AUC (kept distinct from the detection composite).
        """
        nir = np.asarray(nir, dtype=float)
        red = np.asarray(red, dtype=float)
        ndvi = DataProcessor.calculate_ndvi(nir, red)
        with np.errstate(invalid='ignore'):
            nirv = nir * ndvi
            nirv = np.where(np.isfinite(nirv), np.clip(nirv, -1, 1), np.nan)
        return nirv

    @staticmethod
    def calculate_lswi(nir, swir1):
        """
        LSWI (Land Surface Water Index) = (NIR - SWIR1) / (NIR + SWIR1).

        Canopy/soil water content; rises on flooding (paddy transplant) and
        falls under water stress. Formula matches NDMI but is used with the
        water-stress / paddy semantics in Stage 5/6.
        """
        nir = np.asarray(nir, dtype=float)
        swir1 = np.asarray(swir1, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            lswi = (nir - swir1) / (nir + swir1 + _EPS)
            lswi = np.where(np.isfinite(lswi), np.clip(lswi, -1, 1), np.nan)
        return lswi

    @staticmethod
    def calculate_gcvi(nir, green):
        """
        GCVI (Green Chlorophyll Vegetation Index) = NIR / GREEN - 1.

        Sensitive to chlorophyll / high-LAI canopies (maize, sugarcane);
        does not saturate as early as NDVI at high biomass.
        """
        nir = np.asarray(nir, dtype=float)
        green = np.asarray(green, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            gcvi = nir / (green + _EPS) - 1.0
            gcvi = np.where(np.isfinite(gcvi), np.clip(gcvi, -1, 12), np.nan)
        return gcvi

    # ── Land-cover discriminators ────────────────────────────────────────
    #
    # These three exist to answer "is this parcel farmland at all?", not to
    # measure crop vigor. All are computed from bands already downloaded
    # (B02 blue, B03 green, B04 red, B08 NIR, B11 SWIR1), so they add no
    # acquisition cost. Threshold guidance in land_cover_gate.py cites the
    # source for each; the formulas below are the standard published forms.

    @staticmethod
    def calculate_ndbi(swir1, nir):
        """
        NDBI (Normalized Difference Built-up Index) = (SWIR1 - NIR) / (SWIR1 + NIR).

        Built-up surfaces reflect more in SWIR than NIR, so NDBI goes positive
        over concrete/asphalt and negative over vegetation. This is the primary
        built-up discriminator and was NOT previously computed — B11 was
        downloaded and used only for NDMI/LSWI.

        Note NDBI is the sign-flipped twin of NDMI; it is kept separate because
        the two are read with different intent (moisture vs impervious surface)
        and conflating them has caused bugs elsewhere in this file's history.
        """
        swir1 = np.asarray(swir1, dtype=float)
        nir = np.asarray(nir, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            ndbi = (swir1 - nir) / (swir1 + nir + _EPS)
            ndbi = np.where(np.isfinite(ndbi), np.clip(ndbi, -1, 1), np.nan)
        return ndbi

    @staticmethod
    def calculate_bsi(swir1, red, nir, blue):
        """
        BSI (Bare Soil Index)
            = ((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE))

        Positive over exposed soil and rock, negative over vegetation and water.
        Distinguishes genuinely barren ground from a fallow field, which matters
        because a fallow farm is still farmland and must not be rejected.
        """
        swir1 = np.asarray(swir1, dtype=float)
        red = np.asarray(red, dtype=float)
        nir = np.asarray(nir, dtype=float)
        blue = np.asarray(blue, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            num = (swir1 + red) - (nir + blue)
            den = (swir1 + red) + (nir + blue)
            bsi = num / (den + _EPS)
            bsi = np.where(np.isfinite(bsi), np.clip(bsi, -1, 1), np.nan)
        return bsi

    @staticmethod
    def calculate_mndwi(green, swir1):
        """
        MNDWI (Modified Normalized Difference Water Index)
            = (GREEN - SWIR1) / (GREEN + SWIR1).

        Preferred over NDWI for open water: it suppresses the built-up
        false-positives that NDWI is prone to, because water absorbs SWIR far
        more strongly than built surfaces do. Strongly positive over water
        bodies, negative over vegetation and soil.
        """
        green = np.asarray(green, dtype=float)
        swir1 = np.asarray(swir1, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            mndwi = (green - swir1) / (green + swir1 + _EPS)
            mndwi = np.where(np.isfinite(mndwi), np.clip(mndwi, -1, 1), np.nan)
        return mndwi

    @staticmethod
    def calculate_kndvi(nir, red):
        """
        kNDVI (kernel NDVI) = tanh(NDVI^2).

        Saturation-resistant, higher SNR variant of NDVI. Simplified fixed-
        sigma form (sigma = 0.5*(NIR+RED)) which reduces to tanh(NDVI^2).

        ⚠ NOT suitable for discrimination: squaring NDVI discards its sign, so
        open water (NDVI -0.30) and sparse crop (NDVI +0.30) both map to 0.0876.
        Fine as a vigor index; do not use it to tell surfaces apart.
        """
        nir = np.asarray(nir, dtype=float)
        red = np.asarray(red, dtype=float)
        ndvi = DataProcessor.calculate_ndvi(nir, red)
        with np.errstate(invalid='ignore'):
            kndvi = np.tanh(ndvi ** 2)
            kndvi = np.where(np.isfinite(kndvi), np.clip(kndvi, 0, 1), np.nan)
        return kndvi

    @staticmethod
    def calculate_ndre(nir, rededge1):
        """NDRE = (NIR - RedEdge1) / (NIR + RedEdge1). Chlorophyll / N status."""
        nir = np.asarray(nir, dtype=float)
        re1 = np.asarray(rededge1, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            ndre = (nir - re1) / (nir + re1 + _EPS)
            ndre = np.where(np.isfinite(ndre), np.clip(ndre, -1, 1), np.nan)
        return ndre

    @staticmethod
    def calculate_psri(red, blue, rededge2):
        """PSRI (Plant Senescence Reflectance Index) = (RED - BLUE) / RedEdge2."""
        red = np.asarray(red, dtype=float)
        blue = np.asarray(blue, dtype=float)
        re2 = np.asarray(rededge2, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            psri = (red - blue) / (re2 + _EPS)
            psri = np.where(np.isfinite(psri), np.clip(psri, -1, 1), np.nan)
        return psri

    @staticmethod
    def calculate_ndwi(green, nir):
        """NDWI (McFeeters, water/moisture) = (GREEN - NIR) / (GREEN + NIR)."""
        green = np.asarray(green, dtype=float)
        nir = np.asarray(nir, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            ndwi = (green - nir) / (green + nir + _EPS)
            ndwi = np.where(np.isfinite(ndwi), np.clip(ndwi, -1, 1), np.nan)
        return ndwi

    # =====================================================================
    # PILLAR 1 — SAR (Sentinel-1) INDICES
    # Inputs are linear-power backscatter (NOT dB). Convert dB->linear with
    # db_to_linear() first if your source provides dB.
    # =====================================================================

    @staticmethod
    def db_to_linear(db):
        """Convert backscatter from dB to linear power: 10^(dB/10)."""
        db = np.asarray(db, dtype=float)
        return np.power(10.0, db / 10.0)

    @staticmethod
    def calculate_rvi(vv, vh):
        """
        RVI (Radar Vegetation Index) = 4*VH / (VV + VH), linear power inputs.

        Cloud-proof biomass proxy. ~0 for bare/smooth surfaces, rises toward
        ~1 for dense random-oriented canopy. Used to fill optical gaps in the
        monsoon and to confirm green-up under cloud.
        """
        vv = np.asarray(vv, dtype=float)
        vh = np.asarray(vh, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            rvi = (4.0 * vh) / (vv + vh + _EPS)
            rvi = np.where(np.isfinite(rvi), np.clip(rvi, 0, 1), np.nan)
        return rvi

    @staticmethod
    def calculate_vh_vv_ratio(vh, vv):
        """
        Cross-pol ratio VH/VV (linear). Sharp drop then rise is the classic
        paddy transplanting / flooding signature used to confirm kharif sowing.
        """
        vh = np.asarray(vh, dtype=float)
        vv = np.asarray(vv, dtype=float)
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = vh / (vv + _EPS)
            ratio = np.where(np.isfinite(ratio), np.clip(ratio, 0, 5), np.nan)
        return ratio

    # =====================================================================
    # PILLAR 1 — TIME-SERIES SMOOTHING
    # Operate on a 1-D scalar series (one value per temporal bin). NaN =
    # missing bin; smoothers gap-fill it. Never mutate the input.
    # =====================================================================

    @staticmethod
    def whittaker_smooth(
        y: np.ndarray,
        lmbd: float = 10.0,
        d: int = 2,
        weights: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Whittaker smoother (Eilers 2003) with missing-data support.

        Minimises  (y - z)' W (y - z) + lmbd * || D^d z ||^2
        where W is a diagonal weight matrix (0 for NaN/missing bins) and D^d is
        the d-th order difference operator. This simultaneously smooths and
        gap-fills, which is exactly what the 10-day composited series needs.

        Args:
            y:       1-D series (NaN allowed for missing bins).
            lmbd:    smoothness (higher = smoother). ~10-100 for 10-day grids.
            d:       difference order (2 = penalise curvature; recommended).
            weights: optional per-bin weights in [0,1]; NaN bins forced to 0.

        Returns:
            Smoothed, gap-filled series (same length as y).
        """
        y = np.asarray(y, dtype=float)
        n = y.size
        if n == 0:
            return y.copy()
        if n <= d + 1:
            # Too short to difference; fall back to mean-fill.
            filled = y.copy()
            m = np.nanmean(filled) if np.any(np.isfinite(filled)) else 0.0
            filled[~np.isfinite(filled)] = m
            return filled

        finite = np.isfinite(y)
        if not finite.any():
            return np.zeros(n, dtype=float)

        w = np.ones(n, dtype=float) if weights is None else np.asarray(weights, dtype=float).copy()
        w = np.where(finite, np.clip(w, 0.0, 1.0), 0.0)
        y0 = np.where(finite, y, 0.0)

        if _SCIPY_OK:
            D = _sp.eye(n, format='csc')
            for _ in range(d):
                D = D[1:] - D[:-1]
            W = _sp.diags(w, 0, format='csc')
            A = (W + lmbd * (D.transpose() @ D)).tocsc()
            try:
                z = _spla.spsolve(A, w * y0)
                if np.all(np.isfinite(z)):
                    return np.asarray(z, dtype=float)
            except Exception:
                pass  # fall through to dense

        # Dense numpy fallback (fine for the ~100-150 bins we use).
        D = np.eye(n)
        for _ in range(d):
            D = np.diff(D, axis=0)
        W = np.diag(w)
        A = W + lmbd * (D.T @ D)
        try:
            z = np.linalg.solve(A, w * y0)
        except np.linalg.LinAlgError:
            z = np.linalg.lstsq(A, w * y0, rcond=None)[0]
        return np.asarray(z, dtype=float)

    @staticmethod
    def savgol_smooth(
        y: np.ndarray,
        window: int = 7,
        poly: int = 2,
    ) -> np.ndarray:
        """
        Savitzky-Golay smoother. NaNs are linearly pre-filled (endpoints held)
        so the polynomial fit is stable, then smoothed. Falls back to a simple
        moving average when scipy is unavailable or the window is invalid.
        """
        y = np.asarray(y, dtype=float)
        n = y.size
        if n == 0:
            return y.copy()

        # Pre-fill NaNs by linear interpolation over index.
        idx = np.arange(n)
        finite = np.isfinite(y)
        if not finite.any():
            return np.zeros(n, dtype=float)
        filled = np.interp(idx, idx[finite], y[finite])

        win = int(window)
        if win % 2 == 0:
            win += 1
        win = max(3, min(win, n if n % 2 == 1 else n - 1))
        if win < 3 or poly >= win:
            return filled

        if _SCIPY_OK:
            try:
                return np.asarray(_savgol_filter(filled, win, poly), dtype=float)
            except Exception:
                pass

        # Moving-average fallback
        kernel = np.ones(win) / win
        return np.convolve(filled, kernel, mode='same')

    @staticmethod
    def smooth_series(
        y: np.ndarray,
        method: str = 'whittaker',
        lmbd: float = 10.0,
        d: int = 2,
        window: int = 7,
        poly: int = 2,
        weights: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Dispatch to the configured smoother ('whittaker' | 'savgol')."""
        if method == 'savgol':
            return DataProcessor.savgol_smooth(y, window=window, poly=poly)
        return DataProcessor.whittaker_smooth(y, lmbd=lmbd, d=d, weights=weights)

    # =====================================================================
    # PILLAR 1 — PER-BIN QUALITY + OPTICAL/SAR FUSION
    # =====================================================================

    @staticmethod
    def bin_quality(
        valid_pixel_fraction: Optional[float],
        cloud_prob: Optional[float] = None,
        parcel_area_ha: Optional[float] = None,
        min_area_ha: float = 0.2,
    ) -> float:
        """
        Per-bin optical quality in [0, 1] used as the fusion weight and as an
        input to the Data-Confidence sub-index.

        quality = f(valid pixel fraction, 1 - cloud probability, parcel-size factor)

        - valid_pixel_fraction: fraction of AOI pixels that survived masking.
        - cloud_prob:           mean cloud probability [0,1] (optional).
        - parcel_area_ha:       small parcels -> mixed pixels -> lower ceiling.
        """
        vpf = 0.0 if valid_pixel_fraction is None else float(np.clip(valid_pixel_fraction, 0.0, 1.0))
        clr = 1.0 if cloud_prob is None else float(np.clip(1.0 - cloud_prob, 0.0, 1.0))

        if parcel_area_ha is None:
            size_factor = 1.0
        else:
            # 0.2 ha -> ~0.6, 1 ha -> ~0.9, >=2 ha -> 1.0
            a = max(float(parcel_area_ha), 0.0)
            size_factor = float(np.clip(0.5 + 0.5 * np.sqrt(a / max(min_area_ha, 1e-6)) / np.sqrt(10.0), 0.4, 1.0))

        q = vpf * clr * size_factor
        return float(np.clip(q, 0.0, 1.0))

    @staticmethod
    def fuse_optical_sar(
        optical: np.ndarray,
        sar: Optional[np.ndarray],
        quality: Optional[np.ndarray],
        min_pairs: int = 6,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Build the composite vegetation signal by blending an optical series with
        a SAR-derived proxy, weighted by per-bin optical quality:

            VS = q * optical + (1 - q) * sar_scaled

        SAR is first rescaled to the optical range. When >= ``min_pairs`` bins
        have BOTH a clear optical value and a SAR value, a per-parcel linear
        regression (sar -> optical) is used; otherwise a global min-max match.

        Returns:
            (fused_series, source_code) where source_code per bin is:
              0 = optical, 1 = fused (optical present but low quality),
              2 = sar-only (optical missing), 3 = imputed (neither present).
        """
        optical = np.asarray(optical, dtype=float)
        n = optical.size
        source = np.zeros(n, dtype=int)

        if quality is None:
            quality = np.where(np.isfinite(optical), 1.0, 0.0)
        quality = np.clip(np.asarray(quality, dtype=float), 0.0, 1.0)

        if sar is None:
            fused = optical.copy()
            source[~np.isfinite(optical)] = 3
            return fused, source

        sar = np.asarray(sar, dtype=float)

        opt_ok = np.isfinite(optical)
        sar_ok = np.isfinite(sar)
        both = opt_ok & sar_ok

        # Rescale SAR into optical units.
        sar_scaled = np.full(n, np.nan, dtype=float)
        if both.sum() >= min_pairs:
            # Robust-ish least squares sar->optical on paired bins.
            xs = sar[both]
            ys = optical[both]
            A = np.vstack([xs, np.ones_like(xs)]).T
            try:
                slope, intercept = np.linalg.lstsq(A, ys, rcond=None)[0]
                sar_scaled[sar_ok] = slope * sar[sar_ok] + intercept
            except np.linalg.LinAlgError:
                both = np.zeros(n, dtype=bool)  # force global fallback below
        if not (both.sum() >= min_pairs):
            # Global min-max match of SAR distribution onto optical distribution.
            if opt_ok.any() and sar_ok.any():
                o_lo, o_hi = np.nanpercentile(optical[opt_ok], [5, 95])
                s_lo, s_hi = np.nanpercentile(sar[sar_ok], [5, 95])
                if s_hi > s_lo:
                    sar_scaled[sar_ok] = o_lo + (sar[sar_ok] - s_lo) * (o_hi - o_lo) / (s_hi - s_lo)
                else:
                    sar_scaled[sar_ok] = np.nanmean(optical[opt_ok])
        sar_scaled = np.clip(sar_scaled, -1.0, 1.5)

        fused = np.full(n, np.nan, dtype=float)
        for i in range(n):
            o, s, q = optical[i], sar_scaled[i], quality[i]
            o_present = np.isfinite(o)
            s_present = np.isfinite(s)
            if o_present and q >= 0.999:
                fused[i] = o
                source[i] = 0
            elif o_present and s_present:
                fused[i] = q * o + (1.0 - q) * s
                source[i] = 1 if q > 0 else 2
            elif o_present:
                fused[i] = o
                source[i] = 0
            elif s_present:
                fused[i] = s
                source[i] = 2
            else:
                fused[i] = np.nan
                source[i] = 3

        return fused, source


__all__ = ['DataProcessor']
