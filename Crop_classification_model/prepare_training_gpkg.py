"""
Build a single balanced training GeoPackage: 500 farms per crop.

Selection rules (applied per crop):
  1. Non-null crop name and Area
  2. Valid, non-empty geometry
  3. Non-null Date (cultivation season / year)
  4. Prefer mid-to-large farm Area (ha), drop extreme outliers
  5. Take the top 500 by Area after ranking
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

FOLDER = Path(__file__).resolve().parent
OUT_PATH = FOLDER / "crop_classification_train_500.gpkg"
SAMPLES_PER_CROP = 500
AREA_MIN = 0.5
AREA_MAX = 50.0  # drop unrealistic aggregated / bad polygons
RANDOM_SEED = 42

# Non-crop / land-cover labels in Others_16000.gpkg
NON_CROP_LABELS = {
    "Forest Area",
    "Forest",
    "Built up Area",
    "Built up area",
    "Barren land",
    "Water Bodies",
}

FILE_SPECS = [
    {"file": "Elai_Cotton_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Gram_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Maize_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Mustard_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Onion_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Potato_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Rice_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Soyabean_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Elai_Wheat_10000.gpkg", "crop_col": "Crop_Name", "date_col": "Date"},
    {"file": "Others_16000.gpkg", "crop_col": "Crop", "date_col": "Date"},
]


def _to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def load_normalized(spec: dict) -> gpd.GeoDataFrame:
    path = FOLDER / spec["file"]
    gdf = gpd.read_file(path, engine="pyogrio")

    crop = gdf[spec["crop_col"]].astype("string").str.strip()
    area = pd.to_numeric(gdf["Area"], errors="coerce")
    date = (
        _to_datetime(gdf[spec["date_col"]])
        if spec["date_col"] in gdf.columns
        else pd.Series(pd.NaT, index=gdf.index)
    )

    return gpd.GeoDataFrame(
        {
            "Crop_Name": crop,
            "Area": area,
            "Date": date,
            "source_file": spec["file"],
        },
        geometry=gdf.geometry,
        crs=gdf.crs,
    )


def quality_filter(gdf: gpd.GeoDataFrame, area_min: float = AREA_MIN) -> gpd.GeoDataFrame:
    m = (
        gdf["Crop_Name"].notna()
        & (gdf["Crop_Name"].str.len() > 0)
        & (~gdf["Crop_Name"].isin(NON_CROP_LABELS))
        & gdf["Area"].notna()
        & (gdf["Area"] >= area_min)
        & (gdf["Area"] <= AREA_MAX)
        & gdf["Date"].notna()
        & gdf.geometry.notna()
        & (~gdf.geometry.is_empty)
        & gdf.geometry.is_valid
    )
    return gdf.loc[m].copy()


def select_top_n(gdf: gpd.GeoDataFrame, n: int = SAMPLES_PER_CROP) -> gpd.GeoDataFrame:
    """Prefer larger Area. Stable tie-break with random key."""
    if len(gdf) == 0:
        return gdf

    ranked = gdf.copy()
    rng = np.random.default_rng(RANDOM_SEED)
    ranked["_rand"] = rng.random(len(ranked))
    ranked = ranked.sort_values(by=["Area", "_rand"], ascending=[False, True])
    return ranked.head(n).drop(columns=["_rand"])


def main() -> None:
    frames: list[gpd.GeoDataFrame] = []
    print("Loading & normalizing source GeoPackages...")
    for spec in FILE_SPECS:
        part = load_normalized(spec)
        print(
            f"  {spec['file']}: {len(part):,} rows, "
            f"crops={sorted(part['Crop_Name'].dropna().unique().tolist())[:12]}"
        )
        frames.append(part)

    all_gdf = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=frames[0].crs,
    )
    all_gdf["Date"] = pd.to_datetime(all_gdf["Date"], errors="coerce")
    print(f"\nCombined raw rows: {len(all_gdf):,}")

    filtered = quality_filter(all_gdf)
    print(
        f"After quality filter (Area {AREA_MIN}-{AREA_MAX} ha, valid geom, Date): "
        f"{len(filtered):,}"
    )

    counts = filtered["Crop_Name"].value_counts()
    print("\nEligible counts by crop:")
    print(counts.to_string())

    eligible_crops = counts[counts >= SAMPLES_PER_CROP].index.tolist()
    short_crops = counts[counts < SAMPLES_PER_CROP]

    # For near-miss crops (e.g. Chilli ~498), relax Area min slightly
    if len(short_crops):
        print("\nCrops below 500 after strict filter — trying Area >= 0.4:")
        relaxed = quality_filter(all_gdf, area_min=0.4)
        for crop, n in short_crops.items():
            n_rel = int((relaxed["Crop_Name"] == crop).sum())
            print(f"  {crop}: strict={n}, relaxed={n_rel}")
            if n_rel >= SAMPLES_PER_CROP and crop not in eligible_crops:
                eligible_crops.append(crop)
                add = relaxed[relaxed["Crop_Name"] == crop]
                filtered = pd.concat(
                    [filtered[filtered["Crop_Name"] != crop], add],
                    ignore_index=True,
                )
                filtered = gpd.GeoDataFrame(filtered, geometry="geometry", crs=all_gdf.crs)

    selected_parts: list[gpd.GeoDataFrame] = []
    summary_rows = []

    print(f"\nSelecting top {SAMPLES_PER_CROP} per crop...")
    for crop in sorted(eligible_crops):
        crop_df = filtered[filtered["Crop_Name"] == crop]
        picked = select_top_n(crop_df, SAMPLES_PER_CROP)
        if len(picked) < SAMPLES_PER_CROP:
            print(f"  SKIP {crop}: only {len(picked)} eligible")
            continue
        selected_parts.append(picked)
        summary_rows.append(
            {
                "Crop_Name": crop,
                "n": len(picked),
                "area_min": round(picked["Area"].min(), 4),
                "area_median": round(picked["Area"].median(), 4),
                "area_max": round(picked["Area"].max(), 4),
                "date_min": str(picked["Date"].min().date()),
                "date_max": str(picked["Date"].max().date()),
                "sources": ",".join(sorted(picked["source_file"].unique())),
            }
        )
        print(
            f"  {crop}: {len(picked)} | Area {picked['Area'].min():.2f}–{picked['Area'].max():.2f} "
            f"(med {picked['Area'].median():.2f})"
        )

    final = pd.concat(selected_parts, ignore_index=True)
    final = gpd.GeoDataFrame(final, geometry="geometry", crs=all_gdf.crs)
    final["geometry"] = final.geometry.force_2d()

    final = final.sort_values(["Crop_Name", "Area"], ascending=[True, False]).reset_index(drop=True)
    final["sample_id"] = final.index + 1
    final = final[["sample_id", "Crop_Name", "Area", "Date", "source_file", "geometry"]]

    if OUT_PATH.exists():
        OUT_PATH.unlink()

    final.to_file(OUT_PATH, layer="train_500", driver="GPKG", engine="pyogrio")

    summary = pd.DataFrame(summary_rows).sort_values("Crop_Name")
    print("\n" + "=" * 72)
    print(f"Wrote {OUT_PATH.name}")
    print(f"Total features: {len(final):,}  |  Crops: {final['Crop_Name'].nunique()}")
    print(summary.to_string(index=False))
    print("=" * 72)


if __name__ == "__main__":
    main()
