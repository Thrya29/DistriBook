import re

import distribook


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", distribook.__version__)
