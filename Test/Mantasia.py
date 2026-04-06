from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict
import numpy as np
import pandas as pd


# ============================================================
# 1. PROJECT INPUTS
# ============================================================

@dataclass
class ProjectInputs:
    # Project-specific parameters from Mantasia status report
    pv_capacity_mwp: float = 251.904      # MWp
    electrolyzer_capacity_mw: float = 100.0  # MWel

    # Technical assumptions
    electrolyzer_specific_consumption_kwh_per_kg: float = 52.0
    electrolyzer_min_load_fraction: float = 0.10
    electrolyzer_availability: float = 0.97
    pv_degradation_annual: float = 0.004
    stack_life_operating_hours: float = 80000.0

    # Economic assumptions
    pv_capex_eur_per_kwp: float = 550.0
    electrolyzer_capex_eur_per_kw: float = 900.0
    bop_capex_eur_per_kw: float = 250.0
    h2_compression_capex_eur_per_kw: float = 120.0
    indirect_capex_fraction: float = 0.12

    pv_fixed_opex_pct_of_capex: float = 0.015
    electrolyzer_fixed_opex_pct_of_capex: float = 0.03
    variable_opex_eur_per_kg: float = 0.20

    water_cost_eur_per_m3: float = 1.5
    water_consumption_l_per_kg_h2: float = 10.0

    stack_replacement_fraction_of_elx_capex: float = 0.30

    # Financing assumptions
    discount_rate: float = 0.08
    project_life_years: int = 20

    # Electricity assumptions
    allow_grid_import: bool = False
    grid_import_price_eur_per_mwh: float = 80.0

    # Optional grid export / spilled PV value
    allow_pv_export: bool = False
    pv_export_price_eur_per_mwh: float = 50.0

    # Hydrogen delivery / downstream costs
    h2_delivery_cost_eur_per_kg: float = 0.15


# ============================================================
# 2. DEFAULT TIME SERIES GENERATION
# ============================================================

def create_placeholder_hourly_profile() -> pd.DataFrame:
    """
    Creates a synthetic 8760-hour dataset as a placeholder.
    Replace this with real Mantasia PV profile and optionally market prices.

    Columns:
        pv_cf      : PV capacity factor [0-1]
        grid_price : optional hourly grid import price [EUR/MWh]
        export_price : optional hourly PV export price [EUR/MWh]
    """
    hours = pd.date_range("2025-01-01", periods=8760, freq="H")

    # Simple synthetic solar profile:
    doy = hours.dayofyear.values
    hod = hours.hour.values

    seasonal = 0.55 + 0.45 * np.sin(2 * np.pi * (doy - 80) / 365.0)
    daytime = np.maximum(0, np.sin(np.pi * (hod - 6) / 12.0))
    pv_cf = np.clip(0.22 * seasonal * daytime * 2.2, 0, 1)

    # Simple price placeholder
    grid_price = 75 + 20 * (1 - pv_cf)   # more expensive when solar is low
    export_price = 45 + 10 * pv_cf

    return pd.DataFrame({
        "pv_cf": pv_cf,
        "grid_price": grid_price,
        "export_price": export_price
    }, index=hours)


# ============================================================
# 3. ECONOMIC HELPERS
# ============================================================

def capital_recovery_factor(discount_rate: float, project_life_years: int) -> float:
    r = discount_rate
    n = project_life_years
    if r == 0:
        return 1 / n
    return (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def annualized_capex(total_capex_eur: float, discount_rate: float, project_life_years: int) -> float:
    crf = capital_recovery_factor(discount_rate, project_life_years)
    return total_capex_eur * crf


# ============================================================
# 4. CAPEX MODEL
# ============================================================

def calculate_capex(inputs: ProjectInputs) -> Dict[str, float]:
    pv_capex = inputs.pv_capacity_mwp * 1000 * inputs.pv_capex_eur_per_kwp
    electrolyzer_capex = inputs.electrolyzer_capacity_mw * 1000 * inputs.electrolyzer_capex_eur_per_kw
    bop_capex = inputs.electrolyzer_capacity_mw * 1000 * inputs.bop_capex_eur_per_kw
    compression_capex = inputs.electrolyzer_capacity_mw * 1000 * inputs.h2_compression_capex_eur_per_kw

    direct_capex = pv_capex + electrolyzer_capex + bop_capex + compression_capex
    indirect_capex = direct_capex * inputs.indirect_capex_fraction
    total_capex = direct_capex + indirect_capex

    return {
        "pv_capex": pv_capex,
        "electrolyzer_capex": electrolyzer_capex,
        "bop_capex": bop_capex,
        "compression_capex": compression_capex,
        "indirect_capex": indirect_capex,
        "total_capex": total_capex
    }


# ============================================================
# 5. HOURLY DISPATCH MODEL
# ============================================================

def run_hourly_dispatch(
    data: pd.DataFrame,
    inputs: ProjectInputs
) -> pd.DataFrame:
    """
    Dispatch logic:
    - PV produces electricity each hour
    - Electrolyzer consumes PV first
    - Optional grid import tops up to electrolyzer max
    - Optional export of unused PV
    - Electrolyzer respects minimum load if turned on

    Returns hourly dataframe with energy and H2 production.
    """
    df = data.copy()

    pv_generation_mwh = df["pv_cf"].values * inputs.pv_capacity_mwp
    elx_max_mwh = inputs.electrolyzer_capacity_mw * inputs.electrolyzer_availability
    elx_min_mwh = inputs.electrolyzer_capacity_mw * inputs.electrolyzer_min_load_fraction

    pv_to_elx = np.minimum(pv_generation_mwh, elx_max_mwh)
    remaining_elx_capacity = np.maximum(0, elx_max_mwh - pv_to_elx)

    if inputs.allow_grid_import:
        grid_to_elx = remaining_elx_capacity
    else:
        grid_to_elx = np.zeros_like(remaining_elx_capacity)

    total_elx_load = pv_to_elx + grid_to_elx

    # Enforce minimum load: if below minimum, shut off
    operating_mask = total_elx_load >= elx_min_mwh
    total_elx_load = np.where(operating_mask, total_elx_load, 0.0)
    pv_to_elx = np.where(operating_mask, pv_to_elx, 0.0)
    grid_to_elx = np.where(operating_mask, grid_to_elx, 0.0)

    pv_surplus = np.maximum(0, pv_generation_mwh - pv_to_elx)

    if inputs.allow_pv_export:
        pv_export = pv_surplus
        pv_spill = np.zeros_like(pv_surplus)
    else:
        pv_export = np.zeros_like(pv_surplus)
        pv_spill = pv_surplus

    h2_kg = total_elx_load * 1000 / inputs.electrolyzer_specific_consumption_kwh_per_kg
    operating_hours_flag = (total_elx_load > 0).astype(float)

    df["pv_generation_mwh"] = pv_generation_mwh
    df["pv_to_elx_mwh"] = pv_to_elx
    df["grid_to_elx_mwh"] = grid_to_elx
    df["elx_load_mwh"] = total_elx_load
    df["pv_export_mwh"] = pv_export
    df["pv_spill_mwh"] = pv_spill
    df["h2_kg"] = h2_kg
    df["operating_hour_flag"] = operating_hours_flag

    return df


# ============================================================
# 6. ANNUAL COST MODEL
# ============================================================

def calculate_annual_costs(
    hourly: pd.DataFrame,
    inputs: ProjectInputs
) -> Dict[str, float]:
    capex = calculate_capex(inputs)

    annualized_total_capex = annualized_capex(
        capex["total_capex"],
        inputs.discount_rate,
        inputs.project_life_years
    )

    pv_fixed_opex = (capex["pv_capex"] * inputs.pv_fixed_opex_pct_of_capex)
    elx_related_capex = capex["electrolyzer_capex"] + capex["bop_capex"] + capex["compression_capex"]
    electrolyzer_fixed_opex = elx_related_capex * inputs.electrolyzer_fixed_opex_pct_of_capex

    annual_h2_kg = hourly["h2_kg"].sum()

    variable_opex = annual_h2_kg * inputs.variable_opex_eur_per_kg

    water_m3 = annual_h2_kg * inputs.water_consumption_l_per_kg_h2 / 1000
    water_cost = water_m3 * inputs.water_cost_eur_per_m3

    grid_import_cost = (hourly["grid_to_elx_mwh"] * hourly["grid_price"]).sum()

    pv_export_revenue = 0.0
    if inputs.allow_pv_export:
        pv_export_revenue = (hourly["pv_export_mwh"] * hourly["export_price"]).sum()

    annual_operating_hours = hourly["operating_hour_flag"].sum()
    annual_stack_usage_hours = annual_operating_hours
    stack_replacement_interval_years = inputs.stack_life_operating_hours / max(annual_stack_usage_hours, 1e-6)

    annualized_stack_replacement = (
        calculate_capex(inputs)["electrolyzer_capex"]
        * inputs.stack_replacement_fraction_of_elx_capex
        / max(stack_replacement_interval_years, 1e-6)
    )

    delivery_cost = annual_h2_kg * inputs.h2_delivery_cost_eur_per_kg

    total_annual_cost = (
        annualized_total_capex
        + pv_fixed_opex
        + electrolyzer_fixed_opex
        + variable_opex
        + water_cost
        + grid_import_cost
        + annualized_stack_replacement
        + delivery_cost
        - pv_export_revenue
    )

    return {
        "annualized_total_capex": annualized_total_capex,
        "pv_fixed_opex": pv_fixed_opex,
        "electrolyzer_fixed_opex": electrolyzer_fixed_opex,
        "variable_opex": variable_opex,
        "water_cost": water_cost,
        "grid_import_cost": grid_import_cost,
        "annualized_stack_replacement": annualized_stack_replacement,
        "delivery_cost": delivery_cost,
        "pv_export_revenue": pv_export_revenue,
        "total_annual_cost": total_annual_cost,
        "annual_h2_kg": annual_h2_kg,
        "annual_h2_tonnes": annual_h2_kg / 1000,
        "annual_operating_hours": annual_operating_hours,
        "stack_replacement_interval_years": stack_replacement_interval_years
    }


# ============================================================
# 7. LCOH CALCULATION
# ============================================================

def calculate_lcoh(
    hourly: pd.DataFrame,
    inputs: ProjectInputs
) -> Dict[str, float]:
    annual = calculate_annual_costs(hourly, inputs)

    if annual["annual_h2_kg"] <= 0:
        raise ValueError("Annual hydrogen production is zero; LCOH cannot be calculated.")

    lcoh_eur_per_kg = annual["total_annual_cost"] / annual["annual_h2_kg"]

    return {
        **annual,
        "lcoh_eur_per_kg": lcoh_eur_per_kg
    }


# ============================================================
# 8. SIMPLE SENSITIVITY ANALYSIS
# ============================================================

def run_sensitivity_cases(base_inputs: ProjectInputs, data: pd.DataFrame) -> pd.DataFrame:
    cases = []

    for specific_consumption in [50.0, 52.0, 55.0]:
        for elx_capex in [700.0, 900.0, 1100.0]:
            for grid_import_price in [60.0, 80.0, 120.0]:
                inputs = ProjectInputs(
                    **{
                        **base_inputs.__dict__,
                        "electrolyzer_specific_consumption_kwh_per_kg": specific_consumption,
                        "electrolyzer_capex_eur_per_kw": elx_capex,
                        "grid_import_price_eur_per_mwh": grid_import_price
                    }
                )

                local = data.copy()
                local["grid_price"] = grid_import_price

                hourly = run_hourly_dispatch(local, inputs)
                results = calculate_lcoh(hourly, inputs)

                cases.append({
                    "specific_consumption_kwh_per_kg": specific_consumption,
                    "electrolyzer_capex_eur_per_kw": elx_capex,
                    "grid_import_price_eur_per_mwh": grid_import_price,
                    "annual_h2_tonnes": results["annual_h2_tonnes"],
                    "annual_operating_hours": results["annual_operating_hours"],
                    "lcoh_eur_per_kg": results["lcoh_eur_per_kg"]
                })

    return pd.DataFrame(cases)


# ============================================================
# 9. EXAMPLE MAIN EXECUTION
# ============================================================

if __name__ == "__main__":
    # Base case aligned with current project framing:
    # PV + 100 MW electrolyzer, initially without grid import/export.
    inputs = ProjectInputs(
        pv_capacity_mwp=251.904,
        electrolyzer_capacity_mw=100.0,
        allow_grid_import=False,
        allow_pv_export=False
    )

    data = create_placeholder_hourly_profile()
    hourly = run_hourly_dispatch(data, inputs)
    results = calculate_lcoh(hourly, inputs)

    print("=== MANTASIA BASE CASE ===")
    for k, v in results.items():
        if isinstance(v, float):
            print(f"{k}: {v:,.2f}")
        else:
            print(f"{k}: {v}")

    print("\n=== SENSITIVITY CASES ===")
    sens = run_sensitivity_cases(inputs, data)
    print(sens.sort_values("lcoh_eur_per_kg").head(10).to_string(index=False))