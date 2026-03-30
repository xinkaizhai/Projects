"""
demo_run.py — minimal example: pass inputs, call calc_prepay_and_default_mthread, print SMM.

Edit DLL_DIR and DATA_DIR before running.
"""

import sys
sys.path.insert(0, r"C:\path\to\wrapper")          # adjust if esp_wrapper.py is elsewhere

from esp_wrapper import AFTModel

# ── 1.  Paths ────────────────────────────────────────────────────────────────
DLL_DIR  = r"C:\AFT\WIN64bit_6.43-BUILDAUTO_20250602_90009_USE_ORIG_V6_OFFSET_vs2019"
DATA_DIR = r"C:\AFT\data"                           # folder with model param / score files

model = AFTModel(DLL_DIR, DATA_DIR)

# ── 2.  Market rates (flat vectors — replace with real paths) ─────────────────
N = 360                                             # projection horizon in months
mtg30 = [7.00] * N
mtg15 = [6.50] * N
mtg7  = [6.75] * N
mtg5  = [6.60] * N
t10   = [4.50] * N
t5    = [4.20] * N

# ── 3.  Call ──────────────────────────────────────────────────────────────────
smm, defaults, scores = model.calc_prepay_and_default_mthread(
    agency_name      = b"FNMA",
    orig_term_months = 360,
    age_months       = 36,
    wam_months       = 324,
    gross_wac_pct    = 7.25,
    net_coupon_pct   = 6.75,
    settle_date      = 202503,
    mtg_rate_30yr    = mtg30,
    mtg_rate_15yr    = mtg15,
    mtg_rate_7yr     = mtg7,
    mtg_rate_5yr     = mtg5,
    tnote_10yr       = t10,
    tnote_5yr        = t5,
)

# ── 4.  Outputs ───────────────────────────────────────────────────────────────
print(f"SMM length : {len(smm)}")
print(f"SMM[0:6]   : {[round(v, 6) for v in smm[:6]]}")
print(f"SMM[0:6] as CPR: {[round(1-(1-v)**12, 4)*100 for v in smm[:6]]}")

if defaults:
    d0 = defaults[0]
    print(f"\nDefault[0] keys : {list(d0.keys())}")
    print(f"Default[0]      : {d0}")

print(f"\nScores returned : {scores}")
