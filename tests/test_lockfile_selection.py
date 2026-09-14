"""Inspect and select source lockfile data without package-cache access."""

from __future__ import annotations

from copy import deepcopy

import pytest
from conda.core.package_cache_data import ProgressiveFetchExtract
from conda_lockfiles.rattler_lock import v6

from conda_workspaces.exceptions import LockfileIntegrityError
from conda_workspaces.lockfile import CondaLockLoader


@pytest.fixture
def selection_data(monkeypatch: pytest.MonkeyPatch) -> dict:
    def forbid_fetch(*args, **kwargs):
        pytest.fail("Lockfile inspection and selection must not fetch packages")

    monkeypatch.setattr(ProgressiveFetchExtract, "execute", forbid_fetch)
    monkeypatch.setattr(v6, "records_from_conda_urls", forbid_fetch)
    channel = "https://conda.anaconda.org/conda-forge"
    records = [
        {
            "conda": f"{channel}/{subdir}/{name}-1.0-h0_0.conda",
            "sha256": digest * 64,
            "md5": digest * 32,
            "depends": [],
            "license": "BSD-3-Clause",
            "timestamp": 1726000000000,
            "source-metadata": {"keep": True},
        }
        for subdir, name, digest in (
            ("linux-64", "linux-package", "a"),
            ("osx-arm64", "osx-package", "b"),
            ("noarch", "shared-package", "c"),
        )
    ]
    refs = [{"conda": record["conda"]} for record in records]
    return {
        "version": 1,
        "source-metadata": {"keep": [1, 2]},
        "environments": {
            "default": {
                "channels": [{"url": channel, "source-metadata": "channel"}],
                "packages": {"linux-64": [refs[0], refs[2]]},
            },
            "test": {
                "channels": [{"url": channel}],
                "source-metadata": {"keep": "environment"},
                "packages": {
                    "linux-cuda": [refs[0], refs[2]],
                    "osx-arm64": [refs[1], refs[2]],
                    "portable": [refs[2]],
                    "empty": [],
                },
            },
        },
        "packages": records,
    }


def test_selection_preserves_source_metadata_and_input(selection_data: dict) -> None:
    original = deepcopy(selection_data)
    loader = CondaLockLoader("conda.lock", data=selection_data)

    selected = loader.select({"test": ("linux-cuda", "portable")})

    assert loader.available_environments == ("default", "test")
    assert loader.platforms_for("test") == (
        "empty",
        "linux-cuda",
        "osx-arm64",
        "portable",
    )
    expected = deepcopy(original)
    expected["environments"].pop("default")
    expected["environments"]["test"]["packages"].pop("osx-arm64")
    expected["environments"]["test"]["packages"].pop("empty")
    expected["packages"].pop(1)
    assert selected == expected
    assert selection_data == original
    selected["source-metadata"]["keep"].append(3)
    assert loader.select({"test": ("linux-cuda", "portable")}) == expected


@pytest.mark.parametrize(
    ("target", "subdir"),
    [
        ("linux-cuda", "linux-64"),
        ("osx-arm64", "osx-arm64"),
        ("portable", None),
        ("empty", None),
    ],
)
def test_selection_discovers_concrete_and_logical_targets(
    selection_data: dict, target: str, subdir: str | None
) -> None:
    loader = CondaLockLoader("conda.lock", data=selection_data)

    assert loader.package_platform_for(target, "test") == subdir


def test_metadata_environment_reads_named_only_selection(selection_data: dict) -> None:
    loader = CondaLockLoader("conda.lock", data=selection_data)
    selected = loader.select({"test": ("linux-cuda",)})
    selected["packages"][0]["build_number"] = 17
    selected["environments"]["unrelated"] = None

    env = CondaLockLoader("conda.lock", data=selected).env_for(
        "linux-cuda", name="test", package_platform="linux-64", metadata_only=True
    )

    assert env.name == "test"
    assert env.platform == "linux-64"
    assert getattr(env, "lock_platform") == "linux-cuda"
    assert [record.url for record in env.explicit_packages] == [
        record["conda"] for record in selected["packages"]
    ]
    assert env.explicit_packages[0].build_number == 17
    assert env.explicit_packages[0].dump()["timestamp"] == 1726000000000
    assert env.explicit_packages[0].sha256 == "a" * 64


@pytest.mark.parametrize(
    "selections",
    [
        pytest.param({}, id="empty"),
        pytest.param({"unknown": ("linux-64",)}, id="unknown-environment"),
        pytest.param({"test": ("unknown",)}, id="unknown-target"),
        pytest.param({"test": ()}, id="empty-targets"),
        pytest.param({"test": "linux-cuda"}, id="string-targets"),
        pytest.param({"test": (None,)}, id="invalid-target"),
        pytest.param({"test": None}, id="invalid-target-collection"),
    ],
)
def test_selection_rejects_invalid_selections(selection_data: dict, selections) -> None:
    with pytest.raises(ValueError):
        CondaLockLoader("conda.lock", data=selection_data).select(selections)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda d: d.update(version=2), id="unsupported-version"),
        pytest.param(lambda d: d.update(environments=[]), id="environment-list"),
        pytest.param(
            lambda d: d["environments"].update(test=None), id="invalid-environment"
        ),
        pytest.param(
            lambda d: d["environments"]["test"].update(packages=[]),
            id="invalid-target-map",
        ),
        pytest.param(
            lambda d: d["environments"]["test"]["packages"].update(
                **{"linux-cuda": {}}
            ),
            id="invalid-target-refs",
        ),
        pytest.param(
            lambda d: d["environments"]["test"].update(channels="conda-forge"),
            id="invalid-channels",
        ),
        pytest.param(lambda d: d.update(packages={}), id="invalid-records"),
        pytest.param(
            lambda d: d["environments"]["test"]["packages"]["linux-cuda"].append(
                {"pypi": "https://example.test/pkg.whl"}
            ),
            id="external-ref",
        ),
        pytest.param(
            lambda d: d["environments"]["test"]["packages"]["linux-cuda"].append(None),
            id="invalid-ref",
        ),
        pytest.param(lambda d: d["packages"].pop(0), id="missing-record"),
        pytest.param(
            lambda d: d["packages"][0].update(sha256="bad"), id="invalid-digest"
        ),
        pytest.param(
            lambda d: d["packages"][0].update(pypi=d["packages"][0].pop("conda")),
            id="external-package-record",
        ),
        pytest.param(
            lambda d: d["packages"][0].update(url="https://example.test/other.conda"),
            id="conflicting-source-url",
        ),
        pytest.param(
            lambda d: d["environments"]["test"].update(
                channels=[{"url": "https://example.test/other"}]
            ),
            id="off-channel",
        ),
        pytest.param(
            lambda d: d["environments"]["test"]["packages"]["linux-cuda"].append(
                {"conda": d["packages"][1]["conda"]}
            ),
            id="mixed-subdirs",
        ),
    ],
)
def test_selection_rejects_malformed_or_unverifiable_data(
    selection_data: dict, mutation
) -> None:
    mutation(selection_data)

    with pytest.raises((ValueError, LockfileIntegrityError)):
        CondaLockLoader("conda.lock", data=selection_data).select(
            {"test": ("linux-cuda",)}
        )


@pytest.mark.parametrize("field", ["name", "version", "build", "subdir", "fn"])
def test_selection_rejects_conflicting_package_identity(
    selection_data: dict, field: str
) -> None:
    selection_data["packages"][0][field] = "disagrees"

    with pytest.raises(ValueError, match="disagrees"):
        CondaLockLoader("conda.lock", data=selection_data).select(
            {"test": ("linux-cuda",)}
        )


def test_selection_rejects_package_subdir_different_from_target(
    selection_data: dict,
) -> None:
    selection_data["environments"]["test"]["packages"]["osx-arm64"] = [
        {"conda": selection_data["packages"][0]["conda"]}
    ]

    with pytest.raises(ValueError, match="does not match target"):
        CondaLockLoader("conda.lock", data=selection_data).select(
            {"test": ("osx-arm64",)}
        )


def test_selection_rejects_unknown_package_subdir(selection_data: dict) -> None:
    package = selection_data["packages"][0]
    package["conda"] = package["conda"].replace("/linux-64/", "/unknown-subdir/")
    selection_data["environments"]["test"]["packages"]["unknown-subdir"] = [
        {"conda": package["conda"]}
    ]

    with pytest.raises(ValueError, match="supported conda subdir"):
        CondaLockLoader("conda.lock", data=selection_data).package_platform_for(
            "unknown-subdir", "test"
        )
