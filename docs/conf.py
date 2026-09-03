project = "inspect-brief"
author = "Hirundo"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.githubpages",
    "sphinx_multiversion",
]

exclude_patterns = ["_build"]

# Publish one subdirectory per release tag; branches are intentionally excluded
# so the published site only ever shows released documentation.
smv_tag_whitelist = r"^v\d+\.\d+\.\d+$"
smv_branch_whitelist = "None"
smv_remote_whitelist = None
smv_released_pattern = r"^refs/tags/v\d+\.\d+\.\d+$"

html_theme = "furo"
templates_path = ["_templates"]
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/versions.html",
        "sidebar/scroll-end.html",
    ],
}
