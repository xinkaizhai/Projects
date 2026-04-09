"""
Python ctypes wrapper for AFT ESP Model DLLs:
  - espmodel.dll   : prepayment / default model engine
  - prepayScore.dll: loan scoring functions

Target platform: Windows x64 (DLLs are PE32+ x86-64)

Typical usage
-------------
from esp_wrapper import AFTModel

DLL_DIR  = r"C:\\path\\to\\dll_folder"   # contains espmodel.dll & prepayScore.dll
DATA_DIR = r"C:\\path\\to\\data_folder"  # contains model params and score data

model = AFTModel(DLL_DIR, DATA_DIR)
print(model.version())

# ── Prepay speed (modern multi-thread API) ──────────────────────────────
smm, _ = model.calc_prepay_mthread(
    agency_name=b"FNMA",
    orig_term_months=360,
    amort_period_months=360,
    age_months=24,
    wam_months=336,
    gross_wac_pct=6.5,
    net_coupon_pct=6.0,
    settle_date=202501,
    mrate_date=202501,
    mtg_rate_30yr=[6.5] * 336,
)

# ── Prepay + default ────────────────────────────────────────────────────
smm, defaults = model.calc_prepay_and_default_mthread(
    agency_name=b"FNMA",
    orig_term_months=360,
    amort_period_months=360,
    age_months=24,
    wam_months=336,
    gross_wac_pct=6.5,
    net_coupon_pct=6.0,
    settle_date=202501,
    mrate_date=202501,
    mtg_rate_30yr=[6.5] * 336,
)

# ── Loan scoring ────────────────────────────────────────────────────────
rc = model.calc_loan_score_q(
    agency_name=b"FNMA",
    orig_term_months=360,
    age_months=24,
    gross_wac_pct=6.5,
)

model.cleanup()

# ── Input parameter guide ────────────────────────────────────────────────
#
# Parameters fall into two categories with very different effects on SMM:
#
# DIRECT MODEL INPUTS  (always affect SMM)
# ─────────────────────────────────────────
#   EspMortgageCollatDescStruct  – agency_name, gross_wac_pct,
#                                  orig_term_months, age_months, wam_months
#                                  net_coupon_pct: for ARM clarifies "at settle date";
#                                  FRM prepay speed calc does not use this field
#   EspPrepayProjStruct          – settle_date, mrate_date, mtg_rate_30yr (and
#                                  other rate vectors)
#   fine_tune dict               – EspPrepayFineTuneStruct multipliers (ht_mult,
#                                  rf_mult, age_mult, hpa_rf_mult, etc.) directly
#                                  scale prepay components
#   default_dials dict           – EspDefaultModelInput fields: orig_ltv,
#                                  cur_adj_ltv, and all transition/rate multipliers
#                                  in EspDefaultModelDials directly affect the
#                                  default and prepay calculations
#   proj_hpi                     – HPA path for default model
#                                  (no effect in calc_prepay_mthread; use
#                                  calc_prepay_and_default_mthread instead)
#   proj_unemp                   – MF unemployment path (multifamily loans)
#                                  (same: no effect in calc_prepay_mthread)
#
# SCORING INPUTS  (affect SMM only when score files cover your loan type)
# ────────────────────────────────────────────────────────────────────────
#   loan_level dict  → EspPrepayLoanLevelDescStruct
#   loan_info dict   → EspLoanAndBorrowerInfoStruct
#
#   Per esp_defs.h (lines 2787–2791): both structs are used by the DLL to
#   "find the proper prepayment scores and apply them to the model projections."
#   They control WHICH score bin is looked up from the AFT score files, and that
#   score then scales the base prepay speed.  They have NO direct effect on SMM
#   independently of scoring.
#
#   Additionally (esp_defs.h line 4431): when pEspPrepaymentScoreStruct is
#   non-NULL (i.e. input_scores is provided), the DLL ignores BOTH
#   EspPrepayLoanLevelDescStruct and EspLoanAndBorrowerInfoStruct entirely.
#
#   Consequence: if no score files match your loan type, or if you supply
#   input_scores directly, loan_level and loan_info have zero effect on SMM.
"""

import ctypes
import os
import sys
from typing import List, Optional, Tuple


# ── Windows-only guard ──────────────────────────────────────────────────────
if sys.platform != "win32":
    raise OSError("ESP DLLs are Windows-only.  Run this wrapper on a Windows x64 system.")


# ═══════════════════════════════════════════════════════════════════════════
# Constants (from esp_defs.h)
# ═══════════════════════════════════════════════════════════════════════════

ESP_GENERIC_MBS_COLLATERAL_AGENCY_NAME_LENGTH = 40
ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH            = 20
ESP_GENERIC_MBS_ISSUER_NAME_LENGTH            = 200
ESP_GENERIC_PREPAYMENT_MODEL_PARAMETER_PATH_DIRECTORY_LENGTH = 200
ESP_GENERIC_CMO_DEAL_NAME_LENGTH              = 40
ESP_GENERIC_CMO_TRANCHE_NAME_LENGTH           = 20
ESP_GENERIC_CMO_DATA_PATH_DIRECTORY_LENGTH    = 200

MAX_MORTGAGE_TERM_IN_MONTH = 960  # common value; adjust if needed

# DLL return codes (esp_defs.h)
ESP_RC_OK                                                    = 0
ESP_ERROR_CODE_FOR_FAILURE_OF_READING_DEFAULT_MODEL_PARAMETER_FILES = 1109
ESP_ERROR_CODE_FOR_FAILURE_OF_READING_PREPAY_MODEL_HT_PARAMETER_FILE    = 1110
ESP_ERROR_CODE_FOR_FAILURE_OF_READING_PREPAY_MODEL_RF_PARAMETER_FILE    = 1111
ESP_ERROR_CODE_FOR_FAILURE_OF_READING_PREPAY_MODEL_GENERIC_PARAMETER_FILES = 1112



# ═══════════════════════════════════════════════════════════════════════════
# ctypes Structure definitions
# NOTE: Windows x64 uses LLP64 – 'long' is 32-bit.
#       ctypes.c_long matches this on Windows.
# ═══════════════════════════════════════════════════════════════════════════

class EsPInputs(ctypes.Structure):
    """
    Legacy prepayment input struct (used by EsPModel / EsPModel_mthread).
    Corresponds to 'struct EsPInputs' in esp_defs.h.
    """
    _fields_ = [
        ("agency_type",                    ctypes.c_int),
        ("agency_string_name",             ctypes.c_char_p),
        ("origTerm",                       ctypes.c_int),
        ("grossWAC",                       ctypes.c_double),
        ("age",                            ctypes.c_double),
        ("armPP",                          ctypes.c_void_p),   # StructArmPp* – NULL for FRM
        ("mtgRate30yr",                    ctypes.POINTER(ctypes.c_double)),
        ("mtgRate15yr",                    ctypes.POINTER(ctypes.c_double)),
        ("mtgRate7yr",                     ctypes.POINTER(ctypes.c_double)),
        ("mtgRate5yr",                     ctypes.POINTER(ctypes.c_double)),
        ("tnoteRate10",                    ctypes.POINTER(ctypes.c_double)),
        ("tnoteRate5",                     ctypes.POINTER(ctypes.c_double)),
        ("tnoteRate3",                     ctypes.POINTER(ctypes.c_double)),
        ("settleDate",                     ctypes.c_long),
        ("mrateDate",                      ctypes.c_long),
        ("paramsDir",                      ctypes.c_char_p),
        ("wl_pointer",                     ctypes.c_void_p),   # IndivMortgagePoolSpecifications*
        ("finetune_pointer",               ctypes.c_void_p),   # PrepayFineTuneStruct*
        ("pEspPrepayPenaltyDataStruct",    ctypes.c_void_p),
        ("pEspMortgageGeoData",            ctypes.c_void_p),
        ("futureUsePtr3",                  ctypes.c_void_p),
        ("futureUsePtr4",                  ctypes.c_void_p),
    ]


class EspDefaultRate(ctypes.Structure):
    """
    Per-month default projection output.
    Corresponds to 'EspDefaultRate' in esp_defs.h (version 5.45+, without COMPAT flags).

    Fields
    ------
    dPopulation0   : fraction of balance current (0-29 day)
    dPopulation30  : fraction 30-day delinquent
    dPopulation60  : fraction 60-day delinquent
    dPopulation90  : fraction 90-day delinquent
    dPopulationFc  : fraction in foreclosure
    defaultRate    : MDR (Monthly Default Rate)
    defaultLoss    : MDR * severity
    dPopulationREO : fraction in REO
    dEspCurrLTV    : current LTV
    dEspProjMod    : projected loan modification rate
    """
    _fields_ = [
        ("dPopulation0",               ctypes.c_double),
        ("dPopulation30",              ctypes.c_double),
        ("dPopulation60",              ctypes.c_double),
        ("dPopulation90",              ctypes.c_double),
        ("dPopulationFc",              ctypes.c_double),
        ("defaultRate",                ctypes.c_double),   # MDR
        ("defaultLoss",                ctypes.c_double),   # defaultRate * severity
        ("dPopulationREO",             ctypes.c_double),
        ("dEspCurrLTV",                ctypes.c_double),
        ("dEspProjMod",                ctypes.c_double),
        ("dEspProjMonthsDelinquent90", ctypes.c_double),
        ("dEspProjMonthsDelinquentFC", ctypes.c_double),
        ("dEspProjMonthsDelinquentREO",ctypes.c_double),
    ]

    def to_dict(self) -> dict:
        return {
            "population_current_pct":    self.dPopulation0,
            "population_30d_pct":        self.dPopulation30,
            "population_60d_pct":        self.dPopulation60,
            "population_90d_pct":        self.dPopulation90,
            "population_foreclosure_pct":self.dPopulationFc,
            "population_reo_pct":        self.dPopulationREO,
            "mdr":                       self.defaultRate,
            "default_loss":              self.defaultLoss,
            "curr_ltv":                  self.dEspCurrLTV,
            "proj_mod_rate":             self.dEspProjMod,
        }


class EspPrepaymentScoreStruct(ctypes.Structure):
    """
    Score outputs returned by GetPrepaymentScores APIs and scoring functions.
    Corresponds to 'EspPrepaymentScoreStruct' in esp_defs.h.

    The DLL allocates this struct and returns a pointer; always free it with
    freeEspPrepaymentScoreStruct() (called automatically by get_prepay_scores_mthread).

    Key output fields
    -----------------
    dEspHtPrepaymentScore  : Housing Turnover prepayment score
    dEspRfPrepaymentScore  : Refinancing prepayment score
    dEspFcDefaultScore     : Foreclosure default score
    dEspDelDefaultScore    : Delinquency default score
    dEspDrawScore          : Draw model score (for HELOC/draw products)
    dEspRatioOfScorableBal : Fraction of balance that was scored (0–1)
    nHasAdditionalScores   : 0 = only HT+RF scores; 1–4 = extra scores present
    """
    _fields_ = [
        ("szScoreIDString",              ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("lScoreID",                     ctypes.c_long),
        ("dEspHtPrepaymentScore",        ctypes.c_double),  # Housing Turnover score
        ("dEspRfPrepaymentScore",        ctypes.c_double),  # Refinancing score
        ("nHasAdditionalScores",         ctypes.c_int),
        ("dEspAdditionalPrepaymentScore1", ctypes.c_double),
        ("dEspRatioOfScorableBal",       ctypes.c_double),  # fraction 0–1
        ("dEspFcDefaultScore",           ctypes.c_double),  # Foreclosure default score
        ("dEspDelDefaultScore",          ctypes.c_double),  # Delinquency default score
        ("szFurtherNotes",               ctypes.c_char * ESP_GENERIC_MBS_ISSUER_NAME_LENGTH),
        ("szParamsDir",                  ctypes.c_char * ESP_GENERIC_PREPAYMENT_MODEL_PARAMETER_PATH_DIRECTORY_LENGTH),
        ("nForceUseInputScores",         ctypes.c_int),
        ("_ESPPSS_Int2",                 ctypes.c_int),
        ("dEspDrawScore",                ctypes.c_double),  # Draw model score
        ("_ESPPSS_Double2",              ctypes.c_double),
        ("_ESPPSS_CharPointer1",         ctypes.c_void_p),
        ("_ESPPSS_CharPointer2",         ctypes.c_void_p),
        ("pEspAdditionalScoreStruct",    ctypes.c_void_p),
        ("pvEspAddlScoreUpdateStruct",   ctypes.c_void_p),
    ]

    def to_dict(self) -> dict:
        """Return all key score outputs as a plain Python dict."""
        return {
            "score_id":              self.szScoreIDString.decode(errors="replace"),
            "ht_prepay_score":       self.dEspHtPrepaymentScore,   # Housing Turnover
            "rf_prepay_score":       self.dEspRfPrepaymentScore,   # Refinancing
            "fc_default_score":      self.dEspFcDefaultScore,      # Foreclosure
            "del_default_score":     self.dEspDelDefaultScore,     # Delinquency
            "draw_score":            self.dEspDrawScore,           # Draw / HELOC
            "ratio_scorable_bal":    self.dEspRatioOfScorableBal,
            "has_additional_scores": self.nHasAdditionalScores,
            "additional_score_1":    self.dEspAdditionalPrepaymentScore1,
        }


class EspMortgageCollatDescStruct(ctypes.Structure):
    """
    Modern collateral description input (used by EspPrep_PrepayModelMThread etc.).
    Corresponds to 'EspMortgageCollatDescStruct' in esp_defs.h.
    Pointer fields for optional sub-structs are left as c_void_p;
    use the DLL's create* helpers to build them when needed.
    """
    _fields_ = [
        ("agencyName",                          ctypes.c_char * ESP_GENERIC_MBS_COLLATERAL_AGENCY_NAME_LENGTH),
        ("origTermInMonth",                     ctypes.c_int),
        ("amortizationPeriodInMonth",           ctypes.c_int),
        ("ageAtSettlementInMonth",              ctypes.c_int),
        ("wamAtSettlementInMonth",              ctypes.c_int),
        ("grossWACPercent",                     ctypes.c_double),
        ("netCouponPercent",                    ctypes.c_double),   # For ARM: net at settle date (vs. origination); FRM prepay speed calc does not consume this field
        # Optional sub-struct pointers – set to NULL if not used
        ("arm_desc_ptr",                        ctypes.c_void_p),   # EspARMDescStruct*
        ("prepay_loan_level_ptr",               ctypes.c_void_p),   # EspPrepayLoanLevelDescStruct*
        ("prepay_fine_tune_ptr",                ctypes.c_void_p),   # EspPrepayFineTuneStruct*
        ("pEspPrepayPenaltyDataStruct",         ctypes.c_void_p),
        ("pEspMortgageGeoData",                 ctypes.c_void_p),
        ("pOptionaInputToCFGenerator",          ctypes.c_void_p),
        ("pEspCMOCollatClusterSpecStruct",      ctypes.c_void_p),
        ("pMBSDollarRollStruct",                ctypes.c_void_p),
        ("pEspLoanAndBorrowerInfoStruct",       ctypes.c_void_p),
        ("pEspCollateralCompositionIDs",        ctypes.c_void_p),
        ("pEspPrepaymentScoreStruct",           ctypes.c_void_p),
        ("pEspDefaultModelInput",               ctypes.c_void_p),   # EspDefaultModelInput*
        ("pv_PoolOfLoansClass",                 ctypes.c_void_p),
        ("pv_EspAdditionalMortgageCollatDescStruct", ctypes.c_void_p),
    ]


class OptionaInputToCFGenerator(ctypes.Structure):
    """
    Optional controls passed via EspMortgageCollatDescStruct.pOptionaInputToCFGenerator.

    Note: nPrepayAndDefaultScoreSwitch is defined but not reliably honoured by
    EspPrep_PrepayModelMThread for standalone loan/aggregate calls — scoring is
    determined by loan type (file presence), not by this flag.  Use
    AFTModel._attach_scores() / EspPrepaymentScoreStruct.nForceUseInputScores
    to supply pre-computed scores directly instead.

    Layout assumes ADD_CMO_USE_HIST_CDU_AND_IGNORE_PREPAY_PENALTY_FLAGS is defined,
    which is true for all builds >= v5.42-BUILT6 (including v6.43).
    ctypes automatically inserts alignment padding between unlike field types.
    """
    _fields_ = [
        # Pointers / doubles require 8-byte alignment; ctypes pads automatically.
        ("szBondPassword",                                    ctypes.c_void_p),   # char*
        ("defaults_vector",                                   ctypes.c_void_p),   # double*
        ("defaults_vector_length",                            ctypes.c_int),
        ("loss_type",                                         ctypes.c_int),
        ("loss_severity_vector",                              ctypes.c_void_p),   # double*
        ("loss_severity_vector_length",                       ctypes.c_int),
        # 4-byte pad auto-inserted before next pointer
        ("interest_loss_severity_vector",                     ctypes.c_void_p),   # double*
        ("interest_loss_severity_vector_length",              ctypes.c_int),
        # 4-byte pad before double
        ("recovery_in_month",                                 ctypes.c_double),
        ("init_defaults_vector",                              ctypes.c_void_p),   # double*
        ("init_defaults_vector_length",                       ctypes.c_int),
        # 4-byte pad before pointer
        ("init_loss_severity_vector",                         ctypes.c_void_p),   # double*
        ("init_loss_severity_vector_length",                  ctypes.c_int),
        ("servicer_advances_option_type",                     ctypes.c_int),
        ("servicer_advances_percent_principal",               ctypes.c_double),
        ("servicer_advances_percent_interest",                ctypes.c_double),
        ("delinquency_option_type",                           ctypes.c_int),
        # 4-byte pad before pointer
        ("delinquency_vector",                                ctypes.c_void_p),   # double*
        ("delinquency_vector_length",                         ctypes.c_int),
        ("deal_load_option_type",                             ctypes.c_int),
        ("cash_flow_generation_type",                         ctypes.c_int),
        ("pseudo_bond_type",                                  ctypes.c_int),
        ("use_vindex_flag",                                   ctypes.c_int),
        # 4-byte pad before double
        ("vindex0",                                           ctypes.c_double),
        ("explode_megas_flag",                                ctypes.c_int),
        ("use_notional_balance_flag",                         ctypes.c_int),
        ("accept_potfolio_level_accuracy_flag",               ctypes.c_int),
        # --- Fields inside ADD_CMO_USE_HIST_CDU... ifdef (v5.42-BUILT6+) ---
        ("n_ignore_hist_cdu_after_settlement_flag",           ctypes.c_int),
        ("n_ignore_prepay_penalty_of_cmo_collateral_flag",    ctypes.c_int),
        ("n_turn_on_cluster_arm_reset_detail_flag",           ctypes.c_int),
        ("n_turn_on_cluster_pct_detail_flag",                 ctypes.c_int),
        ("n_RetrieveDetailedCollatInfoForPrepayScoringFlag",  ctypes.c_int),
        ("n_ForcePaydownAtNextARMReset",                      ctypes.c_int),
        ("n_DoNotSetMiscLoadingOptionsToTypicalFirstFlag",    ctypes.c_int),
        ("n_DoNotPrepricingReremicFlag",                      ctypes.c_int),
        ("nDoesPrepayContainDefault",                         ctypes.c_int),
        ("nActivateEspDefaultModel",                          ctypes.c_int),
        ("nPrepayAndDefaultScoreSwitch",                      ctypes.c_int),  # ← scoring toggle
        ("nCashflowEntitlementFlag",                          ctypes.c_int),
        ("nClusterPrepayPenaltyFlag",                         ctypes.c_int),
        ("nOverrideTriggerFlag",                              ctypes.c_int),
        ("nAcceptCurrency",                                   ctypes.c_int),
        ("iEspNoDealBinningFromScoreFile",                    ctypes.c_int),
        ("iEspIgnoreCredit_FG",                              ctypes.c_int),
        ("iDonotReimburse",                                   ctypes.c_int),
        ("n_OITCFG_Future_Use_Flag_15",                       ctypes.c_int),
        ("n_OITCFG_Future_Use_Flag_16",                       ctypes.c_int),
        ("n_OITCFG_Future_Use_Flag_17",                       ctypes.c_int),
        ("n_OITCFG_Future_Use_Flag_18",                       ctypes.c_int),
        ("n_OITCFG_Future_Use_Flag_19",                       ctypes.c_int),
        ("n_OITCFG_Future_Use_Flag_20",                       ctypes.c_int),
    ]


class EspDefaultModelInput(ctypes.Structure):
    """
    Default model inputs attached via EspMortgageCollatDescStruct.pEspDefaultModelInput.

    Activate v5.43 HPI projection support by setting:
        projHPI           – pointer to a monthly HPI vector
        nProjHPI          – number of elements in the vector
        nProjHPIStartYyyyMm – 0 → projHPI[0] aligned with settleDateYyyyMm
                              non-0 (e.g. mrateDateYyyyMm) → aligned with that date

    All other fields default to 0 (zero-initialised by ctypes).
    """
    _fields_ = [
        ("dOrigLTV",                                 ctypes.c_double),
        ("dCurAdjLTV",                               ctypes.c_double),
        ("nMSACode",                                 ctypes.c_int),
        # 4-byte implicit pad before next double (offset 24)
        ("dCurPrepayExp",                            ctypes.c_double),
        ("dCurrPopulation0",                         ctypes.c_double),
        ("dCurrPopulation30",                        ctypes.c_double),
        ("dCurrPopulation60",                        ctypes.c_double),
        ("dCurrPopulation90",                        ctypes.c_double),
        ("dCurrPopulationFc",                        ctypes.c_double),
        ("nHasCurrDefaultRates",                     ctypes.c_int),
        ("nProjHPIStartYyyyMm",                      ctypes.c_int),      # 0=settle-aligned; non-0=mrate-aligned
        ("projHPI",                                  ctypes.POINTER(ctypes.c_double)),
        ("nProjHPI",                                 ctypes.c_int),
        # 4-byte implicit pad before pointer (offset 96)
        ("pEspDefaultModelDials",                    ctypes.c_void_p),
        ("_LCurrDefaultRateAsofYyyyMm",              ctypes.c_int),
        ("nUseInputPrepaySpeedsForDefaultModelFlag",  ctypes.c_int),
        ("nTotalLTVFromOtherLiensTimes10000",         ctypes.c_int),
        ("nLTVOfSecondLiensTimes10000",               ctypes.c_int),
        # 4-byte implicit pad before double (offset 120)
        ("dCurrPopulationREO",                       ctypes.c_double),
        ("dFractionOfLoansAreSecondLien",            ctypes.c_double),
    ]


class EspMultiFamilyDescStruct(ctypes.Structure):
    """
    Multifamily loan descriptor struct.
    Corresponds to 'EspMultiFamilyDescStruct' in esp_defs.h.

    Key projection fields (v5.43+):
        lMFProjBegYyyyMm  – start YyyyMm of the projections (must be valid)
        nMFProjLength     – number of elements in the projection arrays (must be > 0)
        dArrUnemploymentPct – pointer to monthly unemployment-rate vector (%)
        dArrOccupancyPct    – pointer to monthly occupancy-rate vector (%)
        dArrDSCR            – pointer to monthly DSCR vector
    """
    _fields_ = [
        # 14 doubles (property / financial metrics)
        ("dPropertyVal",            ctypes.c_double),
        ("dOrigDSCR",               ctypes.c_double),
        ("dOccupancyPct",           ctypes.c_double),
        ("dEconOccupancyPct",       ctypes.c_double),
        ("dMonthlyDebtServ",        ctypes.c_double),
        ("dEGI",                    ctypes.c_double),
        ("dTotOperatingExp",        ctypes.c_double),
        ("dReplacementRsrv",        ctypes.c_double),
        ("dNCF",                    ctypes.c_double),
        ("dCurDSCR",                ctypes.c_double),
        ("dCurOccupancyPct",        ctypes.c_double),
        ("dCurEconOccupancyPct",    ctypes.c_double),
        ("dCurBal",                 ctypes.c_double),
        ("dCurNoteRate",            ctypes.c_double),
        # String fields (char arrays; MAX_MF_STRVAL_SHRT=50, MAX_MF_STRVAL_LONG=200)
        ("szDefStatus",                    ctypes.c_char * 50),
        ("szPropertyStatus",               ctypes.c_char * 50),
        ("szPrepaymentProvision",          ctypes.c_char * 200),
        ("szPrepaymentProvisionEndDate",   ctypes.c_char * 200),
        ("nIOTerm",                        ctypes.c_int),
        ("szLoanPurpose",                  ctypes.c_char * 200),
        ("szAmortType",                    ctypes.c_char * 200),
        # Projection control (XYZ20181102)
        ("lMFProjBegYyyyMm",    ctypes.c_long),    # Windows x64: long = 32-bit
        ("nMFProjLength",       ctypes.c_int),
        # Projection arrays (pointers; 8-byte, naturally 8-byte aligned after the two 4-byte fields)
        ("dArrOccupancyPct",    ctypes.c_void_p),
        ("dArrDSCR",            ctypes.c_void_p),
        ("dArrUnemploymentPct", ctypes.c_void_p),
        # Reserved
        ("_emfds_RsvdInt01",    ctypes.c_int),
        ("_emfds_RsvdInt02",    ctypes.c_int),
        ("_emfds_RsvdInt03",    ctypes.c_int),
        ("_emfds_RsvdInt04",    ctypes.c_int),
        ("_emfds_RsvdInt05",    ctypes.c_int),
        ("_emfds_RsvdLong01",   ctypes.c_long),
        ("_emfds_RsvdLong02",   ctypes.c_long),
        ("_emfds_RsvdLong03",   ctypes.c_long),
        ("_emfds_RsvdLong04",   ctypes.c_long),
        ("_emfds_RsvdLong05",   ctypes.c_long),
        ("_emfds_RsvdDouble01", ctypes.c_double),
        ("_emfds_RsvdDouble02", ctypes.c_double),
        ("_emfds_RsvdDouble03", ctypes.c_double),
        ("_emfds_RsvdDouble04", ctypes.c_double),
        ("_emfds_RsvdDouble05", ctypes.c_double),
        ("_emfds_pvRsvdPtr01",  ctypes.c_void_p),
        ("_emfds_pvRsvdPtr02",  ctypes.c_void_p),
        ("_emfds_pvRsvdPtr03",  ctypes.c_void_p),
        ("_emfds_pvRsvdPtr04",  ctypes.c_void_p),
        ("_emfds_pvRsvdPtr05",  ctypes.c_void_p),
    ]


class EspAdditionalMortgageCollatDescStruct(ctypes.Structure):
    """
    Additional collateral descriptor struct (v5.45+).
    Corresponds to 'EspAdditionalMortgageCollatDescStruct' in esp_defs.h,
    compiled without COMPAT_ESP60_TO_551 (i.e. the full v6.x layout; 272 bytes).

    Attach to EspMortgageCollatDescStruct.pv_EspAdditionalMortgageCollatDescStruct.
    Set pv_EspMultiFamilyDescStruct to point to an EspMultiFamilyDescStruct instance
    to pass MF/unemployment projections into the model.
    """
    _fields_ = [
        # Pointer to loan modification structure (unused here, keep NULL)
        ("pMod",                                     ctypes.c_void_p),
        # 5 ints (offsets 8–27)
        ("nInterestOnlyTerm",                        ctypes.c_int),
        ("nMonthsDelinquent",                        ctypes.c_int),
        ("nEspAddReservedInt2",                      ctypes.c_int),
        ("nEspAddReservedInt3",                      ctypes.c_int),
        ("nEspAddReservedInt4",                      ctypes.c_int),
        # 4 longs (offsets 28–43; Windows x64 long = 4 bytes)
        ("lScoreAsOfDateOverride",                   ctypes.c_long),
        ("lPayHistDateYyyyMm",                       ctypes.c_long),
        ("lEspAddReservedLong3",                     ctypes.c_long),
        ("lEspAddReservedLong4",                     ctypes.c_long),
        # implicit 4-byte pad before next double (offset 48)
        # 4 doubles (offsets 48–79)
        ("dIncomeToOrigBalRatio",                    ctypes.c_double),
        ("dPurchaseFraction",                        ctypes.c_double),
        ("nEspAddReservedDouble3",                   ctypes.c_double),
        ("nEspAddReservedDouble4",                   ctypes.c_double),
        # 2 double pointers (offsets 80–95)
        ("scheduledFactorFromOrigination",           ctypes.c_void_p),
        ("paymentToAmortizingRatioFromOrigination",  ctypes.c_void_p),
        # 4 void pointers (offsets 96–127)
        ("pv_sPaymentHistory",                       ctypes.c_void_p),
        ("pEspReadOnlyAttributeSet",                 ctypes.c_void_p),
        ("pv_EspPreCalcFieldsForPrepay",             ctypes.c_void_p),
        ("pv_EspStateDistributionString",            ctypes.c_void_p),
        # Fields inside #ifndef COMPAT_ESP60_TO_551 (present in v6.43)
        # 5 ints (offsets 128–147)
        ("nIsLOC",                                   ctypes.c_int),
        ("nDrawPeriodInMonth",                       ctypes.c_int),
        ("nRepaymentPeriodInMonth",                  ctypes.c_int),
        ("nIsReverse",                               ctypes.c_int),
        ("nEspAddReservedInt9",                      ctypes.c_int),
        # 5 longs (offsets 148–167; Windows x64 long = 4 bytes)
        ("len_projected_10yr_swap",                  ctypes.c_long),
        ("len_projected_10yr_treasury",              ctypes.c_long),
        ("lEspAddReservedLong7",                     ctypes.c_long),
        ("lEspAddReservedLong8",                     ctypes.c_long),
        ("lEspAddReservedLong9",                     ctypes.c_long),
        # 8 doubles (offsets 168–231; 168 is 8-byte aligned)
        ("dMortgageInsurancePct",                    ctypes.c_double),
        ("dUpfrontMIP",                              ctypes.c_double),
        ("dAnnualMIP",                               ctypes.c_double),
        ("dOrigPrincipalLimit",                      ctypes.c_double),
        ("dCurPrincipalLimit",                       ctypes.c_double),
        ("dMaxClaimAmount",                          ctypes.c_double),
        ("dPrincipalLimitGrowth",                    ctypes.c_double),
        ("dSchedPaymentAmt",                         ctypes.c_double),
        # 5 void pointers (offsets 232–271)
        ("pv_projected_10yr_swap",                   ctypes.c_void_p),
        ("pv_projected_10yr_treasury",               ctypes.c_void_p),
        ("pv_EspMultiFamilyDescStruct",              ctypes.c_void_p),
        ("pv_EspAddReservedPtr8",                    ctypes.c_void_p),
        ("pv_EspAddlStrHolder",                      ctypes.c_void_p),
    ]


class EspDefaultModelDials(ctypes.Structure):
    """
    Default model scalar multipliers attached via EspDefaultModelInput.pEspDefaultModelDials.
    Corresponds to 'EspDefaultModelDials' in esp_defs.h.
    All multipliers default to 1.0 (neutral); set to 0 to fully disable a transition.
    """
    _fields_ = [
        # 15 transition / prepay multipliers (offsets 0–119)
        ("dTransitionToPop00Multiplier",  ctypes.c_double),
        ("dTransitionToPop30Multiplier",  ctypes.c_double),
        ("dTransitionToPop60Multiplier",  ctypes.c_double),
        ("dTransitionToPop90Multiplier",  ctypes.c_double),
        ("dTransitionToPopFcMultiplier",  ctypes.c_double),
        ("dTransitionToPopDfMultiplier",  ctypes.c_double),
        ("dPrepayFromCurrMultiplier",     ctypes.c_double),
        ("dPrepayFromPop00Multiplier",    ctypes.c_double),
        ("dPrepayFromPop30Multiplier",    ctypes.c_double),
        ("dPrepayFromPop60Multiplier",    ctypes.c_double),
        ("dPrepayFromPop90Multiplier",    ctypes.c_double),
        ("dPrepayFromPopFcMultiplier",    ctypes.c_double),
        ("dDefaultAgeMultiplier",         ctypes.c_double),
        ("dPaymentIncreaseMultiplier",    ctypes.c_double),
        ("dAdjustedCurrLTVMultiplier",    ctypes.c_double),
        # 1 int (offset 120); implicit 4-byte pad before next double (offset 128)
        ("nApplyDialsToBothFixedOrArmFlag", ctypes.c_int),
        # 6 more multipliers (offsets 128–175)
        ("dDefaultRateMultiplier",        ctypes.c_double),
        ("dLossSeverityMultiplier",       ctypes.c_double),
        ("_EDMD_FutureMultiplier3",       ctypes.c_double),
        ("_EDMD_FutureMultiplier4",       ctypes.c_double),
        ("_EDMD_FutureMultiplier5",       ctypes.c_double),
        ("_EDMD_FutureMultiplier6",       ctypes.c_double),
    ]


class EspARMCFGeneratDescStruct(ctypes.Structure):
    """
    Nested within EspARMDescStruct — CF generation parameters for ARM.
    Per esp_defs.h: for prepayment model functions the indicatives in this
    struct are not used (zero-fill is safe).
    """
    _fields_ = [
        ("netMarginPercent",              ctypes.c_double),
        ("servicingFeePercent",           ctypes.c_double),
        ("grossLifeFloorPercent",         ctypes.c_double),
        ("initialPayResetPeriodInMonth",  ctypes.c_int),
        ("paymentResetPeriodInMonth",     ctypes.c_int),
        ("paymentChangeCapPercent",       ctypes.c_double),
        ("negAmortFlag",                  ctypes.c_int),
        ("negAmortLimitPercent",          ctypes.c_double),
        ("paymentCapWaivePeriodInMonth",  ctypes.c_int),
        ("EspReservedPtr",               ctypes.c_void_p),
    ]


class EspAdditionalARMDescStruct(ctypes.Structure):
    """
    Optional extension to EspARMDescStruct pointed to by pEspAdditionalARMDescStruct.
    Currently only dInitialPeriodicCapLimitPercent is meaningful; all other fields
    are reserved for future use and must be zero.

    Use when the first-reset cap differs from the ongoing periodic cap, which is
    common for 5/1, 7/1 ARMs (e.g. initial cap = 5%, ongoing periodic cap = 2%).
    """
    _fields_ = [
        ("dInitialPeriodicCapLimitPercent", ctypes.c_double),
        ("_reserved_int1",  ctypes.c_int),
        ("_reserved_int2",  ctypes.c_int),
        ("_reserved_int3",  ctypes.c_int),
        ("_reserved_int4",  ctypes.c_int),
        ("_reserved_int5",  ctypes.c_int),
        ("_reserved_int6",  ctypes.c_int),
        ("optArmMinPayment",    ctypes.c_double),   # _EAADS_FutureUseDouble1
        ("_reserved_double2",   ctypes.c_double),
        ("_reserved_double3",   ctypes.c_double),
        ("_reserved_double4",   ctypes.c_double),
        ("_reserved_double5",   ctypes.c_double),
        ("_reserved_double6",   ctypes.c_double),
        ("_reserved_str1",      ctypes.c_void_p),
        ("_reserved_str2",      ctypes.c_void_p),
        ("_reserved_void1",     ctypes.c_void_p),
        ("_reserved_void2",     ctypes.c_void_p),
        ("_reserved_void3",     ctypes.c_void_p),
        ("_reserved_void4",     ctypes.c_void_p),
        ("_reserved_void5",     ctypes.c_void_p),
        ("_reserved_void6",     ctypes.c_void_p),
    ]


class EspARMDescStruct(ctypes.Structure):
    """
    ARM-specific descriptor attached via EspMortgageCollatDescStruct.arm_desc_ptr.
    Corresponds to 'EspARMDescStruct' in esp_defs.h.

    Key fields for model users:
        indexTypeFlag                   – index type (use ESP_ARM_INDEX_* constants)
        grossARMWACAtOriginPercent       – gross ARM WAC at origination (e.g. 6.5)
        projGrossARMWACPercent           – pointer to projected gross ARM WAC path (%)
                                          starting at settle date; length = wam_months
        grossLifeCapPercent             – life cap rate (e.g. 11.5)
        grossMarginPercent              – ARM margin over index (e.g. 2.75)
        teaserPeriodInMonth             – teaser / fixed period length in months
        couponResetPeriodInMonth        – months between subsequent coupon resets
        periodicCapLimitPercent         – ongoing max coupon change per reset (e.g. 2.0)
        pEspAdditionalARMDescStruct     – points to EspAdditionalARMDescStruct when
                                          initial_periodic_cap differs from periodic_cap
    """
    _fields_ = [
        ("indexTypeFlag",                ctypes.c_int),
        ("grossARMWACAtOriginPercent",   ctypes.c_double),
        ("projGrossARMWACPercent",       ctypes.POINTER(ctypes.c_double)),
        ("grossLifeCapPercent",          ctypes.c_double),
        ("grossMarginPercent",           ctypes.c_double),
        ("indexLookbackInDays",          ctypes.c_int),
        ("teaserPeriodInMonth",          ctypes.c_int),
        ("couponResetPeriodInMonth",     ctypes.c_int),
        ("periodicCapLimitPercent",      ctypes.c_double),
        ("convStartMonth",               ctypes.c_int),
        ("convEndMonth",                 ctypes.c_int),
        ("arm_cfg_desc_struct",          EspARMCFGeneratDescStruct),
        ("pEspAdditionalARMDescStruct",  ctypes.c_void_p),
    ]


class EspPrepayLoanLevelDescStruct(ctypes.Structure):
    """
    Loan-level composition ratios attached via EspMortgageCollatDescStruct.prepay_loan_level_ptr.
    Corresponds to 'EspPrepayLoanLevelDescStruct' in esp_defs.h (v6.43, no COMPAT_ESP63_TO_62).
    All ratios are between 0 and 1 except dLoanSize (in $K) and dEquityLtv.

    NOTE: This struct is a *scoring* input — it feeds the DLL's score-file lookup to
    determine prepayment scores (HT/RF).  It does NOT directly drive prepay speed.
    Per esp_defs.h: when pEspPrepaymentScoreStruct is non-NULL the DLL ignores this
    struct entirely.  For LTV effects on prepay/default speed use
    EspDefaultModelInput.dOrigLTV / dCurAdjLTV (via default_dials["orig_ltv"] /
    default_dials["cur_adj_ltv"]).
    """
    _fields_ = [
        ("dLoanSize",           ctypes.c_double),   # In thousand dollars
        ("dEquityLtv",          ctypes.c_double),   # LTV for score-bin lookup (not direct prepay input)
        ("fullDocRatio",        ctypes.c_double),
        ("singleFamilyRatio",   ctypes.c_double),
        ("primaryResidRatio",   ctypes.c_double),
        ("cashOutRatio",        ctypes.c_double),
        # Fields in v6.43 (no COMPAT_ESP63_TO_62)
        ("limitedDocRatio",     ctypes.c_double),
        ("mutlfiFamilyRatio",   ctypes.c_double),
        ("condoRatio",          ctypes.c_double),
        ("secondaryResidRatio", ctypes.c_double),
        ("investorRatio",       ctypes.c_double),
        ("purchaseRatio",       ctypes.c_double),
        ("refinanceRatio",      ctypes.c_double),
    ]


class EspAdvancedPrepayFineTuningStruct(ctypes.Structure):
    """
    Advanced fine-tuning, nested via EspPrepayFineTuneStruct.pEspAdvancedPrepayFineTuningStruct.
    Corresponds to 'EspAdvancedPrepayFineTuningStruct' in esp_defs.h.
    """
    _fields_ = [
        ("dRefiAgeMultiplierChange",  ctypes.c_double),
        # 6 ints (offset 8–31; 32 is 8-byte aligned)
        ("nEspPrepayMtgRateType",     ctypes.c_int),
        ("_EAPFTS_FutureUseInt2",     ctypes.c_int),
        ("_EAPFTS_FutureUseInt3",     ctypes.c_int),
        ("_EAPFTS_FutureUseInt4",     ctypes.c_int),
        ("_EAPFTS_FutureUseInt5",     ctypes.c_int),
        ("_EAPFTS_FutureUseInt6",     ctypes.c_int),
        # 6 doubles (offsets 32–79)
        ("_EAPFTS_FutureUseDouble1",  ctypes.c_double),
        ("_EAPFTS_FutureUseDouble2",  ctypes.c_double),
        ("_EAPFTS_FutureUseDouble3",  ctypes.c_double),
        ("_EAPFTS_FutureUseDouble4",  ctypes.c_double),
        ("_EAPFTS_FutureUseDouble5",  ctypes.c_double),
        ("_EAPFTS_FutureUseDouble6",  ctypes.c_double),
        # 6 void pointers (offsets 80–127)
        ("_EAPFTS_FutureUseVoid1",    ctypes.c_void_p),
        ("_EAPFTS_FutureUseVoid2",    ctypes.c_void_p),
        ("_EAPFTS_FutureUseVoid3",    ctypes.c_void_p),
        ("_EAPFTS_FutureUseVoid4",    ctypes.c_void_p),
        ("_EAPFTS_FutureUseVoid5",    ctypes.c_void_p),
        ("_EAPFTS_FutureUseVoid6",    ctypes.c_void_p),
    ]


class EspPrepayFineTuneStruct(ctypes.Structure):
    """
    Prepayment fine-tuning multipliers and flags attached via
    EspMortgageCollatDescStruct.prepay_fine_tune_ptr.
    Corresponds to 'EspPrepayFineTuneStruct' in esp_defs.h.

    Layout assumes all v6.43 feature flags active:
        ADD_FEATURES_FOR_542_BUILT10, ADD_ALLOWING_FINE_RESOLUTION_FOR_MORTGAGE_RATES_FOR_542_BUILT9,
        ESP_ENABLE_546_TUNES; COMPAT_ESP62_TO_60 and COMPAT_ESP63_TO_62 NOT defined.

    All multipliers default to 1.0 (neutral) and shift/flag fields to 0.
    Output-only fields (curPopDens*, htSmmComponent, refiSmmComponent) are ignored on input.
    """
    _fields_ = [
        # Pointer (output WAC override; leave NULL) – offset 0
        ("projGrossWACPercent",  ctypes.c_void_p),
        # 6 doubles (offsets 8–55)
        ("rfMultiplier",         ctypes.c_double),
        ("htMultiplier",         ctypes.c_double),
        ("ageMultiplier",        ctypes.c_double),
        ("burnMultiplier1",      ctypes.c_double),
        ("burnMultiplier2",      ctypes.c_double),
        ("premiumOriginationAdjFactor", ctypes.c_double),
        # 3 ints (offsets 56–67); implicit 4-byte pad → next double at 72
        ("ageShiftInHousingTurnoverAgingFunction",    ctypes.c_int),
        ("useMultiplicativeOrAdditivePopShiftFlag",   ctypes.c_int),
        ("applyPopShiftFromWhatDateFlag",             ctypes.c_int),
        # 4 doubles (offsets 72–103)
        ("additivePop1ToPop2Shift", ctypes.c_double),
        ("additivePop2ToPop3Shift", ctypes.c_double),
        ("curPop1ToPop2Shift",      ctypes.c_double),
        ("curPop2ToPop3Shift",      ctypes.c_double),
        # Output-only pointers (offsets 104–119; keep NULL)
        ("htSmmComponent",   ctypes.c_void_p),
        ("refiSmmComponent", ctypes.c_void_p),
        # Output-only population densities (offsets 120–143)
        ("curPopDens1",      ctypes.c_double),
        ("curPopDens2",      ctypes.c_double),
        ("curPopDens3",      ctypes.c_double),
        # 5 int flags (offsets 144–163); implicit 4-byte pad → next double at 168
        ("calcHousingSalesStartingFromMRatedateFlag",             ctypes.c_int),
        ("applyPremiumOriginationAdjFactorOnHousingTurnoverFlag", ctypes.c_int),
        ("nCreditScoreTypeMeasuringInitialSpread",                ctypes.c_int),  # ADD_FEATURES_FOR_542_BUILT10
        ("turnOffShortTermMultiplicativeAdjustments",             ctypes.c_int),
        ("applyPrepaymentMultipliersToFixedPeriodOnlyForHybridARM", ctypes.c_int),
        # 5 doubles (offsets 168–207)
        ("elbowShiftForRefiMortgageRateInPercent", ctypes.c_double),   # ElbowShift
        ("publicityMultiplierChange",              ctypes.c_double),
        ("refiLagInMonth",                         ctypes.c_double),
        ("changeOfCreditRelatedPrepaymentMultiplier", ctypes.c_double),
        ("creditScoreMeasuringInitialSpread",      ctypes.c_double),   # ADD_FEATURES_FOR_542_BUILT10
        # 3 void pointers (offsets 208–231)
        ("optionalHousingSalesStructPointer",      ctypes.c_void_p),
        ("pEspHistorySMMsForDynamicAdjustment",    ctypes.c_void_p),
        ("pEspAdvancedPrepayFineTuningStruct",     ctypes.c_void_p),
        # ADD_ALLOWING_FINE_RESOLUTION_FOR_MORTGAGE_RATES_FOR_542_BUILT9 (offsets 232–279)
        ("pAdditionalMR30",        ctypes.c_void_p),
        ("pAdditionalMR15",        ctypes.c_void_p),
        ("_ESP_FT_ReservedVoid1",  ctypes.c_void_p),
        ("_ESP_FT_ReservedVoid2",  ctypes.c_void_p),
        ("_ESP_FT_ReservedVoid3",  ctypes.c_void_p),
        ("_ESP_FT_ReservedVoid4",  ctypes.c_void_p),
        # ESP_ENABLE_546_TUNES: 6 doubles + 1 int (offsets 280–331);
        # implicit 4-byte pad → next double at 336
        ("dHpaRfMultiplier",       ctypes.c_double),
        ("dHpaRfAgeMultiplier",    ctypes.c_double),
        ("dHpaHtMultiplier",       ctypes.c_double),
        ("dHpaHtAgeMultiplier",    ctypes.c_double),
        ("dCurrLtvEffMultiplier",  ctypes.c_double),
        ("dCurrLtvEffHtMultiplier",ctypes.c_double),
        ("iAddlSprdOnOff",         ctypes.c_int),
        # #ifndef COMPAT_ESP62_TO_60: 2 more doubles (offsets 336–351)
        ("dDrawrateMultiplier",    ctypes.c_double),
        ("dLifeventMultiplier",    ctypes.c_double),
        # Reserved pointer (offset 352)
        ("_EspReservedInFineTuning", ctypes.c_void_p),
    ]


class EspLoanAndBorrowerInfoStructAddlDoubleIntTypes(ctypes.Structure):
    """
    Additional integer/double fields for EspLoanAndBorrowerInfoStruct.
    Attached via EspLoanAndBorrowerInfoStruct.pvEspLoanAndBorrowerInfoStructAddlDoubleIntTypes.
    """
    _fields_ = [
        # 10 ints (offsets 0–39)
        ("nBorrowerGender",         ctypes.c_int),   # 0=unknown, 1=female, 2=male
        ("nBorrower2Gender",        ctypes.c_int),
        ("nOrigChannelType",        ctypes.c_int),   # 0=unknown, 1=retail, 2=wholesale
        ("ELABISADIT_addl_int_04",  ctypes.c_int),
        ("ELABISADIT_addl_int_05",  ctypes.c_int),
        ("ELABISADIT_addl_int_06",  ctypes.c_int),
        ("ELABISADIT_addl_int_07",  ctypes.c_int),
        ("ELABISADIT_addl_int_08",  ctypes.c_int),
        ("ELABISADIT_addl_int_09",  ctypes.c_int),
        ("ELABISADIT_addl_int_10",  ctypes.c_int),
        # 10 doubles (offsets 40–119)
        ("dMortInsurancePct",       ctypes.c_double),
        ("dBorrower2Age",           ctypes.c_double),
        ("dRetailFraction",         ctypes.c_double),
        ("dWholesaleFraction",      ctypes.c_double),
        ("dTotalDTI",               ctypes.c_double),
        ("ELABISADIT_addl_double_06", ctypes.c_double),
        ("ELABISADIT_addl_double_07", ctypes.c_double),
        ("ELABISADIT_addl_double_08", ctypes.c_double),
        ("ELABISADIT_addl_double_09", ctypes.c_double),
        ("ELABISADIT_addl_double_10", ctypes.c_double),
    ]


class EspLoanAndBorrowerInfoStruct(ctypes.Structure):
    """
    Loan and borrower characteristics attached via
    EspMortgageCollatDescStruct.pEspLoanAndBorrowerInfoStruct.
    Corresponds to 'EspLoanAndBorrowerInfoStruct' in esp_defs.h.

    Key fields used by the scoring/prepay model:
        dBorrowerFICO, dOrigBal, dOrigLTV, dCurrBal,
        szUSStateAbbrev, szZipCode, szGeoAreaCode,
        szPropertyTypeCode, szOccupancyCode, szLoanPurposeCode, szProductCode

    Attach additional integer/double fields via
    pvEspLoanAndBorrowerInfoStructAddlDoubleIntTypes (see _attach_loan_info).

    String sizes (from esp_defs.h):
        CUSIP_OR_ID = 20, ISSUER_NAME = 200,
        US_ZIP = 20, US_MSA = 20, TYPE_CODE = 40
    """
    _fields_ = [
        # offset 0: long lLoanIDNumber (4 bytes), then char[20] szLoanIDString (20 bytes)
        ("lLoanIDNumber",     ctypes.c_long),                          # 4 bytes at 0
        ("szLoanIDString",    ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),  # 20 bytes at 4
        # offset 24: int nNumberOfLoans (4 bytes), then char[200] szIssuerName (200 bytes)
        ("nNumberOfLoans",    ctypes.c_int),                           # 4 bytes at 24
        ("szIssuerName",      ctypes.c_char * ESP_GENERIC_MBS_ISSUER_NAME_LENGTH),  # 200 bytes at 28
        # offset 228: long lOrigDateYyyyMmDd (4 bytes); 228 % 4 == 0
        ("lOrigDateYyyyMmDd", ctypes.c_long),                          # 4 bytes at 228
        # offset 232: doubles (8-byte aligned)
        ("dOrigBal",          ctypes.c_double),
        ("dCurrBal",          ctypes.c_double),
        ("dOrigLTV",          ctypes.c_double),
        # offset 256: char fields (alignment 1 – packed with no padding between them)
        ("szUSStateAbbrev",        ctypes.c_char * 3),
        ("szZipCode",              ctypes.c_char * 20),    # ESP_DEF_US_ZIP_CODE_STRING_SIZE
        ("szGeoAreaCode",          ctypes.c_char * 20),    # ESP_DEF_US_MSA_CODE_STRING_SIZE
        ("szPropertyTypeCode",     ctypes.c_char * 40),    # ESP_DEF_TYPE_CODE_STRING_SIZE
        ("szOccupancyCode",        ctypes.c_char * 40),
        ("szLoanPurposeCode",      ctypes.c_char * 40),
        ("szLoanCategoryCode",     ctypes.c_char * 40),
        ("szProductCode",          ctypes.c_char * 40),
        ("szDocumentationCode",    ctypes.c_char * 40),
        ("szLoanStatusCode",       ctypes.c_char * 40),
        ("szForeClosureCode",      ctypes.c_char * 40),
        ("szBankruptcyCode",       ctypes.c_char * 40),
        ("szPayScheduleCode",      ctypes.c_char * 40),
        ("szInvestorCode",         ctypes.c_char * 40),
        ("szServicerCode",         ctypes.c_char * 40),
        # After szServicerCode ends at 779; implicit 1-byte pad → nServicerNumber at 780
        ("nServicerNumber",        ctypes.c_int),
        # offset 784: double (8-byte aligned)
        ("dAFTRating",             ctypes.c_double),
        ("szMoodyRating",          ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("szSPRating",             ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("szFitchRating",          ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("szBorrowerName",         ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("szCoBorrowerName",       ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("szBorrowerAddress",      ctypes.c_char * ESP_GENERIC_MBS_ISSUER_NAME_LENGTH),  # 200 chars
        ("szBorrowerPhoneNumber",  ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("szBorrowerEmailAddress", ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        # After szBorrowerEmailAddress ends at 1132; implicit 4-byte pad → double at 1136
        ("dBorrowerFamilySize",    ctypes.c_double),
        ("dBorrowerAge",           ctypes.c_double),
        ("dBorrowerFICO",          ctypes.c_double),
        ("dBorrowerAnnualIncome",  ctypes.c_double),
        # 3 single chars (offsets 1168-1170); implicit 5-byte pad → double at 1176
        ("cBorrowerMaritalStatus",       ctypes.c_char),
        ("cBorrowerEducationCode",       ctypes.c_char),
        ("cBorrowerPoliticalAffiliation",ctypes.c_char),
        # doubles (offsets 1176+)
        ("dFractionOfFirstTimeBuyers",          ctypes.c_double),
        ("dAvgNumberOfRefisBorrowerAlreadyHad", ctypes.c_double),
        ("dBorrowerMortgageCreditLine",         ctypes.c_double),
        # 200-char notes field
        ("szFurtherNotes",   ctypes.c_char * ESP_GENERIC_MBS_ISSUER_NAME_LENGTH),
        # long is_io (4 bytes); after szFurtherNotes ends at 1400; 1400 % 4 == 0
        ("is_io",            ctypes.c_long),
        # void* fields; after is_io ends at 1404; implicit 4-byte pad → void* at 1408
        ("pvEspLoanAndBorrowerInfoStructAddlDoubleIntTypes", ctypes.c_void_p),
        ("_ELABIS_FutureUse2",  ctypes.c_void_p),
        ("_ELABIS_FutureUse3",  ctypes.c_void_p),
        ("_ELABIS_FutureUse4",  ctypes.c_void_p),
        ("_ELABIS_FutureUse5",  ctypes.c_void_p),
        ("_ELABIS_FutureUse6",  ctypes.c_void_p),
        ("_ELABIS_FutureUse7",  ctypes.c_void_p),
        ("_ELABIS_FutureUse8",  ctypes.c_void_p),
        # Self-referential next pointer (linked list; always NULL for single loan)
        ("pNext",            ctypes.c_void_p),
    ]


class EspPrepayProjStruct(ctypes.Structure):
    """
    Projection input / output container.
    Corresponds to 'EspPrepayProjStruct' in esp_defs.h (version 6.4+, no COMPAT flags).
    Fill rate arrays before calling, read projected_smm_percent after.
    """
    _fields_ = [
        ("settleDateYyyyMm",                    ctypes.c_long),
        ("mrateDateYyyyMm",                     ctypes.c_long),
        ("projMortCommitRate30InPercent",        ctypes.POINTER(ctypes.c_double)),
        ("projMortCommitRate15InPercent",        ctypes.POINTER(ctypes.c_double)),
        ("projMortCommitRate07InPercent",        ctypes.POINTER(ctypes.c_double)),
        ("projMortCommitRate05InPercent",        ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate10InPercent",          ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate05InPercent",          ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate03InPercent",          ctypes.POINTER(ctypes.c_double)),
        ("projected_smm_percent",               ctypes.POINTER(ctypes.c_double)),  # OUTPUT
        ("projDefaultVector",                   ctypes.POINTER(EspDefaultRate)),   # OUTPUT
        ("projected_loan_mods",                 ctypes.c_void_p),   # EspLoanModStructExt*
        ("paramsDir",                           ctypes.c_char * ESP_GENERIC_PREPAYMENT_MODEL_PARAMETER_PATH_DIRECTORY_LENGTH),
        # v6.0+ additions
        ("projected_mdp_percent",               ctypes.POINTER(ctypes.c_double)),
        ("projected_life_event_smm",            ctypes.POINTER(ctypes.c_double)),
        ("parrDoubleRsvd",                      ctypes.POINTER(ctypes.c_double)),
        # v6.4+ additions (CMT rates)
        ("projTreasuryRate01InPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate02InPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate07InPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate1mInPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate3mInPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate6mInPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate20InPercent",         ctypes.POINTER(ctypes.c_double)),
        ("projTreasuryRate30InPercent",         ctypes.POINTER(ctypes.c_double)),
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Helper utilities
# ═══════════════════════════════════════════════════════════════════════════

def _to_double_array(values: Optional[List[float]]) -> Tuple[Optional[ctypes.Array], Optional[ctypes.POINTER(ctypes.c_double)]]:
    """Convert a Python list to a ctypes double array + pointer.  Returns (array, ptr)."""
    if values is None:
        return None, None
    arr = (ctypes.c_double * len(values))(*values)
    ptr = ctypes.cast(arr, ctypes.POINTER(ctypes.c_double))
    return arr, ptr


def _read_double_array(ptr: ctypes.POINTER(ctypes.c_double), length: int) -> List[float]:
    """Read 'length' doubles from a ctypes pointer into a Python list."""
    if not ptr:
        return []
    return list(ptr[:length])


# ═══════════════════════════════════════════════════════════════════════════
class EspMBSCalcInputStruct(ctypes.Structure):
    """
    Top-level MBS input container returned by loadEspMBSCalcInputStructFromCmoVendor.
    Corresponds to 'EspMBSCalcInputStruct' in esp_defs.h.

    When cusip is provided to loadEspMBSCalcInputStructFromCmoVendor, AFT queries
    Intex (or another vendor) and fully populates oEspMortgageCollatDescStruct
    (agency name, WAC, WAM, age, ARM flag, etc.) automatically.
    dealName is populated from Intex so it can be passed to
    EspPrep_PrepayAndDefaultModelMThread as szDealName.
    """
    _fields_ = [
        ("cusip",           ctypes.c_char * ESP_GENERIC_MBS_CUSIP_OR_ID_LENGTH),
        ("dealName",        ctypes.c_char * ESP_GENERIC_CMO_DEAL_NAME_LENGTH),
        ("trancheName",     ctypes.c_char * ESP_GENERIC_CMO_TRANCHE_NAME_LENGTH),
        ("cmo_provider_data_directory_path", ctypes.c_char * ESP_GENERIC_CMO_DATA_PATH_DIRECTORY_LENGTH),
        ("ARM_Y_N",         ctypes.c_char),
        # ctypes auto-pads 7 bytes here so oEspMortgageCollatDescStruct is 8-byte aligned
        ("oEspMortgageCollatDescStruct", EspMortgageCollatDescStruct),
        ("doNotUpdateEspMortgageCollatDescStructFlag", ctypes.c_int),
        ("settleDateYyyyMmDd",  ctypes.c_long),   # Windows long = 32-bit
        ("mrateDateYyyyMmDd",   ctypes.c_long),
        ("cashFlowDelayInDays",                       ctypes.c_double),
        ("numberOfDaysOfAccruStartFromMonthBeginning", ctypes.c_double),
        ("spreadMortCommit30ToCMT10", ctypes.c_double),
        ("spreadMortCommit15ToCMT05", ctypes.c_double),
        ("spreadMortCommit07ToCMT03", ctypes.c_double),
        ("spreadMortCommit05ToCMT03", ctypes.c_double),
        ("prinCashFlowMultiplyFactor", ctypes.c_double),
        ("intCashFlowMultiplyFactor",  ctypes.c_double),
        ("paramsDir",       ctypes.c_char * ESP_GENERIC_PREPAYMENT_MODEL_PARAMETER_PATH_DIRECTORY_LENGTH),
        ("pEspCMOCallOptionSpecStruct", ctypes.c_void_p),
        ("pEspSyntheticSecurityStruct", ctypes.c_void_p),
        ("EspReservedPtr3", ctypes.c_void_p),
        ("EspReservedPtr4", ctypes.c_void_p),
    ]


# AFTModel  –  unified wrapper for espmodel.dll + prepayScore.dll
# ═══════════════════════════════════════════════════════════════════════════

class AFTModel:
    """
    Unified wrapper around espmodel.dll and prepayScore.dll.

    Parameters
    ----------
    dll_dir  : str
        Folder containing espmodel.dll and prepayScore.dll.
    data_dir : str
        Folder containing model parameter files and scoring data files.
        Used as both the prepay params path and the score input path in DLL calls.
    intex_dll_dir : str, optional
        Folder containing the Intex DLL (e.g. intex.dll / icmo32.dll).
        Required for Intex-based CMO methods (calc_prepay_from_cusip,
        calc_prepay_and_default_mthread_itx).  AFT loads the Intex DLL
        internally via Windows DLL search — this directory must be on the
        search path before calling those methods.
        Intex data paths (CDI/CDU) are passed separately per call.
    """

    def __init__(self, dll_dir: str, data_dir: str,
                 intex_dll_dir: Optional[str] = None) -> None:
        os.add_dll_directory(dll_dir)
        if intex_dll_dir is not None:
            os.add_dll_directory(intex_dll_dir)
        self._esp   = ctypes.CDLL(os.path.join(dll_dir, "espmodel.dll"))
        self._score = ctypes.CDLL(os.path.join(dll_dir, "prepayScore.dll"))
        self._data_dir: bytes = (
            data_dir.encode() if isinstance(data_dir, str) else data_dir)
        self._data_holders: dict = {}
        self._setup_prototypes()

    # ── Prototype binding ─────────────────────────────────────────────────

    @staticmethod
    def _bind(lib, name: str, restype, argtypes: list) -> None:
        """Bind restype/argtypes on a DLL function, silently skip if not exported."""
        try:
            fn = getattr(lib, name)
            fn.restype  = restype
            fn.argtypes = argtypes
        except AttributeError:
            pass  # function absent in this DLL build; will raise on first call

    def _setup_prototypes(self) -> None:
        esp   = self._esp
        score = self._score
        b = self._bind   # shorthand

        # ── espmodel.dll ──────────────────────────────────────────────────
        b(esp, "EsppVersion",  ctypes.c_char_p, [])
        b(esp, "EspApiVersion", ctypes.c_char_p, [])

        b(esp, "getPrepayExpDate",       ctypes.c_long, [])
        b(esp, "getDefaultModelExpDate", ctypes.c_long, [])
        b(esp, "getScoreExpDate",        ctypes.c_long, [])

        b(esp, "EsPModel", ctypes.c_long, [
            ctypes.POINTER(EsPInputs),
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_char_p,
        ])

        b(esp, "EsPModel_mthread", ctypes.c_long, [
            ctypes.POINTER(EsPInputs),
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ])

        b(esp, "EspPrep_PrepayModelMThread", ctypes.c_long, [
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.POINTER(EspPrepayProjStruct),
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ])

        b(esp, "EspPrep_PrepayAndDefaultModelMThread", ctypes.c_long, [
            ctypes.c_char_p,                             # szDealName
            ctypes.c_int,                                # nGroupNumber
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.POINTER(EspPrepayProjStruct),
            ctypes.c_int,                                # nTotalSamplingPaths
            ctypes.c_int,                                # ithSamplingPath
            ctypes.c_char_p,                             # errorMessage
            ctypes.POINTER(ctypes.c_void_p),             # ppv_DataHolder
        ])

        b(esp, "loadEspMBSCalcInputStructFromCmoVendor",
          ctypes.POINTER(EspMBSCalcInputStruct), [
              ctypes.c_char_p,   # vendor_name (e.g. b"INTEX")
              ctypes.c_char_p,   # cmo_data_dir ("cdi_path cdu_path" for Intex)
              ctypes.c_char_p,   # szId  — CUSIP or "DealName*TrancheName"
              ctypes.c_long,     # lSettleDateYyyyMmDd (YYYYMMDD, Windows long=32-bit)
              ctypes.c_void_p,   # vpMiscOptions (EspCmoLoadMiscOptions* or NULL)
              ctypes.c_char_p,   # szErrorMsgBufferChar200
          ])

        b(esp, "freeEspMBSCalcInputStruct", None, [
            ctypes.POINTER(ctypes.POINTER(EspMBSCalcInputStruct)),
        ])

        b(esp, "EspPrep_PrepayAndDefaultModelMThread_itx", ctypes.c_long, [
            ctypes.c_char_p,                             # szDealName
            ctypes.c_int,                                # nGroupNumber
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.POINTER(EspPrepayProjStruct),
            ctypes.c_int,                                # nTotalSamplingPaths
            ctypes.c_int,                                # ithSamplingPath (-1 = no MC)
            ctypes.c_void_p,                             # pv_ICMO  (Intex ICMO*)
            ctypes.c_void_p,                             # pv_POOL_INFO (Intex POOL_INFO*)
            ctypes.c_char_p,                             # errorMessage
            ctypes.POINTER(ctypes.c_void_p),             # ppv_DataHolder
        ])

        b(esp, "freeVoidTypeCastedDataHolderForEspmodelImpl", None, [
            ctypes.POINTER(ctypes.c_void_p),
        ])

        b(esp, "cprToSmm", ctypes.c_double, [ctypes.c_double])
        b(esp, "smmToCpr", ctypes.c_double, [ctypes.c_double])
        b(esp, "psaToCpr", ctypes.c_double, [ctypes.c_double, ctypes.c_int])
        b(esp, "cprToPsa", ctypes.c_double, [ctypes.c_double, ctypes.c_int])

        b(esp, "EspPrep_PrepayModelMThread_GetPrepaymentScores",
          ctypes.POINTER(EspPrepaymentScoreStruct), [
              ctypes.POINTER(EspMortgageCollatDescStruct),
              ctypes.POINTER(EspPrepayProjStruct),
              ctypes.c_char_p,
              ctypes.POINTER(ctypes.c_void_p),
          ])

        b(esp, "EsPModel_mthread_GetPrepaymentScores",
          ctypes.POINTER(EspPrepaymentScoreStruct), [
              ctypes.POINTER(EsPInputs),
              ctypes.c_char_p,
              ctypes.POINTER(ctypes.c_void_p),
          ])

        b(esp, "freeEspPrepaymentScoreStruct", None, [
            ctypes.POINTER(ctypes.POINTER(EspPrepaymentScoreStruct)),
        ])

        # ── prepayScore.dll ───────────────────────────────────────────────
        b(score, "EspCalcLoanScore_Q", ctypes.c_int, [
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.c_char_p,    # szPrepayModelPath
            ctypes.c_char_p,    # szScoreInputPath
            ctypes.c_char_p,    # szFailedMessage
        ])

        b(score, "EspCalcLoanScore_QX", ctypes.c_int, [
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.POINTER(EspPrepayProjStruct),
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ])

        b(score, "EspCalcLoanScore_G", ctypes.c_int, [
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ])

        b(score, "EspCalcLoanScore_v6", ctypes.c_int, [
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.POINTER(EspPrepayProjStruct),
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ])

        b(score, "EspCalcLoanScoreWithParams_G", ctypes.c_int, [
            ctypes.POINTER(EspMortgageCollatDescStruct),
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ])

        b(score, "lookupLoanSizeFromYear", ctypes.c_int,
          [ctypes.c_char_p, ctypes.c_int])

        b(score, "lookupMSAFromZipOrStateUsingParamsDir_G", ctypes.c_int, [
            ctypes.c_char_p,  # szMSACode (output buffer)
            ctypes.c_char_p,  # szPrepayModelPath
            ctypes.c_char_p,  # szZipCode
            ctypes.c_char_p,  # szUSStateAbbrev
        ])

        b(esp, "isNonTrivialOfEspPrepaymentScoreStruct", ctypes.c_int, [
            ctypes.POINTER(EspPrepaymentScoreStruct),
        ])

        b(esp, "EspPrep_initEspPrepayLoanLevelDescStructToDefault", None, [
            ctypes.POINTER(EspPrepayLoanLevelDescStruct),
        ])

    # ── Internal helpers ──────────────────────────────────────────────────

    def _holder(self, thread_key: str):
        """Return a byref to the per-thread void* data holder (creating if needed)."""
        if thread_key not in self._data_holders:
            self._data_holders[thread_key] = ctypes.c_void_p(None)
        return ctypes.byref(self._data_holders[thread_key])

    @staticmethod
    def _make_desc(
        agency_name: bytes,
        orig_term_months: int,
        amort_period_months: int,
        age_months: int,
        wam_months: int,
        gross_wac_pct: float,
        net_coupon_pct: float,
    ) -> EspMortgageCollatDescStruct:
        """Build and return a zero-initialised EspMortgageCollatDescStruct."""
        desc = EspMortgageCollatDescStruct()
        desc.agencyName                = agency_name
        desc.origTermInMonth           = orig_term_months
        desc.amortizationPeriodInMonth = amort_period_months
        desc.ageAtSettlementInMonth    = age_months
        desc.wamAtSettlementInMonth    = wam_months
        desc.grossWACPercent           = gross_wac_pct
        desc.netCouponPercent          = net_coupon_pct
        return desc

    @staticmethod
    def _attach_hpi(
        desc: EspMortgageCollatDescStruct,
        proj_hpi: Optional[List[float]],
        hpi_start_yyyymm: int = 0,
    ):
        """
        Build an EspDefaultModelInput carrying a projHPI vector and attach it to *desc*.

        Parameters
        ----------
        proj_hpi : list of float, or None
            Monthly HPI values.  If None this is a no-op and (None, None) is returned.
        hpi_start_yyyymm : int
            0  → projHPI[0] is aligned with settleDateYyyyMm (the valuation date).
            non-0 (typically mrateDateYyyyMm) → projHPI[0] aligned with that date,
            so the vector starts one mrate-date period before the settle date.

        Returns
        -------
        (dmi, hpi_arr) – keepalive objects the caller must hold until after the DLL call.
        """
        if proj_hpi is None:
            return None, None
        hpi_arr = (ctypes.c_double * len(proj_hpi))(*proj_hpi)
        dmi = EspDefaultModelInput()
        dmi.nProjHPIStartYyyyMm = hpi_start_yyyymm
        dmi.projHPI = ctypes.cast(hpi_arr, ctypes.POINTER(ctypes.c_double))
        dmi.nProjHPI = len(proj_hpi)
        desc.pEspDefaultModelInput = ctypes.cast(ctypes.pointer(dmi), ctypes.c_void_p)
        return dmi, hpi_arr

    @staticmethod
    def _attach_scores(
        desc: EspMortgageCollatDescStruct,
        input_scores: Optional[dict],
        on_the_fly_scoring: bool = False,
    ) -> Optional["EspPrepaymentScoreStruct"]:
        """
        Attach EspPrepaymentScoreStruct based on scoring mode.

        input_scores=None, on_the_fly_scoring=False (default):
            pEspPrepaymentScoreStruct stays NULL.  DLL performs file-based
            score lookup via pool/collateral composition IDs.
            score_switch (nPrepayAndDefaultScoreSwitch) is honoured here.

        input_scores=None, on_the_fly_scoring=True:
            Struct allocated with nForceUseInputScores=0.  DLL computes scores
            from loan_info / loan_level attributes and writes results back into
            the struct.  score_switch is still honoured here.

        input_scores=dict:
            Struct allocated with nForceUseInputScores=1.  DLL applies the
            supplied scores directly, bypassing file lookup and ignoring
            loan_info / loan_level.
        """
        if input_scores is None and not on_the_fly_scoring:
            return None
        sc = EspPrepaymentScoreStruct()
        if input_scores is not None:
            sc.nForceUseInputScores   = 1
            sc.dEspHtPrepaymentScore  = input_scores.get("ht_prepay_score",   0.0)
            sc.dEspRfPrepaymentScore  = input_scores.get("rf_prepay_score",   0.0)
            sc.dEspFcDefaultScore     = input_scores.get("fc_default_score",  0.0)
            sc.dEspDelDefaultScore    = input_scores.get("del_default_score", 0.0)
            sc.dEspDrawScore          = input_scores.get("draw_score",        0.0)
            sc.dEspRatioOfScorableBal = input_scores.get("ratio_scorable_bal", 1.0)
        desc.pEspPrepaymentScoreStruct = ctypes.cast(
            ctypes.pointer(sc), ctypes.c_void_p)
        return sc

    @staticmethod
    def _attach_unemployment(
        desc,
        proj_unemp: Optional[List[float]],
        unemp_start_yyyymm: int = 0,
    ):
        """
        Build an EspMultiFamilyDescStruct carrying an unemployment projection vector,
        wrap it in an EspAdditionalMortgageCollatDescStruct, and attach the latter
        to *desc* via pv_EspAdditionalMortgageCollatDescStruct.

        Parameters
        ----------
        proj_unemp : list of float, or None
            Monthly unemployment-rate values (percent, e.g. 5.0 for 5%).
            If None this is a no-op and (None, None, None) is returned.
        unemp_start_yyyymm : int
            The YyyyMm that proj_unemp[0] corresponds to.
            Must be a valid non-zero YyyyMm for the DLL to apply the projection.

        Returns
        -------
        (mf, addl, unemp_arr) – keepalive objects the caller must hold until
        after the DLL call returns.  All three are None when proj_unemp is None.
        """
        if proj_unemp is None:
            return None, None, None
        unemp_arr = (ctypes.c_double * len(proj_unemp))(*proj_unemp)
        mf = EspMultiFamilyDescStruct()
        mf.lMFProjBegYyyyMm    = unemp_start_yyyymm
        mf.nMFProjLength        = len(proj_unemp)
        mf.dArrUnemploymentPct  = ctypes.cast(unemp_arr, ctypes.c_void_p)
        addl = EspAdditionalMortgageCollatDescStruct()
        addl.pv_EspMultiFamilyDescStruct = ctypes.cast(
            ctypes.pointer(mf), ctypes.c_void_p)
        desc.pv_EspAdditionalMortgageCollatDescStruct = ctypes.cast(
            ctypes.pointer(addl), ctypes.c_void_p)
        return mf, addl, unemp_arr

    @staticmethod
    def _attach_default_model_input(
        desc,
        proj_hpi: Optional[List[float]],
        hpi_start_yyyymm: int,
        default_dials: Optional[dict],
    ):
        """
        Build an EspDefaultModelInput (with optional HPI vector and/or default model dials)
        and attach it to *desc*.  Replaces _attach_hpi when dials are also needed.

        Returns (dmi, hpi_arr, dials) keepalives; all None when both inputs are None.
        """
        if proj_hpi is None and default_dials is None:
            return None, None, None
        dmi     = EspDefaultModelInput()
        hpi_arr = None
        if proj_hpi is not None:
            hpi_arr = (ctypes.c_double * len(proj_hpi))(*proj_hpi)
            dmi.nProjHPIStartYyyyMm = hpi_start_yyyymm
            dmi.projHPI = ctypes.cast(hpi_arr, ctypes.POINTER(ctypes.c_double))
            dmi.nProjHPI = len(proj_hpi)
        if default_dials is not None:
            dmi.dOrigLTV   = default_dials.get("orig_ltv",    0.0)
            dmi.dCurAdjLTV = default_dials.get("cur_adj_ltv", 0.0)
        dials = None
        if default_dials is not None:
            dials = EspDefaultModelDials()
            g = default_dials.get
            dials.dTransitionToPop00Multiplier  = g("transition_to_pop00", 1.0)
            dials.dTransitionToPop30Multiplier  = g("transition_to_pop30", 1.0)
            dials.dTransitionToPop60Multiplier  = g("transition_to_pop60", 1.0)
            dials.dTransitionToPop90Multiplier  = g("transition_to_pop90", 1.0)
            dials.dTransitionToPopFcMultiplier   = g("transition_to_pop_fc", 1.0)
            dials.dTransitionToPopDfMultiplier   = g("transition_to_pop_df", 1.0)
            dials.dPrepayFromCurrMultiplier      = g("prepay_from_curr", 1.0)
            dials.dPrepayFromPop00Multiplier     = g("prepay_from_pop00", 1.0)
            dials.dPrepayFromPop30Multiplier     = g("prepay_from_pop30", 1.0)
            dials.dPrepayFromPop60Multiplier     = g("prepay_from_pop60", 1.0)
            dials.dPrepayFromPop90Multiplier     = g("prepay_from_pop90", 1.0)
            dials.dPrepayFromPopFcMultiplier     = g("prepay_from_pop_fc", 1.0)
            dials.dDefaultAgeMultiplier          = g("default_age_mult",  1.0)
            dials.dPaymentIncreaseMultiplier     = g("payment_increase_mult", 1.0)
            dials.dAdjustedCurrLTVMultiplier     = g("adjusted_curr_ltv_mult", 1.0)
            dials.nApplyDialsToBothFixedOrArmFlag = g("apply_dials_flag", 0)
            dials.dDefaultRateMultiplier         = g("default_rate_mult", 1.0)
            dials.dLossSeverityMultiplier        = g("loss_severity_mult", 1.0)
            dmi.pEspDefaultModelDials = ctypes.cast(
                ctypes.pointer(dials), ctypes.c_void_p)
        desc.pEspDefaultModelInput = ctypes.cast(ctypes.pointer(dmi), ctypes.c_void_p)
        return dmi, hpi_arr, dials

    def _attach_loan_level(
        self,
        desc,
        loan_level: Optional[dict],
    ):
        """
        Build an EspPrepayLoanLevelDescStruct from a dict and attach it to
        *desc* via prepay_loan_level_ptr.

        This struct feeds the DLL's score-file lookup (scoring inputs).  It does NOT
        directly affect prepay speed.  For LTV effects on SMM use
        default_dials["orig_ltv"] / default_dials["cur_adj_ltv"] instead.

        Keys (all optional, default 0.0):
            ``loan_size_k``       – loan size in $K (scoring input)
            ``ltv``               – LTV for score-bin selection only (not direct SMM input)
            ``full_doc_ratio``    – full documentation fraction (0–1)
            ``single_family``     – single-family fraction (0–1)
            ``primary_resid``     – primary residence fraction (0–1)
            ``cash_out``          – cash-out refi fraction (0–1)
            ``limited_doc``       – limited documentation fraction (0–1)
            ``multi_family``      – multifamily fraction (0–1)
            ``condo``             – condo fraction (0–1)
            ``secondary_resid``   – secondary residence fraction (0–1)
            ``investor``          – investor fraction (0–1)
            ``purchase``          – purchase fraction (0–1)
            ``refinance``         – refinance fraction (0–1)

        Returns EspPrepayLoanLevelDescStruct or None (keepalive).
        """
        if loan_level is None:
            return None
        ll = EspPrepayLoanLevelDescStruct()
        # initialise to DLL defaults so has-loan-level-data-flag is recognised as 1
        self._esp.EspPrep_initEspPrepayLoanLevelDescStructToDefault(ctypes.byref(ll))
        g = loan_level.get
        ll.dLoanSize          = g("loan_size_k",    0.0)
        ll.dEquityLtv         = g("ltv",            0.0)
        ll.fullDocRatio       = g("full_doc_ratio", 0.0)
        ll.singleFamilyRatio  = g("single_family",  0.0)
        ll.primaryResidRatio  = g("primary_resid",  0.0)
        ll.cashOutRatio       = g("cash_out",       0.0)
        ll.limitedDocRatio    = g("limited_doc",    0.0)
        ll.mutlfiFamilyRatio  = g("multi_family",   0.0)
        ll.condoRatio         = g("condo",          0.0)
        ll.secondaryResidRatio= g("secondary_resid",0.0)
        ll.investorRatio      = g("investor",       0.0)
        ll.purchaseRatio      = g("purchase",       0.0)
        ll.refinanceRatio     = g("refinance",      0.0)
        desc.prepay_loan_level_ptr = ctypes.cast(
            ctypes.pointer(ll), ctypes.c_void_p)
        return ll

    @staticmethod
    def _attach_fine_tune(
        desc,
        fine_tune: Optional[dict],
    ):
        """
        Build an EspPrepayFineTuneStruct (with nested EspAdvancedPrepayFineTuningStruct)
        from a dict and attach it to *desc* via prepay_fine_tune_ptr.

        All multiplier keys default to 1.0; flag/shift keys default to 0.
        Common keys:
            ``ht_mult``            – Housing Turnover multiplier (htMultiplier)
            ``rf_mult``            – Refinancing multiplier (rfMultiplier)
            ``age_mult``           – Age multiplier (ageMultiplier)
            ``burn_mult1``         – Burnout multiplier 1
            ``burn_mult2``         – Burnout multiplier 2
            ``elbow_shift``        – ElbowShift (elbowShiftForRefiMortgageRateInPercent)
            ``premium_orig_adj``   – premiumOriginationAdjFactor
            ``hpa_rf_mult``        – dHpaRfMultiplier
            ``hpa_rf_age_mult``    – dHpaRfAgeMultiplier
            ``hpa_ht_mult``        – dHpaHtMultiplier
            ``hpa_ht_age_mult``    – dHpaHtAgeMultiplier
            ``curr_ltv_eff_mult``  – dCurrLtvEffMultiplier
            ``curr_ltv_eff_ht_mult``– dCurrLtvEffHtMultiplier
            ``addl_sprd_on_off``   – iAddlSprdOnOff (-1 to disable)
            ``drawrate_mult``      – dDrawrateMultiplier
            ``lifeevent_mult``     – dLifeventMultiplier
            ``cur_pop1_to_pop2``   – curPop1ToPop2Shift
            ``cur_pop2_to_pop3``   – curPop2ToPop3Shift
            ``refi_age_mult_change``– advanced: dRefiAgeMultiplierChange
            ``mtg_rate_type``       – advanced: nEspPrepayMtgRateType

        Returns (ft, adv) keepalives.
        """
        # Always create the struct so prepay_fine_tune_ptr is non-NULL.
        #
        # Multipliers (rfMultiplier, htMultiplier, etc.) must be 1.0 by default —
        # NOT 0.0.  The DLL interprets 0.0 literally (zero-out that component).
        # 1.0 = neutral / "use model's own calibrated coefficients."
        # Header confirmation: premiumOriginationAdjFactor "default value is 1,
        # when 0 the effect is completely turned off"; curPop1ToPop2Shift /
        # curPop2ToPop3Shift "default value is changed to 1."
        #
        # Additive shifts (elbowShift, refiLag, pop shifts) and flags stay 0.
        # Caller overrides only what they explicitly supply in fine_tune dict.
        ft = EspPrepayFineTuneStruct()
        g = (fine_tune or {}).get

        # --- Multiplicative fields: default 1.0 ---
        ft.rfMultiplier              = g("rf_mult",            1.0)
        ft.htMultiplier              = g("ht_mult",            1.0)
        ft.ageMultiplier             = g("age_mult",           1.0)
        ft.burnMultiplier1           = g("burn_mult1",         1.0)
        ft.burnMultiplier2           = g("burn_mult2",         1.0)
        ft.premiumOriginationAdjFactor = g("premium_orig_adj", 1.0)
        ft.curPop1ToPop2Shift        = g("cur_pop1_to_pop2",   1.0)
        ft.curPop2ToPop3Shift        = g("cur_pop2_to_pop3",   1.0)
        ft.dHpaRfMultiplier          = g("hpa_rf_mult",        1.0)
        ft.dHpaRfAgeMultiplier       = g("hpa_rf_age_mult",    1.0)
        ft.dHpaHtMultiplier          = g("hpa_ht_mult",        1.0)
        ft.dHpaHtAgeMultiplier       = g("hpa_ht_age_mult",    1.0)
        ft.dCurrLtvEffMultiplier     = g("curr_ltv_eff_mult",  1.0)
        ft.dCurrLtvEffHtMultiplier   = g("curr_ltv_eff_ht_mult", 1.0)
        ft.dDrawrateMultiplier       = g("drawrate_mult",      1.0)
        ft.dLifeventMultiplier       = g("lifeevent_mult",     1.0)

        # --- Additive shifts and flags: default 0 ---
        ft.ageShiftInHousingTurnoverAgingFunction    = g("age_shift",             0)
        ft.useMultiplicativeOrAdditivePopShiftFlag   = g("use_additive_pop_shift",0)
        ft.applyPopShiftFromWhatDateFlag             = g("apply_pop_shift_from",  0)
        ft.additivePop1ToPop2Shift                   = g("additive_pop1_to_pop2", 0.0)
        ft.additivePop2ToPop3Shift                   = g("additive_pop2_to_pop3", 0.0)
        ft.calcHousingSalesStartingFromMRatedateFlag             = g("calc_housing_from_mrate", 0)
        ft.applyPremiumOriginationAdjFactorOnHousingTurnoverFlag = g("premium_on_ht",           0)
        ft.nCreditScoreTypeMeasuringInitialSpread                = g("credit_score_type",       0)
        ft.turnOffShortTermMultiplicativeAdjustments             = g("turn_off_short_term",     0)
        ft.applyPrepaymentMultipliersToFixedPeriodOnlyForHybridARM = g("mult_fixed_period_only", 0)
        ft.elbowShiftForRefiMortgageRateInPercent    = g("elbow_shift",               0.0)
        ft.publicityMultiplierChange                 = g("publicity_mult",            0.0)
        ft.refiLagInMonth                            = g("refi_lag",                  0.0)
        ft.changeOfCreditRelatedPrepaymentMultiplier = g("credit_prepay_mult_change", 0.0)
        ft.creditScoreMeasuringInitialSpread         = g("credit_score",              0.0)
        ft.iAddlSprdOnOff                            = g("addl_sprd_on_off",          0)

        adv = EspAdvancedPrepayFineTuningStruct()
        adv.dRefiAgeMultiplierChange = g("refi_age_mult_change", 0.0)
        adv.nEspPrepayMtgRateType    = g("mtg_rate_type",        0)
        ft.pEspAdvancedPrepayFineTuningStruct = ctypes.cast(
            ctypes.pointer(adv), ctypes.c_void_p)
        desc.prepay_fine_tune_ptr = ctypes.cast(
            ctypes.pointer(ft), ctypes.c_void_p)
        return ft, adv

    @staticmethod
    def _attach_loan_info(
        desc,
        loan_info: Optional[dict],
    ):
        """
        Build an EspLoanAndBorrowerInfoStruct (with nested addl types struct) from a dict
        and attach it to *desc* via pEspLoanAndBorrowerInfoStruct.

        Common keys:
            ``orig_bal``         – dOrigBal (original balance in $)
            ``curr_bal``         – dCurrBal
            ``orig_ltv``         – dOrigLTV (decimal, e.g. 0.8)
            ``fico``             – dBorrowerFICO (400–800)
            ``borrower_age``     – dBorrowerAge
            ``state``            – szUSStateAbbrev (2-char, e.g. "CA")
            ``zip_code``         – szZipCode
            ``geo_area_code``    – szGeoAreaCode (MSA code)
            ``property_type``    – szPropertyTypeCode
            ``occupancy``        – szOccupancyCode
            ``loan_purpose``     – szLoanPurposeCode
            ``product_code``     – szProductCode
            ``doc_code``         – szDocumentationCode
            ``investor_code``    – szInvestorCode
            ``loan_id``          – lLoanIDNumber (int)
            ``orig_date``        – lOrigDateYyyyMmDd (int)
            ``borrower_gender``  – nBorrowerGender (addl: 0=unknown,1=female,2=male)
            ``borrower2_age``    – dBorrower2Age (addl)
            ``borrower2_gender`` – nBorrower2Gender (addl)
            ``channel_type``     – nOrigChannelType (addl: 0=unknown,1=retail,2=wholesale)
            ``retail_fraction``  – dRetailFraction (addl)
            ``wholesale_fraction``– dWholesaleFraction (addl)
            ``total_dti``        – dTotalDTI (addl)

        Returns (elabis, elabis_adit) keepalives, or (None, None) when loan_info is None.
        """
        if loan_info is None:
            return None, None
        li = EspLoanAndBorrowerInfoStruct()
        g = loan_info.get

        def _s(key: str, max_len: int) -> bytes:
            v = g(key, "")
            return (v.encode() if isinstance(v, str) else v)[:max_len]

        li.lLoanIDNumber     = g("loan_id",    0)
        li.lOrigDateYyyyMmDd = g("orig_date",  0)
        li.dOrigBal          = g("orig_bal",   0.0)
        li.dCurrBal          = g("curr_bal",   0.0)
        li.dOrigLTV          = g("orig_ltv",   0.0)
        li.dBorrowerFICO     = g("fico",       0.0)
        li.dBorrowerAge      = g("borrower_age", 0.0)
        li.szLoanIDString        = _s("loan_id_str",    20)
        li.szUSStateAbbrev       = _s("state",           3)
        li.szZipCode             = _s("zip_code",       20)
        li.szGeoAreaCode         = _s("geo_area_code",  20)
        li.szPropertyTypeCode    = _s("property_type",  40)
        li.szOccupancyCode       = _s("occupancy",      40)
        li.szLoanPurposeCode     = _s("loan_purpose",   40)
        li.szProductCode         = _s("product_code",   40)
        li.szDocumentationCode   = _s("doc_code",       40)
        li.szInvestorCode        = _s("investor_code",  40)
        # Additional integer/double fields
        adit = EspLoanAndBorrowerInfoStructAddlDoubleIntTypes()
        adit.nBorrowerGender  = g("borrower_gender",   0)
        adit.nBorrower2Gender = g("borrower2_gender",  0)
        adit.nOrigChannelType = g("channel_type",      0)
        adit.dBorrower2Age    = g("borrower2_age",     0.0)
        adit.dRetailFraction  = g("retail_fraction",   0.0)
        adit.dWholesaleFraction = g("wholesale_fraction", 0.0)
        adit.dTotalDTI        = g("total_dti",         0.0)
        li.pvEspLoanAndBorrowerInfoStructAddlDoubleIntTypes = ctypes.cast(
            ctypes.pointer(adit), ctypes.c_void_p)
        desc.pEspLoanAndBorrowerInfoStruct = ctypes.cast(
            ctypes.pointer(li), ctypes.c_void_p)
        return li, adit

    @staticmethod
    def _attach_arm_desc(
        desc,
        arm_desc: Optional[dict],
        wam_months: int,
    ):
        """
        Build an EspARMDescStruct from a dict and attach it to *desc* via arm_desc_ptr.

        Use for ARM / hybrid-ARM loans.  For FRM pools leave arm_desc=None.

        Common keys:
            ``index_type``           – indexTypeFlag (ESP_ARM_INDEX_* int; default 0)
            ``gross_wac_at_origin``  – grossARMWACAtOriginPercent (e.g. 6.5)
            ``proj_gross_wac``       – list of projected gross ARM WAC values (%) starting
                                       at settle date; length >= wam_months
            ``life_cap``             – grossLifeCapPercent (e.g. 11.5)
            ``margin``               – grossMarginPercent (e.g. 2.75)
            ``teaser_months``        – teaserPeriodInMonth
            ``reset_period_months``  – couponResetPeriodInMonth
            ``periodic_cap``         – periodicCapLimitPercent, ongoing cap per reset (e.g. 2.0)
            ``initial_periodic_cap`` – first-reset cap (e.g. 5.0 for a 5/2/5 ARM); when
                                       provided, attaches EspAdditionalARMDescStruct with
                                       dInitialPeriodicCapLimitPercent set accordingly.
                                       Omit (or set equal to periodic_cap) for simple ARMs.
            ``lookback_days``        – indexLookbackInDays (default 45)

        Returns (arm_s, addl_arm_s, proj_arr) keepalives, or (None, None, None) when arm_desc is None.
        """
        if arm_desc is None:
            return None, None, None
        g = arm_desc.get
        arm_s = EspARMDescStruct()
        arm_s.indexTypeFlag              = g("index_type",          0)
        arm_s.grossARMWACAtOriginPercent = g("gross_wac_at_origin", 0.0)
        arm_s.grossLifeCapPercent        = g("life_cap",            0.0)
        arm_s.grossMarginPercent         = g("margin",              0.0)
        arm_s.indexLookbackInDays        = g("lookback_days",       45)
        arm_s.teaserPeriodInMonth        = g("teaser_months",       0)
        arm_s.couponResetPeriodInMonth   = g("reset_period_months", 0)
        arm_s.periodicCapLimitPercent    = g("periodic_cap",        0.0)

        proj_wac = g("proj_gross_wac", None)
        proj_arr, proj_ptr = _to_double_array(proj_wac)
        if proj_ptr:
            arm_s.projGrossARMWACPercent = proj_ptr

        # Attach EspAdditionalARMDescStruct only when initial_periodic_cap is provided.
        addl_arm_s = None
        initial_cap = g("initial_periodic_cap", None)
        if initial_cap is not None:
            addl_arm_s = EspAdditionalARMDescStruct()
            addl_arm_s.dInitialPeriodicCapLimitPercent = initial_cap
            arm_s.pEspAdditionalARMDescStruct = ctypes.cast(
                ctypes.pointer(addl_arm_s), ctypes.c_void_p)

        desc.arm_desc_ptr = ctypes.cast(ctypes.pointer(arm_s), ctypes.c_void_p)
        return arm_s, addl_arm_s, proj_arr

    # ── Info / conversion utilities ───────────────────────────────────────

    def version(self) -> str:
        """Return the DLL version string."""
        return self._esp.EsppVersion().decode()

    def api_version(self) -> str:
        return self._esp.EspApiVersion().decode()

    def prepay_expiration(self) -> int:
        """Return the prepay model license expiration date as YyyyMmDd."""
        return self._esp.getPrepayExpDate()

    def cpr_to_smm(self, cpr: float) -> float:
        return self._esp.cprToSmm(cpr)

    def smm_to_cpr(self, smm: float) -> float:
        return self._esp.smmToCpr(smm)

    def psa_to_cpr(self, psa: float, age: int) -> float:
        return self._esp.psaToCpr(psa, age)

    def cpr_to_psa(self, cpr: float, age: int) -> float:
        return self._esp.cprToPsa(cpr, age)

    # ── Prepayment speed (legacy single-thread) ───────────────────────────

    def calc_prepay_speed(
        self,
        agency_type: int,
        agency_name: bytes,
        orig_term: int,
        gross_wac: float,
        age: float,
        settle_date: int,
        mrate_date: int,
        mtg_rate_30yr: Optional[List[float]] = None,
        mtg_rate_15yr: Optional[List[float]] = None,
        mtg_rate_7yr:  Optional[List[float]] = None,
        mtg_rate_5yr:  Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        tnote_5yr:     Optional[List[float]] = None,
        tnote_3yr:     Optional[List[float]] = None,
    ) -> List[float]:
        """Legacy single-thread prepay speed via EsPModel()."""
        rem_term = orig_term - int(age)

        arr30, ptr30 = _to_double_array(mtg_rate_30yr)
        arr15, ptr15 = _to_double_array(mtg_rate_15yr)
        arr7,  ptr7  = _to_double_array(mtg_rate_7yr)
        arr5,  ptr5  = _to_double_array(mtg_rate_5yr)
        arr10, ptr10 = _to_double_array(tnote_10yr)
        arr5t, ptr5t = _to_double_array(tnote_5yr)
        arr3,  ptr3  = _to_double_array(tnote_3yr)

        inp = EsPInputs()
        inp.agency_type        = agency_type
        inp.agency_string_name = agency_name
        inp.origTerm           = orig_term
        inp.grossWAC           = gross_wac
        inp.age                = age
        inp.settleDate         = settle_date
        inp.mrateDate          = mrate_date
        inp.paramsDir          = self._data_dir
        if ptr30: inp.mtgRate30yr = ptr30
        if ptr15: inp.mtgRate15yr = ptr15
        if ptr7:  inp.mtgRate7yr  = ptr7
        if ptr5:  inp.mtgRate5yr  = ptr5
        if ptr10: inp.tnoteRate10 = ptr10
        if ptr5t: inp.tnoteRate5  = ptr5t
        if ptr3:  inp.tnoteRate3  = ptr3

        smm_buf = (ctypes.c_double * rem_term)()
        err_buf = ctypes.create_string_buffer(200)
        rc = self._esp.EsPModel(
            ctypes.byref(inp),
            ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double)),
            err_buf)
        if rc != 0:
            raise RuntimeError(f"EsPModel failed (rc={rc}): {err_buf.value.decode()}")
        return list(smm_buf)

    @staticmethod
    def _attach_oitcfg(
        desc: EspMortgageCollatDescStruct,
        on_the_fly_scoring: bool,
        score_switch: Optional[int] = None,
    ) -> Optional[OptionaInputToCFGenerator]:
        """
        Attach OptionaInputToCFGenerator to *desc* when any flag is needed.

        on_the_fly_scoring : bool
            Sets n_RetrieveDetailedCollatInfoForPrepayScoringFlag=1 — instructs
            the DLL to compute prepay scores from detailed collateral/loan
            attributes before the main prepay run.
        score_switch : int, optional
            nPrepayAndDefaultScoreSwitch — explicit scoring on/off control:
              0 (default) – both prepay and default scores from file
              1           – prepay scores OFF, default scores ON
              2           – both prepay and default scores OFF
              3           – prepay scores ON, default scores OFF
            None leaves the field at its zero-init default (same as 0).
        """
        if not on_the_fly_scoring and score_switch is None:
            return None
        oitcfg = OptionaInputToCFGenerator()
        if on_the_fly_scoring:
            oitcfg.n_RetrieveDetailedCollatInfoForPrepayScoringFlag = 1
        if score_switch is not None:
            oitcfg.nPrepayAndDefaultScoreSwitch = score_switch
        desc.pOptionaInputToCFGenerator = ctypes.cast(
            ctypes.pointer(oitcfg), ctypes.c_void_p)
        return oitcfg

    # ── Scoring ───────────────────────────────────────────────────────────

    def calc_loan_score(
        self,
        agency_name: bytes = b"",
        orig_term_months: int = 360,
        amort_period_months: int = 0,
        age_months: int = 0,
        wam_months: int = 0,
        gross_wac_pct: float = 0.0,
        net_coupon_pct: float = 0.0,
        loan_level: Optional[dict] = None,
        loan_info: Optional[dict] = None,
        arm_desc: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Compute prepayment scores via EspCalcLoanScore_G (prepayScore.dll).

        Builds a descriptor from the given loan attributes, calls the
        scoring-only API, and returns the computed scores as a dict —
        or None if the loan type has no score file coverage.

        The returned dict can be passed directly as ``input_scores`` to
        calc_prepay_and_default_mthread so that scores are pre-filled in
        pEspPrepaymentScoreStruct at call time and appear in the DLL log.

        Example two-step flow::

            scores = model.calc_loan_score(
                agency_name=b"FNMA", orig_term_months=360, age_months=36,
                loan_level={...}, loan_info={...})

            if scores:
                smm, defaults, _ = model.calc_prepay_and_default_mthread(
                    ..., input_scores=scores)
        """
        amort_period_months = amort_period_months or orig_term_months
        wam_months          = wam_months or (orig_term_months - age_months)

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)

        # Pre-allocate score struct — EspCalcLoanScore_G writes into it
        sc = EspPrepaymentScoreStruct()
        desc.pEspPrepaymentScoreStruct = ctypes.cast(
            ctypes.pointer(sc), ctypes.c_void_p)

        ll                             = self._attach_loan_level(desc, loan_level)
        li, adit                       = self._attach_loan_info(desc, loan_info)
        arm_s, addl_arm_s, arm_wac_arr = self._attach_arm_desc(desc, arm_desc, wam_months)

        err_buf = ctypes.create_string_buffer(200)
        rc = self._score.EspCalcLoanScore_G(
            ctypes.byref(desc),
            self._data_dir,
            self._data_dir,
            err_buf,
        )
        if rc != 0:
            raise RuntimeError(
                f"EspCalcLoanScore_G failed (rc={rc}): {err_buf.value.decode()}")

        # isNonTrivialOfEspPrepaymentScoreStruct returns 0 if scores are
        # trivial (neutral/500) meaning no score file coverage for this loan type
        if not self._esp.isNonTrivialOfEspPrepaymentScoreStruct(ctypes.byref(sc)):
            return None

        return sc.to_dict()

    # ── Modern multi-thread prepayment ────────────────────────────────────

    def calc_prepay_mthread(
        self,
        agency_name: bytes = b"",
        orig_term_months: int = 360,
        amort_period_months: int = 0,
        age_months: int = 0,
        wam_months: int = 0,
        gross_wac_pct: float = 0.0,
        net_coupon_pct: float = 0.0,
        settle_date: int = 0,
        mrate_date: int = 0,
        mtg_rate_30yr: Optional[List[float]] = None,
        mtg_rate_15yr: Optional[List[float]] = None,
        mtg_rate_7yr:  Optional[List[float]] = None,
        mtg_rate_5yr:  Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        tnote_5yr:     Optional[List[float]] = None,
        tnote_3yr:     Optional[List[float]] = None,
        calc_defaults: bool = False,
        input_scores: Optional[dict] = None,
        proj_hpi: Optional[List[float]] = None,
        hpi_start_yyyymm: int = 0,
        proj_unemp: Optional[List[float]] = None,
        unemp_start_yyyymm: int = 0,
        default_dials: Optional[dict] = None,
        loan_level: Optional[dict] = None,
        fine_tune: Optional[dict] = None,
        loan_info: Optional[dict] = None,
        arm_desc: Optional[dict] = None,
        on_the_fly_scoring: bool = False,
        score_switch: Optional[int] = None,
        thread_key: str = "default",
    ) -> Tuple[List[float], List[dict]]:
        """
        EspPrep_PrepayModelMThread – multi-thread-safe prepayment speed.

        Parameters
        ----------
        input_scores : dict, optional
            Pre-computed scores to force into the model (nForceUseInputScores=1).
            Pass the dict returned by get_prepay_scores_mthread(), or supply keys
            ``ht_prepay_score``, ``rf_prepay_score``, ``fc_default_score``,
            ``del_default_score``, ``draw_score``, ``ratio_scorable_bal`` directly.
            When None (default) scoring is file-driven (loan-type dependent).
        proj_hpi : list of float, optional
            **No effect in this API.**  projHPI is a default-model input; it is
            only processed by EspPrep_PrepayAndDefaultModelMThread.  Use
            calc_prepay_and_default_mthread() if you need HPI projection.
            For a static LTV adjustment use default_dials["orig_ltv"] /
            default_dials["cur_adj_ltv"] instead — those ARE read here.
        proj_unemp : list of float, optional
            **No effect in this API.**  The MF unemployment path is a default-model
            input and is only processed by EspPrep_PrepayAndDefaultModelMThread.
            Use calc_prepay_and_default_mthread() for MF unemployment projection.
        arm_desc : dict, optional
            ARM / hybrid-ARM descriptor.  Leave None for FRM pools.
            Keys: ``index_type``, ``gross_wac_at_origin``, ``proj_gross_wac`` (list),
            ``life_cap``, ``margin``, ``teaser_months``, ``reset_period_months``,
            ``periodic_cap``, ``lookback_days``.
            ``proj_gross_wac`` is the projected gross ARM coupon path (%) starting at
            settle_date, length >= wam_months — this is the ARM equivalent of the
            FRM mtg_rate_30yr/15yr vectors.
        thread_key : str
            Unique key per thread; reuse within a thread to avoid re-initialisation.
            Call free_thread_data() when the thread is done.

        Returns
        -------
        (smm_list, default_list)
            smm_list    : list of float – monthly SMM projections
            default_list: list of dict  – empty unless calc_defaults=True
        """
        amort_period_months = amort_period_months or orig_term_months
        wam_months          = wam_months or (orig_term_months - age_months)
        mrate_date          = mrate_date or settle_date
        rem_term            = wam_months

        arr30, ptr30 = _to_double_array(mtg_rate_30yr)
        # DLL requires all four mtg rate vectors to be non-NULL when any one is set;
        # fall back to the 30yr vector for any that were not explicitly supplied.
        arr15, ptr15 = _to_double_array(mtg_rate_15yr if mtg_rate_15yr is not None else mtg_rate_30yr)
        arr7,  ptr7  = _to_double_array(mtg_rate_7yr  if mtg_rate_7yr  is not None else mtg_rate_30yr)
        arr5,  ptr5  = _to_double_array(mtg_rate_5yr  if mtg_rate_5yr  is not None else mtg_rate_30yr)
        arr10, ptr10 = _to_double_array(tnote_10yr)
        arr5t, ptr5t = _to_double_array(tnote_5yr)
        arr3,  ptr3  = _to_double_array(tnote_3yr)

        smm_buf = (ctypes.c_double * rem_term)()
        def_buf = (EspDefaultRate * rem_term)() if calc_defaults else None

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)
        sc                   = self._attach_scores(desc, input_scores, on_the_fly_scoring)
        dmi, hpi_arr, dials  = self._attach_default_model_input(
            desc, proj_hpi, hpi_start_yyyymm, default_dials)
        mf, addl, unemp_arr  = self._attach_unemployment(
            desc, proj_unemp, unemp_start_yyyymm or settle_date)
        ll                   = self._attach_loan_level(desc, loan_level)
        arm_s, addl_arm_s, arm_wac_arr = self._attach_arm_desc(desc, arm_desc, wam_months)
        ft, adv              = self._attach_fine_tune(desc, fine_tune)
        li, adit             = self._attach_loan_info(desc, loan_info)
        oitcfg               = self._attach_oitcfg(desc, on_the_fly_scoring, score_switch)

        proj = EspPrepayProjStruct()
        proj.settleDateYyyyMm      = settle_date
        proj.mrateDateYyyyMm       = mrate_date
        proj.paramsDir             = self._data_dir
        proj.projected_smm_percent = ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double))
        if def_buf is not None:
            proj.projDefaultVector = ctypes.cast(def_buf, ctypes.POINTER(EspDefaultRate))
        if ptr30: proj.projMortCommitRate30InPercent = ptr30
        if ptr15: proj.projMortCommitRate15InPercent = ptr15
        if ptr7:  proj.projMortCommitRate07InPercent = ptr7
        if ptr5:  proj.projMortCommitRate05InPercent = ptr5
        if ptr10: proj.projTreasuryRate10InPercent   = ptr10
        if ptr5t: proj.projTreasuryRate05InPercent   = ptr5t
        if ptr3:  proj.projTreasuryRate03InPercent   = ptr3

        err_buf = ctypes.create_string_buffer(200)
        rc = self._esp.EspPrep_PrepayModelMThread(
            ctypes.byref(desc), ctypes.byref(proj), err_buf, self._holder(thread_key))
        if rc != 0:
            raise RuntimeError(
                f"EspPrep_PrepayModelMThread failed (rc={rc}): {err_buf.value.decode()}")

        return list(smm_buf), ([d.to_dict() for d in def_buf] if def_buf else [])

    def calc_prepay_and_default_mthread(
        self,
        agency_name: bytes = b"",
        orig_term_months: int = 360,
        amort_period_months: int = 0,
        age_months: int = 0,
        wam_months: int = 0,
        gross_wac_pct: float = 0.0,
        net_coupon_pct: float = 0.0,
        settle_date: int = 0,
        mrate_date: int = 0,
        mtg_rate_30yr: Optional[List[float]] = None,
        mtg_rate_15yr: Optional[List[float]] = None,
        mtg_rate_7yr:  Optional[List[float]] = None,
        mtg_rate_5yr:  Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        tnote_5yr:     Optional[List[float]] = None,
        tnote_3yr:     Optional[List[float]] = None,
        deal_name: Optional[bytes] = None,
        group_number: int = -1,
        input_scores: Optional[dict] = None,
        proj_hpi: Optional[List[float]] = None,
        hpi_start_yyyymm: int = 0,
        proj_unemp: Optional[List[float]] = None,
        unemp_start_yyyymm: int = 0,
        default_dials: Optional[dict] = None,
        loan_level: Optional[dict] = None,
        fine_tune: Optional[dict] = None,
        loan_info: Optional[dict] = None,
        arm_desc: Optional[dict] = None,
        on_the_fly_scoring: bool = False,
        score_switch: Optional[int] = None,
        thread_key: str = "default",
    ) -> Tuple[List[float], List[dict], dict]:
        """
        EspPrep_PrepayAndDefaultModelMThread – combined prepay + default projections.

        Parameters
        ----------
        proj_hpi : list of float, optional
            Monthly HPI projection vector for the default model HPA path.
            Valid here (default model is always invoked by this API when
            pEspDefaultModelInput is non-NULL).
        hpi_start_yyyymm : int
            0 (default) → proj_hpi[0] aligned with settle_date.
            Set to mrate_date → proj_hpi[0] aligned with mrate_date so the
            vector can cover the period from mrate_date through the projection horizon.
        proj_unemp : list of float, optional
            Monthly unemployment-rate projection (percent) for MF/commercial loans.
            Valid here — processed by the default model for MF loan types.
        unemp_start_yyyymm : int
            The YyyyMm that proj_unemp[0] corresponds to (must be non-zero).
            Defaults to settle_date when 0.
        arm_desc : dict, optional
            ARM / hybrid-ARM descriptor.  Leave None for FRM pools.
            Keys: ``index_type``, ``gross_wac_at_origin``, ``proj_gross_wac`` (list),
            ``life_cap``, ``margin``, ``teaser_months``, ``reset_period_months``,
            ``periodic_cap``, ``lookback_days``.
            ``proj_gross_wac`` is the projected gross ARM coupon path (%) starting at
            settle_date, length >= wam_months — ARM equivalent of mtg_rate_30yr/15yr.
        """
        amort_period_months = amort_period_months or orig_term_months
        wam_months          = wam_months or (orig_term_months - age_months)
        mrate_date          = mrate_date or settle_date
        rem_term            = wam_months

        arr30, ptr30 = _to_double_array(mtg_rate_30yr)
        # DLL requires all four mtg rate vectors to be non-NULL when any one is set;
        # fall back to the 30yr vector for any that were not explicitly supplied.
        arr15, ptr15 = _to_double_array(mtg_rate_15yr if mtg_rate_15yr is not None else mtg_rate_30yr)
        arr7,  ptr7  = _to_double_array(mtg_rate_7yr  if mtg_rate_7yr  is not None else mtg_rate_30yr)
        arr5,  ptr5  = _to_double_array(mtg_rate_5yr  if mtg_rate_5yr  is not None else mtg_rate_30yr)
        arr10, ptr10 = _to_double_array(tnote_10yr)
        arr5t, ptr5t = _to_double_array(tnote_5yr)
        arr3,  ptr3  = _to_double_array(tnote_3yr)

        smm_buf = (ctypes.c_double * rem_term)()
        def_buf = (EspDefaultRate * rem_term)()

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)
        sc                   = self._attach_scores(desc, input_scores, on_the_fly_scoring)
        dmi, hpi_arr, dials  = self._attach_default_model_input(
            desc, proj_hpi, hpi_start_yyyymm, default_dials)
        mf, addl, unemp_arr  = self._attach_unemployment(
            desc, proj_unemp, unemp_start_yyyymm or settle_date)
        ll                   = self._attach_loan_level(desc, loan_level)
        arm_s, addl_arm_s, arm_wac_arr = self._attach_arm_desc(desc, arm_desc, wam_months)
        ft, adv              = self._attach_fine_tune(desc, fine_tune)
        li, adit             = self._attach_loan_info(desc, loan_info)
        oitcfg               = self._attach_oitcfg(desc, on_the_fly_scoring, score_switch)

        proj = EspPrepayProjStruct()
        proj.settleDateYyyyMm      = settle_date
        proj.mrateDateYyyyMm       = mrate_date
        proj.paramsDir             = self._data_dir
        proj.projected_smm_percent = ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double))
        proj.projDefaultVector     = ctypes.cast(def_buf, ctypes.POINTER(EspDefaultRate))
        if ptr30: proj.projMortCommitRate30InPercent = ptr30
        if ptr15: proj.projMortCommitRate15InPercent = ptr15
        if ptr7:  proj.projMortCommitRate07InPercent = ptr7
        if ptr5:  proj.projMortCommitRate05InPercent = ptr5
        if ptr10: proj.projTreasuryRate10InPercent   = ptr10
        if ptr5t: proj.projTreasuryRate05InPercent   = ptr5t
        if ptr3:  proj.projTreasuryRate03InPercent   = ptr3

        err_buf = ctypes.create_string_buffer(200)
        rc = self._esp.EspPrep_PrepayAndDefaultModelMThread(
            deal_name, group_number,
            ctypes.byref(desc), ctypes.byref(proj),
            0, -1, err_buf, self._holder(thread_key))

        if rc == ESP_ERROR_CODE_FOR_FAILURE_OF_READING_DEFAULT_MODEL_PARAMETER_FILES:
            # Prepay succeeded; default model parameter files (e.g. HPI data) missing.
            # SMM is valid; default rates are unavailable.
            import warnings
            warnings.warn(
                f"Default model parameter files missing (rc={rc}): "
                f"{err_buf.value.decode()} — SMM returned, default rates zeroed.",
                RuntimeWarning, stacklevel=2)
            return list(smm_buf), [], (sc.to_dict() if sc is not None else {})
        if rc != 0:
            raise RuntimeError(
                f"EspPrep_PrepayAndDefaultModelMThread failed (rc={rc}): "
                f"{err_buf.value.decode()}")

        return list(smm_buf), [d.to_dict() for d in def_buf], (sc.to_dict() if sc is not None else {})

    def calc_prepay_from_cusip(
        self,
        cusip: bytes,
        intex_data_dir: bytes,
        settle_date: int,
        mrate_date: int = 0,
        mtg_rate_30yr: Optional[List[float]] = None,
        mtg_rate_15yr: Optional[List[float]] = None,
        mtg_rate_7yr:  Optional[List[float]] = None,
        mtg_rate_5yr:  Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        tnote_5yr:     Optional[List[float]] = None,
        tnote_3yr:     Optional[List[float]] = None,
        proj_hpi: Optional[List[float]] = None,
        hpi_start_yyyymm: int = 0,
        group_number: int = -1,
        thread_key: str = "default",
    ) -> Tuple[List[float], List[dict]]:
        """
        CUSIP-based CMO prepay + default: AFT queries Intex to load all deal
        attributes automatically.

        Parameters
        ----------
        cusip : bytes
            9-character CUSIP of the CMO tranche (e.g. b"3128M5GE0").
            AFT resolves this to deal name + tranche via the Intex data folder.
        intex_data_dir : bytes
            Path to the Intex data root folder.  Must contain subfolders:
              cmo_cdi\  — deal structure files
              cmo_cdu\  — collateral/historical update files
            e.g. rb"C:\\intex\\data"
            Passed to AFT as "cdi_path cdu_path" (space-separated) per the
            loadEspMBSCalcInputStructFromCmoVendor API for Intex.
        settle_date : int
            Settlement date in YyyyMm format (e.g. 202501).  Converted to
            YyyyMmDd = settle_date * 100 + 1 for the Intex loader.
        mrate_date : int
            Mortgage rate date in YyyyMm (defaults to settle_date).
        proj_hpi : list of float, optional
            Monthly HPA override for the default model.  When None, AFT uses its
            own internal HPA projection from deal parameter files.
        group_number : int
            Intex group number; -1 when not applicable (most cases).

        Returns
        -------
        (smm_list, default_list)
        """
        mrate_date = mrate_date or settle_date
        # loadEspMBSCalcInputStructFromCmoVendor expects YYYYMMDD
        settle_yyyymmdd = settle_date * 100 + 1
        # Intex requires "cdi_path cdu_path" as a single space-separated string
        # cmo_cdi\ and cmo_cdu\ are subfolders under intex_data_dir
        sep = b"\\" if b"\\" in intex_data_dir else b"/"
        cmo_data_dir = (intex_data_dir + sep + b"cmo_cdi" + b" " +
                        intex_data_dir + sep + b"cmo_cdu")

        err_buf = ctypes.create_string_buffer(200)
        mbs_ptr = self._esp.loadEspMBSCalcInputStructFromCmoVendor(
            b"INTEX",
            cmo_data_dir,
            cusip,
            ctypes.c_long(settle_yyyymmdd),
            None,   # vpMiscOptions — NULL for defaults
            err_buf,
        )
        if not mbs_ptr:
            raise RuntimeError(
                f"loadEspMBSCalcInputStructFromCmoVendor failed for CUSIP "
                f"{cusip!r}: {err_buf.value.decode()}")

        try:
            mbs = mbs_ptr.contents
            deal_name = mbs.dealName  # populated by Intex lookup
            desc      = mbs.oEspMortgageCollatDescStruct  # all indicatives from Intex
            rem_term  = desc.wamAtSettlementInMonth or (
                desc.origTermInMonth - desc.ageAtSettlementInMonth)

            arr30, ptr30 = _to_double_array(mtg_rate_30yr)
            arr15, ptr15 = _to_double_array(mtg_rate_15yr if mtg_rate_15yr is not None else mtg_rate_30yr)
            arr7,  ptr7  = _to_double_array(mtg_rate_7yr  if mtg_rate_7yr  is not None else mtg_rate_30yr)
            arr5,  ptr5  = _to_double_array(mtg_rate_5yr  if mtg_rate_5yr  is not None else mtg_rate_30yr)
            arr10, ptr10 = _to_double_array(tnote_10yr)
            arr5t, ptr5t = _to_double_array(tnote_5yr)
            arr3,  ptr3  = _to_double_array(tnote_3yr)

            smm_buf = (ctypes.c_double * rem_term)()
            def_buf = (EspDefaultRate * rem_term)()

            # Attach optional HPI override; pEspDefaultModelInput not set for CMO
            # (AFT reads default model inputs from its deal parameter files)
            hpi_dmi, hpi_arr, _ = self._attach_default_model_input(
                desc, proj_hpi, hpi_start_yyyymm, None)

            proj = EspPrepayProjStruct()
            proj.settleDateYyyyMm      = settle_date
            proj.mrateDateYyyyMm       = mrate_date
            proj.paramsDir             = self._data_dir
            proj.projected_smm_percent = ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double))
            proj.projDefaultVector     = ctypes.cast(def_buf, ctypes.POINTER(EspDefaultRate))
            if ptr30: proj.projMortCommitRate30InPercent = ptr30
            if ptr15: proj.projMortCommitRate15InPercent = ptr15
            if ptr7:  proj.projMortCommitRate07InPercent = ptr7
            if ptr5:  proj.projMortCommitRate05InPercent = ptr5
            if ptr10: proj.projTreasuryRate10InPercent   = ptr10
            if ptr5t: proj.projTreasuryRate05InPercent   = ptr5t
            if ptr3:  proj.projTreasuryRate03InPercent   = ptr3

            err_buf2 = ctypes.create_string_buffer(200)
            rc = self._esp.EspPrep_PrepayAndDefaultModelMThread(
                deal_name, group_number,
                ctypes.byref(desc), ctypes.byref(proj),
                0, -1, err_buf2, self._holder(thread_key))
            if rc == ESP_ERROR_CODE_FOR_FAILURE_OF_READING_DEFAULT_MODEL_PARAMETER_FILES:
                import warnings
                warnings.warn(
                    f"Default model parameter files missing (rc={rc}): "
                    f"{err_buf2.value.decode()} — SMM returned, default rates zeroed.",
                    RuntimeWarning, stacklevel=2)
                return list(smm_buf), []
            if rc != 0:
                raise RuntimeError(
                    f"EspPrep_PrepayAndDefaultModelMThread failed (rc={rc}): "
                    f"{err_buf2.value.decode()}")

            return list(smm_buf), [d.to_dict() for d in def_buf]

        finally:
            self._esp.freeEspMBSCalcInputStruct(ctypes.byref(mbs_ptr))

    def calc_prepay_and_default_mthread_itx(
        self,
        deal_name: bytes,
        pv_icmo: int,
        pv_pool_info: int,
        group_number: int = -1,
        agency_name: bytes = b"",
        orig_term_months: int = 360,
        amort_period_months: int = 0,
        age_months: int = 0,
        wam_months: int = 0,
        gross_wac_pct: float = 0.0,
        net_coupon_pct: float = 0.0,
        settle_date: int = 0,
        mrate_date: int = 0,
        mtg_rate_30yr: Optional[List[float]] = None,
        mtg_rate_15yr: Optional[List[float]] = None,
        mtg_rate_7yr:  Optional[List[float]] = None,
        mtg_rate_5yr:  Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        tnote_5yr:     Optional[List[float]] = None,
        tnote_3yr:     Optional[List[float]] = None,
        proj_hpi: Optional[List[float]] = None,
        hpi_start_yyyymm: int = 0,
        arm_desc: Optional[dict] = None,
        thread_key: str = "default",
    ) -> Tuple[List[float], List[dict]]:
        """
        EspPrep_PrepayAndDefaultModelMThread_itx — combined prepay + default with
        live Intex objects.

        AFT reads collateral composition (OLTV, CLTV, MSA, ARM indicatives, etc.)
        directly from the live Intex ICMO / POOL_INFO handles.
        desc_ptr->pEspDefaultModelInput is NOT used for CMO mode — the default model
        inputs come from AFT's deal parameter files and the Intex objects.

        Parameters
        ----------
        deal_name : bytes
            Intex deal name (e.g. b"FNMA_2024_M1").  Must be non-NULL.
        pv_icmo : int
            Raw pointer to the live Intex ICMO object (cast from ctypes.c_void_p or
            obtained from Intex DLL as an integer address).
        pv_pool_info : int
            Raw pointer to the live Intex POOL_INFO object for the specific pool /
            collateral group being evaluated.
        group_number : int
            Intex group number; -1 when not applicable.
        proj_hpi : list of float, optional
            Monthly HPA projection (%). When provided, overrides AFT's internal HPA
            for the default model.
        """
        amort_period_months = amort_period_months or orig_term_months
        wam_months          = wam_months or (orig_term_months - age_months)
        mrate_date          = mrate_date or settle_date
        rem_term            = wam_months

        arr30, ptr30 = _to_double_array(mtg_rate_30yr)
        arr15, ptr15 = _to_double_array(mtg_rate_15yr if mtg_rate_15yr is not None else mtg_rate_30yr)
        arr7,  ptr7  = _to_double_array(mtg_rate_7yr  if mtg_rate_7yr  is not None else mtg_rate_30yr)
        arr5,  ptr5  = _to_double_array(mtg_rate_5yr  if mtg_rate_5yr  is not None else mtg_rate_30yr)
        arr10, ptr10 = _to_double_array(tnote_10yr)
        arr5t, ptr5t = _to_double_array(tnote_5yr)
        arr3,  ptr3  = _to_double_array(tnote_3yr)

        smm_buf = (ctypes.c_double * rem_term)()
        def_buf = (EspDefaultRate * rem_term)()

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)
        # Note: pEspDefaultModelInput is NOT attached for CMO — Intex supplies it
        hpi_dmi, hpi_arr, _ = self._attach_default_model_input(
            desc, proj_hpi, hpi_start_yyyymm, None)
        arm_s, addl_arm_s, arm_wac_arr = self._attach_arm_desc(desc, arm_desc, wam_months)

        proj = EspPrepayProjStruct()
        proj.settleDateYyyyMm      = settle_date
        proj.mrateDateYyyyMm       = mrate_date
        proj.paramsDir             = self._data_dir
        proj.projected_smm_percent = ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double))
        proj.projDefaultVector     = ctypes.cast(def_buf, ctypes.POINTER(EspDefaultRate))
        if ptr30: proj.projMortCommitRate30InPercent = ptr30
        if ptr15: proj.projMortCommitRate15InPercent = ptr15
        if ptr7:  proj.projMortCommitRate07InPercent = ptr7
        if ptr5:  proj.projMortCommitRate05InPercent = ptr5
        if ptr10: proj.projTreasuryRate10InPercent   = ptr10
        if ptr5t: proj.projTreasuryRate05InPercent   = ptr5t
        if ptr3:  proj.projTreasuryRate03InPercent   = ptr3

        err_buf = ctypes.create_string_buffer(200)
        rc = self._esp.EspPrep_PrepayAndDefaultModelMThread_itx(
            deal_name, group_number,
            ctypes.byref(desc), ctypes.byref(proj),
            0, -1,
            ctypes.c_void_p(pv_icmo),
            ctypes.c_void_p(pv_pool_info),
            err_buf, self._holder(thread_key))
        if rc != 0:
            raise RuntimeError(
                f"EspPrep_PrepayAndDefaultModelMThread_itx failed (rc={rc}): "
                f"{err_buf.value.decode()}")

        return list(smm_buf), [d.to_dict() for d in def_buf]

    def get_prepay_scores_mthread(
        self,
        agency_name: bytes = b"",
        orig_term_months: int = 360,
        amort_period_months: int = 0,
        age_months: int = 0,
        wam_months: int = 0,
        gross_wac_pct: float = 0.0,
        net_coupon_pct: float = 0.0,
        settle_date: int = 0,
        mrate_date: int = 0,
        mtg_rate_30yr: Optional[List[float]] = None,
        mtg_rate_15yr: Optional[List[float]] = None,
        mtg_rate_7yr:  Optional[List[float]] = None,
        mtg_rate_5yr:  Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        tnote_5yr:     Optional[List[float]] = None,
        tnote_3yr:     Optional[List[float]] = None,
        arm_desc: Optional[dict] = None,
        thread_key: str = "default",
    ) -> Optional[dict]:
        """
        EspPrep_PrepayModelMThread_GetPrepaymentScores.

        Returns a dict with score fields + 'smm' key, or None if loan type is unscored.
        Scoring availability is determined by loan type (AFT score file coverage),
        not by any user-controlled flag.
        arm_desc : dict, optional — same as calc_prepay_mthread; for ARM pool scoring.
        """
        amort_period_months = amort_period_months or orig_term_months
        wam_months          = wam_months or (orig_term_months - age_months)
        mrate_date          = mrate_date or settle_date

        arr30, ptr30 = _to_double_array(mtg_rate_30yr)
        arr15, ptr15 = _to_double_array(mtg_rate_15yr if mtg_rate_15yr is not None else mtg_rate_30yr)
        arr7,  ptr7  = _to_double_array(mtg_rate_7yr  if mtg_rate_7yr  is not None else mtg_rate_30yr)
        arr5,  ptr5  = _to_double_array(mtg_rate_5yr  if mtg_rate_5yr  is not None else mtg_rate_30yr)
        arr10, ptr10 = _to_double_array(tnote_10yr)
        arr5t, ptr5t = _to_double_array(tnote_5yr)
        arr3,  ptr3  = _to_double_array(tnote_3yr)

        smm_buf = (ctypes.c_double * wam_months)()

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)
        arm_s, addl_arm_s, arm_wac_arr = self._attach_arm_desc(desc, arm_desc, wam_months)

        proj = EspPrepayProjStruct()
        proj.settleDateYyyyMm      = settle_date
        proj.mrateDateYyyyMm       = mrate_date
        proj.paramsDir             = self._data_dir
        proj.projected_smm_percent = ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double))
        if ptr30: proj.projMortCommitRate30InPercent = ptr30
        if ptr15: proj.projMortCommitRate15InPercent = ptr15
        if ptr7:  proj.projMortCommitRate07InPercent = ptr7
        if ptr5:  proj.projMortCommitRate05InPercent = ptr5
        if ptr10: proj.projTreasuryRate10InPercent   = ptr10
        if ptr5t: proj.projTreasuryRate05InPercent   = ptr5t
        if ptr3:  proj.projTreasuryRate03InPercent   = ptr3

        err_buf   = ctypes.create_string_buffer(200)
        score_ptr = self._esp.EspPrep_PrepayModelMThread_GetPrepaymentScores(
            ctypes.byref(desc), ctypes.byref(proj), err_buf, self._holder(thread_key))

        if not score_ptr:
            return None

        result = score_ptr.contents.to_dict()
        result["smm"] = list(smm_buf)
        self._esp.freeEspPrepaymentScoreStruct(ctypes.byref(score_ptr))
        return result

    # ── Loan scoring ──────────────────────────────────────────────────────

    def calc_loan_score_q(
        self,
        agency_name: bytes,
        orig_term_months: int,
        age_months: int,
        gross_wac_pct: float,
        net_coupon_pct: float = 0.0,
        wam_months: int = 0,
        amort_period_months: int = 0,
    ) -> int:
        """
        EspCalcLoanScore_Q – quiet loan scoring (no market projection needed).
        Returns score code; raises RuntimeError on failure.
        """
        if wam_months == 0:
            wam_months = orig_term_months - age_months
        if amort_period_months == 0:
            amort_period_months = orig_term_months

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)

        err_buf = ctypes.create_string_buffer(200)
        rc = self._score.EspCalcLoanScore_Q(
            ctypes.byref(desc), self._data_dir, self._data_dir, err_buf)
        if rc < 0:
            raise RuntimeError(
                f"EspCalcLoanScore_Q failed (rc={rc}): {err_buf.value.decode()}")
        return rc

    def calc_loan_score_g(
        self,
        agency_name: bytes,
        orig_term_months: int,
        age_months: int,
        gross_wac_pct: float,
        net_coupon_pct: float = 0.0,
        wam_months: int = 0,
        amort_period_months: int = 0,
    ) -> int:
        """EspCalcLoanScore_G – generic loan scoring (no market projection)."""
        if wam_months == 0:
            wam_months = orig_term_months - age_months
        if amort_period_months == 0:
            amort_period_months = orig_term_months

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)

        err_buf = ctypes.create_string_buffer(200)
        rc = self._score.EspCalcLoanScore_G(
            ctypes.byref(desc), self._data_dir, self._data_dir, err_buf)
        if rc < 0:
            raise RuntimeError(
                f"EspCalcLoanScore_G failed (rc={rc}): {err_buf.value.decode()}")
        return rc

    def calc_loan_score_v6(
        self,
        agency_name: bytes,
        orig_term_months: int,
        age_months: int,
        gross_wac_pct: float,
        net_coupon_pct: float,
        settle_date: int,
        mrate_date: int,
        mtg_rate_30yr: Optional[List[float]] = None,
        tnote_10yr:    Optional[List[float]] = None,
        wam_months: int = 0,
        amort_period_months: int = 0,
        thread_key: str = "default",
    ) -> int:
        """EspCalcLoanScore_v6 – loan scoring with market projection (multi-thread-safe)."""
        if wam_months == 0:
            wam_months = orig_term_months - age_months
        if amort_period_months == 0:
            amort_period_months = orig_term_months

        arr30, ptr30 = _to_double_array(mtg_rate_30yr)
        arr10, ptr10 = _to_double_array(tnote_10yr)

        desc = self._make_desc(
            agency_name, orig_term_months, amort_period_months,
            age_months, wam_months, gross_wac_pct, net_coupon_pct)

        smm_buf = (ctypes.c_double * wam_months)()
        proj = EspPrepayProjStruct()
        proj.settleDateYyyyMm      = settle_date
        proj.mrateDateYyyyMm       = mrate_date
        proj.paramsDir             = self._data_dir
        proj.projected_smm_percent = ctypes.cast(smm_buf, ctypes.POINTER(ctypes.c_double))
        if ptr30: proj.projMortCommitRate30InPercent = ptr30
        if ptr10: proj.projTreasuryRate10InPercent   = ptr10

        err_buf = ctypes.create_string_buffer(200)
        rc = self._score.EspCalcLoanScore_v6(
            ctypes.byref(desc), ctypes.byref(proj),
            self._data_dir, self._data_dir, err_buf, self._holder(thread_key))
        if rc < 0:
            raise RuntimeError(
                f"EspCalcLoanScore_v6 failed (rc={rc}): {err_buf.value.decode()}")
        return rc

    # ── Lookup helpers ────────────────────────────────────────────────────

    def lookup_msa_from_zip(self, zip_code: bytes, state_abbrev: bytes = b"") -> str:
        """Return the MSA code string for a ZIP code."""
        msa_buf = ctypes.create_string_buffer(20)
        self._score.lookupMSAFromZipOrStateUsingParamsDir_G(
            msa_buf, self._data_dir, zip_code, state_abbrev)
        return msa_buf.value.decode()

    def lookup_loan_size_from_year(self, year: int) -> int:
        """Return the reference loan size (in $K) for a given origination year."""
        return self._score.lookupLoanSizeFromYear(self._data_dir, year)

    # ── Thread and resource cleanup ───────────────────────────────────────

    def free_thread_data(self, thread_key: str = "default") -> None:
        """Release the per-thread DLL data holder."""
        if thread_key in self._data_holders:
            self._esp.freeVoidTypeCastedDataHolderForEspmodelImpl(
                ctypes.byref(self._data_holders[thread_key]))
            del self._data_holders[thread_key]

    def cleanup(self) -> None:
        """Free all thread data holders and reset the model state."""
        for key in list(self._data_holders.keys()):
            self.free_thread_data(key)
        self._esp.EsPModel(None, None, None)
