"""Package entry point: composes the mixins back into the public class."""
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .schema import StoreSchema
from .assets import StoreAssets
from .queries import StoreQueries


class PatientRecordStore(
    StoreSchema,
    StoreAssets,
    StoreQueries
):

    pass


__all__ = list(_PREAMBLE_ALL) + [
    "PatientRecordStore"
]
