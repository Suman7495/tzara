"""Errors raised while setting Tzara up."""


class SetupError(RuntimeError):
    """A setup step failed. The message says what happened and what to do about it."""


class AppNotPublished(SetupError):
    """There is no app build for this version of Tzara (yet).

    Setup carries on without the app instead of failing: everything else it set up works.
    """
