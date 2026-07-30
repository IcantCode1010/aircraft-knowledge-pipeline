from __future__ import annotations


CORE_SOURCE_TYPES = ("training", "amm")
OPTIONAL_SOURCE_TYPES = ("mel", "qrh")
DEFAULT_EXCLUDED_SOURCE_TYPES = ("fcom",)
SOURCE_PROFILES = {
    "core": CORE_SOURCE_TYPES,
    "all-approved": (),
}
