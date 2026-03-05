# Contributing to conda-workspaces

Thank you for your interest in improving conda-workspaces! This document
describes how to contribute to the project.

## Code of Conduct

This project follows the [conda Organization Code of Conduct](CODE_OF_CONDUCT.md).
Please read it before participating.

## Getting started

1. Fork the repository on [GitHub](https://github.com/conda-incubator/conda-workspaces).
2. Clone your fork locally.
3. Install [pixi](https://pixi.sh) (used for development environments).
4. Run the tests to make sure everything works:

   ```bash
   pixi run test
   ```

## Development setup

conda-workspaces uses pixi for development environment management. The
available tasks are defined in `pixi.toml`:

```bash
pixi run test          # run tests
pixi run test-cov      # run tests with coverage
pixi run lint          # run ruff linter
pixi run format        # run ruff formatter
pixi run typecheck     # run ty type checker
pixi run docs          # build documentation
```

## Making changes

### Branch workflow

1. Create a new branch from `main` for your changes.
2. Keep changes focused on a single issue or feature.
3. Write tests for new functionality.
4. Make sure all tests pass before submitting.

### Code style

- All code must be typed using modern annotations (`str | None`, `list[str]`).
- Use `from __future__ import annotations` in all modules.
- Use relative imports for intra-package references.
- Run `pixi run lint` and `pixi run format` before committing.
- Run `pixi run typecheck` to verify type annotations.

### Testing

- Tests are plain `pytest` functions — no `unittest.TestCase` classes.
- Use `pytest.mark.parametrize` for multiple test cases with the same logic.
- Use `monkeypatch` and native pytest fixtures instead of `unittest.mock`.
- Tests mirror the source structure (e.g., tests for
  `conda_workspaces/cli/install.py` live in `tests/cli/test_install.py`).

### Documentation

- Docs use Sphinx with MyST Markdown.
- Build locally with `pixi run docs`.
- Follow the [Diataxis framework](https://diataxis.fr/) for new content.

## Submitting a pull request

1. Push your branch to your fork.
2. Open a pull request against `main`.
3. Describe what your change does and why.
4. Link any related issues.
5. Make sure CI passes.

## Conda Contributor License Agreement

To contribute to conda ecosystem projects, you need to sign the
[Conda Contributor License Agreement (CLA)](https://conda.io/en/latest/contributing.html#conda-contributor-license-agreement).

## Generative AI

You're welcome to use generative AI tools when contributing. However:

- You are responsible for all of your contributions. Review and understand any
  AI-generated content before including it in a pull request.
- Be prepared to discuss changes during review — do not paste AI responses
  verbatim.
- Make minimal, focused changes that match the existing style and patterns.
- Ensure AI-assisted changes actually fix the underlying problem rather than
  altering tests to make them pass.

Pull requests consisting of unchecked AI-generated content may be closed.

## Getting help

- Open an [issue](https://github.com/conda-incubator/conda-workspaces/issues) for
  bug reports or feature requests.
- Join the [conda community chat](https://conda.zulipchat.com) for questions.
