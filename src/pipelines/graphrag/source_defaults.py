"""Source-type whitelist — defense against Cypher injection via connector config.

``source_type`` originates in connector configuration (user input), so the
resolver must not trust it blindly when computing file paths or logging it.
The whitelist is *file-existence-based*: only source_types that have a
``_defaults/<source_type>.yaml`` present are accepted. This also keeps the
whitelist trivially auditable — adding a source means committing a YAML.
"""

from __future__ import annotations

from pathlib import Path

_DEFAULTS_DIR = Path("deploy/config/graph_schemas/_defaults")


def is_valid_source_type(source_type: str) -> bool:
    """Return True iff ``source_type`` is a known, safely-named connector.

    Rules:
    - Must be non-empty lowercase alnum + underscore only (so never contains
      path separators, newlines, or Cypher special chars).
    - Must correspond to an existing ``_defaults/<name>.yaml`` file.
    """
    if not source_type:
        return False
    if not source_type.replace("_", "").isalnum():
        return False
    if source_type != source_type.lower():
        return False
    return (_DEFAULTS_DIR / f"{source_type}.yaml").exists()
