"""Tests for the manifest schema.

The schema is the contract every target and every command reads, so these
tests check the failures that matter: a misspelled key, a missing target, and
a target that is not implemented.
"""

from __future__ import annotations

import textwrap

import pytest

from appctl.config import load
from appctl.errors import ConfigError
from appctl.targets import TargetError
from appctl.targets import registry


def write(tmp_path, body: str):
    path = tmp_path / "app.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


BASE = """
    version: 1
    applications:
      example-app:
        description: sample
        target:
          kind: proxmox_lxc
          options:
            hostname: example-app
        source:
          kind: package
          package: "example-app==1.0.0"
"""


def test_loads_an_application(tmp_path):
    cfg = load(write(tmp_path, BASE))
    app = cfg.app("example-app")
    assert app.target.kind == "proxmox_lxc"
    assert app.target.options["hostname"] == "example-app"
    assert app.source.package == "example-app==1.0.0"


def test_rejects_an_unknown_key(tmp_path):
    body = BASE.replace(
        "        description: sample\n",
        "        description: sample\n        guest:\n          hostname: x\n",
    )
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "guest" in str(excinfo.value)


def test_requires_a_target(tmp_path):
    body = BASE.replace(
        """        target:
          kind: proxmox_lxc
          options:
            hostname: example-app
""",
        "",
    )
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "target" in str(excinfo.value)


def test_rejects_an_unknown_top_level_key(tmp_path):
    body = "version: 1\nappications: {}\n"
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "appications" in str(excinfo.value)


def test_target_options_are_kept(tmp_path):
    body = BASE.replace(
        "            hostname: example-app\n",
        "            hostname: example-app\n            vmid: 121\n",
    )
    cfg = load(write(tmp_path, body))
    assert cfg.app("example-app").target.options["vmid"] == 121


TOOLCHAIN = """
    version: 1
    applications:
      builder:
        description: the build host
        target:
          kind: proxmox_lxc
          options:
            hostname: builder
        source:
          kind: toolchain
"""


class TestToolchainSource:
    """The third source kind.

    A toolchain application installs nothing from anywhere: it IS the
    environment another application is built in. These tests cover the three
    things that distinguish it from the other two kinds.
    """

    def test_a_toolchain_loads_without_a_package_or_a_repo(self, tmp_path):
        cfg = load(write(tmp_path, TOOLCHAIN))
        app = cfg.app("builder")
        assert app.source.kind == "toolchain"
        assert app.source.package is None
        assert app.source.repo is None

    def test_a_toolchain_needs_no_build_block(self, tmp_path):
        # The other kind that installs nothing — a package — also needs no
        # build block, so this is the case that would break if the schema
        # ever started requiring one for every source.
        cfg = load(write(tmp_path, TOOLCHAIN))
        assert cfg.app("builder").build is None

    def test_a_toolchain_needs_no_build(self, tmp_path):
        assert load(write(tmp_path, TOOLCHAIN)).app("builder").needs_build is False

    def test_a_toolchain_reports_its_kind_as_its_label(self, tmp_path):
        # The label is what `appctl list` prints. A toolchain has no package
        # and no repo, so printing either would look like a missing value
        # rather than a category.
        cfg = load(write(tmp_path, TOOLCHAIN))
        assert cfg.app("builder").source.label == "toolchain"

    def test_an_unknown_kind_names_the_known_ones(self, tmp_path):
        body = TOOLCHAIN.replace("kind: toolchain", "kind: toolbox")
        with pytest.raises(ConfigError) as excinfo:
            load(write(tmp_path, body))
        message = str(excinfo.value)
        assert "toolchain" in message
        assert "package" in message
        assert "repo" in message

    def test_a_repo_source_still_needs_a_build_block(self, tmp_path):
        # The toolchain kind must not have loosened this for everybody.
        body = """
            version: 1
            applications:
              from-source:
                description: sample
                target:
                  kind: proxmox_lxc
                  options:
                    hostname: sample
                source:
                  kind: repo
                  repo: /tmp/checkout
        """
        with pytest.raises(ConfigError) as excinfo:
            load(write(tmp_path, body))
        assert "build" in str(excinfo.value)


class TestRegistry:
    def test_proxmox_lxc_is_implemented(self):
        assert "proxmox_lxc" in registry.available_targets()

    def test_planned_targets_are_not_implemented(self):
        for kind in ("vm", "docker_vm"):
            assert not registry.is_implemented(kind)
            assert kind in registry.unimplemented_targets()

    def test_resolve_returns_the_target(self):
        target = registry.resolve("proxmox_lxc")
        assert target.kind == "proxmox_lxc"
        assert target.capabilities

    def test_a_planned_target_fails_before_anything_runs(self):
        with pytest.raises(TargetError) as excinfo:
            registry.resolve("vm")
        assert "not implemented" in str(excinfo.value)

    def test_an_unknown_target_names_the_known_ones(self):
        with pytest.raises(TargetError) as excinfo:
            registry.resolve("mainframe")
        assert "proxmox_lxc" in (excinfo.value.hint or "")

    def test_no_target_fails_with_a_message(self):
        with pytest.raises(TargetError) as excinfo:
            registry.resolve("")
        assert "target.kind" in str(excinfo.value)


SHARED_DISK = """
    version: 1
    applications:
      example-app:
        description: sample
        target:
          kind: proxmox_lxc
          options:
            hostname: example-app
        source:
          kind: package
          package: "example-app==1.0.0"
    shared_disks:
      media:
        storage: data2
        size_gb: 1000
        volume: vm-900-disk-0
        filesystem: btrfs
        idmap: "u:1000:101000:1;g:1000:101000:1"
        host_uid: 101000
        trees:
          - /music
          - /movies
"""


def test_loads_a_shared_disk(tmp_path):
    cfg = load(write(tmp_path, SHARED_DISK))
    disk = cfg.shared_disk("media")
    assert disk.storage == "data2"
    assert disk.volume == "vm-900-disk-0"
    assert disk.filesystem == "btrfs"
    assert disk.host_uid == 101000
    assert list(disk.trees) == ["/music", "/movies"]


def test_rejects_a_shared_disk_that_is_not_btrfs(tmp_path):
    body = SHARED_DISK.replace("filesystem: btrfs", "filesystem: ext4")
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "btrfs" in str(excinfo.value)


def test_rejects_a_shared_volume_without_a_conventional_name(tmp_path):
    body = SHARED_DISK.replace("vm-900-disk-0", "shared-media")
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "vm-" in str(excinfo.value)


def test_rejects_a_shared_volume_whose_vmid_is_not_a_number(tmp_path):
    body = SHARED_DISK.replace("vm-900-disk-0", "vm-abc-disk-0")
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "vm-" in str(excinfo.value)


def test_rejects_a_shared_disk_without_an_idmap(tmp_path):
    body = SHARED_DISK.replace(
        '        idmap: "u:1000:101000:1;g:1000:101000:1"\n', ""
    )
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "idmap" in str(excinfo.value)


def test_rejects_an_idmap_that_overlaps_the_container_range(tmp_path):
    # The container's own lxc.idmap covers u 0 100000 65536. A per-mount
    # idmap that overlaps it makes the tree appear as `nobody`, and the
    # writer cannot write to it.
    body = SHARED_DISK.replace(
        '        idmap: "u:1000:101000:1;g:1000:101000:1"',
        '        idmap: "u:0:101000:65536;g:0:101000:65536"',
    )
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "idmap" in str(excinfo.value)


def test_rejects_a_relative_tree_path(tmp_path):
    body = SHARED_DISK.replace("          - /music", "          - music")
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "music" in str(excinfo.value)


def test_rejects_two_disks_claiming_one_volume(tmp_path):
    body = SHARED_DISK.replace(
        "    shared_disks:\n      media:",
        "    shared_disks:\n      other:\n"
        "        storage: data1\n"
        "        size_gb: 100\n"
        "        volume: vm-900-disk-0\n"
        "        filesystem: btrfs\n"
        '        idmap: "u:1000:101000:1;g:1000:101000:1"\n'
        "        host_uid: 101000\n"
        "      media:",
    )
    with pytest.raises(ConfigError) as excinfo:
        load(write(tmp_path, body))
    assert "vm-900-disk-0" in str(excinfo.value)
