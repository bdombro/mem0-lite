from __future__ import annotations

import json
from typing import Any


def dump(obj: Any) -> str:
    return json.dumps(obj, default=str, indent=2)
