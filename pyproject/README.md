# pyproject

Dagger module that discovers the Python projects (`pyproject.toml`) and workspaces (`uv.lock`, `pylock.toml`, or any marker you configure) in a Dagger workspace, and loads exactly the source each one needs.

## Installation

```sh
dagger install github.com/typesafe-ai/daggerverse/pyproject
```

## Usage

```sh
dagger -c 'pyproject | workspaces | keys'
dagger -c 'pyproject | projects | get services/api | source | entries'
```

Learn more in [docs](https://daggerverse.docs.typesafe.ai/pyproject).
