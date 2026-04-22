# AGENTS.md — conda-workspaces coding guidelines

## Project structure

- The package provides two conda subcommands from a single plugin:
  `conda workspace` (environment and workspace management) and
  `conda task` (task execution and management).

- CLI modules are organized into subpackages by subcommand group:
  `conda_workspaces/cli/workspace/` (init, install, list, info, add,
  remove, clean, lock, activate, shell) and
  `conda_workspaces/cli/task/` (run, list, add, remove, export).
  `cli/main.py` contains parser configuration and dispatch for both
  subcommands; `cli/__init__.py` is a thin re-export shim.

- Task-related modules live alongside workspace modules at the
  package root: `models.py` (both workspace and task models),
  `exceptions.py` (all exceptions), `graph.py` (task DAG resolution),
  `runner.py` (shell execution), `template.py` (Jinja2 rendering),
  `cache.py` (task output caching), `context.py` (workspace context
  and template context for tasks).

- Parser implementations use submodules, not subpackages. Each file
  format has a single parser that handles both workspace and task
  parsing: `parsers/toml.py` (conda.toml), `parsers/pixi_toml.py`
  (pixi.toml), `parsers/pyproject_toml.py` (pyproject.toml). Shared
  task normalization: `parsers/normalize.py`. Base class:
  `parsers/base.py` (`ManifestParser`).

- Tests mirror the source structure. Tests for
  `conda_workspaces/cli/workspace/install.py` live in
  `tests/cli/workspace/test_install.py`, tests for
  `conda_workspaces/cli/task/run.py` live in
  `tests/cli/task/test_run.py`, etc. Test module names match their
  corresponding source module names.

## Imports

- Use relative imports for all intra-package references
  (`from .models import WorkspaceConfig`,
  `from ..exceptions import CondaWorkspacesError`,
  `from ...parsers import detect_and_parse_tasks`).
  Absolute `conda_workspaces.*` imports should only appear in tests
  and entry points.

- Inline (lazy) imports are reserved for performance-critical paths
  or optional dependencies that may not be installed. Acceptable
  cases: `plugin.py` hooks (loaded on every `conda` invocation),
  `__main__.py` entry point (defers parser setup until invoked),
  `cli/main.py` subcommand dispatch (only the chosen handler is
  loaded), `context.py` methods (lazy by design to keep plugin load
  under 1 ms), `lockfile.py` solver helpers inside
  `_solve_for_records` and `install_from_lockfile` (avoids pulling
  in heavy solver/envs machinery for lockfile-read operations),
  `template.py` where `_get_jinja_env()` lazily imports jinja2,
  `cli/task/run.py` where workspace context is lazily imported for
  environment resolution (tasks work without a workspace),
  `parsers/toml.py` where `CondaTomlParser.parse()` delegates to
  `PixiTomlParser` (breaks a real circular dependency since
  `pixi_toml` imports helpers from `toml`),
  `cli/workspace/shell.py` where `conda_spawn` is an optional
  dependency, `envs.py` where `conda_pypi` is an optional
  dependency, and `env_spec.py` where heavy workspace parsing and
  lockfile imports are deferred to avoid slowing conda startup
  (environment spec plugins are discovered early). Everywhere else,
  imports belong at the top of the module.

## Dependencies

- Minimize the dependency graph. Prefer stdlib or already-required
  packages over adding new ones. When a single library covers multiple
  use cases (e.g., `tomlkit` for both reading and writing TOML), use
  it instead of carrying separate read-only and read-write libraries.

- Pin minimum versions in `pyproject.toml` dependencies (e.g.,
  `"tomlkit >=0.13"`), not exact versions.

## Typing and linting

- All code must be typed using modern annotations (`str | None` not
  `Optional[str]`, `list[str]` not `List[str]`). `ClassVar` from
  `typing` is the correct annotation for class-level attributes — it
  is not deprecated.

- Use `ty` for type checking and `ruff` for linting and formatting.
  Both are configured in `pyproject.toml`.

- Use `from __future__ import annotations` in all modules.

## Testing

- Tests are plain `pytest` functions — no `unittest.TestCase` or other
  class-based test grouping. Do not group tests in classes; use
  module-level functions with descriptive names.

- Never use `unittest.mock`, `MagicMock`, `patch`, `Mock`, or any other
  `mock` library. Use `pytest` native fixtures (`tmp_path`,
  `monkeypatch`, `capsys`, `tmp_path_factory`) and real fakes. Build
  small local classes or `monkeypatch.setattr` with recording closures
  when a test needs to observe calls; do not reach for the `mock`
  package even as a last resort.

- Do not use section comments (e.g., `# --- Section ---`,
  `# -- Feature X ---`) to group tests. Rely on function naming and
  module structure for organization. If a file feels like it wants
  section headers, it should probably be split into multiple test
  modules.

- Use `pytest.mark.parametrize` extensively. When multiple test cases
  exercise the same logic with different inputs, consolidate them into
  a single parameterized test with `ids=[...]` for readable output.
  Always check whether a new test can be expressed as a new parameter
  case on an existing test before writing a standalone function. Stack
  multiple `@pytest.mark.parametrize` decorators to cross-product
  independent axes (e.g. `add` vs `remove` × flag combinations).

- Put shared setup in fixtures, not in repeated inline code. Fixtures
  that return recording closures / call logs (for asserting what a
  stubbed function was called with) are the preferred alternative to
  `mock`. Shared fixtures belong in `conftest.py` at the appropriate
  level (root `tests/conftest.py` for cross-cutting fixtures,
  subdirectory `conftest.py` for module-specific ones).

- After adding or modifying tests or production code, always run the
  full test suite (`pixi run -e test pytest`) **and** both
  `pixi run ruff check` and `pixi run ruff format --check` to verify
  the changes pass before considering the work done. Fix any lint or
  formatting issues introduced by the changes; do not leave them for
  CI to catch.

- Coverage is measured with `pytest-cov`. Thresholds and exclusions are
  configured in `pyproject.toml` under `[tool.coverage.*]`. Run
  `pixi run -e <test-env> test-cov` to generate a coverage report.

## Lockfile maintenance

- After any change to `pyproject.toml` that affects pixi metadata
  (dependencies, features, tasks, or workspace settings), always run
  `pixi lock` and commit the updated `pixi.lock` alongside the
  `pyproject.toml` change. CI will fail if the lockfile is out of date.

## Conda integration

- Workspace subcommands use `-e`/`--environment` for environment
  targeting. Task subcommands also use `-e`/`--environment` to select
  which workspace environment to run in (aligned with pixi).

- Use conda's own APIs where available (e.g., `conda.base.constants`,
  `conda.base.context.context`, `context.plugins.raw_data` for
  `.condarc` settings) rather than reimplementing platform detection or
  config parsing.

- The `add_output_and_prompt_options` helper from conda already
  provides `--json`, `--dry-run`, `--yes`, `-v`, `-q`, `--debug`,
  `--trace`, and `--console`. Do not add custom arguments that
  duplicate these — it causes `ArgumentError: conflicting option
  string` at runtime.

- The plugin registers via `pluggy` hooks (`conda_subcommands`,
  `conda_settings`, `conda_pre_commands`) and the
  `[project.entry-points.conda]` entry point.

## Parser search order

- The parser registry searches for workspace manifests in this order:
  1. `conda.toml` — conda-native workspace manifest
  2. `pixi.toml` — pixi-native format (compatibility)
  3. `pyproject.toml` — embedded under `[tool.conda.*]` or
     `[tool.pixi.*]`

- Task file search order:
  1. `conda.toml` — conda-native task manifest
  2. `pixi.toml` — pixi-native format (task compatibility)
  3. `pyproject.toml` — embedded task definitions

- Each parser produces a `WorkspaceConfig` via `parse()` and
  `dict[str, Task]` via `parse_tasks()`. Parser-specific logic stays
  in the parser; downstream code only depends on the models.

## CLI architecture

- All task execution goes through `conda task run`. There is no
  `conda workspace run` — it was consolidated to avoid confusion.
  `conda workspace shell` remains for interactive sessions.

- `conda task run` handles both named tasks (from the manifest) and
  ad-hoc shell commands (fallback when the name is not a known task).
  `--templated` enables Jinja2 rendering for ad-hoc commands.

- `conda workspace list` shows packages in an environment.
  `conda workspace envs` shows environments. These were split to
  avoid the confusing `-e` vs `--envs` overload.

- `conda task export` exports tasks to `conda.toml` format. This is
  a conda-specific feature with no pixi equivalent.

## Documentation

- Docs use Sphinx with `conda-sphinx-theme`, `myst-parser`, and
  `sphinx-design`.

- Follow the Diataxis framework: tutorials, how-to guides, reference,
  and explanation sections.

- Key relationship with pixi must be documented prominently:
  `conda-workspaces` reads pixi-compatible manifests but delegates
  solving and environment management to conda's own infrastructure.
  It does not replace pixi — it brings workspace management and task
  execution into the conda CLI.

- Avoid excessive bold and italic in prose, list items, and headings.
  Don't bold every list item or key term — let the text speak for
  itself. In docstrings, use `*param*` for parameter names (standard
  Sphinx convention) but avoid bold elsewhere.

- Keep `sphinx-design` tab labels short. Use "pixi.toml" / "TOML" /
  "pyproject.toml" instead of verbose labels when the tab content
  already identifies the format. This prevents tab overflow on narrow
  viewports.

- The API reference is split into focused sub-pages by concern (models,
  parsers, resolver, context, environments, tasks) rather than a single
  monolithic page. The index uses `sphinx-design` grid cards for
  navigation.
