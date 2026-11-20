from dataclasses import dataclass


# Rough Gemini Flash-class defaults; override via record_usage kwargs if needed.
_DEFAULT_USD_PER_1M_INPUT = 0.10
_DEFAULT_USD_PER_1M_OUTPUT = 0.40


@dataclass
class BudgetGuard:
    daily_usd_cap: float
    monthly_usd_cap: float
    spent_today_usd: float = 0.0
    spent_month_usd: float = 0.0
    kill_switch: bool = False

    def allow_request(self) -> bool:
        if self.kill_switch:
            return False
        return self.spent_today_usd < self.daily_usd_cap and self.spent_month_usd < self.monthly_usd_cap

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        usd_per_1m_input: float = _DEFAULT_USD_PER_1M_INPUT,
        usd_per_1m_output: float = _DEFAULT_USD_PER_1M_OUTPUT,
    ) -> float:
        """Accumulate estimated USD spend; return the amount added this call."""
        cost = (
            max(0, input_tokens) * usd_per_1m_input
            + max(0, output_tokens) * usd_per_1m_output
        ) / 1_000_000.0
        self.spent_today_usd += cost
        self.spent_month_usd += cost
        return cost
