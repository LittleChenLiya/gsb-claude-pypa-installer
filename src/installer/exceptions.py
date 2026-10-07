"""Errors raised from this package."""


class InstallerError(Exception):
    """All exceptions raised from this package's code."""


class InvalidWheelSource(InstallerError):
    """When a wheel source violates a contract, or is not supported."""


class DestinationCollisionError(InstallerError):
    """When two items of one installation would write to the same target path.

    This is raised when a destination is asked to check for target path
    collisions (e.g. paths differing only in case, which collide on
    case-insensitive filesystems like the ones used by Windows) *before* any
    target file is written.
    """
