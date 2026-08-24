class ComicloadError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(ComicloadError):
    """Configuration is missing or invalid."""


class CatalogError(ComicloadError):
    """The local metadata catalogue is missing or unusable."""


class SinkError(ComicloadError):
    """An export destination rejected or could not accept the data."""
