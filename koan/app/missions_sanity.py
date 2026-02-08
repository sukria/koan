"""
Backward-compatibility shim — real implementation is in sanity.missions_structure.
"""

from sanity.missions_structure import (  # noqa: F401
    find_issues,
    sanitize,
    run_sanity_check,
)
