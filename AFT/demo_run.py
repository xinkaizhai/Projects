"""
demo_run.py — usage examples for esp_wrapper.AFTModel

Edit DLL_DIR and DATA_DIR before running.
"""

from esp_wrapper import AFTModel

# ── Paths ─────────────────────────────────────────────────────────────────────
DLL_DIR  = r"C:\AFT\WIN64bit_6.43-BUILDAUTO_20250602_90009_USE_ORIG_V6_OFFSET_vs2019"
DATA_DIR = r"C:\AFT\data"   # folder containing model param files and score files

model = AFTModel(DLL_DIR, DATA_DIR)

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
        "ltv":           80.0,
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
        "ltv":           80.0,
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
# Example 4 — 5/1 ARM with HPI, UE, and two-step scoring
# ─────────────────────────────────────────────────────────────────────────────
proj_arm_wac = [6.50] * 48 + [7.20] * (N - 48)   # teaser then reset path

scores_arm = model.calc_loan_score(
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 12,
    wam_months       = N,
    gross_wac_pct    = 6.50,
    net_coupon_pct   = 6.00,
    loan_level = {"loan_size_k": 400.0, "ltv": 80.0, "single_family": 1.0},
    loan_info  = {"orig_ltv": 0.80, "fico": 740.0, "state": "CA",
                  "product_code": "ARM", "occupancy": "P"},
    arm_desc   = {"index_type": 2, "gross_wac_at_origin": 6.50,
                  "margin": 2.75, "life_cap": 11.50, "teaser_months": 60,
                  "reset_period_months": 12, "periodic_cap": 2.0,
                  "initial_periodic_cap": 5.0, "lookback_days": 45},
)
print("\n=== Example 4: 5/1 ARM, two-step scoring ===")
print(f"ARM scores : {scores_arm}")

smm_arm, defaults_arm, _ = model.calc_prepay_and_default_mthread(
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 12,
    wam_months       = N,
    gross_wac_pct    = 6.50,
    net_coupon_pct   = 6.00,
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
    default_dials = {"orig_ltv": 80.0, "cur_adj_ltv": 78.0},
    loan_level    = {"loan_size_k": 400.0, "ltv": 80.0, "single_family": 1.0},
    loan_info     = {"orig_ltv": 0.80, "fico": 740.0, "state": "CA",
                     "product_code": "ARM", "occupancy": "P"},
    arm_desc = {
        "index_type":           2,       # ESP_INDEX_TR12MO (1yr CMT)
        "gross_wac_at_origin":  6.50,
        "margin":               2.75,
        "life_cap":             11.50,
        "teaser_months":        60,
        "reset_period_months":  12,
        "periodic_cap":         2.0,
        "initial_periodic_cap": 5.0,
        "lookback_days":        45,
        "proj_gross_wac":       proj_arm_wac,
    },
    input_scores = scores_arm,   # None-safe: wrapper skips if None
)
print(f"ARM SMM[0:6] : {[round(v, 6) for v in smm_arm[:6]]}")
