# Changelog

All notable changes to conda-workspaces will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

## 0.2.0 — 2026-03-30

### Added

- `conda task` subcommand with `run`, `list`, `add`, `remove`, and `export`
- `conda workspace run` command for one-shot execution in environments
- Task dependencies with topological ordering (`depends-on`)
- Jinja2 template support in task commands (`{{ conda.platform }}`, conditionals)
- Task output caching with input/output file declarations
- Per-platform task overrides via `[target.<platform>.tasks]`
- Task arguments with default values
- Rich terminal output for all CLI commands (tables, status, errors)
- Structured error rendering with actionable hints
- Integration tests for CLI workflows
- Demo recordings for terminal screencasts

### Changed

- Verb-based status messages (Installing, Installed, etc.) replace
  symbol-based markers
- All CLI output routed through Rich console for consistent formatting
- Documentation standardized to use `conda workspace` / `conda task`
  as primary CLI forms (`cw` / `ct` noted as aliases)
- Aligned parsers with pixi workspace semantics for broader manifest
  compatibility
- Exception hierarchy expanded with type annotations and actionable hints

### Fixed

- JSON output in `conda task list --json` no longer includes ANSI escapes
- Activation script handling on Windows uses correct path validation
- Solver output noise suppressed during lockfile generation
- Stdout flushed after conda solver and transaction API calls

## 0.1.1 — 2026-03-05

### Changed

- Transferred repository to conda-incubator organization
- Added PyPI release workflow with trusted publishing
- Moved changelog to repository root

## 0.1.0 — 2026-03-05

### Added

- Initial implementation of conda-workspaces plugin
- `conda workspace` subcommand with `init`, `install`, `list`, `info`,
  `add`, `remove`, `clean`, `run`, and `activate` subcommands
- `conda workspace` standalone CLI (also available as `cw`)
- Parser support for `pixi.toml`, `conda.toml`, and `pyproject.toml`
  manifests
- Multi-environment workspace model with composable features
- Solve-group support for version coordination across environments
- Per-platform dependency overrides via `[target.<platform>]`
- PyPI dependency parsing (requires conda-pypi for installation)
- Project-local environments under `.conda/envs/`
- Sphinx documentation with conda-sphinx-theme
- PyPI release workflow with trusted publishing
