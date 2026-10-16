from distribook.config import Settings
from distribook.strategies.base import BookingStrategy, ClaimResult
from distribook.strategies.conditional import ConditionalStrategy
from distribook.strategies.naive import NaiveStrategy

__all__ = ["BookingStrategy", "ClaimResult", "build_strategy"]


def build_strategy(settings: Settings) -> BookingStrategy:
    match settings.booking_strategy:
        case "conditional":
            return ConditionalStrategy()
        case "naive":
            return NaiveStrategy()
