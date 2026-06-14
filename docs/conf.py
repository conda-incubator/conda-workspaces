"""Sphinx configuration for conda-workspaces documentation."""

import os
import re
import sys

from docutils import nodes

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

myst_url_schemes = {
    "http": None,
    "https": None,
    "mailto": None,
    "ftp": None,
}

GITHUB_REF_RE = re.compile(r"(?<![\w/])#([0-9]+)\b")
GITHUB_ISSUE_URL = "https://github.com/conda-incubator/conda-workspaces/issues/"


def link_changelog_github_refs(
    app,
    doctree: nodes.document,
    docname: str,
) -> None:
    if docname != "changelog":
        return

    for text_node in list(doctree.findall(nodes.Text)):
        skip_node = False
        parent = text_node.parent
        while parent is not None:
            if isinstance(
                parent,
                (nodes.reference, nodes.literal, nodes.literal_block, nodes.raw),
            ):
                skip_node = True
                break
            parent = parent.parent

        if skip_node:
            continue

        text = text_node.astext()
        matches = list(GITHUB_REF_RE.finditer(text))
        if not matches:
            continue

        replacements: list[nodes.Node] = []
        cursor = 0
        for match in matches:
            if match.start() > cursor:
                replacements.append(nodes.Text(text[cursor : match.start()]))

            ref_text = match.group(0)
            replacements.append(
                nodes.reference(
                    "",
                    ref_text,
                    refuri=f"{GITHUB_ISSUE_URL}{match.group(1)}",
                    classes=["github"],
                )
            )
            cursor = match.end()

        if cursor < len(text):
            replacements.append(nodes.Text(text[cursor:]))

        text_node.parent.replace(text_node, replacements)


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
html_extra_path = ["../demos", "../schema"]
html_css_files = ["css/custom.css"]

html_baseurl = "https://conda-incubator.github.io/conda-workspaces/"

exclude_patterns = ["_build", "superpowers"]


def setup(app):
    app.connect("doctree-resolved", link_changelog_github_refs)
