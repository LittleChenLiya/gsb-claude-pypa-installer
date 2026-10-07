"""Errors raised from this package."""

from collections.abc import Iterable


class InstallerError(Exception):
    """All exceptions raised from this package's code."""


class InvalidWheelSource(InstallerError):
    """When a wheel source violates a contract, or is not supported."""


class CaseCollisionError(InstallerError):
    """When multiple install targets collide on a case-insensitive filesystem.

    This is raised before any file is written, when the optional pre-install
    collision check (``check_case_collisions`` of :py:func:`installer.install`)
    determines that two or more sources would be written to the same target
    path under Windows-style case-insensitive path rules.
    """

    def __init__(self, collisions: Iterable[tuple[str, str, str, str]]) -> None:
        """Initialize a CaseCollisionError object.

        :param collisions: iterable of ``(scheme, target_path, first_source,
            second_source)`` tuples, describing each colliding pair of sources.
        """
        self.collisions = list(collisions)
        lines = [
            "Files would collide on a case-insensitive filesystem:",
            *(
                f"  {first} and {second} both target {scheme}:{path}"
                for scheme, path, first, second in self.collisions
            ),
        ]
        super().__init__("\n".join(lines))
