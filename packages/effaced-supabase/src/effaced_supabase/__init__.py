"""effaced-supabase — first-party Supabase resolvers for effaced."""

from importlib.metadata import PackageNotFoundError, version

from effaced_supabase.auth_resolver import SupabaseAuthResolver
from effaced_supabase.postgrest_column import PostgrestColumn
from effaced_supabase.postgrest_resolver import SupabasePostgrestResolver
from effaced_supabase.postgrest_table import PostgrestTable

try:
    __version__ = version("effaced-supabase")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = [
    "PostgrestColumn",
    "PostgrestTable",
    "SupabaseAuthResolver",
    "SupabasePostgrestResolver",
    "__version__",
]
