from typing import Annotated, Self

from dagger import Doc, field, function, object_type
from dagger_clients.core import Container, Directory, Workspace, core

from ruff.args import SOURCE_EXCLUDE, VERSION_FILES
from ruff.checker import RuffChecker
from ruff.formatter import RuffFormatter
from ruff.utils import _DEFAULT_IMAGE, resolve_version


@object_type
class Ruff:
    """Ruff Python linter and formatter."""

    ctr: Annotated[
        Container,
        Doc("Container with ruff installed."),
    ] = field()

    source: Annotated[
        Directory,
        Doc("Source tree: the Dagger workspace, less caches, virtualenvs and gitignored files."),
    ] = field()

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        ctr: Annotated[
            Container | None,
            Doc("Container with ruff installed. Defaults to the official ghcr.io/astral-sh/ruff image."),
        ] = None,
        version: Annotated[
            str | None,
            Doc("Ruff image tag. Only used when `ctr` is not provided. Defaults to the version the workspace pins."),
        ] = None,
    ) -> Self:
        if ctr is None:
            if version is None:
                version = await resolve_version(ws.directory("/", include=VERSION_FILES))
            ctr = core().container().from_(f"{_DEFAULT_IMAGE}:{version}")
        return cls(ctr=ctr, source=ws.directory("/", exclude=SOURCE_EXCLUDE, gitignore=True))

    @function(cache="1h")
    async def version(self) -> str:
        """The resolved ruff version."""
        return (await self.ctr.with_exec(["/ruff", "version"]).stdout()).strip()

    @function
    def check(self) -> RuffChecker:
        """Return the ruff linter (`ruff check`)."""
        return RuffChecker(ctr=self.ctr, source=self.source)

    @function
    def format(self) -> RuffFormatter:
        """Return the ruff formatter (`ruff format`)."""
        return RuffFormatter(ctr=self.ctr, source=self.source)
