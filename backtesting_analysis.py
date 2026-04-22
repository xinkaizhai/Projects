"""
backtesting_analysis.py

Python equivalent of the second R script:
- Loads prediction SMM results and the actual backtesting data (import_tab from
  backtesting_prep.py, referred to as `actual_file` in the original R code).
- Aggregates actual balances and prepayments by collateral group.
- Recalculates projected balances using the predicted SMM values.
- Exports actuals and predicted summary tables.

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

# ── Load data ─────────────────────────────────────────────────────────────────
actual_file = pd.read_csv(ACTUAL_FILE)

if OUTPUT in ("predict", "both"):
    predsmm_file = pd.read_csv(PRED_FILE)

# ── Shared aggregation spec ───────────────────────────────────────────────────
AGG = {
    "n":        ("Uniqueid",      "count"),
    **{f"bal{i}":      (f"curbookbal{i}",  "sum") for i in range(13)},
    **{f"unsched{i}":  (f"unschedPmt{i}",  "sum") for i in range(1, 13)},
    **{f"sbal{i}":     (f"schedBal{i}",    "sum") for i in range(1, 13)},
}

# ── Actuals CPR Summary ───────────────────────────────────────────────────────
if OUTPUT in ("actual", "both"):
    actuals = (
        actual_file
        .groupby(["Cltrl_Group", "Cltrl_Id_2"])
        .agg(**{k: pd.NamedAgg(column=v[0], aggfunc=v[1]) for k, v in AGG.items()})
        .reset_index()
    )

# ── Predictions Aggregation ───────────────────────────────────────────────────
if OUTPUT in ("predict", "both"):
    PRED_COLS = (
        ["Uniqueid", "Cltrl_Group", "Cltrl_Id_2", "curbookbal0"]
        + [f"custrt{i}"  for i in range(13)]
        + [f"curpmt{i}"  for i in range(12)]
    )

    predsmm_calc = (
        actual_file[PRED_COLS]
        .merge(predsmm_file, on="Uniqueid", how="left")
        .copy()
    )

    # Iteratively recompute balances using predicted SMM values.
    # Each period's curbookbal is overwritten so subsequent periods use it.
    for i in range(1, 13):
        p = i - 1  # previous period index

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
        predsmm_calc[f"curbookbal{i}"] = new_bal  # overwrite for next period

    predicts = (
        predsmm_calc
        .groupby(["Cltrl_Group", "Cltrl_Id_2"])
        .agg(**{k: pd.NamedAgg(column=v[0], aggfunc=v[1]) for k, v in AGG.items()})
        .reset_index()
    )

# ── Export ────────────────────────────────────────────────────────────────────
if OUTPUT in ("actual", "both"):
    actuals.to_csv(f"{OUT_DIR}USmortgageBN_Actual_Results.csv", index=False)
    print("Written: USmortgageBN_Actual_Results.csv")

if OUTPUT in ("predict", "both"):
    predicts.to_csv(f"{OUT_DIR}USmortgageBN_Predict_Results.csv", index=False)
    print("Written: USmortgageBN_Predict_Results.csv")

print("Done. Files written to", OUT_DIR)
