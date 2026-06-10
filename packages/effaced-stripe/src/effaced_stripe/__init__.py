"""effaced-stripe — first-party Stripe resolver for effaced."""

from importlib.metadata import PackageNotFoundError, version

from effaced_stripe.resolver import StripeResolver

try:
    __version__ = version("effaced-stripe")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = ["StripeResolver", "__version__"]
