import zipfile

from scripts.build_skill import (
    ZIP_PATH,
    build_sample_data,
    build_zip,
    parse_frontmatter,
    validate,
    verify_zip,
)


def test_skill_frontmatter_valid():
    fm = validate()
    assert fm["name"] == "project-manager"
    assert 0 < len(fm["description"]) <= 1024


def test_skill_zip_self_contained():
    build_sample_data()
    path = build_zip()
    names = verify_zip(path)
    assert "project-manager/assets/sample-data/monday-board.json" in names
    assert "project-manager/references/approval-rules.md" in names
    with zipfile.ZipFile(ZIP_PATH) as zf:
        assert parse_frontmatter(zf.read("project-manager/SKILL.md").decode())["name"] == "project-manager"
