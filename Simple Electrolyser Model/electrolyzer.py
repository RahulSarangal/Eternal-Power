class SimpleElectrolyzer:
    """
    Very simple electrolyzer model.

    Assumptions:
    - Steady-state only
    - Fixed efficiency
    - No degradation
    - No temperature/pressure dynamics
    - No startup/shutdown effects
    """

    def __init__(self, rated_power_kw, efficiency=0.65, hydrogen_lhv_kwh_per_kg=33.33):
        """
        Parameters
        ----------
        rated_power_kw : float
            Maximum electrical input power [kW]
        efficiency : float
            Conversion efficiency based on hydrogen LHV [-]
        hydrogen_lhv_kwh_per_kg : float
            Lower heating value of hydrogen [kWh/kg]
        """
        if rated_power_kw <= 0:
            raise ValueError("rated_power_kw must be positive")
        if not (0 < efficiency <= 1):
            raise ValueError("efficiency must be between 0 and 1")

        self.rated_power_kw = rated_power_kw
        self.efficiency = efficiency
        self.hydrogen_lhv_kwh_per_kg = hydrogen_lhv_kwh_per_kg

    def hydrogen_production_rate(self, power_input_kw):
        """
        Calculate hydrogen production rate [kg/h].

        Parameters
        ----------
        power_input_kw : float
            Electrical input power [kW]

        Returns
        -------
        float
            Hydrogen production rate [kg/h]
        """
        power_input_kw = max(0.0, min(power_input_kw, self.rated_power_kw))

        hydrogen_energy_kw = power_input_kw * self.efficiency
        hydrogen_kg_per_h = hydrogen_energy_kw / self.hydrogen_lhv_kwh_per_kg

        return hydrogen_kg_per_h

    def simulate_hour(self, power_input_kw, electricity_price_eur_per_mwh=None):
        """
        Simulate one hour of operation.

        Returns a dictionary with key outputs.
        """
        h2_rate = self.hydrogen_production_rate(power_input_kw)
        energy_used_kwh = max(0.0, min(power_input_kw, self.rated_power_kw)) * 1.0

        result = {
            "power_input_kw": max(0.0, min(power_input_kw, self.rated_power_kw)),
            "hydrogen_kg_per_h": h2_rate,
            "energy_used_kwh": energy_used_kwh,
            "specific_energy_kwh_per_kg": (
                energy_used_kwh / h2_rate if h2_rate > 0 else None
            ),
        }

        if electricity_price_eur_per_mwh is not None:
            electricity_price_eur_per_kwh = electricity_price_eur_per_mwh / 1000.0
            result["electricity_cost_eur"] = energy_used_kwh * electricity_price_eur_per_kwh
            result["electricity_cost_eur_per_kg_h2"] = (
                result["electricity_cost_eur"] / h2_rate if h2_rate > 0 else None
            )

        return result


# Example usage
if __name__ == "__main__":
    electrolyzer = SimpleElectrolyzer(rated_power_kw=1000, efficiency=0.65)  # 1 MW system

    output = electrolyzer.simulate_hour(
        power_input_kw=800,
        electricity_price_eur_per_mwh=60
    )

    for key, value in output.items():
        print(f"{key}: {value}")