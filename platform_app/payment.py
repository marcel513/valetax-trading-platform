from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    @abstractmethod
    def checkout(self, user_id: int, plan: str) -> str:
        """Return a hosted checkout URL after a verified integration exists."""


class UnconfiguredPaymentProvider(PaymentProvider):
    def checkout(self, user_id: int, plan: str) -> str:
        raise RuntimeError("Payments are not configured in this demo release")
