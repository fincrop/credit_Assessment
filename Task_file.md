Agricultural Credit Pipeline Refactoring Tasks
 Stage 1: Data Collection Polish
 Add 
_snap_to_season_start()
 to satellite_collector.py to align 3-year lookback to Jun 1 / Nov 15
 Remove forced kharif/rabi re-segmentation from main.py ENHANCED path — raw continuous data now feeds the cycle detector directly
 Stage 2: Dynamic Crop Cycle Detection
 Fix dt_dates NameError in CropCycleDetector._trace_crop_cycle() — now passed explicitly
 Add cloud-gap detection (gaps ≥ 25 days flagged as monsoon blackouts)
 Add PCHIP time-aware interpolation across cloud gaps (biologically sound, no-overshoot)
 Build multi-index Composite Vegetation Index: CVI = 0.5·NDVI + 0.3·EVI + 0.2·NDMI
 Switch to Savitzky-Golay smoothing (preserves peak height vs. moving average)
 Add three-trigger harvest cascade: rapid CVI drop, sustained senescence, NDMI divergence
 Add season labelling (Kharif/Rabi/Zaid) to each detected cycle
 Update 
main.py
 to pass EVI, NDMI, and scenes to the new detector
 Stage 3: Dynamic Crop Classification
 Refactor CropDetector.analyze_cycles() to work purely with dynamic date-span intervals
 Add 
cultivation_signal
 (0–100) as primary crop-name-independent metric per cycle
 Make ML classification best-effort: failures, low confidence, Unknown — all handled gracefully, cycle never dropped
 Replace kharif/rabi intensity key lookup with 
_calculate_intensity_from_cycles()
 (date-span based)
 Update CropPerformanceAnalyzer.analyze_performance() with dual-path scoring: crop_specific when reliable, signal_blended (60/40) or signal_only fallback
 Add 
_signal_based_health()
 and 
_signal_based_yield()
 methods to performance_analyzer.py
 Stage 4: Context-Aware Weather Analysis
 Rewrite 
analyze_cycle_weather()
 to use real days-since-sowing for stage positioning (was df-index fraction)
 Add 
_detect_cycle_extreme_events()
: per-event stage_name, 
days_since_sowing
, crop_impact_narrative
 Adaptive drought threshold: ≤90d cycle → base−5 days; ≥150d → base+5 days
 Drought severity expressed as % of cycle duration (not flat 60/90-day absolute)
 Add 
_compute_cycle_stats()
 with duration-proportional rainfall norm (3.5mm/day)
 Add 
_calculate_cycle_risk()
 — cycle-label-safe risk scorer (no kharif/rabi string dependency)
 Preserve legacy BASIC-mode 
_detect_extreme_events()
, 
_calculate_weather_risk()
, 
_get_critical_stage_fracs()
 as fallbacks
 Add 
_get_critical_stage_fracs_v4()
 returning both fracs list and named stage_map dict
 Stage 5: Dynamic Crop Performance Evaluation
 Dual-path architecture: PATH A (crop-specific BASIC) + PATH B (ENHANCED crop-agnostic)
 PATH A preserved: CropGrowthCurves benchmarks, curve fit, senescence — selected when crop reliable
 PATH B — _enhanced_health_score(): CVI = 0.5·NDVI + 0.3·EVI + 0.2·NDMI multi-index
Arc quality (rise→peak→fall) 30 pts, Biomass level 25 pts, Stability 20 pts, Growth momentum 15 pts, Canopy duration 10 pts
 IQR-based _detect_index_anomalies(): sudden drops, sustained depressions, peak volatility — each tagged with stage, magnitude, impact (HIGH/MEDIUM/LOW), plain-English description
 PATH B — _enhanced_yield_potential(): CVI AUC accumulation vs adaptive baseline (35%), anomaly-free fraction (30%), peak biomass (20%), peak-phase consistency (15%)
 Yield expressed as % with yield_potential_pct field
 Active cycle detection: end_date >= TODAY → is_active_cycle=True, capped scoring + projection note
 _build_narrative(): 2-4 sentence plain-English performance summary per cycle (for AI/lenders)
 Aggregate scores exclude active cycles (partial data) to avoid biasing historical averages
 signal_only fallback preserved for no-scene-data scenarios
 Stage 6: Credit Scorer Improvements
 Rewrote 
unsupervised_segmentation.py
 v2.0: 14-feature extraction consuming cultivation_signal, n_complete_cycles, n_active_cycles, avg_cycle_duration, n_anomalies_total, n_high_impact_anomalies, cycle_weather_risk from Stages 2-5
 Fixed single-farmer bootstrap: 
build_synthetic_population()
 perturbs all 14 features with realistic agricultural Gaussian noise (n=60 synthetic farmers)
 Rewrote 
advanced_credit_scorer.py
 v3.0:
7-component rule_based with weights 35/25/15/8/7/5/5 consuming Stage 4/5 outputs
_score_crop_detection_v4()
: cultivation_signal + consistency + n_complete_cycles
_score_weather_safety_v4()
: 70% cycle_risk_scores (Stage 4) + 30% seasonal
_score_anomaly_penalty()
: HIGH=8pts, MEDIUM=3pts, LOW=1pt per event (Stage 5)
Fixed hybrid weights: 65% rule_based + 35% unsupervised (was 70% ML)
Added scoring_narrative plain-English field to all score outputs
calculate_credit_limit()
 flags active cycles in credit limit notes
 
extract_features()
 updated to 16 features matching unsupervised extractor
 Stage 7: Updating AI Integrations
 Ensure Groq/Sarvam payloads correctly interpret the new dynamic cycles structure
 Polish SHAP and Counterfactual integrations
 Stage 8: Pipeline Assembly (
main.py
)
 Tie all pieces together into a coherent flow
 Test end-to-end execution of ENHANCED mode