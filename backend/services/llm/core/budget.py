from dataclasses import dataclass


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
