"""Package entry point: composes the mixins back into the public class."""

from .assets import StoreAssets
from .queries import StoreQueries
from .schema import StoreSchema


class PatientRecordStore(
    StoreSchema,
    StoreAssets,
    StoreQueries
):

    pass


__all__ = [
    "PatientRecordStore",
]
