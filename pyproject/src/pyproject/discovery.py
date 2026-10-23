"""Project and workspace discovery, with no Dagger calls.

Every directory holding a `pyproject.toml` is a project, and every file belongs
to the deepest project containing it. Every directory holding a workspace
marker (a lockfile by default) is a workspace: the environment root that
installs its members and their local dependencies.

`discover` takes what the Dagger layer read from the workspace (project roots,
manifest text, which markers each workspace root holds) and works out the rest,
so it can be unit-tested without an engine. Paths are workspace-root-relative,
`.` for the root.
"""

import posixpath
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

import pathspec

# Never part of any project's input: caches, virtualenvs, VCS metadata and build output.
SOURCE_EXCLUDE = [
    "**/__pycache__",
    "**/*.pyc",
    "**/node_modules",
    "**/.venv",
    "**/.tox",
    "**/.nox",
    "**/.git",
    "**/.mypy_cache",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/.direnv",
    "**/.devenv",
    "**/dist",
    "**/build",
    "**/*.egg-info",
]

DEFAULT_WORKSPACE_MARKERS = ["uv.lock", "pylock.toml"]


def normalize_name(name: str) -> str:
    """PEP 503 name normalization: lowercase, collapse [-_.] runs to a single hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def root_relative(cwd: str, path: str) -> str:
    """A workspace-root-relative path for `path`, which is relative to the workspace `cwd`.

    `Workspace.find_roots` answers relative to the cwd (`.` for the cwd itself,
    `../x` for a sibling); every path here is relative to the workspace root so
    it doesn't depend on where `dagger` runs.
    """
    return posixpath.normpath(posixpath.join(cwd.lstrip("/"), path))


def join(base: str, path: str) -> str:
    """`path` resolved against the root-relative directory `base`, normalized."""
    return posixpath.normpath(posixpath.join(base, path))


def under(path: str, root: str) -> bool:
    """Whether root-relative `path` is `root` or below it."""
    return root == "." or path == root or path.startswith(root + "/")


def escapes(path: str) -> bool:
    """Whether a normalized root-relative path points above the workspace root."""
    return path == ".." or path.startswith("../")


def matches(path: str, patterns: Iterable[str]) -> bool:
    """Whether `path` matches any glob in `patterns` (`*` within a segment, `**` across segments).

    A trailing `/**` also matches the directory itself, so `docs/**` selects
    `docs` and everything below it.
    """
    candidate = PurePosixPath(path)
    for pattern in patterns:
        pattern = pattern.removeprefix("./").rstrip("/")
        if candidate.full_match(pattern) or (pattern.endswith("/**") and candidate.full_match(pattern[:-3])):
            return True
    return False


def tree(path: str) -> list[str]:
    """Include patterns covering `path` and everything below it."""
    return ["**"] if path == "." else [path, f"{path}/**"]


def topmost(paths: Iterable[str]) -> list[str]:
    """The paths in `paths` that no other path in it contains."""
    unique = sorted(set(paths))
    return [p for p in unique if not any(q != p and under(p, q) for q in unique)]


class Gitignore:
    """The `.gitignore` files of a workspace, each applying below its own directory.

    `Workspace.find_roots` walks gitignored directories too, on a git-backed
    workspace as much as on a `Directory.as_workspace()` one, so a gitignored
    copy of the repository (e.g. an agent's worktree) would otherwise turn up
    as a second set of projects.
    """

    def __init__(self, files: Mapping[str, str]) -> None:
        """`files` maps each root-relative directory to its `.gitignore` text."""
        self._specs = [
            (directory, pathspec.GitIgnoreSpec.from_lines(text.splitlines()))
            for directory, text in sorted(files.items())
        ]

    def ignores(self, path: str) -> bool:
        """Whether a root-relative path is ignored by any `.gitignore` above it."""
        for directory, spec in self._specs:
            if path != directory and under(path, directory):
                relative = path if directory == "." else path[len(directory) + 1 :]
                if spec.match_file(relative):
                    return True
        return False


@dataclass(frozen=True)
class Source:
    """One `[tool.uv.sources]` entry, as its declaring manifest resolved it."""

    paths: tuple[str, ...]
    """Root-relative `path` targets (one per marker-split alternative)."""
    workspace: bool
    """Whether any alternative is `{ workspace = true }`."""


@dataclass(frozen=True)
class Manifest:
    """The parts of a `pyproject.toml` that decide what a project needs."""

    path: str
    name: str | None
    sources: Mapping[str, Source]
    """`[tool.uv.sources]`, keyed by normalized package name."""
    members: tuple[str, ...] | None
    """`[tool.uv.workspace] members` globs, or None when this is no uv workspace root."""
    member_exclude: tuple[str, ...]

    @classmethod
    def parse(cls, path: str, text: str) -> "Manifest":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            msg = f"{join(path, 'pyproject.toml')}: {exc}"
            raise ValueError(msg) from exc
        uv = data.get("tool", {}).get("uv", {})
        sources: dict[str, Source] = {}
        for name, value in uv.get("sources", {}).items():
            # A source is a table, or a list of tables split by environment markers.
            alternatives = value if isinstance(value, list) else [value]
            sources[normalize_name(name)] = Source(
                paths=tuple(join(path, alt["path"]) for alt in alternatives if "path" in alt),
                workspace=any(alt.get("workspace") is True for alt in alternatives),
            )
        workspace = uv.get("workspace")
        return cls(
            path=path,
            name=data.get("project", {}).get("name"),
            sources=sources,
            members=None if workspace is None else tuple(workspace.get("members", [])),
            member_exclude=() if workspace is None else tuple(workspace.get("exclude", [])),
        )


@dataclass(frozen=True)
class Project:
    path: str
    name: str | None
    workspace: str | None
    nested: tuple[str, ...]
    local_dependencies: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class Workspace:
    path: str
    lockfiles: tuple[str, ...]
    members: tuple[str, ...]
    local_dependencies: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    manifest_include: tuple[str, ...]


class Discovery:
    """Projects and workspaces over one snapshot of a Dagger workspace.

    Manifests are parsed on first use, so a malformed `pyproject.toml` only
    fails discovery when something actually depends on it.
    """

    def __init__(self, manifests: Mapping[str, str], lockfiles: Mapping[str, Iterable[str]]) -> None:
        self._texts = dict(manifests)
        self.project_paths = sorted(self._texts)
        self.workspace_paths = sorted(lockfiles)
        self._lockfiles = {path: tuple(sorted(names)) for path, names in lockfiles.items()}
        # Bound per instance: a functools.cache on the methods would key on `self` and keep it alive.
        self.manifest: Callable[[str], Manifest] = cache(self._manifest)
        self.members: Callable[[str], tuple[str, ...]] = cache(self._members)
        self.workspace_of: Callable[[str], str | None] = cache(self._workspace_of)
        self.closure: Callable[[str], tuple[str, ...]] = cache(self._closure)
        self.project: Callable[[str], Project] = cache(self._project)
        self.workspace: Callable[[str], Workspace] = cache(self._workspace)

    def _manifest(self, path: str) -> Manifest:
        return Manifest.parse(path, self._texts[path])

    def _members(self, root: str) -> tuple[str, ...]:
        """Projects a workspace root installs as members, the root project first.

        A uv workspace (`[tool.uv.workspace]`) has the root plus every project
        below it matching `members` and not `exclude`, as uv decides. Any other
        workspace has just its root project, or none for a bare lockfile.
        """
        if root not in self._texts:
            return ()
        manifest = self.manifest(root)
        if manifest.members is None:
            return (root,)
        below = [p for p in self.project_paths if p != root and under(p, root)]
        rel = {p: posixpath.relpath(p, root) for p in below}
        return (
            root,
            *(p for p in below if matches(rel[p], manifest.members) and not matches(rel[p], manifest.member_exclude)),
        )

    def _workspace_of(self, path: str) -> str | None:
        """The deepest workspace that has the project at `path` as a member."""
        owners = [w for w in self.workspace_paths if under(path, w) and path in self.members(w)]
        return max(owners, key=lambda w: (w != ".", w.count("/")), default=None)

    def _direct_dependencies(self, path: str) -> set[str]:
        """Root-relative paths the project at `path` depends on through `[tool.uv.sources]`.

        A uv workspace member inherits the root's sources, overridden per
        package by its own. `{ workspace = true }` names a member of the same
        workspace. Paths above the workspace root can't be loaded and are left
        out; paths that don't exist stay in, and simply load nothing.
        """
        manifest = self.manifest(path)
        workspace = self.workspace_of(path)
        sources: dict[str, Source] = {}
        if workspace is not None and workspace != path and self.manifest(workspace).members is not None:
            sources.update(self.manifest(workspace).sources)
        sources.update(manifest.sources)
        members = {}
        if workspace is not None:
            for member in self.members(workspace):
                name = self.manifest(member).name
                if name is not None:
                    members[normalize_name(name)] = member
        dependencies: set[str] = set()
        for name, source in sources.items():
            dependencies.update(source.paths)
            if source.workspace and name in members:
                dependencies.add(members[name])
        return {d for d in dependencies if d != path and not escapes(d)}

    def _closure(self, path: str) -> tuple[str, ...]:
        """Every local dependency of the project at `path`, transitively, without `path` itself."""
        seen: set[str] = set()
        pending = [path]
        while pending:
            current = pending.pop()
            for dependency in self._direct_dependencies(current):
                if dependency not in seen and dependency != path:
                    seen.add(dependency)
                    if dependency in self._texts:
                        pending.append(dependency)
        return tuple(sorted(seen))

    def _unrelated_nested(self, loaded: set[str]) -> list[str]:
        """Topmost projects nested in a `loaded` tree that hold nothing in `loaded`.

        A project's files are its own, so a tree loaded for one project leaves
        out every project nested in it that isn't needed. That keeps a monorepo
        root's source from carrying every project below it, and its digest from
        changing with theirs.
        """
        return topmost(
            p for p in self.project_paths if any(under(p, t) for t in loaded) and not any(under(t, p) for t in loaded)
        )

    def _loads(self, root: str, needed: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Include and exclude patterns loading `root` and `needed` from the workspace root."""
        needed = sorted(set(needed))
        include = [*tree(root), *(pattern for path in needed for pattern in tree(path))]
        unrelated = self._unrelated_nested({root, *needed})
        exclude = [*SOURCE_EXCLUDE, *(pattern for path in unrelated for pattern in tree(path))]
        return tuple(dict.fromkeys(include)), tuple(exclude)

    def _project(self, path: str) -> Project:
        closure = self.closure(path)
        include, exclude = self._loads(path, closure)
        return Project(
            path=path,
            name=self.manifest(path).name,
            workspace=self.workspace_of(path),
            nested=tuple(topmost(p for p in self.project_paths if p != path and under(p, path))),
            local_dependencies=tuple(d for d in closure if d in self._texts),
            include=include,
            exclude=exclude,
        )

    def _workspace(self, path: str) -> Workspace:
        members = self.members(path)
        dependencies = sorted({d for m in members for d in self.closure(m)} - set(members))
        include, exclude = self._loads(path, [*members, *dependencies])
        projects = [*members, *(d for d in dependencies if d in self._texts)]
        manifest_include = [
            *(join(path, name) for name in self._lockfiles[path]),
            join(path, "uv.toml"),
            *(join(p, name) for p in projects for name in ("pyproject.toml", ".python-version")),
            # A path source that isn't a project is an archive (or a directory uv
            # builds with a legacy backend): resolution needs the whole thing.
            *(pattern for d in dependencies if d not in self._texts for pattern in tree(d)),
        ]
        return Workspace(
            path=path,
            lockfiles=self._lockfiles[path],
            members=members,
            local_dependencies=tuple(d for d in dependencies if d in self._texts),
            include=include,
            exclude=exclude,
            manifest_include=tuple(dict.fromkeys(manifest_include)),
        )
