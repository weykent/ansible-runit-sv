import pytest

import runit_sv as _runit_sv_module


def assert_no_local_failure(contacted):
    assert not contacted['local'].get('failed')


def assert_local_failure(contacted):
    assert contacted['local'].get('failed')


class FakeAnsibleModuleBailout(Exception):
    def __init__(self, success, params):
        super(FakeAnsibleModuleBailout, self).__init__(success, params)
        self.success = success
        self.params = params


class FakeAnsibleModule(object):
    def __init__(self, params):
        self.params = params

    def __call__(self, argument_spec):
        self.argument_spec = argument_spec
        for name, spec in self.argument_spec.iteritems():
            if name not in self.params:
                self.params[name] = spec.get('default')
        return self

    def exit_json(self, **params):
        raise FakeAnsibleModuleBailout(success=True, params=params)

    def fail_json(self, **params):
        raise FakeAnsibleModuleBailout(success=False, params=params)


@pytest.fixture(params=['real', 'fake'])
def runit_sv(request):
    if request.param == 'real':
        ansible_module = request.getfuncargvalue('ansible_module')

        def do(**params):
            should_fail = params.pop('_should_fail', False)
            contacted = ansible_module.runit_sv(**params)
            if should_fail:
                assert_local_failure(contacted)
            else:
                assert_no_local_failure(contacted)

    elif request.param == 'fake':
        def do(**params):
            should_fail = params.pop('_should_fail', False)
            module = FakeAnsibleModule(params)
            with pytest.raises(FakeAnsibleModuleBailout) as excinfo:
                _runit_sv_module.main(module)
            assert excinfo.value.success != should_fail

    else:
        raise ValueError('unknown param', request.param)

    return do


@pytest.fixture
def basedir(tmpdir):
    tmpdir.join('sv').mkdir()
    tmpdir.join('service').mkdir()
    tmpdir.join('init.d').mkdir()
    return tmpdir


def base_directories(basedir, **overrides):
    ret = {'sv_directory': [basedir.join('sv').strpath],
           'service_directory': [basedir.join('service').strpath],
           'init_d_directory': [basedir.join('init.d').strpath]}
    ret.update(overrides)
    return ret


def settable_mode(path):
    return path.stat().mode & 0o7777


def assert_file(path, contents, mode):
    assert path.read() == contents and settable_mode(path) == mode


def test_basic_runscript(runit_sv, basedir):
    """
    A basic invocation with name and runscript creates the sv directory
    containing just the runscript, links the service directory, and links an
    LSB service.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 1
    assert_file(sv.join('run'), contents='spam eggs', mode=0o755)
    assert basedir.join('service', 'testsv').readlink() == sv.strpath
    assert basedir.join('init.d', 'testsv').readlink() == '/usr/bin/sv'


def test_log_runscript(runit_sv, basedir):
    """
    Adding a log_runscript creates a log/run script as well.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        log_runscript='eggs spam',
        **base_directories(basedir))
    sv_log = basedir.join('sv', 'testsv', 'log')
    assert len(sv_log.listdir()) == 1
    assert_file(sv_log.join('run'), contents='eggs spam', mode=0o755)


def test_supervise_link(runit_sv, basedir):
    """
    The supervise_link option will create a link to some arbitrary location.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        supervise_link='/spam/eggs',
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 2
    assert sv.join('supervise').readlink() == '/spam/eggs'


def test_log_supervise_link(runit_sv, basedir):
    """
    The log_supervise_link option will also create a link to some arbitrary
    location.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        log_runscript='eggs spam',
        log_supervise_link='/eggs/spam',
        **base_directories(basedir))
    sv_log = basedir.join('sv', 'testsv', 'log')
    assert len(sv_log.listdir()) == 2
    assert sv_log.join('supervise').readlink() == '/eggs/spam'


def test_extra_files(runit_sv, basedir):
    """
    Adding extra_files will copy additional files into the sv directory.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        extra_files={
            'spam': 'eggs',
            'eggs': 'spam',
        },
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 3
    assert_file(sv.join('spam'), contents='eggs', mode=0o644)
    assert_file(sv.join('eggs'), contents='spam', mode=0o644)


def test_extra_scripts(runit_sv, basedir):
    """
    Adding extra_scripts will copy additional scripts into the sv directory.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        extra_scripts={
            'spam': 'eggs',
            'eggs': 'spam',
        },
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 3
    assert_file(sv.join('spam'), contents='eggs', mode=0o755)
    assert_file(sv.join('eggs'), contents='spam', mode=0o755)


def test_extra_files_and_scripts(runit_sv, basedir):
    """
    Adding extra_files and extra_scripts both will create both additional files
    and additional scripts.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        extra_files={
            'spam': 'eggs',
            'eggs': 'spam',
        },
        extra_scripts={
            'spams': 'eggs',
            'eggss': 'spam',
        },
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 5
    assert_file(sv.join('spam'), contents='eggs', mode=0o644)
    assert_file(sv.join('eggs'), contents='spam', mode=0o644)
    assert_file(sv.join('spams'), contents='eggs', mode=0o755)
    assert_file(sv.join('eggss'), contents='spam', mode=0o755)


def test_no_overlapping_extra_files_and_scripts(runit_sv, basedir):
    """
    If extra_files and extra_scripts both touch the same path, there's an
    immediate failure.
    """
    runit_sv(
        _should_fail=True,
        name='testsv',
        runscript='spam eggs',
        extra_files={
            'spam': 'eggs',
        },
        extra_scripts={
            'spam': 'eggs',
        },
        **base_directories(basedir))


def test_no_overlapping_extra_scripts_with_runscripts(runit_sv, basedir):
    """
    Similarly if extra_scripts specifies the name of a runscript there's an
    immediate failure.
    """
    runit_sv(
        _should_fail=True,
        name='testsv',
        runscript='spam eggs',
        extra_scripts={
            'run': 'eggs',
        },
        **base_directories(basedir))


def test_extra_files_and_scripts_with_umask(runit_sv, basedir):
    """
    Setting a umask will mask the modes used on all files.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        extra_files={
            'spam': 'eggs',
        },
        extra_scripts={
            'eggs': 'spam',
        },
        umask=0o007,
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 3
    assert_file(sv.join('spam'), contents='eggs', mode=0o660)
    assert_file(sv.join('eggs'), contents='spam', mode=0o770)
    assert_file(sv.join('run'), contents='spam eggs', mode=0o770)


def test_envdir(runit_sv, basedir):
    """
    Adding an envdir option will create an env directory.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        envdir={
            'spam': 'eggs',
            'eggs': 'spam',
        },
        **base_directories(basedir))
    envdir = basedir.join('sv', 'testsv', 'env')
    assert len(envdir.listdir()) == 2
    assert_file(envdir.join('spam'), contents='eggs', mode=0o644)
    assert_file(envdir.join('eggs'), contents='spam', mode=0o644)


def test_no_lsb_service(runit_sv, basedir):
    """
    Setting lsb_service=absent will prevent the creation of an LSB-style init.d
    script.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        lsb_service='absent',
        **base_directories(basedir))
    assert not basedir.join('init.d', 'testsv').exists()


def test_no_lsb_service_or_service_directory(runit_sv, basedir):
    """
    Setting state=absent will prevent the creation of both a service directory
    and an LSB-style init.d script.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        state='absent',
        **base_directories(basedir))
    assert not basedir.join('service', 'testsv').exists()
    assert not basedir.join('init.d', 'testsv').exists()


def test_down_state(runit_sv, basedir):
    """
    Setting state=down creates everything as usual, but marks a service as down
    by default.
    """
    runit_sv(
        name='testsv',
        runscript='spam eggs',
        state='down',
        **base_directories(basedir))
    sv = basedir.join('sv', 'testsv')
    assert len(sv.listdir()) == 2
    assert_file(sv.join('run'), contents='spam eggs', mode=0o755)
    assert_file(sv.join('down'), contents='', mode=0o644)
    assert basedir.join('service', 'testsv').readlink() == sv.strpath
    assert basedir.join('init.d', 'testsv').readlink() == '/usr/bin/sv'
