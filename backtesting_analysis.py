"""
backtesting_analysis.py

Python equivalent of the second R script:
- Loads prediction SMM results and the actual backtesting data (import_tab from
  backtesting_prep.py, referred to as `actual_file` in the original R code).
- Aggregates actual balances and prepayments by collateral group.
- Recalculates projected balances using the predicted SMM values.
- Exports one Excel file per output type (actual/predict), each with two sheets:
    "Data" — aggregated rows by Cltrl_Group/Cltrl_Id_2, with a Category column
    "CPR"  — one row per category + Overall Portfolio, CPR1…CPR{N-1}

NOTE: `actual_file` corresponds to the `import_tab` produced by backtesting_prep.py.
"""

import pandas as pd
import numpy as np

# ── Configuration ─────────────────────────────────────────────────────────────
FEED = "UsMortgageBN"

# Output toggle: "actual", "predict", or "both"
OUTPUT = "both"

PRED_FILE   = r"C:\Version 6\v6.43f\Backtesting\Mortgage\Backtesting_Outputs_UsMortgageBN_202406ME_v643f_remastered.csv"
ACTUAL_FILE = r"C:\1_Monthly Backtesting\2025\202511ME\Mortgage\Backtesting_Import_UsMortgageBN_202410_new.csv"
OUT_DIR     = r"C:\Version 6\v6.43f" + "\\"

# ── Category definitions (Cltrl_Group sets per loan category) ─────────────────
CATEGORIES = {
    "Fixed_Conforming": {
        "Conf Fixed 30y", "Conf Constr Fixed",
        "COMMUNITY CONFORMING 30 YEAR FIXED", "30YR FIXED REFINOW",
        "Agency Eligible 30y", "Conf Fixed 15y", "15YR FIXED REFINOW",
        "Agency Eligible 15y",
    },
    "Fixed_Conforming_30y": {
        "Conf Fixed 30y", "Conf Constr Fixed",
        "COMMUNITY CONFORMING 30 YEAR FIXED", "30YR FIXED REFINOW",
        "Agency Eligible 30y",
    },
    "Fixed_Conforming_15y": {
        "Conf Fixed 15y", "15YR FIXED REFINOW", "Agency Eligible 15y",
    },
    "Fixed_Jumbo": {
        "Jumbo Fixed 30y", "Jumbo Constr Fixed", "Jumbo Fixed 15y",
    },
    "Fixed_Jumbo_30y": {
        "Jumbo Fixed 30y", "Jumbo Constr Fixed",
    },
    "Fixed_Jumbo_15y": {
        "Jumbo Fixed 15y",
    },
    "ARM_Conforming": {
        "Conf ARM", "Conf Constr ARM", "Agency Eligible ARM",
    },
    "ARM_Jumbo": {
        "Jumbo ARM", "Jumbo Constr ARM",
    },
}

# ── Load data ─────────────────────────────────────────────────────────────────
actual_file = pd.read_csv(ACTUAL_FILE)

# Infer number of periods from curbookbal columns (curbookbal0 … curbookbalN-1)
N = sum(1 for c in actual_file.columns if c.startswith("curbookbal"))

if OUTPUT in ("predict", "both"):
    predsmm_file = pd.read_csv(PRED_FILE)

# ── Shared aggregation spec ───────────────────────────────────────────────────
AGG = {
    "n":        ("Uniqueid",      "count"),
    **{f"bal{i}":      (f"curbookbal{i}",  "sum") for i in range(N)},
    **{f"unsched{i}":  (f"unschedPmt{i}",  "sum") for i in range(1, N)},
    **{f"sbal{i}":     (f"schedBal{i}",    "sum") for i in range(1, N)},
}


def _aggregate(cat_data):
    return (
        cat_data
        .groupby(["Cltrl_Group", "Cltrl_Id_2"])
        .agg(**{k: pd.NamedAgg(column=v[0], aggfunc=v[1]) for k, v in AGG.items()})
        .reset_index()
    )


def _cpr_row(cat_name, agg_df):
    """Return a one-row dict with category-level CPR for each period."""
    row = {"Category": cat_name}
    for i in range(1, N):
        total_unsched = agg_df[f"unsched{i}"].sum()
        total_sbal    = agg_df[f"sbal{i}"].sum()
        smm = 0 if total_sbal == 0 else total_unsched / total_sbal
        row[f"CPR{i}"] = (1 - (1 - smm) ** 12) * 100
    return row


# ── Analysis helper ───────────────────────────────────────────────────────────
def run_analysis(cat_name, cat_data):
    """Return (detail_df, cpr_row) for actuals and predicts."""
    if cat_data.empty:
        print(f"Skipping {cat_name}: no matching loans.")
        return (None, None), (None, None)

    act_detail = act_cpr = pred_detail = pred_cpr = None

    if OUTPUT in ("actual", "both"):
        agg = _aggregate(cat_data)
        agg.insert(0, "Category", cat_name)
        act_detail = agg
        act_cpr    = _cpr_row(cat_name, agg)

    if OUTPUT in ("predict", "both"):
        PRED_COLS = (
            ["Uniqueid", "Cltrl_Group", "Cltrl_Id_2", "curbookbal0"]
            + [f"custrt{i}"  for i in range(N)]
            + [f"curpmt{i}"  for i in range(N-1)]
        )
        predsmm_calc = (
            cat_data[PRED_COLS]
            .merge(predsmm_file, on="Uniqueid", how="left")
            .copy()
        )
        for i in range(1, N):
            p = i - 1
            bal  = predsmm_calc[f"curbookbal{p}"].to_numpy(dtype=float)
            rt   = predsmm_calc[f"custrt{p}"].to_numpy(dtype=float)
            pmt  = predsmm_calc[f"curpmt{p}"].to_numpy(dtype=float)
            smm  = predsmm_calc[f"SMM{i}"].to_numpy(dtype=float)
            principal = pmt - bal * rt / 1200
            sched_pmt = np.where(principal >= bal, bal, principal)
            sched_bal = bal - sched_pmt
            unsched   = np.minimum(sched_bal, bal * smm / 100)
            new_bal   = sched_bal - unsched
            predsmm_calc[f"schedpmt{i}"]   = sched_pmt
            predsmm_calc[f"schedBal{i}"]   = sched_bal
            predsmm_calc[f"unschedPmt{i}"] = unsched
            predsmm_calc[f"curbookbal{i}"] = new_bal
        agg = _aggregate(predsmm_calc)
        agg.insert(0, "Category", cat_name)
        pred_detail = agg
        pred_cpr    = _cpr_row(cat_name, agg)

    return (act_detail, act_cpr), (pred_detail, pred_cpr)


# ── Run all categories ────────────────────────────────────────────────────────
act_details  = []
act_cprs     = []
pred_details = []
pred_cprs    = []

# Fixed / ARM top-level by RiskProduct
for cat_name, keyword in [("Fixed", "Fixed"), ("ARM", "Adjustable")]:
    (ad, ac), (pd_, pc) = run_analysis(
        cat_name,
        actual_file[actual_file["RiskProduct"].str.contains(keyword, case=False, na=False)].copy()
    )
    if ad is not None: act_details.append(ad);  act_cprs.append(ac)
    if pd_ is not None: pred_details.append(pd_); pred_cprs.append(pc)

# Sub-categories by Cltrl_Group
for cat_name, cltrl_groups in CATEGORIES.items():
    (ad, ac), (pd_, pc) = run_analysis(
        cat_name,
        actual_file[actual_file["Cltrl_Group"].isin(cltrl_groups)].copy()
    )
    if ad is not None: act_details.append(ad);  act_cprs.append(ac)
    if pd_ is not None: pred_details.append(pd_); pred_cprs.append(pc)

# Overall Portfolio CPR (all loans, no filter)
overall_agg_act  = _aggregate(actual_file) if OUTPUT in ("actual", "both") else None
overall_agg_pred = None
if OUTPUT in ("predict", "both"):
    PRED_COLS = (
        ["Uniqueid", "Cltrl_Group", "Cltrl_Id_2", "curbookbal0"]
        + [f"custrt{i}"  for i in range(N)]
        + [f"curpmt{i}"  for i in range(N-1)]
    )
    predsmm_calc = (
        actual_file[PRED_COLS]
        .merge(predsmm_file, on="Uniqueid", how="left")
        .copy()
    )
    for i in range(1, N):
        p = i - 1
        bal  = predsmm_calc[f"curbookbal{p}"].to_numpy(dtype=float)
        rt   = predsmm_calc[f"custrt{p}"].to_numpy(dtype=float)
        pmt  = predsmm_calc[f"curpmt{p}"].to_numpy(dtype=float)
        smm  = predsmm_calc[f"SMM{i}"].to_numpy(dtype=float)
        principal = pmt - bal * rt / 1200
        sched_pmt = np.where(principal >= bal, bal, principal)
        sched_bal = bal - sched_pmt
        unsched   = np.minimum(sched_bal, bal * smm / 100)
        new_bal   = sched_bal - unsched
        predsmm_calc[f"schedpmt{i}"]   = sched_pmt
        predsmm_calc[f"schedBal{i}"]   = sched_bal
        predsmm_calc[f"unschedPmt{i}"] = unsched
        predsmm_calc[f"curbookbal{i}"] = new_bal
    overall_agg_pred = _aggregate(predsmm_calc)

if overall_agg_act  is not None: act_cprs.append(_cpr_row("Overall Portfolio", overall_agg_act))
if overall_agg_pred is not None: pred_cprs.append(_cpr_row("Overall Portfolio", overall_agg_pred))

# ── Export Excel files ────────────────────────────────────────────────────────
if OUTPUT in ("actual", "both") and act_details:
    with pd.ExcelWriter(f"{OUT_DIR}USmortgageBN_Actual_Results.xlsx", engine="openpyxl") as writer:
        pd.concat(act_details, ignore_index=True).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame(act_cprs).to_excel(writer, sheet_name="CPR", index=False)
    print("Written: USmortgageBN_Actual_Results.xlsx")

if OUTPUT in ("predict", "both") and pred_details:
    with pd.ExcelWriter(f"{OUT_DIR}USmortgageBN_Predict_Results.xlsx", engine="openpyxl") as writer:
        pd.concat(pred_details, ignore_index=True).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame(pred_cprs).to_excel(writer, sheet_name="CPR", index=False)
    print("Written: USmortgageBN_Predict_Results.xlsx")

print("Done. Files written to", OUT_DIR)
