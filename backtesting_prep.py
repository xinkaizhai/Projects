"""
backtesting_prep.py

Python equivalent of the first R script:
- Loads ALMx clean tables and Canoe SQL (enriched) tables for 13 periods
- Builds customer-rate, book-balance, and current-payment panels
- Computes scheduled balances, d-ratios, and unscheduled prepayments
- Exports a full backtesting table and a slimmer import table
"""

import pandas as pd
import numpy as np
from datetime import datetime

# ── Configuration ─────────────────────────────────────────────────────────────
FEED = "UsMortgageBN"
DATES = [
    "202410", "202411", "202412",
    "202501", "202502", "202503", "202504", "202505", "202506",
    "202507", "202508", "202509", "202510",
]  # must have 13 periods
TODAY = datetime(2025, 10, 31)  # last day of the latest backtesting month
FEEDCODE = "bnmg"

ALMX_DIR  = "C:/1_Monthly Backtesting/MBT/almxclean/"
SQL_DIR   = "C:/1_Monthly Backtesting/MBT/sqldata/"
OUT_DIR   = "C:/1_Monthly Backtesting/2025/202511ME/Mortgage/"
COLLATMAP = "C:/1_Monthly Backtesting/MBT/CollatIdMapping.csv"

N = len(DATES)  # 13

# ── Helper functions ───────────────────────────────────────────────────────────
def cpnfix(coupon):
    """Convert semi-annual compounded coupon to monthly/simple rate."""
    return np.round(1200 * (((coupon / 200) + 1) ** (1 / 6) - 1), 3)


def _ext_cust(table, col_name):
    """Return (Uniqueid, <col_name>) from an ALMx table."""
    tmp = table.copy()
    tmp[col_name] = cpnfix(tmp["Coupon"].to_numpy(dtype=float))
    return tmp.rename(columns={"AlternateUniqueId": "Uniqueid"})[["Uniqueid", col_name]]


def _ext_bal(table, col_name):
    """Return (Uniqueid, <col_name>) from an ALMx table."""
    tmp = table.copy()
    tmp[col_name] = pd.to_numeric(
        tmp["Holdings"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    return tmp.rename(columns={"AlternateUniqueId": "Uniqueid"})[["Uniqueid", col_name]]


def _ext_cur(table, col_name):
    """Return (Uniqueid, <col_name>) from a raw SQL table."""
    tmp = table.copy()
    tmp[col_name] = tmp["R_CurrentPayment"]
    return tmp.rename(columns={"X_K_CertificateCode": "Uniqueid"})[["Uniqueid", col_name]]


# ── Load data ─────────────────────────────────────────────────────────────────
tables = [
    pd.read_csv(f"{ALMX_DIR}{FEED}-{d}-clean.csv")
    for d in DATES
]
raws = [
    pd.read_csv(f"{SQL_DIR}{FEEDCODE}-{d}.csv")
    for d in DATES
]

# ── left_raw: static loan attributes from the first raw snapshot ──────────────
RAW_RENAME = {
    "X_K_CertificateCode": "Uniqueid",
    "R_Coa":               "Coa",
    "R_ProductCode":       "ProductCode",
    "E_RiskProduct":       "RiskProduct",
    "R_LoanType":          "LoanType",
    "R_IssueDate":         "IssueDt",
    "R_MaturityDate":      "Maturity",
    "R_CollateralId":      "Cltrl_Id",
    "R_OriginalLTV":       "LoanToValu",
    "R_OriginalAmount":    "OrigAmount",
    "R_USState":           "State",
    "R_OriginalFICO":      "OrigFico",
    "R_CouponIssueRate":   "Iss_Coupon",
    "R_LifeTimeCap":       "Lt_Cap",
    "R_ArmMarginSpread":   "Arm_Spd",
    "R_IndexSelectionDays": "IdxSelDays",
    "R_TeaserPeriod":      "Tsr_Period",
    "R_IndexCode":         "IndexCode",
    "R_ResetTermInMonth":  "Reset_Term",
    "R_PeriodCap":         "Cap",
    "R_InitialCap":        "First_Cap",
    "R_IndexName":         "IndexName",
    "R_OwnershipCode":     "Ownership",
}
LEFT_RAW_COLS = [
    "Uniqueid", "Coa", "ProductCode", "RiskProduct", "LoanType",
    "IssueDt", "Maturity", "Cltrl_Id", "LoanToValu", "OrigAmount",
    "State", "OrigFico", "Iss_Coupon", "Lt_Cap", "Arm_Spd",
    "IdxSelDays", "Tsr_Period", "IndexCode", "Reset_Term", "Cap",
    "First_Cap", "IndexName", "Ownership",
]
left_raw = (
    raws[0]
    .sort_values("X_K_CertificateCode")
    .rename(columns=RAW_RENAME)[LEFT_RAW_COLS]
)

# ── left_tab: join first ALMx table with left_raw ────────────────────────────
LEFT_TAB_COLS = [
    "Uniqueid", "Coa", "ProductCode", "LoanType", "RiskProduct",
    "IssueDt", "Maturity", "Cltrl_Id", "LoanToValu", "OrigAmount", "RAM",
    "State", "OrigFico", "Iss_Coupon", "Lt_Cap", "Arm_Spd",
    "IdxSelDays", "Tsr_Period", "IndexCode", "Reset_Term", "Cap",
    "First_Cap", "IndexName", "Ownership",
]
left_tab = (
    tables[0]
    .sort_values("AlternateUniqueId")
    .rename(columns={"AlternateUniqueId": "Uniqueid"})[["Uniqueid", "RAM"]]
    .merge(left_raw, on="Uniqueid", how="inner")[LEFT_TAB_COLS]
)

# ── cust_tab: customer rates for all 13 periods ───────────────────────────────
cust_tab = _ext_cust(tables[0], "custrt0")
for i, t in enumerate(tables[1:], 1):
    cust_tab = cust_tab.merge(_ext_cust(t, f"custrt{i}"), on="Uniqueid", how="left")

# Forward-fill NaN values across periods, then fill any remaining with 0
custrt_cols = [f"custrt{i}" for i in range(N)]
for i in range(1, N):
    cust_tab[f"custrt{i}"] = cust_tab[f"custrt{i}"].fillna(cust_tab[f"custrt{i-1}"])
cust_tab[custrt_cols] = cust_tab[custrt_cols].fillna(0)

# ── bal_tab: current book balances for all 13 periods ────────────────────────
bal_tab = _ext_bal(tables[0], "curbookbal0")
for i, t in enumerate(tables[1:], 1):
    bal_tab = bal_tab.merge(_ext_bal(t, f"curbookbal{i}"), on="Uniqueid", how="left")

bal_cols = [f"curbookbal{i}" for i in range(N)]
bal_tab[bal_cols] = bal_tab[bal_cols].fillna(0)

# ── curpmt_tab: current payments for periods 0-11 (12 periods) ───────────────
curpmt_tab = (
    tables[0]
    .sort_values("AlternateUniqueId")
    .rename(columns={"AlternateUniqueId": "Uniqueid"})[["Uniqueid"]]
)
# raw0 through raw11 (note: no raw12 current payment — one fewer than balances)
for i, r in enumerate(raws[:12]):
    curpmt_tab = curpmt_tab.merge(_ext_cur(r, f"curpmt{i}"), on="Uniqueid", how="left")

curpmt_cols = [f"curpmt{i}" for i in range(12)]
for i in range(1, 12):
    curpmt_tab[f"curpmt{i}"] = curpmt_tab[f"curpmt{i}"].fillna(curpmt_tab[f"curpmt{i-1}"])
curpmt_tab[curpmt_cols] = curpmt_tab[curpmt_cols].fillna(0)

# ── full_tab: merge everything ────────────────────────────────────────────────
full_tab = (
    left_tab
    .merge(cust_tab,   on="Uniqueid", how="left")
    .merge(bal_tab,    on="Uniqueid", how="left")
    .merge(curpmt_tab, on="Uniqueid", how="left")
)

# ── Computed columns ──────────────────────────────────────────────────────────

# schedpmt{i} and schedBal{i} — uses actual book balances each period
for i in range(1, 13):
    p = i - 1  # previous period index
    bal  = full_tab[f"curbookbal{p}"].to_numpy(dtype=float)
    rt   = full_tab[f"custrt{p}"].to_numpy(dtype=float)
    pmt  = full_tab[f"curpmt{p}"].to_numpy(dtype=float)
    principal = pmt - bal * rt / 1200
    full_tab[f"schedpmt{i}"] = np.where(principal >= bal, bal, principal)
    full_tab[f"schedBal{i}"] = bal - full_tab[f"schedpmt{i}"].to_numpy()

# dpmt{i} and dBal{i} — chained from schedBal1 (no prepayment adjustments)
# dBal1 ≡ schedBal1 (starting point for the chain)
for i in range(2, 13):
    p = i - 1
    prev_dbal = full_tab["schedBal1"].to_numpy(dtype=float) if i == 2 else full_tab[f"dBal{p}"].to_numpy(dtype=float)
    rt  = full_tab[f"custrt{p}"].to_numpy(dtype=float)
    pmt = full_tab[f"curpmt{p}"].to_numpy(dtype=float)
    principal = pmt - prev_dbal * rt / 1200
    full_tab[f"dpmt{i}"] = np.where(principal >= prev_dbal, prev_dbal, principal)
    full_tab[f"dBal{i}"] = prev_dbal - full_tab[f"dpmt{i}"].to_numpy()

# d{i} — cumulative balance retention ratios (NaN → 0)
bal0 = full_tab["curbookbal0"].to_numpy(dtype=float)
sbal1 = full_tab["schedBal1"].to_numpy(dtype=float)
with np.errstate(divide="ignore", invalid="ignore"):
    d1 = np.where(bal0 == 0, 0, sbal1 / bal0)
full_tab["d1"] = np.nan_to_num(d1, nan=0.0)

prev_d    = full_tab["d1"].to_numpy()
prev_dbal = sbal1
for i in range(2, 13):
    dbal_i = full_tab[f"dBal{i}"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(prev_dbal == 0, 0, dbal_i / prev_dbal)
    d_i = np.nan_to_num(prev_d * ratio, nan=0.0)
    full_tab[f"d{i}"] = d_i
    prev_d    = d_i
    prev_dbal = dbal_i

# unschedPmt{i} — unscheduled prepayments
for i in range(1, 13):
    p = i - 1
    bal_prev = full_tab[f"curbookbal{p}"].to_numpy(dtype=float)
    bal_curr = full_tab[f"curbookbal{i}"].to_numpy(dtype=float)
    sched    = full_tab[f"schedpmt{i}"].to_numpy(dtype=float)
    full_tab[f"unschedPmt{i}"] = np.where(
        bal_prev == 0, 0, bal_prev - bal_curr - sched
    )

# ── excel_tab: full export selection ─────────────────────────────────────────
EXCEL_COLS = (
    ["Uniqueid", "Coa", "ProductCode", "RiskProduct", "LoanType",
     "IssueDt", "Maturity", "Cltrl_Id", "LoanToValu", "OrigAmount", "RAM",
     "State", "OrigFico", "Iss_Coupon", "Lt_Cap", "Arm_Spd",
     "IdxSelDays", "Tsr_Period", "Reset_Term", "Cap", "First_Cap",
     "IndexName", "IndexCode", "Ownership"]
    + [f"custrt{i}"      for i in range(13)]
    + [f"curbookbal{i}"  for i in range(13)]
    + [f"d{i}"           for i in range(1, 13)]
    + [f"unschedPmt{i}"  for i in range(1, 13)]
    + [f"schedBal{i}"    for i in range(1, 13)]
    + [f"curpmt{i}"      for i in range(12)]
)
excel_tab = full_tab[EXCEL_COLS].copy()

# ── Join collateral bucket mapping ───────────────────────────────────────────
collatbuckets = pd.read_csv(COLLATMAP)
collatbuckets.columns = ["ProductCode", "Cltrl_Id_2", "Cltrl_Group"]
excel_tab = excel_tab.merge(collatbuckets, on="ProductCode", how="left")

# ── Filters ───────────────────────────────────────────────────────────────────
# Filter 1: Remove non-performing loans (handled upstream in almxcleaner).
# Filter 2: Remove construction period loans (LoanType == 5).
excel_tab = excel_tab[excel_tab["LoanType"] != 5].copy()

# Override collateral group for COA 30101
excel_tab.loc[excel_tab["Coa"] == 30101, "Cltrl_Group"] = "HE"

# ── Export full table ─────────────────────────────────────────────────────────
excel_tab.to_csv(
    f"{OUT_DIR}Backtesting_Full_Table_{FEED}_{DATES[0]}.csv",
    index=False,
)

# ── import_tab: slim export with formatted date columns ──────────────────────
IMPORT_COLS = (
    ["Uniqueid", "Cltrl_Group", "IssueDt", "Maturity",
     "ProductCode", "RiskProduct", "Cltrl_Id_2",
     "LoanToValu", "OrigAmount", "RAM", "State", "OrigFico",
     "Iss_Coupon", "Lt_Cap", "Arm_Spd", "IdxSelDays", "Tsr_Period",
     "Reset_Term", "Cap", "First_Cap", "IndexName", "IndexCode"]
    + [f"custrt{i}"      for i in range(13)]
    + [f"curbookbal{i}"  for i in range(13)]
    + [f"d{i}"           for i in range(1, 13)]
    + [f"unschedPmt{i}"  for i in range(1, 13)]
    + [f"schedBal{i}"    for i in range(1, 13)]
)
import_tab = excel_tab[IMPORT_COLS].copy()

# Convert IssueDt and Maturity to YYYYMM integer format
for col in ("IssueDt", "Maturity"):
    import_tab[col] = (
        pd.to_datetime(import_tab[col], infer_datetime_format=True)
        .dt.strftime("%Y%m")
        .astype(int)
    )

import_tab.to_csv(
    f"{OUT_DIR}Backtesting_Import_{FEED}_{DATES[0]}_new.csv",
    index=False,
)

print("Done. Files written to", OUT_DIR)
