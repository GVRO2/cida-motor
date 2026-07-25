import pytest

from cida.domain.errors import UnsupportedFrontmatterSyntaxError
from cida.infrastructure.frontmatter_codec import FrontmatterCodec


SUPPORTED_BMAD_FRONTMATTERS = [
    (
        "---\n"
        'title: "Workflow BMAD"\n'
        "owner: docs\n"
        "tags:\n"
        "  - stable\n"
        "  - production\n"
        "metadata:\n"
        "  path: docs/workflow.md\n"
        "  enabled: true\n"
        "---\n",
        {
            "title": "Workflow BMAD",
            "owner": "docs",
            "tags": ["stable", "production"],
            "metadata": {"path": "docs/workflow.md", "enabled": True},
        },
    ),
    (
        "---\n"
        "items:\n"
        "  - name: discovery\n"
        "    enabled: true\n"
        "    metadata:\n"
        "      path: docs/discovery.md\n"
        "      tags:\n"
        "        - stable\n"
        "        - production\n"
        "  -\n"
        "    name: delivery\n"
        "    enabled: false\n"
        "---\n",
        {
            "items": [
                {
                    "name": "discovery",
                    "enabled": True,
                    "metadata": {"path": "docs/discovery.md", "tags": ["stable", "production"]},
                },
                {"name": "delivery", "enabled": False},
            ]
        },
    ),
]


@pytest.mark.parametrize(("frontmatter", "expected"), SUPPORTED_BMAD_FRONTMATTERS)
def test_bmad_supported_frontmatter_subset(frontmatter: str, expected: dict):
    assert FrontmatterCodec().parse_frontmatter_safe(frontmatter) == expected


@pytest.mark.parametrize(
    "frontmatter",
    [
        "---\nbase: &base value\ncopy: *base\n---\n",
        "---\ntext: |\n  block scalar\n---\n",
        "---\nitem: {name: inline-map}\n---\n",
    ],
)
def test_bmad_unsupported_frontmatter_subset_is_rejected(frontmatter: str):
    with pytest.raises(UnsupportedFrontmatterSyntaxError):
        FrontmatterCodec().parse_frontmatter_safe(frontmatter)
