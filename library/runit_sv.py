#!/usr/bin/python
# Copyright (c) weykent <weykent@weasyl.com>
# See COPYING for details.

import errno
import functools
import hashlib
import os
import shutil
import stat
import tempfile
import traceback

EXECUTABLE = 0o777
NONEXECUTABLE = 0o666
SETTABLE_MASK = 0o7777


def settable_mode(m):
    return m & SETTABLE_MASK


def first_directory(directories):
    for d in directories:
        try:
            s = os.lstat(d)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            continue
        if not stat.S_ISDIR(s.st_mode):
            continue
        return d
    return None


def hash_file(path, chunksize=4096):
    try:
        infile = open(path, 'rb')
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise
        return None, None
    hasher = hashlib.sha256()
    with infile:
        while True:
            chunk = infile.read(chunksize)
            if not chunk:
                break
            hasher.update(chunk)
        s = os.fstat(infile.fileno())
    return hasher.hexdigest(), s.st_mode


def makedirs_exist_ok(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


class FileDoesNotExistError(Exception):
    pass


class FileRecord(object):
    def __init__(self, path, mode, content=None):
        self.path = path
        self.mode = mode
        self.content = content
        self.must_change = False
        self.changed = False

    def __repr__(self):
        return '<%s %#x: %r @%r(%o)>' % (
            type(self).__name__, id(self), self.content, self.path, self.mode)

    def _must_change_p(self):
        current_hash, current_mode = hash_file(self.path)
        if current_hash is None:
            return self.content is not None
        else:
            if self.content is None:
                return True
            elif self.content is True:
                content_matches = True
            else:
                content_matches = (
                    hashlib.sha256(self.content).hexdigest() == current_hash)
            return (
                not content_matches
                or self.mode != settable_mode(current_mode))

    def check_if_must_change(self):
        self.must_change = self._must_change_p()

    def commit(self):
        if not self.must_change:
            return
        if self.content is None:
            try:
                os.unlink(self.path)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise
        elif self.content is True:
            try:
                filestat = os.lstat(self.path)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    raise FileDoesNotExistError(self.path)
                else:
                    raise
            if not stat.S_ISLNK(filestat.st_mode):
                os.chmod(self.path, self.mode)
        else:
            outdir = os.path.dirname(self.path)
            makedirs_exist_ok(outdir)
            outfile = tempfile.NamedTemporaryFile(
                dir=outdir, prefix='.tmp', suffix='~', delete=False)
            with outfile:
                outfile.write(self.content)
            os.chmod(outfile.name, self.mode)
            os.rename(outfile.name, self.path)
        self.changed = True


class PathAlreadyExistsError(Exception):
    pass


class LinkRecord(object):
    def __init__(self, path, target=None, dir_ok=False):
        self.path = path
        self.target = target
        self.dir_ok = dir_ok
        self.must_change = False
        self.changed = False

    def __repr__(self):
        return '<%s %#x: %r dir_ok:%s @%r>' % (
            type(self).__name__, id(self), self.target, self.dir_ok, self.path)

    def _must_change_p(self):
        try:
            current_target = os.readlink(self.path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return self.target is not None
            elif e.errno == errno.EINVAL:
                if self.dir_ok and os.path.isdir(self.path):
                    return False
                else:
                    raise PathAlreadyExistsError(self.path)
            raise
        return self.target != current_target

    def check_if_must_change(self):
        self.must_change = self._must_change_p()

    def commit(self):
        if not self.must_change:
            return
        try:
            os.unlink(self.path)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        if self.target is not None:
            makedirs_exist_ok(os.path.dirname(self.path))
            os.symlink(self.target, self.path)
        self.changed = True


class NotAThingError(Exception):
    pass


class RemoveThing(object):
    def __init__(self, path, stat_type, remover):
        self.path = path
        self.stat_type = stat_type
        self.remover = remover
        self.must_change = False
        self.changed = False

    def __repr__(self):
        return '<%s %#x: %r(%r, %r)>' % (
            type(self).__name__, id(self), self.path, self.stat_type,
            self.remover)

    def _must_change_p(self):
        try:
            s = os.lstat(self.path)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            return False
        if not getattr(stat, self.stat_type)(s.st_mode):
            raise NotAThingError(self.path, 'does not match', self.stat_type)
        return True

    def check_if_must_change(self):
        self.must_change = self._must_change_p()

    def commit(self):
        if not self.must_change:
            return
        self.remover(self.path)
        self.changed = True


rm = functools.partial(RemoveThing, stat_type='S_ISREG', remover=os.unlink)
rmdir = functools.partial(
    RemoveThing, stat_type='S_ISDIR', remover=shutil.rmtree)


def main(module_cls):
    module = module_cls(
        argument_spec=dict(
            name=dict(required=True),
            sv_directory=dict(type='list', default=['/etc/sv']),
            service_directory=dict(type='list', default=['/service', '/etc/service']),
            init_d_directory=dict(type='list', default=['/etc/init.d']),
            runscript=dict(required=True),
            log_runscript=dict(),
            supervise_link=dict(),
            log_supervise_link=dict(),
            state=dict(
                choices=['present', 'absent', 'down'], default='present'),
            extra_files=dict(type='dict', default={}),
            extra_scripts=dict(type='dict', default={}),
            envdir=dict(type='dict'),
            lsb_service=dict(choices=['present', 'absent']),
            umask=dict(type='int', default=0o022),
        ),
        supports_check_mode=True,
    )

    try:
        _main(module)
    except Exception:
        module.fail_json(
            msg='unhandled exception', traceback=traceback.format_exc())


def _main(module):
    def first_directory_or_fail(name):
        directories = module.params[name]
        ret = first_directory(directories)
        if ret is None:
            module.fail_json(
                msg='no extant directory found for %r out of %r' % (
                    name, directories))
        return ret

    sv_directory = first_directory_or_fail('sv_directory')
    service_directory = first_directory_or_fail('service_directory')
    name = module.params['name']
    state = module.params['state']
    umask = module.params['umask']
    sv = functools.partial(os.path.join, sv_directory, name)
    exe = functools.partial(FileRecord, mode=EXECUTABLE & ~umask)
    nexe = functools.partial(FileRecord, mode=NONEXECUTABLE & ~umask)

    outfiles = []
    outfiles.append(exe(sv('run'), content=module.params['runscript']))
    directories_to_clear = []
    directories_to_clear.append(sv())
    if module.params['log_runscript'] is None:
        if module.params['log_supervise_link'] is not None:
            module.fail_json(
                msg='log_supervise_link must be specified with log_runscript')
        outfiles.append(rmdir(sv('log')))
    else:
        outfiles.append(
            exe(sv('log', 'run'), content=module.params['log_runscript']))
        directories_to_clear.append(sv('log'))
    for filename, content in module.params['extra_files'].iteritems():
        outfiles.append(nexe(sv(filename), content=content))
    for filename, content in module.params['extra_scripts'].iteritems():
        outfiles.append(exe(sv(filename), content=content))
    envdir = module.params['envdir']
    if envdir is None:
        outfiles.append(rmdir(sv('env')))
    else:
        for key, value in module.params['envdir'].iteritems():
            outfiles.append(nexe(sv('env', key), content=value))
        directories_to_clear.append(sv('env'))
    outfiles.append(nexe(sv('down'), content='' if state == 'down' else None))

    def do_supervise_link(param, *segments):
        target = module.params[param]
        outfiles.append(LinkRecord(
            sv(*segments), target=target, dir_ok=target is None))

    do_supervise_link('supervise_link', 'supervise')
    do_supervise_link('log_supervise_link', 'log', 'supervise')

    outfiles.append(LinkRecord(
        os.path.join(service_directory, name),
        target=None if state == 'absent' else sv()))

    lsb_service = module.params['lsb_service']
    if state == 'absent':
        if lsb_service == 'present':
            module.fail_json(
                msg="lsb_service can't be set to present if state=absent")
    else:
        init_d_directory = first_directory(module.params['init_d_directory'])
        if init_d_directory is None:
            if lsb_service is not None:
                module.fail_json(
                    msg='no /etc/init.d and lsb_service=%r' % (lsb_service,))
        else:
            should_create_lsb = lsb_service == 'present' or lsb_service is None
            outfiles.append(LinkRecord(
                os.path.join(init_d_directory, name),
                target='/usr/bin/sv' if should_create_lsb else None))

    paths_set = {outfile.path for outfile in outfiles}
    if len(paths_set) != len(outfiles):
        module.fail_json(msg='duplicate file paths specified')

    paths_set.update(directories_to_clear)
    for to_clear in directories_to_clear:
        try:
            directory_paths = os.listdir(to_clear)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            continue
        directory_paths = {os.path.join(to_clear, p) for p in directory_paths}
        outfiles.extend(rm(path) for path in directory_paths - paths_set)

    for outfile in outfiles:
        outfile.check_if_must_change()
    paths = {outfile.path: outfile.must_change for outfile in outfiles}
    if not any(outfile.must_change for outfile in outfiles):
        module.exit_json(paths=paths, changed=False)
    elif module.check_mode:
        module.exit_json(paths=paths, changed=True)

    for outfile in outfiles:
        outfile.commit()

    module.exit_json(paths=paths, changed=True)


# This is some gross-ass ansible magic. Unfortunately noqa can't be applied for
# E265, so it had to be disabled in setup.cfg.
#<<INCLUDE_ANSIBLE_MODULE_COMMON>>
if __name__ == '__main__':  # pragma: nocover
    main(AnsibleModule)  # noqa
