"""QQ Music encrypted-container support (QMC1 / QMC2)."""

from __future__ import annotations

from .qmc1 import KEY_TABLE, mask_at
from .qmc2 import MAP_KEY_LIMIT, Qmc2Crypto, Qmc2Error, parse_ekey
from .tc_tea import TcTeaError, tc_tea_decrypt, tc_tea_encrypt

__all__ = [
    "KEY_TABLE",
    "mask_at",
    "MAP_KEY_LIMIT",
    "Qmc2Crypto",
    "Qmc2Error",
    "parse_ekey",
    "TcTeaError",
    "tc_tea_decrypt",
    "tc_tea_encrypt",
]
