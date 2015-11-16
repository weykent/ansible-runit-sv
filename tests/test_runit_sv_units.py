# Copyright (c) weykent <weykent@weasyl.com>
# See COPYING for details.

import decorator
import py.path
import pytest

import runit_sv


# This is unfortunately necessary because of the intersection of two py.test
# features: marks mutate functions, and fixtures are determined entirely from
# argspec. This decorator will make a new function with the same argspec as the
# old function, which lets you have multiple 'functions' with the same argspec
# but different marks which both really just call the same function.
@decorator.decorator
def copy(f, *a, **kw):
    return f(*a, **kw)


def apply_decorators(f, *decos):
    for d in decos:
        f = d(f)
    return f


def mkdir(d):
    return d, 'mkdir'


def empty_file(f):
    return f, 'write', ''


def simple_file(f, content):
    return f, 'write', content


def symlink(l, target):
    return l, 'mksymlinkto', (lambda d: d.join(target)), False


def make_path(tmpdir, value, mode=None):
    if isinstance(value, tuple):
        name, action = value[0], value[1:]
    else:
        name, action = value, None
    ret = tmpdir.join(name)
    if action is not None:
        getattr(ret, action[0])(
            *[a(tmpdir) if callable(a) else a for a in action[1:]])
        if mode is not None:
            ret.chmod(mode)
    return ret.strpath


@pytest.mark.parametrize(('inputs', 'expected'), [
    ([], None),
    ([mkdir('d')], 0),
    ([mkdir('d1'), mkdir('d2')], 0),
    ([mkdir('d2'), mkdir('d1')], 0),
    (['d1'], None),
    (['d1', 'd2'], None),
    ([mkdir('d1'), 'd2'], 0),
    (['d1', mkdir('d2')], 1),
    (['d1', 'd2', 'd3', mkdir('d4')], 3),
    ([empty_file('d1')], None),
    ([empty_file('d1'), empty_file('d2')], None),
    ([mkdir('d1'), empty_file('d2')], 0),
    ([empty_file('d1'), mkdir('d2')], 1),
    ([symlink('d1', 'd2'), mkdir('d2')], 1),
    ([mkdir('d1'), symlink('d2', 'd1')], 0),
    ([empty_file('d1'), symlink('d2', 'd1'), mkdir('d3')], 2),
    ([mkdir('d1'), empty_file('d2'), symlink('d3', 'd2')], 0),
    ([symlink('d1', 'd3'), empty_file('d2'), mkdir('d3')], 2),
])
def test_first_directory(tmpdir, inputs, expected):
    """
    first_directory will return the first extant true directory (i.e. not a
    symlink) or None if none exist.
    """
    inputs = [make_path(tmpdir, x) for x in inputs]
    result = runit_sv.first_directory(inputs)
    if expected is None:
        assert result is None
    else:
        assert result == inputs[expected]


def test_first_directory_propagates_lstat_exceptions(tmpdir):
    """
    If first_directory's lstat raises an exception that isn't ENOENT, it will
    be propagated upward.
    """
    d = tmpdir.join('d')
    d.mkdir()
    tmpdir.chmod(0)
    with pytest.raises(OSError):
        runit_sv.first_directory([d.strpath])


@pytest.mark.parametrize(('data', 'expected'), [
    ('', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
    ('abc',
     'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'),
    ('a' * 1000000,
     'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0'),
], ids=lambda t: t[:12])
@pytest.mark.parametrize('mode', [0o400, 0o600, 0o666, 0o777], ids=oct)
def test_hash_file(tmpdir, data, expected, mode):
    """
    hash_file will return the hex SHA256 digest and mode of a regular file.
    """
    f = tmpdir.join('f')
    f.write(data)
    f.chmod(mode)
    h, m = runit_sv.hash_file(f.strpath)
    assert h == expected and m & runit_sv.SETTABLE_MASK == mode


def test_hash_file_nonextant_files(tmpdir):
    """
    If the path passed to hash_file doesn't refer to any extant thing, (None,
    None) is returned.
    """
    f = tmpdir.join('f')
    assert runit_sv.hash_file(f.strpath) == (None, None)


def test_hash_file_propagates_open_exceptions(tmpdir):
    """
    Any errors raised by opening the path passed to hash_file that aren't
    ENOENT are propagated upward.
    """
    f = tmpdir.join('f')
    f.write('')
    f.chmod(0)
    with pytest.raises(IOError):
        runit_sv.hash_file(f.strpath)


def test_makedirs_exist_ok_ignores_extant_directories(tmpdir):
    """
    If makedirs_exist_ok is passed the path to an extant directory, the
    directory continues to exist but nothing else happens.
    """
    d = tmpdir.join('d')
    d.mkdir()
    runit_sv.makedirs_exist_ok(d.strpath)
    assert d.check(dir=True)


def test_makedirs_exist_ok_creates_one_level(tmpdir):
    """
    If makedirs_exist_ok needs to create one level of directories, it will.
    """
    d = tmpdir.join('d')
    assert not d.exists()
    runit_sv.makedirs_exist_ok(d.strpath)
    assert d.check(dir=True)


def test_makedirs_exist_ok_creates_two_levels(tmpdir):
    """
    If makedirs_exist_ok needs to create more than one level of directories, it
    will.
    """
    d = tmpdir.join('d1').join('d2')
    assert not d.exists()
    runit_sv.makedirs_exist_ok(d.strpath)
    assert d.check(dir=True)


def test_makedirs_exist_ok_propagates_makedirs_exceptions(tmpdir):
    """
    If the os.makedirs call in makedirs_exist_ok raises an exception that isn't
    EEXIST, it will be propagated upward.
    """
    d = tmpdir.join('d')
    tmpdir.chmod(0)
    with pytest.raises(OSError):
        runit_sv.makedirs_exist_ok(d.strpath)


@pytest.mark.parametrize(('ops', 'content', 'content_expected'), [
    ('f', '', True),
    (empty_file('f'), None, True),
    (empty_file('f'), '', False),
    ('f', 'spam', True),
    (simple_file('f', 'spam'), 'spam', False),
    (empty_file('f'), 'spam', True),
    (simple_file('f', 'spam'), '', True),
    ('f', True, True),
    (simple_file('f', 'spam'), True, False),
    (empty_file('f'), True, False),
])
@pytest.mark.parametrize(('set_mode', 'check_mode', 'mode_expected'), [
    (0o644, 0o644, False),
    (0o644, 0o755, True),
    (0o755, 0o755, False),
    (0o755, 0o644, True),
])
def test_filerecord_check_if_must_change(
        tmpdir, ops, content, content_expected,
        set_mode, check_mode, mode_expected):
    """
    FileRecord objects will indicate if their desired state is not the same as
    their referenced path's current state. The file's mode and content are
    independently considered as criteria for if the state must change.
    """
    p = make_path(tmpdir, ops, mode=set_mode)
    fr = runit_sv.FileRecord(p, check_mode, content)
    fr.check_if_must_change()
    assert fr.must_change == (content_expected or mode_expected)


def test_filerecord_check_if_must_change_no_file_or_content(tmpdir):
    """
    If there's no file at the path specified and the FileRecord indicates the
    file shouldn't exist, then nothing must change.
    """
    p = tmpdir.join('f')
    fr = runit_sv.FileRecord(p.strpath, 0, content=None)
    fr.check_if_must_change()
    assert not fr.must_change


@pytest.mark.parametrize('initial_state', [
    'f',
    'd/f',
    'd1/d2/f',
    empty_file('f'),
    simple_file('f', 'spam'),
    symlink('f', 'target'),
])
@pytest.mark.parametrize('mode', [0o644, 0o755, 0o600, 0o700])
@pytest.mark.parametrize('content', [None, '', 'eggs'])
@pytest.mark.parametrize('must_change', [True, False])
def test_filerecord_commit(tmpdir, initial_state, mode, content, must_change):
    """
    FileRecord objects will make their referenced path's state match the
    desired state via the commit method, but only if must_change is true.
    """
    p = make_path(tmpdir, initial_state)
    fr = runit_sv.FileRecord(p, mode, content)
    fr.must_change = must_change
    fr.commit()
    pp = py.path.local(p)
    if must_change:
        if content is None:
            assert not pp.exists()
        else:
            assert pp.read() == content
            assert pp.stat().mode & runit_sv.SETTABLE_MASK == mode
        assert fr.changed
    else:
        assert not fr.changed


@pytest.mark.parametrize('initial_state', [
    'f',
    'd/f',
    'd1/d2/f',
    empty_file('f'),
    simple_file('f', 'spam'),
    symlink('f', 'target'),
])
@pytest.mark.parametrize('mode', [0o644, 0o755, 0o600, 0o700])
@pytest.mark.parametrize('must_change', [True, False])
def test_filerecord_commit_content_true(
        tmpdir, initial_state, mode, must_change):
    """
    A FileRecord with a content of True will ensure the existence of the file
    and the mode of the file, but will not change the content of the file.
    """
    p = make_path(tmpdir, initial_state)
    pp = py.path.local(p)
    fr = runit_sv.FileRecord(p, mode, True)
    fr.must_change = must_change
    if pp.exists():
        content_before = pp.read()
        fr.commit()
    elif must_change:
        with pytest.raises(runit_sv.FileDoesNotExistError):
            fr.commit()
        return

    if must_change:
        assert pp.read() == content_before
        assert pp.stat().mode & runit_sv.SETTABLE_MASK == mode
        assert fr.changed
    else:
        assert not fr.changed


def test_filerecord_propagates_unlink_exceptions(tmpdir):
    """
    If the unlink call in FileRecord's commit method raises an exception that
    isn't ENOENT, it will be propagated upward.
    """
    d = tmpdir.join('d')
    d.mkdir()
    fr = runit_sv.FileRecord(d.strpath, 0o644)
    fr.must_change = True
    with pytest.raises(OSError):
        fr.commit()


@pytest.mark.parametrize(('path', 'mode', 'content', 'expected'), [
    ('x', 0o644, None, '''<FileRecord {}: None @'x'(644)>'''),
    ("y'", 0o755, 'spam', '''<FileRecord {}: 'spam' @"y'"(755)>'''),
])
def test_filerecord_repr(path, mode, content, expected):
    """
    FileRecord has a predictable __repr__.
    """
    fr = runit_sv.FileRecord(path, mode, content)
    assert repr(fr) == expected.format(hex(id(fr)))


def _test_linkrecord_check_if_must_change(
        tmpdir, ops, target, expected, dir_ok):
    """
    LinkRecord objects will indicate if their desired state is not the same as
    their referenced path's current state.
    """
    p = make_path(tmpdir, ops)
    lr = runit_sv.LinkRecord(p, target, dir_ok)
    if expected == 'error':
        with pytest.raises(runit_sv.PathAlreadyExistsError):
            lr.check_if_must_change()
    else:
        lr.check_if_must_change()
        assert lr.must_change == expected

test_linkrecord_check_if_must_change = apply_decorators(
    _test_linkrecord_check_if_must_change,
    copy,
    pytest.mark.parametrize(('ops', 'target', 'expected'), [
        ('l', 'target', True),
        ('l', None, False),
        (symlink('l', 'target'), 'target', False),
        (symlink('l', 'target'), 'spam', True),
        (symlink('l', 'target'), None, True),
        (empty_file('l'), 'target', 'error'),
        (empty_file('l'), None, 'error'),
    ]),
    pytest.mark.parametrize('dir_ok', [True, False]),
)

test_linkrecord_check_if_must_change_with_dir_ok = apply_decorators(
    _test_linkrecord_check_if_must_change,
    copy,
    pytest.mark.parametrize(('ops', 'target', 'dir_ok', 'expected'), [
        (mkdir('l'), None, False, 'error'),
        (mkdir('l'), None, True, False),
        (mkdir('l'), 'target', False, 'error'),
        (mkdir('l'), 'target', True, False),
    ]),
)


@pytest.mark.parametrize('initial_state', [
    'f',
    'd/f',
    'd1/d2/f',
    symlink('f', 'target'),
])
@pytest.mark.parametrize('target', [None, 'eggs'])
@pytest.mark.parametrize('must_change', [True, False])
def test_linkrecord_commit(tmpdir, initial_state, target, must_change):
    """
    LinkRecord objects will make their referenced path's state match the
    desired state via the commit method, but only if must_change is true.
    """
    p = make_path(tmpdir, initial_state)
    lr = runit_sv.LinkRecord(p, target)
    lr.must_change = must_change
    lr.commit()
    pp = py.path.local(p)
    if must_change:
        if target is None:
            assert not pp.exists()
        else:
            assert pp.readlink() == target
        assert lr.changed
    else:
        assert not lr.changed


def test_linkrecord_propagates_readlink_exceptions(tmpdir):
    """
    If the readlink call in LinkRecord's check_if_must_change method raises an
    exception that isn't ENOENT or EINVAL, it will be propagated upward.
    """
    d = tmpdir.join('d')
    d.mkdir()
    tmpdir.chmod(0)
    lr = runit_sv.LinkRecord(d.strpath)
    with pytest.raises(OSError):
        lr.check_if_must_change()


def test_linkrecord_propagates_unlink_exceptions(tmpdir):
    """
    If the unlink call in LinkRecord's commit method raises an exception that
    isn't ENOENT, it will be propagated upward.
    """
    f = tmpdir.join('f')
    f.write('')
    tmpdir.chmod(0)
    lr = runit_sv.LinkRecord(f.strpath)
    lr.must_change = True
    with pytest.raises(OSError):
        lr.commit()


@pytest.mark.parametrize(('path', 'target', 'dir_ok', 'expected'), [
    ('x', None, True, '''<LinkRecord {}: None dir_ok:True @'x'>'''),
    ("y'", 'spam', False, '''<LinkRecord {}: 'spam' dir_ok:False @"y'">'''),
])
def test_linkrecord_repr(path, target, dir_ok, expected):
    """
    LinkRecord has a predictable __repr__.
    """
    lr = runit_sv.LinkRecord(path, target, dir_ok)
    assert repr(lr) == expected.format(hex(id(lr)))


@pytest.mark.parametrize(('ops', 'which', 'expected'), [
    ('f', 'rm', False),
    ('f', 'rmdir', False),
    (empty_file('f'), 'rm', True),
    (simple_file('f', 'spam'), 'rm', True),
    (mkdir('d'), 'rmdir', True),
    (empty_file('f'), 'rmdir', 'error'),
    (simple_file('f', 'spam'), 'rmdir', 'error'),
    (mkdir('d'), 'rm', 'error'),
    (symlink('l', 'target'), 'rm', 'error'),
    (symlink('l', 'target'), 'rmdir', 'error'),
])
def test_removething_check_if_must_change(tmpdir, ops, which, expected):
    """
    RemoveThing objects will indicate if their desired state is not the same as
    their referenced path's current state.
    """
    p = make_path(tmpdir, ops)
    rt = getattr(runit_sv, which)(p)
    if expected == 'error':
        with pytest.raises(runit_sv.NotAThingError):
            rt.check_if_must_change()
    else:
        rt.check_if_must_change()
        assert rt.must_change == expected


def test_removething_propagates_lstat_exceptions(tmpdir):
    """
    If the lstat call in RemoveThing's commit method raises an exception that
    isn't ENOENT, it will be propagated upward.
    """
    d = tmpdir.join('d')
    d.mkdir()
    tmpdir.chmod(0)
    rt = runit_sv.rmdir(d.strpath)
    with pytest.raises(OSError):
        rt.check_if_must_change()


@pytest.mark.parametrize(('initial_state', 'which'), [
    (empty_file('f'), 'rm'),
    (simple_file('f', 'spam'), 'rm'),
    (mkdir('d'), 'rmdir'),
])
@pytest.mark.parametrize('must_change', [True, False])
def test_removething_commit(tmpdir, initial_state, which, must_change):
    """
    RemoveThing objects will make their referenced path's state match the
    desired state via the commit method, but only if must_change is true.
    """
    p = make_path(tmpdir, initial_state)
    rt = getattr(runit_sv, which)(p)
    rt.must_change = must_change
    rt.commit()
    pp = py.path.local(p)
    if must_change:
        assert not pp.exists() and rt.changed
    else:
        assert not rt.changed


@pytest.mark.parametrize(('path', 'stat_type', 'remover', 'expected'), [
    ('x', 'S_ISREG', '<func>',
     '''<RemoveThing {}: 'x'('S_ISREG', '<func>')>'''),
    ("y'", 'S_ISDIR', '<func2>',
     '''<RemoveThing {}: "y'"('S_ISDIR', '<func2>')>'''),
])
def test_removething_repr(path, stat_type, remover, expected):
    """
    RemoveThing has a predictable __repr__.
    """
    rt = runit_sv.RemoveThing(path, stat_type, remover)
    assert repr(rt) == expected.format(hex(id(rt)))
