"""
demo_run.py — usage examples for esp_wrapper.AFTModel

Edit DLL_DIR and DATA_DIR before running.

─────────────────────────────────────────────────────────────────────────────
Field reference — what each input group feeds
─────────────────────────────────────────────────────────────────────────────
Collateral descriptor  (agency_name, orig_term, age, wam, gross_wac, net_coupon)
    → Scoring  ✓   SMM projection  ✓   Default model  ✓

loan_level  (loan_size_k, ltv, single_family, primary_resid, purchase, ...)
    → Scoring  ✓   SMM projection  ✓ (loan size bin)   Default model  ✗
    Notes:
    - loan_size_k  : original loan size in $K  (scoring bin + prepay model)
    - ltv          : decimal e.g. 0.80         (scoring bin + prepay model)
    - ratio fields : 0–1 fractions; for a single loan use 0 or 1

loan_info  (fico, state, orig_ltv, orig_date, property_type, occupancy, ...)
    → Scoring  ✓   SMM projection  ✗   Default model  ✗
    Notes:
    - orig_ltv  : decimal e.g. 0.80  (independent of loan_level["ltv"])
    - orig_bal  : dollars (not $K)
    - orig_date : YYYYMMDD integer

arm_desc  (without proj_gross_wac)
    → Scoring  ✓   SMM projection  ✗   Default model  ✗

arm_desc["proj_gross_wac"]  (WAC path vector)
    → Scoring  ✗   SMM projection  ✓   Default model  ✗

Market rates  (mtg_rate_30yr, mtg_rate_15yr, mtg_rate_7yr, mtg_rate_5yr,
               tnote_10yr, tnote_5yr)
    → Scoring  ✗   SMM projection  ✓   Default model  ✗

proj_hpi / proj_unemp
    → Scoring  ✗   SMM projection  ✗   Default model  ✓

default_dials  (orig_ltv, cur_adj_ltv, transition/prepay multipliers)
    → Scoring  ✗   SMM projection  ✗   Default model  ✓
    Notes:
    - orig_ltv / cur_adj_ltv : unit unconfirmed; demo uses percentage (80.0)
    - multipliers default to 1.0 (neutral) if omitted

input_scores  (ht_prepay_score, rf_prepay_score, fc_default_score, ...)
    → SMM projection  ✓   Default model  ✓
    Notes:
    - Range 0–5000; neutral = 500; values outside range treated as SCORE_NONE
    - Setting nForceUseInputScores=1 automatically; bypasses file lookup
    - Pass None to fall back to file-based scoring (score_switch honoured)

score_switch  (0=both on, 1=prepay off, 2=both off, 3=default off)
    → Only effective when pEspPrepaymentScoreStruct is NULL
      i.e. when input_scores=None and on_the_fly_scoring=False
─────────────────────────────────────────────────────────────────────────────
"""

from esp_wrapper import AFTModel

# ── Paths ─────────────────────────────────────────────────────────────────────
# Four path categories:
#   AFT DLL dir   : espmodel.dll + prepayScore.dll
#   AFT data dir  : model parameter files + score files
#   Intex DLL dir : intex.dll / icmo32.dll  (Intex CMO methods only)
#   Intex data    : single folder containing both CDI and CDU data

DLL_DIR       = r"C:\AFT\WIN64bit_6.43-BUILDAUTO_20250602_90009_USE_ORIG_V6_OFFSET_vs2019"
DATA_DIR      = r"C:\AFT\data"       # AFT model param files and score files
INTEX_DLL_DIR = r"C:\intex\dll"      # Intex DLL folder (intex.dll / icmo32.dll)
INTEX_DATA    = rb"C:\intex\data"    # Intex data root (contains cdi\ and cdu\ subfolders)

# intex_dll_dir is optional — omit or pass None for standalone (non-Intex) runs
model = AFTModel(DLL_DIR, DATA_DIR, intex_dll_dir=INTEX_DLL_DIR)

# ── Market rates (flat — replace with real scenario vectors) ──────────────────
N     = 324     # wam_months
mtg30 = [7.00] * N
mtg15 = [6.50] * N
mtg7  = [6.75] * N
mtg5  = [6.60] * N
t10   = [4.50] * N
t5    = [4.20] * N

# ── HPI and unemployment projections ─────────────────────────────────────────
# proj_hpi   : monthly HPA (%) starting at settle_date; feeds the default model
# proj_unemp : monthly unemployment rate (%) starting at settle_date
proj_hpi   = [0.30] * 24 + [0.20] * 24 + [0.15] * (N - 48)
proj_unemp = [4.2] * 12 + [4.5] * 12 + [4.8] * 12 + [4.5] * (N - 36)

# ─────────────────────────────────────────────────────────────────────────────
# Example 1 — FRM, scoring ON (file-driven, default behaviour)
# ─────────────────────────────────────────────────────────────────────────────
smm, defaults, scores = model.calc_prepay_and_default_mthread(
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 36,
    wam_months       = N,
    gross_wac_pct    = 7.25,
    net_coupon_pct   = 6.75,
    settle_date      = 202503,
    mtg_rate_30yr    = mtg30,
    mtg_rate_15yr    = mtg15,
    mtg_rate_7yr     = mtg7,
    mtg_rate_5yr     = mtg5,
    tnote_10yr       = t10,
    tnote_5yr        = t5,
    proj_hpi           = proj_hpi,
    hpi_start_yyyymm   = 0,        # 0 = align with settle_date
    proj_unemp         = proj_unemp,
    unemp_start_yyyymm = 202503,
    default_dials = {
        "orig_ltv":    80.0,
        "cur_adj_ltv": 76.0,
    },
    loan_level = {
        "loan_size_k":   400.0,
        "ltv":           0.80,    # decimal (0.80 = 80%)
        "full_doc_ratio": 1.0,
        "single_family":  1.0,
        "primary_resid":  1.0,
        "purchase":       0.6,
        "refinance":      0.4,
    },
    loan_info = {
        "orig_bal":      400_000,
        "curr_bal":      385_000,
        "orig_ltv":      0.80,
        "fico":          740.0,
        "borrower_age":  38.0,
        "state":         "CA",
        "zip_code":      "90210",
        "property_type": "SF",
        "occupancy":     "P",
        "loan_purpose":  "PUR",
        "product_code":  "FRM",
        "doc_code":      "FD",
        "orig_date":     20230301,
    },
    # score_switch not set → file-driven scoring (default)
)
print("=== Example 1: FRM, scoring ON ===")
print(f"SMM[0:6]   : {[round(v, 6) for v in smm[:6]]}")
print(f"CPR[0:6]   : {[round((1-(1-v)**12)*100, 4) for v in smm[:6]]}")
if defaults:
    print(f"default[0] : {defaults[0]}")
print(f"scores     : {scores}")

# ─────────────────────────────────────────────────────────────────────────────
# Example 2 — FRM, scoring OFF
# ─────────────────────────────────────────────────────────────────────────────
smm_off, _, _ = model.calc_prepay_and_default_mthread(
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 36,
    wam_months       = N,
    gross_wac_pct    = 7.25,
    net_coupon_pct   = 6.75,
    settle_date      = 202503,
    mtg_rate_30yr    = mtg30,
    mtg_rate_15yr    = mtg15,
    mtg_rate_7yr     = mtg7,
    mtg_rate_5yr     = mtg5,
    tnote_10yr       = t10,
    tnote_5yr        = t5,
    score_switch     = 2,   # 2 = both prepay and default scores OFF
)
print("\n=== Example 2: FRM, scoring OFF ===")
print(f"SMM[0:6]   : {[round(v, 6) for v in smm_off[:6]]}")

# ─────────────────────────────────────────────────────────────────────────────
# Example 3 — Two-step scoring: compute scores first, then main run
#             Scores appear in DLL log as real values (not zeros)
# ─────────────────────────────────────────────────────────────────────────────
scores_pre = model.calc_loan_score(
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 36,
    wam_months       = N,
    gross_wac_pct    = 7.25,
    net_coupon_pct   = 6.75,
    loan_level = {
        "loan_size_k":   400.0,
        "ltv":           0.80,    # decimal (0.80 = 80%)
        "single_family":  1.0,
        "primary_resid":  1.0,
    },
    loan_info = {
        "orig_ltv":      0.80,
        "fico":          740.0,
        "state":         "CA",
        "property_type": "SF",
        "occupancy":     "P",
        "loan_purpose":  "PUR",
        "product_code":  "FRM",
    },
)
print("\n=== Example 3: Two-step scoring ===")
print(f"Pre-computed scores : {scores_pre}")

if scores_pre:
    smm_2step, defaults_2step, _ = model.calc_prepay_and_default_mthread(
        agency_name      = b"FNMA",
        orig_term_months = 360,
        age_months       = 36,
        wam_months       = N,
        gross_wac_pct    = 7.25,
        net_coupon_pct   = 6.75,
        settle_date      = 202503,
        mtg_rate_30yr    = mtg30,
        mtg_rate_15yr    = mtg15,
        mtg_rate_7yr     = mtg7,
        mtg_rate_5yr     = mtg5,
        tnote_10yr       = t10,
        tnote_5yr        = t5,
        proj_hpi           = proj_hpi,
        hpi_start_yyyymm   = 0,
        proj_unemp         = proj_unemp,
        unemp_start_yyyymm = 202503,
        input_scores     = scores_pre,  # pre-filled → appears in DLL log
    )
    print(f"SMM[0:6] (2-step) : {[round(v, 6) for v in smm_2step[:6]]}")

# ─────────────────────────────────────────────────────────────────────────────
# Example 4 — 5/1 ARM, full two-step scoring
#
# Input groups and what they feed:
#
#   Collateral descriptor  → scoring + SMM + default model
#   loan_level             → scoring (bin selection) + SMM (loan size bin)
#   loan_info              → scoring only (borrower/loan attributes)
#   arm_desc (no wac path) → scoring only (ARM table selection)
#   arm_desc[proj_gross_wac] → SMM projection only
#   market rates           → SMM projection only
#   proj_hpi / proj_unemp  → default model only
#   default_dials          → default model only
#   input_scores           → SMM + default model (bypasses file lookup)
# ─────────────────────────────────────────────────────────────────────────────
proj_arm_wac = [6.50] * 60 + [7.20] * (N - 60)   # teaser then reset path

# ── Step 1: compute scores (no market rates or HPI/UE needed) ────────────────
scores_arm = model.calc_loan_score(
    # collateral descriptor → score table selection
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 12,
    wam_months       = N,
    gross_wac_pct    = 6.50,
    net_coupon_pct   = 6.00,

    # loan_level → score bin selection + prepay model loan size bin
    loan_level = {
        "loan_size_k":    400.0,  # original loan size in $K
        "ltv":            0.80,   # decimal (0.80 = 80%)
        "single_family":  1.0,    # 1.0 for single loan
        "primary_resid":  1.0,
        "purchase":       1.0,
        "refinance":      0.0,
        "full_doc_ratio": 1.0,
    },

    # loan_info → borrower/loan attributes for scorer only
    loan_info = {
        "orig_ltv":      0.80,        # decimal; independent of loan_level["ltv"]
        "fico":          740.0,
        "state":         "CA",
        "zip_code":      "90210",
        "property_type": "SF",
        "occupancy":     "P",
        "loan_purpose":  "PUR",
        "product_code":  "ARM",
        "doc_code":      "FD",
        "orig_date":     20240101,    # YYYYMMDD
        "orig_bal":      400_000.0,   # dollars (not $K)
        "curr_bal":      395_000.0,
    },

    # arm_desc (no proj_gross_wac) → ARM score table selection only
    arm_desc = {
        "index_type":           2,     # 1yr CMT
        "gross_wac_at_origin":  6.50,
        "margin":               2.75,
        "life_cap":             11.50,
        "teaser_months":        60,
        "reset_period_months":  12,
        "periodic_cap":         2.0,
        "initial_periodic_cap": 5.0,
        "lookback_days":        45,
        # proj_gross_wac omitted — not needed for scoring
    },
)
print("\n=== Example 4: 5/1 ARM, two-step scoring ===")
print(f"ARM scores : {scores_arm}")

# ── Step 2: SMM projection using pre-computed scores ─────────────────────────
smm_arm, defaults_arm, _ = model.calc_prepay_and_default_mthread(
    # collateral descriptor
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 12,
    wam_months       = N,
    gross_wac_pct    = 6.50,
    net_coupon_pct   = 6.00,
    settle_date      = 202503,

    # market rates → SMM projection only
    mtg_rate_30yr = mtg30,
    mtg_rate_15yr = mtg15,
    mtg_rate_7yr  = mtg7,
    mtg_rate_5yr  = mtg5,
    tnote_10yr    = t10,
    tnote_5yr     = t5,

    # HPI / unemployment → default model only
    proj_hpi           = proj_hpi,
    hpi_start_yyyymm   = 0,
    proj_unemp         = proj_unemp,
    unemp_start_yyyymm = 202503,

    # default_dials → default model LTV inputs + transition multipliers
    default_dials = {
        "orig_ltv":    80.0,   # unit unconfirmed; assumed percentage
        "cur_adj_ltv": 78.0,
    },

    # loan_level → also used directly by prepay model (loan size bin)
    loan_level = {
        "loan_size_k":    400.0,
        "ltv":            0.80,
        "single_family":  1.0,
        "primary_resid":  1.0,
        "purchase":       1.0,
        "refinance":      0.0,
        "full_doc_ratio": 1.0,
    },

    # loan_info → kept for consistency; not used by SMM when input_scores set
    loan_info = {
        "orig_ltv":      0.80,
        "fico":          740.0,
        "state":         "CA",
        "property_type": "SF",
        "occupancy":     "P",
        "loan_purpose":  "PUR",
        "product_code":  "ARM",
    },

    # arm_desc with proj_gross_wac → SMM projection (WAC path for resets)
    arm_desc = {
        "index_type":           2,
        "gross_wac_at_origin":  6.50,
        "margin":               2.75,
        "life_cap":             11.50,
        "teaser_months":        60,
        "reset_period_months":  12,
        "periodic_cap":         2.0,
        "initial_periodic_cap": 5.0,
        "lookback_days":        45,
        "proj_gross_wac":       proj_arm_wac,   # SMM only
    },

    # pre-computed scores → bypasses file lookup (nForceUseInputScores=1)
    # scores affect SMM via ht/rf multipliers and elbow shift (range 0–5000, neutral=500)
    # scores affect default model via fc/del multipliers
    # None-safe: wrapper skips attaching struct if scores_arm is None
    input_scores = scores_arm,
)
print(f"ARM SMM[0:6] : {[round(v, 6) for v in smm_arm[:6]]}")
print(f"ARM CPR[0:6] : {[round((1-(1-v)**12)*100, 4) for v in smm_arm[:6]]}")
if defaults_arm:
    print(f"ARM default[0]: {defaults_arm[0]}")

# ─────────────────────────────────────────────────────────────────────────────
# Example 5 — CMO tranche via Intex (Method 1: CUSIP-based)
#
# AFT queries Intex to load all collateral attributes automatically:
#   agency name, WAC, WAM, age, ARM flag, deal name, tranche name.
# No need to supply loan_level, loan_info, default_dials, or arm_desc —
# all collateral indicatives come from Intex CDI/CDU files.
#
# Architecture:
#   loadEspMBSCalcInputStructFromCmoVendor (AFT) → Intex CDI/CDU lookup
#       → EspMBSCalcInputStruct populated (deal name, desc, WAC, WAM, ...)
#           → EspPrep_PrepayAndDefaultModelMThread (AFT prepay model)
#               → SMM + default rates for the tranche's collateral
#
# Note: returns one SMM vector for the collateral backing this tranche.
# For a multi-collateral deal, call once per collateral CUSIP and
# balance-weight the resulting SMM vectors externally.
#
# Inputs:
#   cusip         : 9-char CUSIP of the CMO tranche
#   intex_data_dir : Intex data root (must contain cmo_cdi\ and cmo_cdu\ subfolders)
#   settle_date   : YYYYMM — converted internally to YYYYMMDD (day=1)
#   market rates  : same vectors as standalone runs
#   proj_hpi      : optional HPA override; omit to use AFT internal projection
#   group_number  : Intex group number; -1 for most single-group deals
# ─────────────────────────────────────────────────────────────────────────────
smm_cmo, defaults_cmo = model.calc_prepay_from_cusip(
    cusip          = b"3128M5GE0",   # replace with actual CMO tranche CUSIP
    intex_data_dir = INTEX_DATA,     # wrapper appends \cdi and \cdu automatically
    settle_date   = 202503,
    mtg_rate_30yr = mtg30,
    mtg_rate_15yr = mtg15,
    mtg_rate_7yr  = mtg7,
    mtg_rate_5yr  = mtg5,
    tnote_10yr    = t10,
    tnote_5yr     = t5,
    # proj_hpi not set → AFT uses internal HPA from deal parameter files
    # group_number not set → defaults to -1 (not applicable)
)
print("\n=== Example 5: CMO tranche via Intex (CUSIP) ===")
print(f"CMO SMM[0:6]    : {[round(v, 6) for v in smm_cmo[:6]]}")
print(f"CMO CPR[0:6]    : {[round((1-(1-v)**12)*100, 4) for v in smm_cmo[:6]]}")
if defaults_cmo:
    print(f"CMO default[0]  : {defaults_cmo[0]}")

# ── Multi-collateral aggregation (balance-weighted) ───────────────────────────
# If a deal has multiple collateral CUSIPs, run once per CUSIP and aggregate:
#
# collaterals = [
#     {"cusip": b"3128M5GE0", "balance": 50_000_000},
#     {"cusip": b"3128M5GE1", "balance": 30_000_000},
#     {"cusip": b"3128M5GE2", "balance": 20_000_000},
# ]
# results = []
# for c in collaterals:
#     smm_c, _ = model.calc_prepay_from_cusip(
#         cusip=c["cusip"], intex_data_dir=INTEX_DATA,
#         settle_date=202503, mtg_rate_30yr=mtg30, tnote_10yr=t10, tnote_5yr=t5)
#     results.append((smm_c, c["balance"]))
# total_bal = sum(b for _, b in results)
# n_months  = len(results[0][0])
# agg_smm   = [sum(s[t] * b / total_bal for s, b in results) for t in range(n_months)]
