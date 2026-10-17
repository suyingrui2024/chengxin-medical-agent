import struct
import pathlib


def consume_struct(_: struct.Struct) -> None:
    pass


def make_struct() -> struct.Struct:
    return struct.Struct('HH')


def make_path() -> pathlib.Path:
    return pathlib.Path()
