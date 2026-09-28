"""
Cross-region benchmark harness.

`classification_model.md` L.7 names the LOEO collapse as a blocker but the ship
gates never measured it per experiment, so nothing could be tuned against it.
This module makes leave-one-ecoregion-out the primary number and gives feature
work an A/B bench.

Why this exists at all: blocked CV holds out *parcels* from regions the model
has already seen. Village mapping asks it for regions it has not. Measured on
`03_features_tier1.parquet`, that gap is 0.7492 -> 0.19. Optimising the blocked
number has been optimising the wrong thing for the deployment we actually want.

Recipes are registered feature transforms. Each returns a matrix from the
feature frame, so a change can be scored against the shipped baseline under an
identical protocol:

    python -m src.crossregion --list
    python -m src.crossregion --recipe baseline
    python -m src.crossregion --recipe baseline --recipe shape_only --blocked
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

from . import _bootstrap  # noqa: F401  (sys.path, .env, native DLLs)
from .features import META_COLS

log = logging.getLogger("crossregion")

SEED = 42
DATA = Path(__file__).resolve().parent.parent / "data"
REPORTS = Path(__file__).resolve().parent.parent / "reports"

# Ecoregions large enough to serve as a held-out fold. CENTRAL_HIGHLAND_MIXED
# (n=2) and NORTH_WEST_SEMI_ARID (n=71) stay permanently in train -- holding out
# 2 rows measures noise, and a fold that small destabilises the mean.
FOLD_ECOREGIONS = [
    "GANGETIC_AND_EASTERN_PLAINS",
    "DECCAN_PLATEAU",
    "SOUTHERN_PENINSULA",
    "INDIA_UNSPECIFIED_PLAINS",
]
MIN_FOLD_ROWS = 200


# =============================================================================
# feature recipes
# =============================================================================
RECIPES: Dict[str, Callable[[pd.DataFrame], Tuple[np.ndarray, List[str]]]] = {}


def recipe(name: str):
    def wrap(fn):
        RECIPES[name] = fn
        return fn
    return wrap


def _base_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c not in META_COLS]


TS_RE = __import__("re").compile(r"^(?P<idx>.+)_t(?P<t>\d\d)$")


def _timeseries_groups(cols: List[str]) -> Dict[str, List[str]]:
    """Group `NDVI_t01..NDVI_t15` style columns by index name, in time order."""
    g: Dict[str, List[str]] = {}
    for c in cols:
        m = TS_RE.match(c)
        if m:
            g.setdefault(m.group("idx"), []).append(c)
    return {k: sorted(v, key=lambda c: int(TS_RE.match(c).group("t")))
            for k, v in g.items()}


@recipe("baseline")
def _baseline(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """The shipped tier-1 vector, unchanged. Every other recipe is scored
    against this."""
    cols = _base_cols(df)
    return df[cols].to_numpy(dtype=np.float32), cols


@recipe("shape_only")
def _shape_only(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """Per-cycle z-scored trajectories: level and amplitude removed, shape kept.

    An index trajectory carries two things -- the *shape* of the crop's growth
    (agronomy, transfers between regions) and its *level* (soil brightness,
    background wetness, atmospheric state -- geography, which does not). The
    shipped vector hands the model both, and adversarial validation says it
    leans on the second: ecoregion is recoverable at 0.53 vs 0.167 chance.

    Each index is rescaled inside its own cycle, so a Punjab and a Telangana
    rice curve of the same shape become the same vector. The level is not
    thrown away -- it returns as two explicit scalars per index (mean, range),
    so the model can still use it where it genuinely discriminates rather than
    having it smeared across all 15 points.
    """
    cols = _base_cols(df)
    groups = _timeseries_groups(cols)
    scalars = [c for c in cols if not TS_RE.match(c)]

    out, names = [], []
    for idx, members in groups.items():
        M = df[members].to_numpy(dtype=np.float64)
        mu = np.nanmean(M, axis=1, keepdims=True)
        sd = np.nanstd(M, axis=1, keepdims=True)
        out.append((M - mu) / np.where(sd < 1e-6, 1.0, sd))
        names += [f"{c}_shape" for c in members]
        out.append(np.hstack([mu, sd]))
        names += [f"{idx}_level_mean", f"{idx}_level_scale"]

    out.append(df[scalars].to_numpy(dtype=np.float64))
    names += scalars
    X = np.nan_to_num(np.hstack(out), nan=0.0, posinf=0.0, neginf=0.0)
    return X.astype(np.float32), names


def _standardize_within(X: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Z-score every column inside each region independently.

    MEASURED AND REJECTED -- kept so the result is not rediscovered the hard way.

    The argument was that this is transductive domain adaptation and honest for
    this deployment: village mapping processes a whole area at once, so the
    target region's feature spread is available before any label is, and
    removing a per-region offset should cancel most of what LOEO punishes.

    It does the opposite, and badly:

        recipe                  LOEO      adversarial ecoregion
        baseline                0.1444    0.7890
        region_standardized     0.0532    0.9987
        shape_region_std        0.0501    0.9989
        region_rank             0.0637    1.0000   (perfect)

    The adversarial column is the explanation. Each region has a different crop
    mix, so its per-column mean is a different quantity; subtracting it
    subtracts something region-specific from every row and stamps the region
    into the values rather than removing it. Region identity becomes *perfectly*
    recoverable -- the exact failure this was meant to prevent. Rank-normalising
    is worst of all, because it discards the magnitudes that carried the
    remaining crop signal while keeping the region-relative ordering.

    Any future attempt at region normalisation has to be checked against the
    adversarial score, not just the LOEO score.
    """
    Z = np.empty_like(X, dtype=np.float64)
    for r in np.unique(region):
        m = region == r
        blk = X[m].astype(np.float64)
        mu = np.nanmean(blk, axis=0)
        sd = np.nanstd(blk, axis=0)
        Z[m] = (blk - mu) / np.where(sd < 1e-9, 1.0, sd)
    return np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)


@recipe("region_standardized")
def _region_std(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """REJECTED (0.0532 vs 0.1444). Z-score within ecoregion -- see
    `_standardize_within` for why it makes region *perfectly* recoverable."""
    cols = _base_cols(df)
    X = df[cols].to_numpy(dtype=np.float64)
    return _standardize_within(X, df.ecoregion.to_numpy()).astype(np.float32), cols


@recipe("shape_region_std")
def _shape_region_std(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """REJECTED (0.0501 vs 0.1444). `shape_only` then `region_standardized`;
    inherits the latter's failure -- see `_standardize_within`."""
    X, names = _shape_only(df)
    Z = _standardize_within(X.astype(np.float64), df.ecoregion.to_numpy())
    return Z.astype(np.float32), names


WEATHER_PATH = DATA / "04_weather_cycle.parquet"
# Only thermal-time and anomaly columns. `rh_mean` and `weather_days` are
# deliberately excluded -- mean humidity is a regional constant and would hand
# the model the location it is meant to be blind to.
WEATHER_COLS = [
    "gdd_total", "log_gdd_total", "gdd_per_day", "gdd_peak_fraction",
    "rain_anomaly_ratio", "gdd_anomaly_ratio", "t2m_anomaly_c",
    "dry_spell_max_days", "rain_days_fraction",
]


def _attach_weather(df: pd.DataFrame) -> pd.DataFrame:
    if not WEATHER_PATH.exists():
        raise SystemExit(f"{WEATHER_PATH} missing -- run `python -m src.weather "
                         f"--fetch --build`")
    w = pd.read_parquet(WEATHER_PATH)
    keys = ["geom_hash", "cycle_index"]
    if "cycle_index" not in df.columns:
        cyc = pd.read_parquet(DATA / "02_cycles.parquet")[keys + ["sowing_date"]]
        df = df.merge(cyc, on=["geom_hash", "sowing_date"], how="left")
    return df.merge(w[keys + WEATHER_COLS], on=keys, how="left")


@recipe("weather_thermal")
def _weather_thermal(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """Shipped features + thermal time and weather anomalies.

    The case for thermal time is visible before any model runs. Wheat, maize
    and rice are nearly inseparable by calendar duration -- 120, 110 and 111
    median days -- yet their heat budgets are 1176, 1607 and 2090 GDD. Rice
    needs 1.8x wheat's accumulated warmth to finish. `duration_days` and
    `log_duration` are both top-ten features by gain today, so the model is
    already leaning hard on a quantity that cannot tell these three apart;
    Wheat->Maize is 13.5% of all wheat in the shipped confusion matrix.

    Nothing absolute enters -- see WEATHER_COLS and the src/weather.py
    docstring for why adding raw rainfall would make cross-region worse.
    """
    d = _attach_weather(df)
    cols = _base_cols(df) + WEATHER_COLS
    X = d[cols].to_numpy(dtype=np.float64)
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32), cols


@recipe("weather_shape")
def _weather_shape(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """The two measures that actually transferred, combined: per-cycle shape
    normalisation (+0.0175) and thermal time + anomalies (+0.0526).

    Region standardisation is deliberately NOT applied on top -- it was measured
    and rejected (see `_standardize_within`). Both survivors are geography-free
    by construction, which is why they transfer where normalisation tricks did
    not: a crop's growth *shape* and its *heat requirement* are properties of
    the plant, while a region-relative z-score is a property of the region.
    """
    d = _attach_weather(df)
    Xs, names = _shape_only(df)
    W = np.nan_to_num(d[WEATHER_COLS].to_numpy(dtype=np.float64), nan=0.0,
                      posinf=0.0, neginf=0.0)
    X = np.hstack([Xs.astype(np.float64), W])
    return X.astype(np.float32), names + WEATHER_COLS


# The same block minus the two absolute degree-day sums. `gdd_anomaly_ratio`
# survives and carries the same phenological meaning relative to local climate.
WEATHER_COLS_RELATIVE = [c for c in WEATHER_COLS
                         if c not in ("gdd_total", "log_gdd_total")]


@recipe("weather_shape_relative")
def _weather_shape_relative(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """REFUTED the hypothesis it was built to test. Keep for the record.

    Adding thermal time lifted accuracy on both axes but pushed the ecoregion
    adversarial score 0.7890 -> 0.8993. The guess was that an absolute
    degree-day sum is partly a latitude reading, so dropping `gdd_total` /
    `log_gdd_total` should give the geography back for free:

        metric        weather_shape    this recipe
        LOEO          0.2026           0.1999
        blocked       0.8081           0.8045
        adversarial   0.8993           0.8995   <-- unmoved

    It cost a little accuracy and recovered no ground at all, so the absolute
    sums are not the leak. The likelier culprits are the columns this recipe
    *keeps*: `gdd_per_day` is total GDD over duration, i.e. mean daily
    temperature, and `dry_spell_max_days` / `rain_days_fraction` describe a
    climatic regime. See src/weather.py -- the useful distinction is not
    absolute vs. ratio but property-of-the-plant vs. property-of-the-place.
    """
    d = _attach_weather(df)
    Xs, names = _shape_only(df)
    W = np.nan_to_num(d[WEATHER_COLS_RELATIVE].to_numpy(dtype=np.float64),
                      nan=0.0, posinf=0.0, neginf=0.0)
    X = np.hstack([Xs.astype(np.float64), W])
    return X.astype(np.float32), names + WEATHER_COLS_RELATIVE


# Columns that describe the *place* rather than the plant. `gdd_per_day` is
# total degree days over duration -- mean daily temperature, a latitude reading.
# The two rainfall-regime columns say what the climate is like, not what the
# crop did. `gdd_total` is deliberately NOT here: a heat budget to maturity is a
# property of the variety, and removing it was already measured as a loss.
PLACE_COLS = ["gdd_per_day", "dry_spell_max_days", "rain_days_fraction",
              "t2m_anomaly_c"]
WEATHER_COLS_PLANT = [c for c in WEATHER_COLS if c not in PLACE_COLS]


@recipe("weather_shape_plant")
def _weather_shape_plant(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """REJECTED (0.1730 vs weather_shape's 0.2026). The third and last attempt
    to separate crop signal from geography by deleting columns.

    Dropping `gdd_per_day` (mean daily temperature under another name) and the
    rainfall-regime columns finally moved the adversarial score -- 0.8993 ->
    0.8533, the first real movement of the three attempts -- and cost 3.0 points
    of LOEO and 2.2 of blocked accuracy to do it.

    Taken with `weather_shape_relative`, the conclusion is not about which
    columns to cut. **In this dataset geography and crop signal cannot be
    separated by feature surgery at all**, because the crops genuinely are
    regionally distributed: 8 of 18 occupy a single ecoregion. Any feature
    informative about the crop is therefore informative about the region, and
    every cut trades away more crop signal than nuisance.

    That closes the feature-engineering route to cross-region accuracy. What
    remains is geographic replication in the training set -- see src/augment.py.
    """
    d = _attach_weather(df)
    Xs, names = _shape_only(df)
    W = np.nan_to_num(d[WEATHER_COLS_PLANT].to_numpy(dtype=np.float64),
                      nan=0.0, posinf=0.0, neginf=0.0)
    X = np.hstack([Xs.astype(np.float64), W])
    return X.astype(np.float32), names + WEATHER_COLS_PLANT


# =============================================================================
# tier-2 blocks (03_features_tier2*.parquet, see src/features_extra.py)
# =============================================================================
def _tier2_cols():
    from crop_analysis.extra_features import (
        embedding_feature_names, refl_feature_names, s1_feature_names, weather_feature_names,
    )
    return {"wx": weather_feature_names(), "s1": s1_feature_names(),
            "refl": refl_feature_names(), "emb": embedding_feature_names()}


def _tier1_cols(df: pd.DataFrame) -> List[str]:
    extra = {c for cols in _tier2_cols().values() for c in cols}
    return [c for c in _base_cols(df) if c not in extra]


def _tier2_recipe(blocks: Tuple[str, ...], shape: bool):
    def fn(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
        t1 = df[_tier1_cols(df)]
        if shape:
            X1, names = _shape_only(t1)
        else:
            X1, names = t1.to_numpy(dtype=np.float32), list(t1.columns)
        parts, cols = [X1.astype(np.float64)], list(names)
        groups = _tier2_cols()
        for b in blocks:
            have = [c for c in groups[b] if c in df.columns]
            # NaN stays NaN: XGBoost routes it down a learned missing branch,
            # exactly as in production when an input source is unavailable.
            parts.append(df[have].to_numpy(dtype=np.float64))
            cols += have
        return np.hstack(parts).astype(np.float32), cols
    fn.__doc__ = f"tier-1{' (shape-normalised)' if shape else ''} + {'+'.join(blocks) or 'nothing'}"
    return fn


for _blocks in [(), ("wx",), ("s1",), ("refl",), ("emb",), ("wx", "s1"),
                ("wx", "s1", "refl"), ("wx", "s1", "refl", "emb"), ("wx", "emb"),
                ("wx", "s1", "emb")]:
    for _shape in (False, True):
        _n = ("t2" + ("s" if _shape else "") + "_" + ("_".join(_blocks) or "t1only"))
        RECIPES[_n] = _tier2_recipe(_blocks, _shape)


@recipe("region_rank")
def _region_rank(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """REJECTED (0.0637 vs 0.1444, adversarial 1.0000 -- region becomes
    perfectly recoverable).

    Intended as the upper-bound experiment for "how much does absolute level
    matter": invariant to any monotone regional distortion, not just shift and
    scale. It answered the question, just not in the expected direction -- rank
    within region discards the magnitudes carrying crop signal and keeps the
    region-relative ordering, which is pure geography. See `_standardize_within`.
    """
    cols = _base_cols(df)
    X = df[cols].to_numpy(dtype=np.float64)
    region = df.ecoregion.to_numpy()
    R = np.empty_like(X)
    for r in np.unique(region):
        m = region == r
        R[m] = pd.DataFrame(X[m]).rank(pct=True, axis=0).to_numpy()
    return np.nan_to_num(R, nan=0.5).astype(np.float32), cols


# =============================================================================
# coarse groups  (classification_model.md E.4 -- designed, never implemented)
# =============================================================================
# The argument for these: a flat 18-way softmax spends capacity on distinctions
# the data cannot support, and downstream a *correct coarse label* is worth far
# more than a confident wrong species -- `CropGrowthCurves` for the group beats
# `Unknown`, and a wrong crop name swings up to 45% of the risk index.
#
# Whether the coarse label survives region shift is an empirical question that
# has never been asked. `--hierarchical` asks it.
COARSE_GROUPS = {
    "Banana": "PERENNIAL_LONG", "Sugarcane": "PERENNIAL_LONG",
    "Grapes": "PERENNIAL_LONG",
    "Cotton": "LONG_ANNUAL", "Tur": "LONG_ANNUAL",
    "Tobacco": "LONG_ANNUAL", "Chilli": "LONG_ANNUAL",
    "Rice": "MED_ANNUAL", "Wheat": "MED_ANNUAL", "Mustard": "MED_ANNUAL",
    "Gram": "MED_ANNUAL", "Onion": "MED_ANNUAL", "Jowar": "MED_ANNUAL",
    "Maize": "MED_ANNUAL", "Potato": "MED_ANNUAL", "Groundnut": "MED_ANNUAL",
    "Soyabean": "MED_ANNUAL",
    "Bajra": "SHORT_ANNUAL",
}

# A functional alternative. E.4's grouping is duration-driven, which puts 10 of
# 18 crops in MED_ANNUAL -- a coarse label that broad carries little downstream
# information. This one groups by what the crop *is*, which is what a lender's
# risk table actually keys on.
FUNCTIONAL_GROUPS = {
    "Rice": "CEREAL", "Wheat": "CEREAL", "Maize": "CEREAL",
    "Bajra": "CEREAL", "Jowar": "CEREAL",
    "Gram": "PULSE", "Tur": "PULSE",
    "Groundnut": "OILSEED", "Mustard": "OILSEED", "Soyabean": "OILSEED",
    "Cotton": "FIBRE", "Tobacco": "FIBRE",
    "Banana": "PERENNIAL", "Sugarcane": "PERENNIAL", "Grapes": "PERENNIAL",
    "Onion": "HORTICULTURE", "Potato": "HORTICULTURE", "Chilli": "HORTICULTURE",
}


def _coarse_score(y: np.ndarray, pred: np.ndarray, classes: List[str],
                  mapping: Dict[str, str]) -> Dict:
    """Collapse a fine-grained prediction onto groups and score it there.

    Note this scores the *existing* flat model's predictions after collapsing,
    not a separately trained coarse model. It answers 'is the group right even
    when the species is wrong', which is the question that decides whether a
    coarse label is shippable today.
    """
    from sklearn.metrics import balanced_accuracy_score, f1_score

    gt = np.array([mapping[classes[i]] for i in y])
    gp = np.array([mapping[classes[i]] for i in pred])
    groups = sorted(set(mapping.values()))
    per = {}
    for g in groups:
        t, p = gt == g, gp == g
        if t.sum() == 0:
            continue
        per[g] = {
            "recall": round(float((t & p).sum() / t.sum()), 4),
            "precision": round(float((t & p).sum() / max(p.sum(), 1)), 4),
            "support": int(t.sum()),
        }
    return {
        "balanced_accuracy": round(float(balanced_accuracy_score(gt, gp)), 4),
        "macro_f1": round(float(f1_score(gt, gp, average="macro")), 4),
        "accuracy": round(float((gt == gp).mean()), 4),
        "n_groups": len(per),
        "per_group": per,
    }


# =============================================================================
# season mask  (classification_model.md E.5 -- designed, never implemented)
# =============================================================================
# Reference seasons, copied from `PipelineConfig.CROP_DURATIONS[crop]['season']`
# -- ICAR agronomy that already governs the downstream curves.
#
# It matters enormously that this table is transcribed from the *reference*, not
# learned from the training data. The observed season distribution here is
# confounded: attribution picked the cycle nearest a survey `Date` that A.3
# showed is near-constant within a class, so "Soyabean is 100% kharif" in this
# dataset is partly agronomy and partly an artifact of that selection. A learned
# season prior would absorb the artifact. A transcribed table cannot -- and a
# lender can read and challenge it, which a learned weight does not allow.
#
# The other reason this is worth trying: season is derived from the *detected
# cycle's own dates*, so it is available at inference by construction, and it is
# national. Unlike every optical feature in the vector, it does not encode
# where the parcel is -- which is exactly the property LOEO rewards.
REFERENCE_SEASON = {
    "Bajra": "kharif", "Jowar": "both", "Maize": "both", "Rice": "kharif",
    "Wheat": "rabi", "Groundnut": "kharif", "Mustard": "rabi",
    "Soyabean": "kharif", "Tobacco": "rabi", "Gram": "rabi", "Tur": "kharif",
    "Cotton": "kharif", "Chilli": "both", "Onion": "both", "Potato": "rabi",
    "Banana": "perennial", "Grapes": "rabi", "Sugarcane": "kharif",
}

# Soft, not zero. Farmers plant off-season, our dates are quantised to 10-day
# bins, and `cross_season` cycles are genuinely ambiguous. A hard mask would
# convert a recoverable error into an unrecoverable one.
SOFT_PENALTY = 0.15


def sowing_season(sowing_date: np.ndarray) -> np.ndarray:
    """Season of *sowing* -- an alternative mask key. Kept, but NOT the default.

    The reasoning for it was sound and the measurement refused it. The detector
    assigns `season_type` by calendar overlap rather than sowing month
    (`crop_cycle_detector.assign_season`), so a 196-day cotton cycle sown in
    June comes back `rabi` and a mask keyed on it penalises the right answer --
    Cotton loses 3.5 points of recall. Keying on the sowing month does repair
    Cotton (-0.035 -> +0.010) but costs more than it recovers elsewhere, most
    sharply Tobacco (-0.105), which is nursery-sown in the kharif/rabi shoulder
    and lands on the wrong side of any month boundary. Widening the boundary to
    leave shoulder months unpenalised did not rescue it either:

        no mask                    0.7492
        mask on season_type        0.7547   <- default
        mask on sowing month       0.7485
        sowing, shoulders ignored  0.7488 / 0.7420 / 0.7466

    Retained so LOEO can re-test it: these are in-region numbers, and the
    cross-region ranking is a separate question.
    """
    m = pd.to_datetime(pd.Series(sowing_date)).dt.month.to_numpy()
    out = np.full(len(m), "zaid", dtype=object)
    out[(m >= 6) & (m <= 9)] = "kharif"
    out[(m >= 10) | (m <= 1)] = "rabi"
    return out


def season_mask(observed: np.ndarray, classes: List[str]) -> np.ndarray:
    """(n, n_classes) multiplier applied to the posterior before argmax."""
    M = np.ones((len(observed), len(classes)))
    for j, crop in enumerate(classes):
        ref = REFERENCE_SEASON.get(crop, "both")
        if ref in ("both", "perennial"):
            continue        # compatible with anything -- no evidence to add
        # cross_season and zaid cycles are never penalised: the detector is
        # telling us it could not place the cycle in a clean season, which is
        # not evidence against any crop.
        bad = (observed == ("rabi" if ref == "kharif" else "kharif"))
        M[bad, j] = SOFT_PENALTY
    return M


def apply_season_mask(proba: np.ndarray, observed: np.ndarray,
                      classes: List[str]) -> np.ndarray:
    p = proba * season_mask(observed, classes)
    s = p.sum(axis=1, keepdims=True)
    return np.divide(p, s, out=np.full_like(p, 1.0 / len(classes)), where=s > 0)


# =============================================================================
# protocol
# =============================================================================
def _fit_predict(Xtr, ytr, Xte, classes: List[str]) -> np.ndarray:
    """Train the shipped estimator config and return a proba matrix widened
    back to the full class list.

    XGBoost needs contiguous labels and a held-out region rarely contains all
    18 crops, so absent classes are trained around and re-inserted as zero
    columns. Scoring them as zero is correct: the model genuinely cannot
    predict a class it never saw.
    """
    from xgboost import XGBClassifier

    present = np.unique(ytr)
    remap = {v: i for i, v in enumerate(present)}

    m = XGBClassifier(
        objective="multi:softprob",
        n_estimators=300,
        learning_rate=0.06,
        max_depth=4,
        min_child_weight=10,
        subsample=0.8,
        colsample_bytree=0.6,
        reg_lambda=3.0,
        reg_alpha=0.5,
        tree_method="hist",
        random_state=SEED,
        n_jobs=-1,
        verbosity=0,
    )
    m.fit(Xtr, np.array([remap[v] for v in ytr]))

    part = m.predict_proba(Xte)
    full = np.zeros((len(Xte), len(classes)), dtype=np.float64)
    full[:, present] = part
    return full


def _metrics(y: np.ndarray, proba: np.ndarray, classes: List[str]) -> Dict:
    from sklearn.metrics import balanced_accuracy_score, f1_score

    pred = proba.argmax(1)
    seen = np.unique(y)
    per_class = {}
    for i in seen:
        t = y == i
        p = pred == i
        tp = int((t & p).sum())
        per_class[classes[i]] = {
            "recall": round(tp / max(int(t.sum()), 1), 4),
            "precision": round(tp / max(int(p.sum()), 1), 4),
            "support": int(t.sum()),
        }
    return {
        "balanced_accuracy": round(float(balanced_accuracy_score(y, pred)), 4),
        "macro_f1": round(float(f1_score(y, pred, average="macro", labels=seen)), 4),
        "accuracy": round(float((pred == y).mean()), 4),
        "per_class": per_class,
    }


def replication(y: np.ndarray, eco: np.ndarray, classes: List[str]) -> Dict:
    """How many of the LOEO regions each crop appears in.

    This has to be read before the LOEO score means anything. Six crops in this
    dataset -- Chilli, Maize, Mustard, Rice, Tobacco, Potato -- sit in exactly
    ONE ecoregion. Hold that region out and the crop has *zero* training rows,
    so its recall is 0 by construction. That is not a transfer failure, it is an
    absent class, and averaging the two together produces a number that cannot
    be acted on: no feature work or estimator change can teach a model a crop it
    has never seen.

    `learnable` below is the subset with >= 2 regions -- the crops for which
    "does the signature transfer?" is a question the data can actually answer.
    """
    keep = np.isin(eco, FOLD_ECOREGIONS)
    out = {}
    for i, c in enumerate(classes):
        m = (y == i) & keep
        regions = sorted(set(eco[m]))
        out[c] = {"n": int(m.sum()), "n_regions": len(regions),
                  "regions": regions, "learnable_under_loeo": len(regions) >= 2}
    return out


def loeo(X: np.ndarray, y: np.ndarray, eco: np.ndarray, classes: List[str],
         season: np.ndarray | None = None) -> Dict:
    """Leave-one-ecoregion-out. The deployment number for village mapping.

    Pass `season` to also score the E.5 posterior mask. It is reported
    alongside rather than instead of the raw number, because E.5's own
    instruction was to keep it only if it measurably helps.
    """
    oof_pred = np.full(len(y), -1, dtype=int)
    oof_masked = np.full(len(y), -1, dtype=int)
    folds = []

    for held in FOLD_ECOREGIONS:
        te = eco == held
        if te.sum() < MIN_FOLD_ROWS:
            continue
        tr = ~te
        proba = _fit_predict(X[tr], y[tr], X[te], classes)
        oof_pred[te] = proba.argmax(1)
        if season is not None:
            oof_masked[te] = apply_season_mask(proba, season[te], classes).argmax(1)

        f = _metrics(y[te], proba, classes)
        f["held_out"] = held
        f["n"] = int(te.sum())
        f["n_classes_in_fold"] = int(len(np.unique(y[te])))
        folds.append(f)

    scored = oof_pred >= 0
    agg = _metrics(y[scored], np.eye(len(classes))[oof_pred[scored]], classes)
    agg["folds"] = folds

    # The headline number is dragged down by classes that are absent from
    # training whenever their only region is held out. Report both: the pooled
    # figure is what a naive run reports, the learnable subset is the one that
    # responds to modelling work.
    rep = replication(y, eco, classes)
    learn = [i for i, c in enumerate(classes) if rep[c]["learnable_under_loeo"]]
    learn_mask = scored & np.isin(y, learn)
    agg["replication"] = rep
    agg["n_single_region_classes"] = sum(
        1 for v in rep.values() if v["n_regions"] == 1)
    agg["learnable_subset"] = _metrics(
        y[learn_mask], np.eye(len(classes))[oof_pred[learn_mask]], classes)
    agg["learnable_subset"]["n_classes"] = len(learn)
    agg["mean_fold_balanced_accuracy"] = round(
        float(np.mean([f["balanced_accuracy"] for f in folds])), 4)
    agg["n_scored"] = int(scored.sum())
    agg["coarse_duration"] = _coarse_score(
        y[scored], oof_pred[scored], classes, COARSE_GROUPS)
    agg["coarse_functional"] = _coarse_score(
        y[scored], oof_pred[scored], classes, FUNCTIONAL_GROUPS)
    if season is not None:
        sm = _metrics(y[scored], np.eye(len(classes))[oof_masked[scored]], classes)
        sm["coarse_functional"] = _coarse_score(
            y[scored], oof_masked[scored], classes, FUNCTIONAL_GROUPS)
        sm["delta_balanced_accuracy"] = round(
            sm["balanced_accuracy"] - agg["balanced_accuracy"], 4)
        agg["season_masked"] = sm
    agg["oof_pred"] = oof_pred.tolist()
    return agg


def blocked(X: np.ndarray, y: np.ndarray, blocks: np.ndarray,
            classes: List[str], n_splits: int = 5) -> Dict:
    """Spatially-blocked GroupKFold -- the shipped protocol, kept so a recipe
    that helps cross-region can be checked for in-region damage."""
    from sklearn.model_selection import GroupKFold

    oof_pred = np.full(len(y), -1, dtype=int)
    for tr, te in GroupKFold(n_splits=n_splits).split(X, y, groups=blocks):
        proba = _fit_predict(X[tr], y[tr], X[te], classes)
        oof_pred[te] = proba.argmax(1)

    return _metrics(y, np.eye(len(classes))[oof_pred], classes)


def adversarial_region(X: np.ndarray, eco: np.ndarray,
                       blocks: np.ndarray) -> float:
    """How well the features predict *ecoregion*, over the 4 fold regions.

    READ THIS BEFORE OPTIMISING AGAINST IT. The obvious interpretation -- "lower
    is better, drive it down and transfer improves" -- is wrong, and three
    experiments were spent proving it:

        recipe                   LOEO      blocked   adversarial
        baseline                 0.1444    0.7492    0.7890   <- LOWEST adv
        weather_shape            0.2026    0.8081    0.8993   <- BEST loeo
        weather_shape_relative   0.1999    0.8045    0.8995
        weather_shape_plant      0.1730    0.7860    0.8533
        region_standardized      0.0532    --        0.9987

    Among the weather variants adversarial and LOEO move *together*: the
    baseline has the least recoverable geography and the worst transfer. Every
    attempt to lower the score by deleting "leaky" columns cost more accuracy
    than it bought.

    The score measures how much location information is *present*, not how
    harmfully it is *relied upon*. Those come apart:

      - Features that discriminate crops and happen to correlate with region
        score high and transfer well. Crops really are regionally distributed
        here, so any genuinely crop-discriminative feature will look leaky.
      - Features that carry region identity with no crop content score high and
        transfer catastrophically -- `region_standardized`, 0.9987 and 0.0532.

    So use it as a *diagnostic alongside* LOEO, never as an objective. It earned
    its place by catching the region-standardisation failure, where LOEO alone
    would have shown a drop without explaining it. It has no business selecting
    between two recipes that both improve LOEO.
    """
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import GroupKFold

    keep = np.isin(eco, FOLD_ECOREGIONS)
    Xk, ek, bk = X[keep], eco[keep], blocks[keep]
    codes, uniq = pd.factorize(ek)

    pred = np.full(len(codes), -1, dtype=int)
    for tr, te in GroupKFold(n_splits=5).split(Xk, codes, groups=bk):
        p = _fit_predict(Xk[tr], codes[tr], Xk[te], list(uniq))
        pred[te] = p.argmax(1)
    return round(float(balanced_accuracy_score(codes, pred)), 4)


# =============================================================================
# cli
# =============================================================================
def _load(tier: int, path: str | None = None) -> pd.DataFrame:
    path = DATA / (path or f"03_features_tier{tier}.parquet")
    if not path.exists():
        raise SystemExit(f"missing {path} -- run `python -m src.features` first")
    return pd.read_parquet(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", type=int, default=1)
    ap.add_argument("--recipe", action="append", default=None,
                    help="repeatable; default 'baseline'")
    ap.add_argument("--blocked", action="store_true",
                    help="also run blocked GroupKFold (slower)")
    ap.add_argument("--adversarial", action="store_true",
                    help="also score how well the features predict ecoregion")
    ap.add_argument("--list", action="store_true", help="list recipes and exit")
    ap.add_argument("--features", default=None,
                    help="feature parquet in data/ (overrides --tier)")
    ap.add_argument("--out", default=None, help="write JSON here")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if a.list:
        for k, fn in RECIPES.items():
            print(f"  {k:24s} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return 0

    df = _load(a.tier, a.features)
    classes = sorted(df.Crop_Name.unique())
    y = np.array([classes.index(c) for c in df.Crop_Name])
    eco = df.ecoregion.to_numpy()
    blk = df.block_id.to_numpy()
    season = df.season_type.to_numpy() if "season_type" in df else None

    results = {}
    for name in (a.recipe or ["baseline"]):
        if name not in RECIPES:
            raise SystemExit(f"unknown recipe {name!r}; --list to see them")
        X, cols = RECIPES[name](df)
        log.info("\n=== recipe %s  (%d features) ===", name, len(cols))

        r = {"n_features": len(cols),
             "loeo": loeo(X, y, eco, classes, season=season)}
        L = r["loeo"]
        log.info("  LOEO balanced acc   : %.4f  (mean over folds %.4f)",
                 L["balanced_accuracy"], L["mean_fold_balanced_accuracy"])
        for f in L["folds"]:
            log.info("      %-30s n=%5d  %d classes  %.4f",
                     f["held_out"], f["n"], f["n_classes_in_fold"],
                     f["balanced_accuracy"])
        log.info("  learnable subset    : %.4f  (%d classes in >=2 regions; "
                 "%d crops are single-region and score 0 by construction)",
                 L["learnable_subset"]["balanced_accuracy"],
                 L["learnable_subset"]["n_classes"],
                 L["n_single_region_classes"])
        log.info("  coarse (functional) : %.4f   coarse (duration) : %.4f",
                 L["coarse_functional"]["balanced_accuracy"],
                 L["coarse_duration"]["balanced_accuracy"])
        if "season_masked" in L:
            log.info("  + E.5 season mask   : %.4f  (%+.4f)  coarse %.4f",
                     L["season_masked"]["balanced_accuracy"],
                     L["season_masked"]["delta_balanced_accuracy"],
                     L["season_masked"]["coarse_functional"]["balanced_accuracy"])

        if a.blocked:
            r["blocked"] = blocked(X, y, blk, classes)
            log.info("  blocked balanced acc: %.4f", r["blocked"]["balanced_accuracy"])
        if a.adversarial:
            r["adversarial_ecoregion"] = adversarial_region(X, eco, blk)
            log.info("  ecoregion adversarial: %.4f (chance %.4f -- lower is better)",
                     r["adversarial_ecoregion"], 1 / len(FOLD_ECOREGIONS))
        results[name] = r

    if len(results) > 1:
        log.info("\n=== comparison (vs baseline) ===")
        base = results.get("baseline", {}).get("loeo", {}).get("balanced_accuracy")
        for k, v in results.items():
            d = (f"{v['loeo']['balanced_accuracy'] - base:+.4f}"
                 if base is not None else "--")
            log.info("  %-24s LOEO %.4f  %s", k, v["loeo"]["balanced_accuracy"], d)

    out = Path(a.out) if a.out else REPORTS / f"crossregion_tier{a.tier}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    log.info("\nwrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
