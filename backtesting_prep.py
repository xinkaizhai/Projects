"""
backtesting_prep.py

Python equivalent of the first R script:
- Loads XFP_CurrHold balances from CanoeReporting SQL DB for N periods
- Loads Canoe SQL (enriched) CSV tables for rates, payments, and loan attributes
- Uses first XFP month as starting portfolio; left-joins SQL attributes
- Builds customer-rate, book-balance, and current-payment panels
- Computes scheduled balances, d-ratios, and unscheduled prepayments
- Exports a full backtesting table and a slimmer import table
"""

import pandas as pd
import numpy as np
import pyodbc
from datetime import datetime
from pandas.tseries.offsets import MonthEnd, BDay

# ── Configuration ─────────────────────────────────────────────────────────────
FEED = "UsMortgageBN"
DATE_START = "202410"
DATE_END   = "202510"

def _gen_dates(start: str, end: str) -> list[str]:
    """Generate YYYYMM strings from start to end inclusive."""
    from datetime import date
    y, m = int(start[:4]), int(start[4:])
    ey, em = int(end[:4]), int(end[4:])
    dates = []
    while (y, m) <= (ey, em):
        dates.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return dates

DATES = _gen_dates(DATE_START, DATE_END)
TODAY = datetime(2025, 10, 31)  # last day of the latest backtesting month
FEEDCODE = "bnmg"

SQL_DIR   = r"C:\1_Monthly Backtesting\MBT\sqldata" + "\\"
OUT_DIR   = r"C:\1_Monthly Backtesting\2025\202511ME\Mortgage" + "\\"
COLLATMAP = r"C:\1_Monthly Backtesting\MBT\CollatIdMapping.csv"

DB_CONN = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=CRSDPSBCA0TBC.tdbfg.com,3341;"
    "DATABASE=CanoeReporting;"
    "Trusted_Connection=yes;"
)

N = len(DATES)

# ── Helper functions ───────────────────────────────────────────────────────────
def last_weekday_of_month(date):
    month_end = date + MonthEnd(0)
    if month_end.weekday() >= 5:
        month_end = month_end - BDay(1)
    return month_end


def cpnfix(coupon):
    """Convert semi-annual compounded coupon to monthly/simple rate."""
    return np.round(1200 * (((coupon / 200) + 1) ** (1 / 6) - 1), 3)


def _ext_cust(table, col_name):
    """Return (Uniqueid, <col_name>) from a SQL table."""
    tmp = table.copy()
    tmp[col_name] = cpnfix(tmp["R_Coupon"].to_numpy(dtype=float))
    return tmp.rename(columns={"_K_CertificateCode": "Uniqueid"})[["Uniqueid", col_name]]



def _ext_cur(table, col_name):
    """Return (Uniqueid, <col_name>) from a raw SQL table."""
    tmp = table.copy()
    tmp[col_name] = tmp["R_CurrentPayment"]
    return tmp.rename(columns={"_K_CertificateCode": "Uniqueid"})[["Uniqueid", col_name]]


# ── Load data ─────────────────────────────────────────────────────────────────
raws = [
    pd.read_csv(
        f"{SQL_DIR}{FEEDCODE}-{d}.csv",
        low_memory=False,
        encoding="utf-8-sig",
        dtype={"_K_CertificateCode": str},
    )
    for d in DATES
]

# ── XFP balances from CanoeReporting DB ───────────────────────────────────────
# month_map: [(YYYYMM, last-weekday-of-month date string), ...]
month_map = [
    (d, last_weekday_of_month(pd.Timestamp(f"{d[:4]}-{d[4:]}-01")).strftime("%Y-%m-%d"))
    for d in DATES
]

conn = pyodbc.connect(DB_CONN)


def _query(sql):
    """Run SQL and return a DataFrame without triggering the SQLAlchemy warning."""
    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    return pd.DataFrame.from_records(cursor.fetchall(), columns=cols)


# Base month — defines starting portfolio universe
base_key, base_date = month_map[0]
xfp_df = _query(f"""
    SELECT _K_CertificateCode, XFP_CurrHold AS [{base_key}]
    FROM [CanoeReporting].[USMortgageBN].[XFpDaily]
    WHERE _K_AsOfDate = '{base_date}'
      AND XFP_ValuationType = 'Closed'
""")

# Remaining months — left join onto base universe
for month_key, asof_date in month_map[1:]:
    temp_df = _query(f"""
        SELECT _K_CertificateCode, XFP_CurrHold AS [{month_key}]
        FROM [CanoeReporting].[USMortgageBN].[XFpDaily]
        WHERE _K_AsOfDate = '{asof_date}'
          AND XFP_ValuationType = 'Closed'
    """)
    xfp_df = xfp_df.merge(temp_df, on="_K_CertificateCode", how="left")

conn.close()

# Cast to string to match _K_CertificateCode dtype in CSV files
xfp_df["_K_CertificateCode"] = xfp_df["_K_CertificateCode"].astype(str)
xfp_df = xfp_df.sort_values("_K_CertificateCode").reset_index(drop=True)

# ── left_raw: static loan attributes from the first raw snapshot ──────────────
RAW_RENAME = {
    "_K_CertificateCode":  "Uniqueid",
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
    "E_Notional":          "Notional",
    "R_Coupon":            "Coupon",
}
LEFT_RAW_COLS = [
    "Uniqueid", "Coa", "ProductCode", "RiskProduct", "LoanType",
    "IssueDt", "Maturity", "Cltrl_Id", "LoanToValu", "OrigAmount",
    "State", "OrigFico", "Iss_Coupon", "Lt_Cap", "Arm_Spd",
    "IdxSelDays", "Tsr_Period", "IndexCode", "Reset_Term", "Cap",
    "First_Cap", "IndexName", "Ownership", "Notional",
]
left_raw = (
    xfp_df[["_K_CertificateCode"]]
    .merge(raws[0], on="_K_CertificateCode", how="left")
    .sort_values("_K_CertificateCode")
    .rename(columns=RAW_RENAME)[LEFT_RAW_COLS]
)

# ── left_tab: static loan attributes from left_raw ───────────────────────────
LEFT_TAB_COLS = [
    "Uniqueid", "Coa", "ProductCode", "LoanType", "RiskProduct",
    "IssueDt", "Maturity", "Cltrl_Id", "LoanToValu", "OrigAmount",
    "State", "OrigFico", "Iss_Coupon", "Lt_Cap", "Arm_Spd",
    "IdxSelDays", "Tsr_Period", "IndexCode", "Reset_Term", "Cap",
    "First_Cap", "IndexName", "Ownership", "Notional",
]
left_tab = left_raw[LEFT_TAB_COLS].copy()

# ── cust_tab: customer rates for all N periods ────────────────────────────────
cust_tab = _ext_cust(raws[0], "custrt0")
for i, t in enumerate(raws[1:], 1):
    cust_tab = cust_tab.merge(_ext_cust(t, f"custrt{i}"), on="Uniqueid", how="left")

# Forward-fill NaN values across periods, then fill any remaining with 0
custrt_cols = [f"custrt{i}" for i in range(N)]
for i in range(1, N):
    cust_tab[f"custrt{i}"] = cust_tab[f"custrt{i}"].fillna(cust_tab[f"custrt{i-1}"])
cust_tab[custrt_cols] = cust_tab[custrt_cols].fillna(0)

# ── bal_tab: XFP_CurrHold balances for all N periods ─────────────────────────
bal_tab = xfp_df.rename(columns={"_K_CertificateCode": "Uniqueid"}).rename(
    columns={d: f"curbookbal{i}" for i, d in enumerate(DATES)}
)
bal_cols = [f"curbookbal{i}" for i in range(N)]
bal_tab = bal_tab[["Uniqueid"] + bal_cols].copy()
bal_tab[bal_cols] = bal_tab[bal_cols].fillna(0)

# ── curpmt_tab: current payments for periods 0 to N-2 (one fewer than balances)
curpmt_tab = xfp_df[["_K_CertificateCode"]].rename(
    columns={"_K_CertificateCode": "Uniqueid"}
)
for i, r in enumerate(raws[:N-1]):
    curpmt_tab = curpmt_tab.merge(_ext_cur(r, f"curpmt{i}"), on="Uniqueid", how="left")

curpmt_cols = [f"curpmt{i}" for i in range(N-1)]
for i in range(1, N-1):
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
for i in range(1, N):
    p = i - 1  # previous period index
    bal  = full_tab[f"curbookbal{p}"].to_numpy(dtype=float)
    rt   = full_tab[f"custrt{p}"].to_numpy(dtype=float)
    pmt  = full_tab[f"curpmt{p}"].to_numpy(dtype=float)
    principal = pmt - bal * rt / 1200
    full_tab[f"schedpmt{i}"] = np.where(principal >= bal, bal, principal)
    full_tab[f"schedBal{i}"] = bal - full_tab[f"schedpmt{i}"].to_numpy()

# dpmt{i} and dBal{i} — chained from schedBal1 (no prepayment adjustments)
# dBal1 ≡ schedBal1 (starting point for the chain)
for i in range(2, N):
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
for i in range(2, N):
    dbal_i = full_tab[f"dBal{i}"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(prev_dbal == 0, 0, dbal_i / prev_dbal)
    d_i = np.nan_to_num(prev_d * ratio, nan=0.0)
    full_tab[f"d{i}"] = d_i
    prev_d    = d_i
    prev_dbal = dbal_i

# unschedPmt{i} — unscheduled prepayments
for i in range(1, N):
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
     "IssueDt", "Maturity", "Cltrl_Id", "LoanToValu", "OrigAmount",
     "State", "OrigFico", "Iss_Coupon", "Lt_Cap", "Arm_Spd",
     "IdxSelDays", "Tsr_Period", "Reset_Term", "Cap", "First_Cap",
     "IndexName", "IndexCode", "Ownership", "Notional"]
    + [f"custrt{i}"      for i in range(N)]
    + [f"curbookbal{i}"  for i in range(N)]
    + [f"d{i}"           for i in range(1, N)]
    + [f"unschedPmt{i}"  for i in range(1, N)]
    + [f"schedBal{i}"    for i in range(1, N)]
    + [f"curpmt{i}"      for i in range(N-1)]
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

# Filter 3: Remove Cash, UsNsFixedRateMortgage, UsNsAdjustableRateMortgage deals.
EXCLUDE_RISK_PRODUCTS = {"Cash", "UsNsFixedRateMortgage", "UsNsAdjustableRateMortgage"}
excel_tab = excel_tab[~excel_tab["RiskProduct"].isin(EXCLUDE_RISK_PRODUCTS)].copy()

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
     "LoanToValu", "OrigAmount", "State", "OrigFico",
     "Iss_Coupon", "Lt_Cap", "Arm_Spd", "IdxSelDays", "Tsr_Period",
     "Reset_Term", "Cap", "First_Cap", "IndexName", "IndexCode", "Notional"]
    + [f"custrt{i}"      for i in range(N)]
    + [f"curbookbal{i}"  for i in range(N)]
    + [f"d{i}"           for i in range(1, N)]
    + [f"unschedPmt{i}"  for i in range(1, N)]
    + [f"schedBal{i}"    for i in range(1, N)]
)
import_tab = excel_tab[IMPORT_COLS].copy()

# Convert IssueDt and Maturity to YYYYMM integer format
for col in ("IssueDt", "Maturity"):
    import_tab[col] = (
        pd.to_datetime(import_tab[col], infer_datetime_format=True)
        .dt.strftime("%Y%m")
        .astype("Int64")  # nullable int — tolerates NaN for unmatched loans
    )

import_tab.to_csv(
    f"{OUT_DIR}Backtesting_Import_{FEED}_{DATES[0]}_new.csv",
    index=False,
)

print("Done. Files written to", OUT_DIR)
