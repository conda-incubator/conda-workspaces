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

- Manifest parser implementations live under `manifests/` (the
  directory is named after its subject, not the verb). Each file
  format has a single parser that handles both workspace and task
  parsing: `manifests/toml.py` (conda.toml), `manifests/pixi_toml.py`
  (pixi.toml), `manifests/pyproject_toml.py` (pyproject.toml). Shared
  task normalization: `manifests/normalize.py`. Base class:
  `manifests/base.py` (`ManifestParser`). The `manifests/` package is
  an internal substrate; public plugin API surfaces (`env_spec.py`,
  `lockfile.py`, `env_export.py`) sit at the package root and own one
  format each.

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
  `from ...manifests import detect_and_parse_tasks`).
  Absolute `conda_workspaces.*` imports should only appear in tests
  and entry points.

- Inline (lazy) imports are reserved for performance-critical paths
  or optional dependencies that may not be installed. Acceptable
  cases: `plugin.py` hooks (loaded on every `conda` invocation),
  `__main__.py` entry point (defers parser setup until invoked),
  `cli/main.py` subcommand dispatch (only the chosen handler is
  loaded), `context.py` methods (lazy by design to keep plugin load
  under 1 ms), solver and virtual-package helpers inside
  `ResolvedEnvironment` (`solve_for_platform`,
  `virtual_package_overrides`, `scoped_virtual_packages`) and
  `lockfile.install_from_lockfile` (avoids pulling in heavy
  solver/envs machinery for lockfile-read operations),
  `template.py` where `_get_jinja_env()` lazily imports jinja2,
  `cli/task/run.py` where workspace context is lazily imported for
  environment resolution (tasks work without a workspace),
  `manifests/toml.py` where `CondaTomlParser.parse()` delegates to
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

## Code structure

- Prefer methods on existing classes over module-level private
  helpers. When a helper is parameterized by data that already lives
  on a class (a `ResolvedEnvironment`'s `system_requirements`, a
  `WorkspaceConfig`'s `platforms`, a `ManifestParser`'s `filenames`,
  etc.), promote it to a method on that class instead of adding a
  `_foo(data, extra)` free function. Call sites read as
  `obj.foo(platform)` instead of `_foo(platform, obj.attr)`, tests
  instantiate the class directly instead of threading loose arguments,
  and the public surface stays discoverable. The canonical examples
  are `ResolvedEnvironment.baseline_virtual_package_env` /
  `scoped_solver_env` in `resolver.py` (moved out of `lockfile.py`
  private helpers) and `ManifestParser.for_format` /
  `resolve_source` / `copy_manifest` in `manifests/base.py` (moved
  out of `cli/workspace/quickstart.py` private helpers).

- Before adding a new private module-level helper, check in order:
  1. Does conda (or another existing dependency like
     `conda_lockfiles`) already expose this? Use that instead of
     re-implementing parts of conda.
  2. Does an existing dataclass or plugin class in `conda_workspaces`
     own the data the helper reads? Put it there as a method.
  3. If it is genuinely generic and reused across modules, make it a
     public module-level function with a docstring explaining why it
     is not on a class.
  4. If it is called exactly once, inline it at the call site.

- Do not use section comments (`# --- Helpers ---`,
  `# === Public API ===`, `# -- Feature X ---`) to group functions,
  classes, or tests inside a module. Rely on ordering and module
  boundaries. If a file feels like it wants section headers, split it
  into multiple modules. `env_export.py` becoming `export.py` with
  the CLI shim stripped out is the canonical example.

- Comments should explain non-obvious intent, trade-offs, or
  constraints the code cannot convey on its own (why a
  `context._override` is used, why a fetch is network-bound, why a
  helper exists despite an upstream deprecation). Do not narrate what
  the code already says (`# increment counter`, `# build the dict`,
  `# handle the error`). Docstrings are the right place for the
  *why*; inline comments are a last resort.

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
  manifests, resolver, context, environments, tasks) rather than a
  single monolithic page. The index uses `sphinx-design` grid cards
  for navigation.

## Pull request and issue descriptions

- GitHub renders Markdown with GitHub Flavored Markdown (GFM), which
  does **not** reflow hand-wrapped prose the way CommonMark does in
  plain text. Hard-wrapping a paragraph at ~72 columns inside a list
  item, blockquote, or bullet produces ragged line breaks on the PR
  page because GFM treats the extra indentation as continuation and
  keeps the physical newlines visible. Write PR and issue bodies
  (titles, summaries, bullet lists, test plans) as **one line per
  paragraph or bullet** — let GitHub wrap them in the browser. Reserve
  hard wraps for code fences, tables, and CLI help text where column
  width actually matters.

- Keep the same rule for commit messages only in the *subject line*
  (~72 chars). Commit bodies can use hand-wrapped paragraphs because
  they're read in terminals via `git log`, not rendered through GFM.

- When passing bodies through `gh pr create` / `gh issue create`, use
  a heredoc (`--body "$(cat <<'EOF' ... EOF)"`) and write each bullet
  on a single line. Do not pre-wrap.

## Plugin design

- Each public plugin module at the package root owns exactly one
  format and exposes its metadata as module-level `Final` constants
  (`FORMAT`, `ALIASES`, `DEFAULT_FILENAMES`). `plugin.py` imports those
  constants; it does not duplicate them. The canonical `FORMAT` name
  is versioned when the on-disk schema has a version byte
  (`conda-workspaces-lock-v1`); unversioned short forms live in
  `ALIASES` so that users can type the convenient name while the
  versioned name stays stable across future schema bumps. See
  `docs/reference/format-aliases.md`.

- When an upstream plugin/loader we depend on already owns a schema,
  build a sibling loader that shares its validation + converters
  rather than re-implementing parsing. `CondaLockLoader` in
  `lockfile.py` is the canonical example: `conda.lock` is a
  derivative of rattler-lock v6 (`pixi.lock`), so the loader composes
  `conda_lockfiles.rattler_lock.v6`'s conversion helper via an
  in-memory `version: 1 -> 6` swap instead of re-implementing YAML ->
  `Environment` conversion.

- Cross-platform solving (multi-platform `conda.lock`): target each
  declared platform by (a) constructing the solver with
  `subdirs=(platform, "noarch")` and (b) overriding `context._subdir`
  for the duration of the solve. Conda's virtual package plugins gate
  on `context.subdir`, so this single override handles both the
  repodata lookup and the `__linux` / `__osx` / `__win` virtual
  packages consistently. Do not try to suppress host virtuals
  manually; that path is already covered. `CONDA_OVERRIDE_*` and the
  manifest `[system-requirements]` table stay the user-facing knobs
  for virtual package versions.
