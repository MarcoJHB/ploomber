"""
Implementation of:

$ plomber install

This command runs a bunch of pip/conda commands (depending on what's available)
and it does the *right thing*: creating a new environment if needed, and
locking dependencies.
"""

import json
import os
import shutil
from pathlib import Path

import yaml
from click import exceptions

from ploomber.io._commander import Commander

from ploomber.telemetry import telemetry
import datetime

_SETUP_PY = 'setup.py'

_REQS_LOCK_TXT = 'requirements.lock.txt'
_REQS_TXT = 'requirements.txt'

_ENV_YML = 'environment.yml'
_ENV_LOCK_YML = 'environment.lock.yml'


# FIXME: add an option to create missing dependencies file
# "ploomber install --add"
# TODO: document the new options
def main(use_lock, create_env=None):
    """
    Install project, automatically detecting if it's a conda-based or pip-based
    project.

    Parameters
    ---------
    use_lock : bool
        If True Uses requirements.lock.txt/environment.lock.yml and
        requirements.dev.lock.txt/environment.dev.lock.yml files. If False
        uses regular files and creates the lock ones after installing
        dependencies. If None, it uses lock files if they exist, if they don't
        it uses regular files

    create_env : bool, default=None
        If True, creates a new environment, if False, it installs in the
        current environment. If None, it creates a new environment if there
        isn't one already active
    """
    start_time = datetime.datetime.now()
    telemetry.log_api("install-started")
    CONDA_INSTALLED = shutil.which('conda')
    ENV_YML_EXISTS = Path(_ENV_YML).exists()
    ENV_LOCK_YML_EXISTS = Path(_ENV_LOCK_YML).exists()
    REQS_TXT_EXISTS = Path(_REQS_TXT).exists()
    REQS_LOCK_TXT_EXISTS = Path(_REQS_LOCK_TXT).exists()

    if use_lock is None:
        if CONDA_INSTALLED:
            use_lock = ENV_LOCK_YML_EXISTS
        else:
            use_lock = REQS_LOCK_TXT_EXISTS

    if use_lock and not ENV_LOCK_YML_EXISTS and not REQS_LOCK_TXT_EXISTS:
        err = ("Expected and environment.lock.yaml "
               "(conda) or requirements.lock.txt (pip) in the current "
               "directory. Add one of them and try again.")
        telemetry.log_api("install-error",
                          metadata={
                              'type': 'no_lock',
                              'exception': err
                          })
        raise exceptions.ClickException(err)
    elif not use_lock and not ENV_YML_EXISTS and not REQS_TXT_EXISTS:
        err = ("Expected an environment.yaml (conda)"
               " or requirements.txt (pip) in the current directory."
               " Add one of them and try again.")
        telemetry.log_api("install-error",
                          metadata={
                              'type': 'no_env_requirements',
                              'exception': err
                          })
        raise exceptions.ClickException(err)
    elif (not CONDA_INSTALLED and use_lock and ENV_LOCK_YML_EXISTS
          and not REQS_LOCK_TXT_EXISTS):
        err = ("Found env environment.lock.yaml "
               "but conda is not installed. Install conda or add a "
               "requirements.lock.txt to use pip instead")
        telemetry.log_api("install-error",
                          metadata={
                              'type': 'no_conda',
                              'exception': err
                          })
        raise exceptions.ClickException(err)
    elif (not CONDA_INSTALLED and not use_lock and ENV_YML_EXISTS
          and not REQS_TXT_EXISTS):
        err = ("Found environment.yaml but conda is not installed."
               " Install conda or add a requirements.txt to use pip instead")
        telemetry.log_api("install-error",
                          metadata={
                              'type': 'no_conda2',
                              'exception': err
                          })
        raise exceptions.ClickException(err)
    elif CONDA_INSTALLED and use_lock and ENV_LOCK_YML_EXISTS:
        # TODO: emit warnings of unused requirements.txt?
        main_conda(start_time,
                   use_lock=True,
                   create_env=create_env
                   if create_env is not None else _create_conda_env())
    elif CONDA_INSTALLED and not use_lock and ENV_YML_EXISTS:
        # TODO: emit warnings of unused requirements.txt?
        main_conda(start_time,
                   use_lock=False,
                   create_env=create_env
                   if create_env is not None else _create_conda_env())
    else:
        # TODO: emit warnings of unused environment.yml?
        main_pip(start_time,
                 use_lock=use_lock,
                 create_env=create_env
                 if create_env is not None else not telemetry.in_virtualenv())


def main_pip(start_time, use_lock, create_env=True):
    """
    Install pip-based project (uses venv), looks for requirements.txt files

    Parameters
    ----------
    start_time : datetime
        The initial runtime of the function.

    use_lock : bool
        If True Uses requirements.txt and requirements.dev.lock.txt files

    create_env : bool
        If True, it uses the venv module to create a new virtual environment,
        then installs the dependencies, otherwise it installs the dependencies
        in the current environment
    """
    reqs_txt = _REQS_LOCK_TXT if use_lock else _REQS_TXT
    reqs_dev_txt = ('requirements.dev.lock.txt'
                    if use_lock else 'requirements.dev.txt')

    cmdr = Commander()

    # TODO: modify readme to add how to activate env? probably also in conda
    name = Path('.').resolve().name

    if create_env:
        venv_dir = f'venv-{name}'
        cmdr.print('Creating venv...')
        # NOTE: explicitly call 'python3'. in some systems 'python' is
        # Python 2, which doesn't have the venv module
        cmdr.run('python3',
                 '-m',
                 'venv',
                 venv_dir,
                 description='Creating venv')

        # add venv_dir to .gitignore if it doesn't exist
        if Path('.gitignore').exists():
            with open('.gitignore') as f:
                if venv_dir not in f.read():
                    cmdr.append_inline(venv_dir, '.gitignore')
        else:
            cmdr.append_inline(venv_dir, '.gitignore')

        folder, bin_name = _get_pip_folder_and_bin_name()
        pip = str(Path(venv_dir, folder, bin_name))

        if os.name == 'nt':
            cmd_activate = f'{venv_dir}\\Scripts\\Activate.ps1'
        else:
            cmd_activate = f'source {venv_dir}/bin/activate'
    else:
        cmdr.print('Installing in current venv...')
        pip = 'pip'
        cmd_activate = None

    if Path(_SETUP_PY).exists():
        _pip_install_setup_py_pip(cmdr, pip)

    _pip_install(cmdr, pip, lock=not use_lock, requirements=reqs_txt)

    if Path(reqs_dev_txt).exists():
        _pip_install(cmdr, pip, lock=not use_lock, requirements=reqs_dev_txt)

    _next_steps(cmdr, cmd_activate, start_time)


def main_conda(start_time, use_lock, create_env=True):
    """
    Install conda-based project, looks for environment.yml files

    Parameters
    ----------
    start_time : datetime
        The initial runtime of the function.

    use_lock : bool
        If True Uses environment.lock.yml and environment.dev.lock.yml files


    create_env : bool
        If True, it uses the venv module to create a new virtual environment,
        then installs the dependencies, otherwise it installs the dependencies
        in the current environment
    """
    env_yml = _ENV_LOCK_YML if use_lock else _ENV_YML

    # TODO: ensure ploomber-scaffold includes dependency file (including
    # lock files in MANIFEST.in
    cmdr = Commander()

    # TODO: provide helpful error messages on each command

    if create_env:
        with open(env_yml) as f:
            env_name = yaml.safe_load(f)['name']

        current_env = _current_conda_env_name()

        if env_name == current_env:
            err = (f'{env_yml} will create an environment '
                   f'named {env_name!r}, which is the current active '
                   'environment. Move to a different one and try '
                   'again (e.g., "conda activate base")')
            telemetry.log_api("install-error",
                              metadata={
                                  'type': 'env_running_conflict',
                                  'exception': err
                              })
            raise RuntimeError(err)
    else:
        env_name = _current_conda_env_name()

    # get current installed envs
    conda = shutil.which('conda')
    mamba = shutil.which('mamba')

    # if already installed and running on windows, ask to delete first,
    # otherwise it might lead to an intermittent error (permission denied
    # on vcruntime140.dll)
    if os.name == 'nt' and create_env:
        envs = cmdr.run(conda, 'env', 'list', '--json', capture_output=True)
        already_installed = any([
            env for env in json.loads(envs)['envs']
            # only check in the envs folder, ignore envs in other locations
            if 'envs' in env and env_name in env
        ])

        if already_installed:
            err = (f'Environment {env_name!r} already exists, '
                   f'delete it and try again '
                   f'(conda env remove --name {env_name})')
            telemetry.log_api("install-error",
                              metadata={
                                  'type': 'duplicate_env',
                                  'exception': err
                              })
            raise ValueError(err)

    pkg_manager = mamba if mamba else conda

    if create_env:
        cmdr.print('Creating conda env...')
        cmdr.run(pkg_manager,
                 'env',
                 'create',
                 '--file',
                 env_yml,
                 '--force',
                 description='Creating env')
    else:
        cmdr.print('Installing in current conda env...')
        cmdr.run(pkg_manager,
                 'env',
                 'update',
                 '--file',
                 env_yml,
                 '--name',
                 env_name,
                 description='Installing dependencies')

    if Path(_SETUP_PY).exists():
        _pip_install_setup_py_conda(cmdr, env_name)

    if not use_lock:
        env_lock = cmdr.run(conda,
                            'env',
                            'export',
                            '--no-build',
                            '--name',
                            env_name,
                            description='Locking dependencies',
                            capture_output=True)
        Path(_ENV_LOCK_YML).write_text(env_lock)

    _try_conda_install_and_lock_dev(cmdr,
                                    pkg_manager,
                                    env_name,
                                    use_lock=use_lock)

    cmd_activate = (f'conda activate {env_name}' if create_env else None)
    _next_steps(cmdr, cmd_activate, start_time)


def _create_conda_env():
    # not in conda env or running in base conda env
    return (not telemetry.is_conda()
            or (telemetry.is_conda() and _current_conda_env_name() == 'base'))


def _current_conda_env_name():
    # NOTE: we can also use env variable: 'CONDA_DEFAULT_ENV'
    return Path(shutil.which('python3')).parents[1].name


def _get_pip_folder_and_bin_name():
    folder = 'Scripts' if os.name == 'nt' else 'bin'
    bin_name = 'pip.exe' if os.name == 'nt' else 'pip'
    return folder, bin_name


def _find_conda_root(conda_bin):
    conda_bin = Path(conda_bin)

    for parent in conda_bin.parents:
        # I've seen variations of this. on windows: Miniconda3 and miniconda3
        # on linux miniconda3, anaconda and miniconda
        if parent.name.lower() in {'miniconda3', 'miniconda', 'anaconda3'}:
            return parent
    err = ('Failed to locate conda root from '
           f'directory: {str(conda_bin)!r}. Please submit an issue: '
           'https://github.com/ploomber/ploomber/issues/new')
    telemetry.log_api("install-error",
                      metadata={
                          'type': 'no_conda_root',
                          'exception': err
                      })
    raise RuntimeError(err)


def _path_to_pip_in_env_with_name(conda_bin, env_name):
    conda_root = _find_conda_root(conda_bin)
    folder, bin_name = _get_pip_folder_and_bin_name()
    return str(conda_root / 'envs' / env_name / folder / bin_name)


def _locate_pip_inside_conda(env_name):
    """
    Locates pip inside the conda env with a given name
    """
    pip = _path_to_pip_in_env_with_name(shutil.which('conda'), env_name)

    # this might happen if the environment does not contain python/pip
    if not Path(pip).exists():
        err = (f'Could not locate pip in environment {env_name!r}, make sure '
               'it is included in your environment.yml and try again')
        telemetry.log_api("install-error",
                          metadata={
                              'type': 'no_pip_env',
                              'exception': err
                          })
        raise FileNotFoundError(err)

    return pip


def _pip_install_setup_py_conda(cmdr, env_name):
    """
    Call "pip install --editable ." if setup.py exists. Automatically locates
    the appropriate pip binary inside the conda env given the env name
    """
    pip = _locate_pip_inside_conda(env_name)
    _pip_install_setup_py_pip(cmdr, pip)


def _pip_install_setup_py_pip(cmdr, pip):
    cmdr.run(pip,
             'install',
             '--editable',
             '.',
             description='Installing project')


def _try_conda_install_and_lock_dev(cmdr, pkg_manager, env_name, use_lock):
    env_yml = 'environment.dev.lock.yml' if use_lock else 'environment.dev.yml'

    if Path(env_yml).exists():
        cmdr.run(pkg_manager,
                 'env',
                 'update',
                 '--file',
                 env_yml,
                 '--name',
                 env_name,
                 description='Installing dev dependencies')

        if not use_lock:
            env_lock = cmdr.run(shutil.which('conda'),
                                'env',
                                'export',
                                '--no-build',
                                '--name',
                                env_name,
                                description='Locking dev dependencies',
                                capture_output=True)
            Path('environment.dev.lock.yml').write_text(env_lock)


def _next_steps(cmdr, cmd_activate, start_time):
    end_time = datetime.datetime.now()
    telemetry.log_api("install-success",
                      total_runtime=str(end_time - start_time))

    cmdr.success('Next steps')

    message = f'$ {cmd_activate}' if cmd_activate else ''
    cmdr.print((f'{message}\n$ ploomber build'))
    cmdr.success()


def _pip_install(cmdr, pip, lock, requirements=_REQS_TXT):
    """Install and freeze requirements

    Parameters
    ----------
    cmdr
        Commander instance

    pip
        Path to pip binary

    lock
        If true, locks dependencies and stores them in a requirements.lock.txt
    """
    cmdr.run(pip,
             'install',
             '--requirement',
             requirements,
             description='Installing dependencies')

    if lock:
        pip_lock = cmdr.run(pip,
                            'freeze',
                            '--exclude-editable',
                            description='Locking dependencies',
                            capture_output=True)

        name = Path(requirements).stem
        Path(f'{name}.lock.txt').write_text(pip_lock)
