"""
Crop-Specific Growth Curves and Yield Parameters
=================================================
Expected NDVI trajectories and yield parameters for 23 crops:
Bajra, Banana, Cabbage, Chilli, Cotton, Gram, Grapes, Groundnut,
Jowar, Maize, Mustard, Onion, Others, Papaya, Pomegranate, Potato,
Rice, Soyabean, Sugarcane, Sunflower, Tobacco, Tur, Wheat

These curves are used for:
1. Health assessment (comparing actual vs expected NDVI growth)
2. Yield estimation (baseline/optimal/max yields)
3. Growth stage identification
4. Credit scoring via CropPerformanceAnalyzer

NDVI Curve Format: List of (days_since_sowing, expected_NDVI, stage_name)
- All curves are India-specific and based on typical field conditions
- NDVI values reflect satellite-observable canopy density, NOT just leaf area
- Crops with inherently low canopy (Groundnut, Bajra early) will have lower peaks
  than dense canopy crops (Rice, Sugarcane) — this is INTENTIONAL
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class CropGrowthCurves:
    """
    Crop-specific growth curve definitions based on agronomic knowledge
    and typical Indian agricultural practices.

    Covers all 23 crops supported by the ML classifier:
    Bajra, Banana, Cabbage, Chilli, Cotton, Gram, Grapes, Groundnut,
    Jowar, Maize, Mustard, Onion, Others, Papaya, Pomegranate, Potato,
    Rice, Soyabean, Sugarcane, Sunflower, Tobacco, Tur, Wheat
    """

    # ========================================================================
    # CROP SEASON DURATIONS (days from sowing/planting to harvest)
    # ========================================================================

    CROP_DURATIONS = {

        # ----- CEREALS & MILLETS -----

        'Bajra': {
            'min_days': 65,
            'typical_days': 85,
            'max_days': 110,
            'season': 'kharif',
            'description': 'Pearl millet — short duration, drought-tolerant millet'
        },
        'Jowar': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'both',   # Kharif & Rabi varieties exist
            'description': 'Sorghum — dual-purpose cereal (grain + fodder)'
        },
        'Maize': {
            'min_days': 80,
            'typical_days': 100,
            'max_days': 120,
            'season': 'both',
            'description': 'Short duration cereal'
        },
        'Rice': {
            'min_days': 100,
            'typical_days': 120,
            'max_days': 150,
            'season': 'kharif',
            'description': 'Wetland cereal'
        },
        'Wheat': {
            'min_days': 110,
            'typical_days': 130,
            'max_days': 150,
            'season': 'rabi',
            'description': 'Winter cereal'
        },

        # ----- OILSEEDS -----

        'Groundnut': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'kharif',
            'description': 'Peanut — kharif oilseed; low spreading canopy'
        },
        'Mustard': {
            'min_days': 110,
            'typical_days': 130,
            'max_days': 150,
            'season': 'rabi',
            'description': 'Rabi oilseed crop'
        },
        'Soyabean': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'kharif',
            'description': 'Kharif oilseed-pulse'
        },
        'Sunflower': {
            'min_days': 85,
            'typical_days': 100,
            'max_days': 120,
            'season': 'both',   # Rabi in South India; Kharif in North
            'description': 'Oilseed crop — tall with large single head'
        },
        'Tobacco': {
            'min_days': 130,
            'typical_days': 160,
            'max_days': 180,
            'season': 'rabi',
            'description': 'Rabi cash crop; dense leaf canopy at peak'
        },

        # ----- PULSES -----

        'Gram': {
            'min_days': 100,
            'typical_days': 120,
            'max_days': 140,
            'season': 'rabi',
            'description': 'Chickpea — rabi pulse crop'
        },
        'Tur': {
            'min_days': 150,
            'typical_days': 180,
            'max_days': 220,
            'season': 'kharif',
            'description': 'Pigeonpea (Arhar) — long duration kharif pulse; deep green canopy'
        },

        # ----- FIBER -----

        'Cotton': {
            'min_days': 150,
            'typical_days': 180,
            'max_days': 210,
            'season': 'kharif',
            'description': 'Long duration fiber crop'
        },

        # ----- VEGETABLES -----

        'Cabbage': {
            'min_days': 60,
            'typical_days': 80,
            'max_days': 100,
            'season': 'rabi',
            'description': 'Cole crop — compact rosette, moderate NDVI'
        },
        'Chilli': {
            'min_days': 120,
            'typical_days': 150,
            'max_days': 180,
            'season': 'both',
            'description': 'Long duration vegetable crop'
        },
        'Onion': {
            'min_days': 100,
            'typical_days': 120,
            'max_days': 150,
            'season': 'both',
            'description': 'Bulb vegetable — moderate canopy'
        },
        'Potato': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'rabi',
            'description': 'Tuber crop — dense canopy during bulking'
        },

        # ----- FRUITS / PERENNIALS -----

        'Banana': {
            'min_days': 270,
            'typical_days': 330,
            'max_days': 365,
            'season': 'perennial',
            'description': 'Perennial fruit crop — evergreen, high NDVI year-round'
        },
        'Grapes': {
            'min_days': 120,
            'typical_days': 150,
            'max_days': 180,
            'season': 'rabi',   # Harvest typically Feb–Mar in Maharashtra
            'description': 'Perennial vine — pruned annually; seasonal NDVI cycle'
        },
        'Papaya': {
            'min_days': 240,
            'typical_days': 300,
            'max_days': 365,
            'season': 'perennial',
            'description': 'Perennial fruit — moderate canopy, high year-round NDVI'
        },
        'Pomegranate': {
            'min_days': 150,
            'typical_days': 180,
            'max_days': 210,
            'season': 'perennial',
            'description': 'Semi-deciduous perennial — moderate canopy NDVI'
        },

        # ----- SUGARCANE -----

        'Sugarcane': {
            'min_days': 270,
            'typical_days': 330,
            'max_days': 365,
            'season': 'kharif',   # Planted around March–June; 10–12 months crop
            'description': 'Long duration; very high NDVI at peak'
        },

        # ----- CATCHALL -----

        'Others': {
            'min_days': 90,
            'typical_days': 120,
            'max_days': 150,
            'season': 'both',
            'description': 'Miscellaneous / unclassified crops — generic curve used'
        },
    }

    # ========================================================================
    # EXPECTED NDVI TRAJECTORIES
    # ========================================================================
    # Format: List of (days_since_sowing, expected_NDVI, stage_name)
    #
    # Design principles:
    #   - Low-canopy crops (Groundnut, Bajra) have lower absolute peaks (~0.65-0.70)
    #   - Dense / irrigated crops (Rice, Sugarcane, Wheat) peak higher (0.85-0.90)
    #   - Perennials (Banana, Papaya) stay elevated throughout season
    #   - All senescence / harvest-ready values reflect real satellite signal decline
    # ========================================================================

    EXPECTED_NDVI_CURVES = {

        # ================================================================
        # CEREALS & MILLETS
        # ================================================================

        'Bajra': [
            # Rapid establishment in hot/dry conditions
            (0,   0.12, 'Germination'),
            (7,   0.22, 'Seedling'),
            (14,  0.38, 'Early Vegetative'),
            (25,  0.55, 'Active Vegetative'),
            (35,  0.68, 'Grand Growth'),
            # Heading & Flowering
            (45,  0.75, 'Panicle Initiation'),
            (55,  0.78, 'Heading'),
            (60,  0.72, 'Flowering'),
            # Grain Fill & Maturity
            (70,  0.62, 'Grain Filling'),
            (80,  0.48, 'Dough Stage'),
            (85,  0.35, 'Harvest Ready'),
        ],

        'Jowar': [
            (0,   0.12, 'Germination'),
            (10,  0.22, 'Seedling'),
            (20,  0.38, 'Early Vegetative'),
            (30,  0.55, 'Active Vegetative'),
            (45,  0.70, 'Grand Growth'),
            # Heading
            (55,  0.78, 'Panicle Initiation'),
            (65,  0.82, 'Heading'),
            (70,  0.80, 'Flowering'),
            # Grain Fill
            (80,  0.72, 'Early Grain Fill'),
            (90,  0.60, 'Mid Grain Fill'),
            (100, 0.45, 'Dough Stage'),
            (110, 0.32, 'Harvest Ready'),
        ],

        'Maize': [
            (0,   0.15, 'Germination'),
            (7,   0.25, 'Seedling'),
            (15,  0.40, 'Early Vegetative'),
            (25,  0.60, 'Active Vegetative'),
            (35,  0.75, 'Rapid Growth'),
            (45,  0.85, 'Tasseling'),
            (55,  0.88, 'Silking/Pollination'),
            (65,  0.85, 'Early Grain Fill'),
            (75,  0.78, 'Mid Grain Fill'),
            (85,  0.68, 'Late Grain Fill'),
            (95,  0.52, 'Physiological Maturity'),
            (100, 0.40, 'Harvest Ready'),
        ],

        'Rice': [
            (0,   0.12, 'Germination/Transplanting'),
            (10,  0.25, 'Seedling Establishment'),
            (20,  0.42, 'Early Tillering'),
            (30,  0.60, 'Active Tillering'),
            (40,  0.75, 'Maximum Tillering'),
            (50,  0.82, 'Panicle Initiation'),
            (60,  0.88, 'Booting'),
            (70,  0.90, 'Heading'),
            (80,  0.88, 'Flowering'),
            (90,  0.82, 'Early Grain Fill'),
            (100, 0.72, 'Mid Grain Fill'),
            (110, 0.58, 'Late Grain Fill'),
            (120, 0.40, 'Harvest Ready'),
        ],

        'Wheat': [
            (0,   0.12, 'Germination'),
            (10,  0.22, 'Seedling'),
            (20,  0.40, 'Early Tillering'),
            (30,  0.58, 'Active Tillering'),
            (40,  0.72, 'Maximum Tillering'),
            (50,  0.82, 'Jointing'),
            (60,  0.88, 'Booting'),
            (70,  0.90, 'Heading'),
            (80,  0.88, 'Flowering'),
            (90,  0.82, 'Early Grain Fill'),
            (100, 0.75, 'Mid Grain Fill'),
            (110, 0.65, 'Late Grain Fill'),
            (120, 0.50, 'Dough Stage'),
            (130, 0.35, 'Harvest Ready'),
        ],

        # ================================================================
        # OILSEEDS
        # ================================================================

        'Groundnut': [
            # Spreading prostrate growth — never gets very dense canopy
            (0,   0.12, 'Germination'),
            (10,  0.20, 'Seedling'),
            (20,  0.32, 'Early Vegetative'),
            (30,  0.48, 'Active Vegetative'),
            (40,  0.60, 'Flowering'),
            # Pegging & Pod development — canopy spreads laterally
            (50,  0.68, 'Pegging'),
            (60,  0.72, 'Pod Initiation'),
            (70,  0.70, 'Pod Development'),
            (80,  0.65, 'Pod Filling'),
            # Maturity
            (95,  0.52, 'Pod Maturation'),
            (110, 0.38, 'Harvest Ready'),
        ],

        'Mustard': [
            (0,   0.12, 'Germination'),
            (7,   0.22, 'Seedling'),
            (15,  0.38, 'Early Vegetative'),
            (25,  0.55, 'Rosette Formation'),
            (35,  0.68, 'Stem Elongation'),
            (45,  0.80, 'Early Flowering'),
            (55,  0.88, 'Peak Flowering'),
            (70,  0.85, 'Late Flowering'),
            (80,  0.78, 'Pod Formation'),
            (95,  0.70, 'Pod Filling'),
            (110, 0.58, 'Pod Maturation'),
            (120, 0.42, 'Late Maturity'),
            (130, 0.30, 'Harvest Ready'),
        ],

        'Soyabean': [
            (0,   0.15, 'Germination'),
            (7,   0.25, 'Emergence'),
            (15,  0.40, 'First Trifoliate'),
            (25,  0.60, 'Early Vegetative'),
            (35,  0.75, 'Rapid Vegetative'),
            (45,  0.85, 'Flowering Initiation'),
            (55,  0.88, 'Peak Flowering'),
            (60,  0.86, 'Pod Initiation'),
            (70,  0.82, 'Pod Development'),
            (80,  0.75, 'Seed Fill'),
            (90,  0.65, 'Seed Maturation'),
            (100, 0.50, 'Leaf Senescence'),
            (110, 0.35, 'Harvest Ready'),
        ],

        'Sunflower': [
            (0,   0.13, 'Germination'),
            (7,   0.22, 'Seedling'),
            (15,  0.38, 'Early Vegetative'),
            (25,  0.55, 'Rapid Vegetative'),
            (35,  0.70, 'Active Growth'),
            # Bud & Flowering
            (45,  0.80, 'Bud Formation'),
            (55,  0.85, 'Flowering'),
            (65,  0.82, 'Post-Flowering'),
            # Seed fill & Maturity
            (75,  0.72, 'Seed Filling'),
            (85,  0.60, 'Seed Maturation'),
            (95,  0.45, 'Physiological Maturity'),
            (100, 0.32, 'Harvest Ready'),
        ],

        'Tobacco': [
            # Transplanted seedlings — slow early establishment
            (0,   0.10, 'Transplanting'),
            (10,  0.18, 'Early Establishment'),
            (20,  0.32, 'Active Vegetative'),
            (35,  0.55, 'Rapid Leaf Growth'),
            # Topping & Full canopy
            (50,  0.75, 'Topping'),
            (65,  0.85, 'Peak Canopy'),
            (80,  0.88, 'Mature Leaf'),
            # Harvest (sequential leaf picking)
            (100, 0.78, 'Early Harvest'),
            (120, 0.62, 'Mid Harvest'),
            (140, 0.45, 'Late Harvest'),
            (160, 0.28, 'Harvest Complete'),
        ],

        # ================================================================
        # PULSES
        # ================================================================

        'Gram': [
            (0,   0.12, 'Germination'),
            (10,  0.20, 'Seedling'),
            (20,  0.35, 'Early Vegetative'),
            (40,  0.58, 'Active Vegetative'),
            (50,  0.68, 'Pre-Flowering'),
            (60,  0.78, 'Flowering'),
            (70,  0.82, 'Peak Flowering'),
            (80,  0.80, 'Pod Formation'),
            (90,  0.75, 'Pod Filling'),
            (100, 0.65, 'Pod Maturation'),
            (110, 0.50, 'Late Maturity'),
            (120, 0.35, 'Harvest Ready'),
        ],

        'Tur': [
            # Pigeonpea — slow-starting, long-season, deep green at peak
            (0,   0.12, 'Germination'),
            (15,  0.20, 'Seedling'),
            (30,  0.35, 'Early Vegetative'),
            (50,  0.55, 'Active Vegetative'),
            (70,  0.70, 'Rapid Growth'),
            (90,  0.80, 'Pre-Flowering'),
            (110, 0.85, 'Flowering'),
            (130, 0.82, 'Pod Formation'),
            (150, 0.78, 'Pod Filling'),
            (165, 0.68, 'Pod Maturation'),
            (180, 0.48, 'Late Maturity'),
            (190, 0.32, 'Harvest Ready'),
        ],

        # ================================================================
        # FIBER
        # ================================================================

        'Cotton': [
            (0,   0.15, 'Germination'),
            (10,  0.22, 'Seedling'),
            (20,  0.35, 'Early Vegetative'),
            (40,  0.55, 'Active Vegetative'),
            (60,  0.72, 'Peak Vegetative'),
            (80,  0.82, 'Flowering Initiation'),
            (100, 0.85, 'Peak Flowering'),
            (120, 0.80, 'Boll Development'),
            (140, 0.72, 'Boll Maturation'),
            (160, 0.58, 'Early Maturity'),
            (180, 0.40, 'Harvest Ready'),
        ],

        # ================================================================
        # VEGETABLES
        # ================================================================

        'Cabbage': [
            # Compact rosette — NDVI moderate, canopy closes early
            (0,   0.10, 'Transplanting'),
            (7,   0.18, 'Early Establishment'),
            (15,  0.30, 'Rosette Stage'),
            (25,  0.50, 'Leaf Formation'),
            (35,  0.65, 'Rapid Leaf Growth'),
            (45,  0.72, 'Head Initiation'),
            (55,  0.75, 'Head Development'),
            (65,  0.72, 'Head Filling'),
            (75,  0.65, 'Maturation'),
            (80,  0.55, 'Harvest Ready'),
        ],

        'Chilli': [
            (0,   0.10, 'Transplanting'),
            (10,  0.18, 'Establishment'),
            (20,  0.30, 'Early Vegetative'),
            (35,  0.50, 'Active Vegetative'),
            (50,  0.65, 'Branching'),
            (65,  0.75, 'Flowering'),
            (80,  0.78, 'Fruit Set'),
            (100, 0.75, 'Fruit Development'),
            (120, 0.70, 'Fruit Maturation'),
            (140, 0.62, 'Peak Harvest'),
            (150, 0.50, 'Late Harvest'),
        ],

        'Onion': [
            (0,   0.10, 'Germination'),
            (10,  0.18, 'Seedling'),
            (20,  0.30, 'Early Vegetative'),
            (30,  0.48, 'Active Vegetative'),
            (40,  0.62, 'Rapid Leaf Growth'),
            (50,  0.74, 'Peak Vegetative'),
            (60,  0.78, 'Maximum Leaf Area'),
            (70,  0.75, 'Bulb Initiation'),
            (80,  0.70, 'Bulb Development'),
            (90,  0.62, 'Bulb Maturation'),
            (100, 0.52, 'Bulb Sizing'),
            (110, 0.38, 'Late Maturity'),
            (120, 0.25, 'Harvest Ready'),
        ],

        'Potato': [
            (0,   0.10, 'Planting'),
            (10,  0.15, 'Sprouting'),
            (15,  0.25, 'Emergence'),
            (20,  0.38, 'Early Vegetative'),
            (30,  0.60, 'Active Vegetative'),
            (40,  0.78, 'Peak Vegetative'),
            (50,  0.85, 'Tuber Initiation'),
            (60,  0.86, 'Early Tuber Bulking'),
            (70,  0.85, 'Peak Tuber Bulking'),
            (85,  0.80, 'Late Tuber Bulking'),
            (95,  0.68, 'Early Maturity'),
            (105, 0.50, 'Senescence'),
            (110, 0.35, 'Harvest Ready'),
        ],

        # ================================================================
        # PERENNIAL FRUITS
        # ================================================================

        'Banana': [
            # Evergreen — high NDVI throughout; gradual rise as plant matures
            (0,   0.45, 'Planting / Ratoon'),
            (30,  0.60, 'Vegetative Establishment'),
            (60,  0.72, 'Active Growth'),
            (90,  0.80, 'Grand Growth'),
            (120, 0.85, 'Shooting'),
            (150, 0.88, 'Bunch Development'),
            (180, 0.88, 'Bunch Filling'),
            (210, 0.85, 'Pre-Harvest'),
            (240, 0.82, 'Harvest Period'),
            (270, 0.80, 'Ratoon Growth'),
            (330, 0.85, 'Peak Season'),
        ],

        'Grapes': [
            # Dormant after pruning → rapid flush → fruiting → senescence
            (0,   0.10, 'Post-Pruning / Dormant'),
            (15,  0.22, 'Bud Burst'),
            (30,  0.40, 'Shoot Growth'),
            (50,  0.62, 'Rapid Vegetative'),
            (70,  0.75, 'Pre-Flowering'),
            (85,  0.78, 'Flowering'),
            (100, 0.72, 'Berry Set'),
            (115, 0.68, 'Berry Development'),
            (130, 0.62, 'Veraison'),
            (145, 0.55, 'Ripening'),
            (155, 0.45, 'Harvest Ready'),
        ],

        'Papaya': [
            # Semi-perennial; NDVI relatively stable once established
            (0,   0.35, 'Transplanting'),
            (30,  0.50, 'Establishment'),
            (60,  0.65, 'Active Growth'),
            (90,  0.75, 'Pre-Flowering'),
            (120, 0.80, 'Flowering'),
            (150, 0.82, 'Fruit Set'),
            (180, 0.82, 'Fruit Development'),
            (210, 0.80, 'Fruit Maturation'),
            (240, 0.80, 'Peak Harvest'),
            (300, 0.78, 'Sustained Production'),
        ],

        'Pomegranate': [
            # Semi-deciduous — pruned, then flushes
            (0,   0.15, 'Post-Pruning'),
            (20,  0.28, 'Bud Break'),
            (40,  0.45, 'New Flush'),
            (60,  0.60, 'Vegetative Growth'),
            (80,  0.70, 'Pre-Flowering'),
            (100, 0.75, 'Flowering'),
            (120, 0.72, 'Fruit Set'),
            (140, 0.68, 'Fruit Development'),
            (160, 0.65, 'Fruit Coloring'),
            (180, 0.58, 'Harvest Ready'),
        ],

        # ================================================================
        # SUGARCANE
        # ================================================================

        'Sugarcane': [
            # Very long duration; builds up to very high NDVI
            (0,   0.15, 'Germination'),
            (20,  0.25, 'Seedling'),
            (40,  0.40, 'Early Tillering'),
            (60,  0.58, 'Active Tillering'),
            (90,  0.72, 'Grand Growth Start'),
            (120, 0.82, 'Grand Growth'),
            (150, 0.88, 'Peak Grand Growth'),
            (180, 0.90, 'Cane Elongation'),
            (210, 0.88, 'Maturation Start'),
            (240, 0.82, 'Ripening'),
            (270, 0.75, 'Late Ripening'),
            (300, 0.65, 'Pre-Harvest'),
            (330, 0.52, 'Harvest Ready'),
        ],

        # ================================================================
        # CATCHALL
        # ================================================================

        'Others': [
            # Generic moderate-canopy crop curve — conservative estimate
            (0,   0.12, 'Germination / Establishment'),
            (15,  0.25, 'Seedling'),
            (30,  0.42, 'Early Vegetative'),
            (50,  0.60, 'Active Vegetative'),
            (70,  0.72, 'Peak Vegetative'),
            (85,  0.75, 'Flowering / Fruiting'),
            (100, 0.70, 'Maturation'),
            (115, 0.55, 'Late Maturity'),
            (120, 0.38, 'Harvest Ready'),
        ],
    }

    # ========================================================================
    # YIELD PARAMETERS  (India-specific, tons per hectare)
    # ========================================================================
    # Sources: ICAR, Directorate of Economics & Statistics (MoAFW),
    #          state agriculture department reports
    # price_per_quintal: Approximate MSP or market price (₹/quintal)
    # ========================================================================

    YIELD_PARAMETERS = {

        # ----- CEREALS & MILLETS -----

        'Bajra': {
            'baseline_yield': 0.8,
            'typical_yield':  1.5,
            'optimal_yield':  2.5,
            'max_yield':      4.0,
            'price_per_quintal': 2350,
            'unit': 'tons'
        },
        'Jowar': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  2.0,
            'max_yield':      3.5,
            'price_per_quintal': 2970,
            'unit': 'tons'
        },
        'Maize': {
            'baseline_yield': 2.5,
            'typical_yield':  3.5,
            'optimal_yield':  5.0,
            'max_yield':      8.0,
            'price_per_quintal': 1850,
            'unit': 'tons'
        },
        'Rice': {
            'baseline_yield': 2.5,
            'typical_yield':  3.5,
            'optimal_yield':  5.0,
            'max_yield':      7.0,
            'price_per_quintal': 2183,
            'unit': 'tons'
        },
        'Wheat': {
            'baseline_yield': 2.5,
            'typical_yield':  3.5,
            'optimal_yield':  5.0,
            'max_yield':      7.5,
            'price_per_quintal': 2275,
            'unit': 'tons'
        },

        # ----- OILSEEDS -----

        'Groundnut': {
            'baseline_yield': 1.0,
            'typical_yield':  1.8,
            'optimal_yield':  2.8,
            'max_yield':      4.0,
            'price_per_quintal': 6377,
            'unit': 'tons'
        },
        'Mustard': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  1.8,
            'max_yield':      2.5,
            'price_per_quintal': 5650,
            'unit': 'tons'
        },
        'Soyabean': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  1.8,
            'max_yield':      3.0,
            'price_per_quintal': 4600,
            'unit': 'tons'
        },
        'Sunflower': {
            'baseline_yield': 0.6,
            'typical_yield':  1.0,
            'optimal_yield':  1.5,
            'max_yield':      2.5,
            'price_per_quintal': 6760,
            'unit': 'tons'
        },
        'Tobacco': {
            'baseline_yield': 1.0,
            'typical_yield':  1.6,
            'optimal_yield':  2.2,
            'max_yield':      3.0,
            'price_per_quintal': 16000,  # FCV tobacco; premium price
            'unit': 'tons'
        },

        # ----- PULSES -----

        'Gram': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  1.8,
            'max_yield':      2.5,
            'price_per_quintal': 5440,
            'unit': 'tons'
        },
        'Tur': {
            'baseline_yield': 0.6,
            'typical_yield':  1.0,
            'optimal_yield':  1.6,
            'max_yield':      2.5,
            'price_per_quintal': 7000,
            'unit': 'tons'
        },

        # ----- FIBER -----

        'Cotton': {
            'baseline_yield': 1.8,
            'typical_yield':  2.5,
            'optimal_yield':  3.5,
            'max_yield':      5.0,
            'price_per_quintal': 6620,
            'unit': 'tons'
        },

        # ----- VEGETABLES -----

        'Cabbage': {
            'baseline_yield': 15.0,
            'typical_yield':  25.0,
            'optimal_yield':  35.0,
            'max_yield':      50.0,
            'price_per_quintal': 800,
            'unit': 'tons'
        },
        'Chilli': {
            'baseline_yield': 1.0,    # Dry chilli
            'typical_yield':  2.0,
            'optimal_yield':  3.5,
            'max_yield':      5.0,
            'price_per_quintal': 8000,
            'unit': 'tons'
        },
        'Onion': {
            'baseline_yield': 12.0,
            'typical_yield':  18.0,
            'optimal_yield':  25.0,
            'max_yield':      40.0,
            'price_per_quintal': 1200,
            'unit': 'tons'
        },
        'Potato': {
            'baseline_yield': 15.0,
            'typical_yield':  22.0,
            'optimal_yield':  30.0,
            'max_yield':      45.0,
            'price_per_quintal': 1000,
            'unit': 'tons'
        },

        # ----- PERENNIAL FRUITS -----

        'Banana': {
            'baseline_yield': 20.0,
            'typical_yield':  35.0,
            'optimal_yield':  50.0,
            'max_yield':      70.0,
            'price_per_quintal': 1500,
            'unit': 'tons'
        },
        'Grapes': {
            'baseline_yield': 10.0,
            'typical_yield':  20.0,
            'optimal_yield':  30.0,
            'max_yield':      40.0,
            'price_per_quintal': 4000,
            'unit': 'tons'
        },
        'Papaya': {
            'baseline_yield': 25.0,
            'typical_yield':  45.0,
            'optimal_yield':  65.0,
            'max_yield':      80.0,
            'price_per_quintal': 1200,
            'unit': 'tons'
        },
        'Pomegranate': {
            'baseline_yield': 5.0,
            'typical_yield':  10.0,
            'optimal_yield':  16.0,
            'max_yield':      22.0,
            'price_per_quintal': 8000,
            'unit': 'tons'
        },

        # ----- SUGARCANE -----

        'Sugarcane': {
            'baseline_yield': 50.0,
            'typical_yield':  70.0,
            'optimal_yield':  90.0,
            'max_yield':      120.0,
            'price_per_quintal': 340,   # FRP (Fair & Remunerative Price)
            'unit': 'tons'
        },

        # ----- CATCHALL -----

        'Others': {
            'baseline_yield': 1.0,
            'typical_yield':  2.0,
            'optimal_yield':  3.0,
            'max_yield':      5.0,
            'price_per_quintal': 2000,
            'unit': 'tons'
        },
    }

    # ========================================================================
    # CRITICAL GROWTH STAGES
    # Stages where NDVI deficit has the highest impact on final yield.
    # Used by performance_analyzer to weight stage-specific health penalties.
    # ========================================================================

    CRITICAL_STAGES = {
        'Bajra':       ['Heading', 'Grain Filling'],
        'Banana':      ['Bunch Development', 'Bunch Filling'],
        'Cabbage':     ['Head Development', 'Head Filling'],
        'Chilli':      ['Flowering', 'Fruit Set'],
        'Cotton':      ['Peak Flowering', 'Boll Development'],
        'Gram':        ['Flowering', 'Pod Formation'],
        'Grapes':      ['Berry Set', 'Berry Development'],
        'Groundnut':   ['Pegging', 'Pod Development'],
        'Jowar':       ['Heading', 'Early Grain Fill'],
        'Maize':       ['Silking/Pollination', 'Early Grain Fill'],
        'Mustard':     ['Peak Flowering', 'Pod Formation'],
        'Onion':       ['Bulb Development', 'Bulb Maturation'],
        'Others':      ['Peak Vegetative', 'Maturation'],
        'Papaya':      ['Fruit Set', 'Fruit Development'],
        'Pomegranate': ['Flowering', 'Fruit Development'],
        'Potato':      ['Tuber Initiation', 'Peak Tuber Bulking'],
        'Rice':        ['Flowering', 'Early Grain Fill'],
        'Soyabean':    ['Peak Flowering', 'Pod Development'],
        'Sugarcane':   ['Grand Growth', 'Cane Elongation'],
        'Sunflower':   ['Flowering', 'Seed Filling'],
        'Tobacco':     ['Peak Canopy', 'Mature Leaf'],
        'Tur':         ['Flowering', 'Pod Formation'],
        'Wheat':       ['Flowering', 'Early Grain Fill'],
    }

    # ========================================================================
    # CANOPY TYPE — affects how health score expected NDVI peak is set
    # LOW: spreading/sparse crops (Groundnut, Bajra, Cabbage, Onion)
    # MEDIUM: moderate canopy crops (Gram, Mustard, Chilli, Jowar, Others)
    # HIGH: dense/irrigated crops (Rice, Wheat, Maize, Sugarcane, Cotton)
    # PERENNIAL: always-on canopy (Banana, Papaya, Pomegranate, Grapes)
    # ========================================================================

    CANOPY_TYPE = {
        'Bajra':       'MEDIUM',
        'Banana':      'PERENNIAL',
        'Cabbage':     'LOW',
        'Chilli':      'MEDIUM',
        'Cotton':      'HIGH',
        'Gram':        'MEDIUM',
        'Grapes':      'PERENNIAL',
        'Groundnut':   'LOW',
        'Jowar':       'MEDIUM',
        'Maize':       'HIGH',
        'Mustard':     'HIGH',
        'Onion':       'LOW',
        'Others':      'MEDIUM',
        'Papaya':      'PERENNIAL',
        'Pomegranate': 'PERENNIAL',
        'Potato':      'HIGH',
        'Rice':        'HIGH',
        'Soyabean':    'HIGH',
        'Sugarcane':   'HIGH',
        'Sunflower':   'MEDIUM',
        'Tobacco':     'HIGH',
        'Tur':         'HIGH',
        'Wheat':       'HIGH',
    }

    # Expected cumulative NDVI (sum of ~10 scene observations across a season)
    # Used by performance_analyzer._estimate_yield_potential()
    # Derived from: peak_ndvi × number_of_peak_weeks + ramp-up + senescence
    EXPECTED_CUMULATIVE_NDVI = {
        'Bajra':       5.5,
        'Banana':      8.0,   # High because perennial stays elevated
        'Cabbage':     5.0,
        'Chilli':      6.5,
        'Cotton':      7.0,
        'Gram':        6.5,
        'Grapes':      5.5,
        'Groundnut':   5.5,
        'Jowar':       6.0,
        'Maize':       7.0,
        'Mustard':     7.0,
        'Onion':       5.5,
        'Others':      6.0,
        'Papaya':      7.5,
        'Pomegranate': 5.5,
        'Potato':      6.5,
        'Rice':        7.5,
        'Soyabean':    7.0,
        'Sugarcane':   8.5,   # Very long, very high
        'Sunflower':   6.0,
        'Tobacco':     7.0,
        'Tur':         7.5,
        'Wheat':       7.5,
    }

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    @classmethod
    def get_expected_ndvi(cls, crop: str, days_since_sowing: int) -> Tuple[float, str]:
        """
        Get expected NDVI value for a crop at specific days since sowing
        via linear interpolation between the defined curve points.

        Args:
            crop: Crop name (must match EXPECTED_NDVI_CURVES keys)
            days_since_sowing: Days elapsed since sowing / transplanting

        Returns:
            Tuple of (expected_ndvi: float, stage_name: str)
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.EXPECTED_NDVI_CURVES:
            logger.warning(f"Crop '{crop}' not found in growth curves — using 'Others'")
            crop = 'Others'

        curve = cls.EXPECTED_NDVI_CURVES[crop]

        # Before first defined point
        if days_since_sowing <= curve[0][0]:
            return curve[0][1], curve[0][2]

        # After last defined point
        if days_since_sowing >= curve[-1][0]:
            return curve[-1][1], curve[-1][2]

        # Linear interpolation between surrounding points
        for i in range(len(curve) - 1):
            day1, ndvi1, stage1 = curve[i]
            day2, ndvi2, stage2 = curve[i + 1]

            if day1 <= days_since_sowing <= day2:
                if day2 == day1:
                    return ndvi1, stage1

                progress = (days_since_sowing - day1) / (day2 - day1)
                expected_ndvi = ndvi1 + (ndvi2 - ndvi1) * progress
                stage = stage1 if progress < 0.5 else stage2

                return float(expected_ndvi), stage

        return curve[-1][1], curve[-1][2]

    @classmethod
    def get_expected_curve(cls, crop: str, num_points: int = 100) -> np.ndarray:
        """
        Get the full expected NDVI curve for a crop as a smoothed array.

        Args:
            crop: Crop name
            num_points: Number of interpolated points

        Returns:
            np.ndarray of shape (num_points, 2) — columns: [days, expected_ndvi]
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.CROP_DURATIONS:
            crop = 'Others'

        duration = cls.CROP_DURATIONS[crop]['typical_days']
        days = np.linspace(0, duration, num_points)

        ndvi_values = [cls.get_expected_ndvi(crop, int(d))[0] for d in days]

        return np.column_stack([days, ndvi_values])

    @classmethod
    def get_peak_ndvi(cls, crop: str) -> float:
        """
        Return the maximum expected NDVI for a crop from its growth curve.
        Used by performance_analyzer as the crop-specific 'expected_peak'.
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.EXPECTED_NDVI_CURVES:
            logger.warning(f"Crop '{crop}' not found — defaulting peak NDVI to 0.75")
            return 0.75

        return float(max(ndvi for _, ndvi, _ in cls.EXPECTED_NDVI_CURVES[crop]))

    @classmethod
    def get_expected_avg_ndvi(cls, crop: str) -> float:
        """
        Return the expected average NDVI across a full season for a crop.
        Used by performance_analyzer as the crop-specific 'expected_avg'.
        Calculated as simple average of all curve-point NDVI values.
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.EXPECTED_NDVI_CURVES:
            return 0.60

        values = [ndvi for _, ndvi, _ in cls.EXPECTED_NDVI_CURVES[crop]]
        return float(np.mean(values))

    @classmethod
    def get_expected_cumulative_ndvi(cls, crop: str) -> float:
        """
        Return expected cumulative NDVI (sum across ~10 observations in a season).
        Used by performance_analyzer._estimate_yield_potential() instead of
        the old hardcoded 7.0 value.
        """
        crop = cls._normalize_crop_name(crop)
        return cls.EXPECTED_CUMULATIVE_NDVI.get(crop, 6.5)

    @classmethod
    def get_crop_duration(cls, crop: str) -> int:
        """Return typical crop duration in days from sowing to harvest."""
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.CROP_DURATIONS:
            return 120

        return cls.CROP_DURATIONS[crop]['typical_days']

    @classmethod
    def get_canopy_type(cls, crop: str) -> str:
        """
        Return canopy type: 'LOW', 'MEDIUM', 'HIGH', or 'PERENNIAL'.
        Used by performance_analyzer to scale health score expectations.
        """
        crop = cls._normalize_crop_name(crop)
        return cls.CANOPY_TYPE.get(crop, 'MEDIUM')

    @classmethod
    def is_critical_stage(cls, crop: str, stage_name: str) -> bool:
        """Check if a growth stage name is critical for yield impact."""
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.CRITICAL_STAGES:
            return False

        return any(crit in stage_name for crit in cls.CRITICAL_STAGES[crop])

    @classmethod
    def get_yield_params(cls, crop: str) -> Dict:
        """
        Return yield parameters dict for a crop.
        Falls back to 'Others' if crop not found.
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.YIELD_PARAMETERS:
            logger.warning(f"Yield parameters not found for '{crop}' — using 'Others'")
            return cls.YIELD_PARAMETERS['Others'].copy()

        return cls.YIELD_PARAMETERS[crop].copy()

    @classmethod
    def validate_crop(cls, crop: str) -> bool:
        """Return True if crop is fully defined in this module."""
        crop = cls._normalize_crop_name(crop)
        return crop in cls.EXPECTED_NDVI_CURVES

    @classmethod
    def get_all_crops(cls) -> List[str]:
        """Return sorted list of all supported crop names."""
        return sorted(cls.EXPECTED_NDVI_CURVES.keys())

    @classmethod
    def get_crop_info(cls, crop: str) -> Dict:
        """
        Return a combined info dict for a crop including duration,
        canopy type, peak NDVI, expected avg NDVI, and yield params.
        Useful for logging / debugging.
        """
        crop = cls._normalize_crop_name(crop)

        if not cls.validate_crop(crop):
            crop = 'Others'

        return {
            'crop': crop,
            'duration': cls.CROP_DURATIONS.get(crop, {}),
            'canopy_type': cls.get_canopy_type(crop),
            'peak_ndvi': cls.get_peak_ndvi(crop),
            'expected_avg_ndvi': cls.get_expected_avg_ndvi(crop),
            'expected_cumulative_ndvi': cls.get_expected_cumulative_ndvi(crop),
            'yield_params': cls.get_yield_params(crop),
            'critical_stages': cls.CRITICAL_STAGES.get(crop, []),
        }

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================

    @classmethod
    def _normalize_crop_name(cls, crop: str) -> str:
        """
        Normalize crop name to handle minor spelling variants used by
        the ML model (e.g. 'Soybean' vs 'Soyabean').
        Extend this dict as new variants are encountered.
        """
        _ALIASES = {
            'Soybean':    'Soyabean',
            'soybean':    'Soyabean',
            'soyabean':   'Soyabean',
            'SOYABEAN':   'Soyabean',
            'cotton':     'Cotton',
            'wheat':      'Wheat',
            'rice':       'Rice',
            'maize':      'Maize',
            'bajra':      'Bajra',
            'jowar':      'Jowar',
            'tur':        'Tur',
            'gram':       'Gram',
            'groundnut':  'Groundnut',
            'mustard':    'Mustard',
            'sunflower':  'Sunflower',
            'tobacco':    'Tobacco',
            'sugarcane':  'Sugarcane',
            'onion':      'Onion',
            'potato':     'Potato',
            'cabbage':    'Cabbage',
            'chilli':     'Chilli',
            'banana':     'Banana',
            'grapes':     'Grapes',
            'papaya':     'Papaya',
            'pomegranate': 'Pomegranate',
            'others':     'Others',
        }
        return _ALIASES.get(crop, crop)