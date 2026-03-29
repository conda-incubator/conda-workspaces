"""Sphinx configuration for conda-workspaces documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = html_title = "conda-workspaces"
copyright = "2025, Jannis Leidel"
author = "Jannis Leidel"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_reredirects",
    "sphinx_sitemap",
    "sphinxarg.ext",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
]

html_theme = "conda_sphinx_theme"

html_theme_options = {
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/conda-incubator/conda-workspaces",
            "icon": "fa-brands fa-square-github",
            "type": "fontawesome",
        },
    ],
}

html_context = {
    "github_user": "conda-incubator",
    "github_repo": "conda-workspaces",
    "github_version": "main",
    "doc_path": "docs",
}

html_static_path = ["_static"]
html_extra_path = ["../demos"]
html_css_files = ["css/custom.css"]

html_baseurl = "https://conda-incubator.github.io/conda-workspaces/"

exclude_patterns = ["_build"]
