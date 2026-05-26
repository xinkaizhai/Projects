"""
backtesting_analysis.py

Python equivalent of the second R script:
- Loads prediction SMM results and the actual backtesting data (import_tab from
  backtesting_prep.py, referred to as `actual_file` in the original R code).
- Aggregates actual balances and prepayments by collateral group.
- Recalculates projected balances using the predicted SMM values.
- Exports actuals and predicted summary tables, split by loan category.

NOTE: `actual_file` corresponds to the `import_tab` produced by backtesting_prep.py.
      Load it from the CSV written there, or pass the DataFrame directly if running
      both scripts in the same session.
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

# ── Analysis helper ───────────────────────────────────────────────────────────
def run_analysis(cat_name, cat_data):
    if cat_data.empty:
        print(f"Skipping {cat_name}: no matching loans.")
        return

    if OUTPUT in ("actual", "both"):
        actuals = (
            cat_data
            .groupby(["Cltrl_Group", "Cltrl_Id_2"])
            .agg(**{k: pd.NamedAgg(column=v[0], aggfunc=v[1]) for k, v in AGG.items()})
            .reset_index()
        )
        actuals.to_csv(f"{OUT_DIR}USmortgageBN_Actual_{cat_name}.csv", index=False)
        print(f"Written: USmortgageBN_Actual_{cat_name}.csv")

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
        predicts = (
            predsmm_calc
            .groupby(["Cltrl_Group", "Cltrl_Id_2"])
            .agg(**{k: pd.NamedAgg(column=v[0], aggfunc=v[1]) for k, v in AGG.items()})
            .reset_index()
        )
        predicts.to_csv(f"{OUT_DIR}USmortgageBN_Predict_{cat_name}.csv", index=False)
        print(f"Written: USmortgageBN_Predict_{cat_name}.csv")


# ── Fixed / ARM top-level categories (by RiskProduct keyword) ─────────────────
run_analysis("Fixed", actual_file[actual_file["RiskProduct"].str.contains("Fixed",      case=False, na=False)].copy())
run_analysis("ARM",   actual_file[actual_file["RiskProduct"].str.contains("Adjustable", case=False, na=False)].copy())

# ── Sub-categories by collateral group ────────────────────────────────────────
for cat_name, cltrl_groups in CATEGORIES.items():
    run_analysis(cat_name, actual_file[actual_file["Cltrl_Group"].isin(cltrl_groups)].copy())

print("Done. Files written to", OUT_DIR)
