"""Swap this module behind a private deployment boundary when a real strategy exists."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from .security import stamp, utcnow


@dataclass(frozen=True)
class Signal:
    id: str
    provider: str
    symbol_base: str
    side: str
    stop_loss: float
    created_at: str
    expires_at: str


class StrategyProvider(ABC):
    @abstractmethod
    def generate(self, symbol_base: str, side: str, stop_loss: float) -> Signal:
        raise NotImplementedError


class ManualDemoProvider(StrategyProvider):
    def generate(self, symbol_base: str, side: str, stop_loss: float) -> Signal:
        if symbol_base != "XAU" or side not in ("BUY", "SELL") or stop_loss <= 0:
            raise ValueError("Only explicit XAU demo signals with a stop loss are allowed")
        now = utcnow()
        return Signal(str(uuid4()), "DEMO_ONLY", symbol_base, side, stop_loss,
                      stamp(now), stamp(now + timedelta(minutes=2)))
