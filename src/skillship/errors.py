"""Errors the command line reports as a message, with an exit status."""

from __future__ import annotations


class SkillshipError(Exception):
    """A problem with the input: the profile, the lock or the source repo. Exit status 1."""

    exit_code = 1


class NotImplementedYet(SkillshipError):
    """A valid profile asks for something this version cannot do yet. Exit status 2."""

    exit_code = 2
