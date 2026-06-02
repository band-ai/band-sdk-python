from importlib import metadata

try:
    __version__ = metadata.version("band-sdk")
except metadata.PackageNotFoundError:
    __version__ = "0.2.9"
