"""Curated configuration for the Mushroom Finder worker.

Everything tweakable lives here: the taxon policy, guild definitions, scoring
weights and attribution. Changing scoring behaviour should mean editing this
file (and bumping MODEL_VERSION), not hunting through the pipeline.

This list is deliberately conservative: common, widespread, non-sensitive
macrofungi only. It must be reviewed by a mycologist before it is treated as
authoritative (see docs/calibration.md).
"""

from __future__ import annotations

# Bump when scoring logic or weights change; stamped on derived rows.
MODEL_VERSION = "2026.09-mvp1"

# Grid: historical evidence is aggregated to 5x5 km (the resolution the open
# Dutch observation data actually supports). Fine work happens at 10 m.
CELL_SIZE_M = 5000
FINE_CELL_SIZE_M = 10

# ── Guilds ───────────────────────────────────────────────────────────────
# Each guild groups taxa with similar habitat drivers so a single score is
# ecologically meaningful.
GUILDS = {
    "wood": "Wood-decaying fungi",
    "mycorrhizal": "Ectomycorrhizal fungi",
    "litter": "Litter / humus fungi",
    "wet": "Wet-ground fungi",
    "grassland": "Grassland fungi",
}

# ── Score weights per guild ──────────────────────────────────────────────
# Components are all normalised to 0..1 before weighting. These are expert
# priors, not fitted parameters; docs/calibration.md describes how to validate
# them against field observations.
WEIGHTS = {
    "wood": {"recurrence": 0.30, "richness": 0.15, "habitat": 0.25, "moisture": 0.15, "access": 0.15},
    "mycorrhizal": {"recurrence": 0.30, "richness": 0.15, "habitat": 0.30, "moisture": 0.10, "access": 0.15},
    "litter": {"recurrence": 0.30, "richness": 0.15, "habitat": 0.25, "moisture": 0.15, "access": 0.15},
    "wet": {"recurrence": 0.25, "richness": 0.10, "habitat": 0.25, "moisture": 0.25, "access": 0.15},
    "grassland": {"recurrence": 0.30, "richness": 0.15, "habitat": 0.30, "moisture": 0.10, "access": 0.15},
}

# Seasonal window per guild (month, day) — used to weight observations and to
# report "in season" state. Derived from Dutch fruiting phenology, not fitted.
SEASON = {
    "wood": ((7, 15), (11, 30)),
    "mycorrhizal": ((8, 1), (11, 15)),
    "litter": ((8, 15), (11, 30)),
    "wet": ((9, 1), (12, 15)),
    "grassland": ((8, 15), (11, 15)),
}

# ── Taxon policy ─────────────────────────────────────────────────────────
# sci  = scientific name resolved via the GBIF species/match API
# nl   = Dutch common name shown in the UI
# guild, photo = photography value 1..5
# sensitive=True taxa are excluded from all fine-grained output.
TAXA = [
    # Wood-decayers
    {"sci": "Trametes versicolor", "nl": "Gewoon elfenbankje", "guild": "wood", "photo": 4},
    {"sci": "Fomes fomentarius", "nl": "Echte tonderzwam", "guild": "wood", "photo": 4},
    {"sci": "Ganoderma applanatum", "nl": "Platte tonderzwam", "guild": "wood", "photo": 4},
    {"sci": "Daedaleopsis confragosa", "nl": "Roodgerande houtzwam", "guild": "wood", "photo": 3},
    {"sci": "Bjerkandera adusta", "nl": "Grijze gaatjeszwam", "guild": "wood", "photo": 3},
    {"sci": "Schizophyllum commune", "nl": "Waaiertje", "guild": "wood", "photo": 3},
    {"sci": "Pleurotus ostreatus", "nl": "Gewone oesterzwam", "guild": "wood", "photo": 4},
    {"sci": "Pluteus cervinus", "nl": "Hertenzwam", "guild": "wood", "photo": 3},
    {"sci": "Mycena galericulata", "nl": "Gewone mycena", "guild": "wood", "photo": 3},
    # Ectomycorrhizal
    {"sci": "Amanita muscaria", "nl": "Vliegenzwam", "guild": "mycorrhizal", "photo": 5},
    {"sci": "Amanita rubescens", "nl": "Parelamaniet", "guild": "mycorrhizal", "photo": 3},
    {"sci": "Boletus edulis", "nl": "Gewoon eekhoorntjesbrood", "guild": "mycorrhizal", "photo": 5},
    {"sci": "Laccaria amethystina", "nl": "Amethistzwam", "guild": "mycorrhizal", "photo": 4},
    {"sci": "Xerocomus subtomentosus", "nl": "Groene fluweelboleet", "guild": "mycorrhizal", "photo": 3},
    {"sci": "Russula emetica", "nl": "Braakrussula", "guild": "mycorrhizal", "photo": 3},
    {"sci": "Lactarius quietus", "nl": "Oranje melkzwam", "guild": "mycorrhizal", "photo": 3},
    # Litter / humus
    {"sci": "Clitocybe nebularis", "nl": "Nevelzwam", "guild": "litter", "photo": 3},
    {"sci": "Gymnopus dryophilus", "nl": "Gewone collybia", "guild": "litter", "photo": 3},
    {"sci": "Coprinopsis atramentaria", "nl": "Geschubde inktzwam", "guild": "litter", "photo": 3},
    {"sci": "Marasmius oreades", "nl": "Weidekringzwam", "guild": "litter", "photo": 3},
    # Wet ground
    {"sci": "Mycena galopus", "nl": "Melksteelmycena", "guild": "wet", "photo": 2},
    {"sci": "Galerina marginata", "nl": "Bundelmosklokje", "guild": "wet", "photo": 2, "sensitive": True},
    # Grassland
    {"sci": "Cuphophyllus virgineus", "nl": "Sneeuwzwammetje", "guild": "grassland", "photo": 3},
    {"sci": "Hygrocybe pratensis", "nl": "Weidewasplaat", "guild": "grassland", "photo": 4},
    {"sci": "Lycoperdon perlatum", "nl": "Parelstuifzwam", "guild": "grassland", "photo": 3},

    # ── Household wishlist (added 2026-09) ───────────────────────────────
    {"sci": "Macrolepiota procera", "nl": "Grote parasolzwam", "guild": "grassland", "photo": 5},
    {"sci": "Phallus impudicus", "nl": "Grote stinkzwam", "guild": "litter", "photo": 4},
    {"sci": "Coprinellus micaceus", "nl": "Gladstelige glimmerinktzwam", "guild": "wood", "photo": 3},
    {"sci": "Cyathus striatus", "nl": "Gestreept nestzwammetje", "guild": "wood", "photo": 4},
    {"sci": "Calocera viscosa", "nl": "Kleverig koraalzwammetje", "guild": "wood", "photo": 4},
    {"sci": "Ganoderma lucidum", "nl": "Gesteelde lakzwam", "guild": "wood", "photo": 4},
    {"sci": "Scleroderma citrinum", "nl": "Aardappelbovist", "guild": "mycorrhizal", "photo": 3},
    {"sci": "Auricularia auricula-judae", "nl": "Judasoor", "guild": "wood", "photo": 3},
    {"sci": "Polyporus squamosus", "nl": "Zadelzwam", "guild": "wood", "photo": 4},
    {"sci": "Tapinella atrotomentosa", "nl": "Dennenvoetzwam", "guild": "wood", "photo": 3},
    {"sci": "Fuligo septica", "nl": "Heksenboter", "guild": "litter", "photo": 3},
    {"sci": "Lepista nuda", "nl": "Paarse schijnridderhoed", "guild": "litter", "photo": 4},
    {"sci": "Calvatia gigantea", "nl": "Reuzenbovist", "guild": "grassland", "photo": 4},
    {"sci": "Gliophorus psittacinus", "nl": "Papegaaizwammetje", "guild": "grassland", "photo": 4},
    {"sci": "Hygrocybe acutoconica", "nl": "Puntmutswasplaat", "guild": "grassland", "photo": 3},
    {"sci": "Xylaria hypoxylon", "nl": "Geweizwammetje", "guild": "wood", "photo": 3},
    # Legally protected in the Netherlands; kept in the taxon list so the
    # record exists, but flagged sensitive so it never appears in rankings.
    {"sci": "Cantharellus cibarius", "nl": "Cantharel (Hanenkam)", "guild": "mycorrhizal",
     "photo": 5, "sensitive": True},
]

# ── Attribution (shown in the UI footer) ─────────────────────────────────
ATTRIBUTIONS = [
    {
        "id": "gbif",
        "label": "Observation data: Observation.org / GBIF (CC BY-NC 4.0)",
        "url": "https://www.gbif.org/dataset/10.15468/5nilie",
        "licence": "CC BY-NC 4.0",
    },
    {
        "id": "pdok",
        "label": "Kadaster / PDOK (BRT, AHN, BRO, BGT) — CC BY 4.0",
        "url": "https://www.pdok.nl/copyright",
        "licence": "CC BY 4.0",
    },
    {
        "id": "osm",
        "label": "OpenStreetMap contributors (ODbL)",
        "url": "https://www.openstreetmap.org/copyright",
        "licence": "ODbL 1.0",
    },
    {
        "id": "knmi",
        "label": "KNMI open data — CC BY 4.0",
        "url": "https://dataplatform.knmi.nl",
        "licence": "CC BY 4.0",
    },
    {
        "id": "openmeteo",
        "label": "Weather: Open-Meteo (CC BY 4.0, non-commercial)",
        "url": "https://open-meteo.com/",
        "licence": "CC BY 4.0",
    },
]

# ── GBIF sampling budget ─────────────────────────────────────────────────
# Records are fetched one *calendar year* at a time. GBIF's result ordering
# clumps by year, so a straight page-through (or coarse year ranges) samples
# only a few years and biases the yearly-recurrence signal. A per-year budget
# spreads the same request volume across the whole record period.
GBIF_PAGE_SIZE = 300
GBIF_YEAR_FROM = 2005
GBIF_MAX_RECORDS_PER_TAXON = 9000

# iNaturalist publishes exact coordinates; Observation.org NL does not. Records
# with no coordinateUncertainty from this dataset are treated as precise.
INATURALIST_DATASET = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"
HTTP_TIMEOUT_S = 60
USER_AGENT = "mushroom-finder/0.1 (private household research; contact: owner)"
