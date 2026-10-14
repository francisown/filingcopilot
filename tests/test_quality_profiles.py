import stat
from pathlib import Path

import pytest

from scripts.run_quality_checks import python_commands, remove_tree


@pytest.mark.parametrize('profile', ['unit', 'fast'])
def test_unit_gate_and_legacy_alias_use_the_same_registered_marker(profile):
    commands = python_commands(profile, Path('unused'))
    assert commands[0][0] == 'unit'
    assert commands[0][1][-2:] == ['-m','not slow']
    assert not any(part.startswith('tests/') for part in commands[0][1])


def test_quality_cleanup_removes_read_only_fixture_files(tmp_path):
    locked = tmp_path / 'fixtures' / '.git' / 'objects' / 'aa' / 'object'
    locked.parent.mkdir(parents=True)
    locked.write_text('disposable', encoding='utf-8')
    locked.chmod(stat.S_IREAD)

    remove_tree(tmp_path / 'fixtures')

    assert not (tmp_path / 'fixtures').exists()


def test_full_keeps_complete_collection_and_separates_fixture_caches():
    commands = python_commands('full', Path('isolated'))
    assert [state for state, _ in commands] == ['cold','warm']
    for state, command in commands:
        assert command.count('-m') == 1  # Python's module launcher, no pytest selection.
        assert command[command.index('--basetemp')+1] == str(Path('isolated')/(state+'-fixtures'))
        assert 'cache_dir='+str(Path('isolated')/'pytest-cache') in command
