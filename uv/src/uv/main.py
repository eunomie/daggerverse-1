import asyncio
from typing import Annotated, Self

from dagger import Doc, collection, function, get, keys, object_type
from dagger_clients.core import Workspace
from dagger_clients.pyproject import pyproject

from uv.args import WorkspacePath
from uv.workspace import UvWorkspaceSource

# A uv workspace is a directory holding a uv.lock.
_WORKSPACE_MARKERS = ["uv.lock"]


def _lookup(items: list[UvWorkspaceSource], path: str) -> UvWorkspaceSource:
    for item in items:
        if item.path == path:
            return item
    msg = f"no uv workspace (uv.lock) at {path!r}"
    raise ValueError(msg)


@object_type
@collection
class UvWorkspaces:
    """Every uv workspace in the source tree (one per uv.lock), keyed by its path.

    `audit` runs once per selected workspace, so each workspace is its own check
    that can be selected or skipped on its own.
    """

    paths: Annotated[
        list[str],
        Doc("uv workspace root paths (holding uv.lock), relative to the workspace root. `.` for the root."),
    ] = keys()

    items: list[UvWorkspaceSource]

    @function
    @get
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """The uv workspace rooted at `path`."""
        return _lookup(self.items, path)


@object_type
class Uv:
    """Entrypoint for the `uv` module.

    Hands out one `UvWorkspaceSource` per uv workspace, as the `pyproject`
    module discovers them.
    Use `workspace` to grab a single workspace by path or `workspaces` to get them all.

    Learn more in the [docs](https://daggerverse.docs.typesafe.ai/uv/)."""

    items: list[UvWorkspaceSource]

    @classmethod
    async def create(
        cls,
        ws: Workspace,
        exclude: Annotated[
            list[str] | None,
            Doc("Glob patterns of workspace paths to leave out of workspaces, e.g. **/tests/_packages/**."),
        ] = None,
    ) -> Self:
        found = await pyproject(ws, exclude=exclude, workspace_markers=_WORKSPACE_MARKERS).workspaces().list_()
        paths = await asyncio.gather(*(workspace.path() for workspace in found))
        return cls(
            items=[
                UvWorkspaceSource(path=path, source=workspace.source(ws), manifests=workspace.manifests(ws))
                for path, workspace in zip(paths, found, strict=True)
            ]
        )

    @function
    def workspace(self, path: WorkspacePath) -> UvWorkspaceSource:
        """A single uv workspace at `path`, relative to the workspace root.

        The returned `UvWorkspaceSource` can `audit` its locked dependencies or
        `build` a minimal container for a package.
        """
        return _lookup(self.items, path)

    @function
    def workspaces(self) -> UvWorkspaces:
        """Every uv workspace in the source tree (one per uv.lock), less those matching `exclude`."""
        return UvWorkspaces(paths=[item.path for item in self.items], items=self.items)
