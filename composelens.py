"""Compare Docker Compose's resolved models without starting containers."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def resolve(files, directory, env_files=None, profiles=None, environment=None):
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('Project directory must be a directory')
    command = ['docker', 'compose', '--ansi', 'never', '--project-name', 'composelens',
               '--project-directory', str(directory)]
    child_env = dict(os.environ if environment is None else environment)
    for file in env_files or []:
        path = Path(file).resolve(strict=True)
        if not path.is_file():
            raise ValueError('Environment inputs must be local files')
        command += ['--env-file', str(path)]
    if profiles is not None:
        # Empty is an explicit choice, distinct from inheriting Compose defaults.
        child_env['COMPOSE_PROFILES'] = ''
        for profile in profiles:
            if profile != '*' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', profile):
                raise ValueError('Invalid Compose profile name')
            command += ['--profile', profile]
    for file in files:
        path = Path(file).resolve(strict=True)
        if not path.is_file():
            raise ValueError('Compose inputs must be local files')
        command += ['-f', str(path)]
    command += ['config', '--format', 'json']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False, env=child_env)
    except FileNotFoundError:
        raise ValueError('Docker CLI not found; install Docker with the Compose plugin') from None
    except subprocess.TimeoutExpired:
        raise ValueError('Compose resolution exceeded 30 seconds') from None
    if result.returncode:
        # Raw resolver diagnostics may echo interpolated credentials.
        raise ValueError(f'Compose resolution failed (exit {result.returncode}); inspect the input locally with docker compose config')
    try:
        model = json.loads(result.stdout)
    except ValueError:
        raise ValueError('Compose did not return valid JSON; config --format json is required') from None
    if not isinstance(model, dict) or not isinstance(model.get('services'), dict):
        raise ValueError('Compose returned an unexpected model')
    return model, bool(result.stderr.strip())


def differences(before, after):
    """Object-order independent; list order matters. Paths use JSON Pointer escaping."""
    changes = []

    def walk(left, right, path):
        if type(left) is not type(right):
            changes.append({'path': path, 'kind': 'changed'})
        elif isinstance(left, dict):
            for key in sorted(left.keys() | right.keys()):
                child = path + '/' + key.replace('~', '~0').replace('/', '~1')
                if key not in left:
                    changes.append({'path': child, 'kind': 'added'})
                elif key not in right:
                    changes.append({'path': child, 'kind': 'removed'})
                else:
                    walk(left[key], right[key], child)
        elif isinstance(left, list):
            for index in range(max(len(left), len(right))):
                child = path + '/' + str(index)
                if index >= len(left):
                    changes.append({'path': child, 'kind': 'added'})
                elif index >= len(right):
                    changes.append({'path': child, 'kind': 'removed'})
                else:
                    walk(left[index], right[index], child)
        elif left != right:
            changes.append({'path': path, 'kind': 'changed'})

    walk(before, after, '')
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-b', '--baseline', action='append', required=True, help='Repeat for ordered baseline files')
    parser.add_argument('-c', '--candidate', action='append', required=True, help='Repeat for ordered candidate files')
    parser.add_argument('--project-directory', default='.', help='Shared path/interpolation base (default current directory)')
    for side in ('baseline', 'candidate'):
        parser.add_argument(f'--{side}-env-file', action='append', help='Repeat for ordered Compose interpolation env files')
        group = parser.add_mutually_exclusive_group()
        group.add_argument(f'--{side}-profile', action='append', help='Repeat to activate profiles; quote * for all')
        group.add_argument(f'--{side}-no-profiles', action='store_true', help='Disable inherited profiles')
    args = parser.parse_args(argv)
    environment = dict(os.environ)
    selections = {}
    for side in ('baseline', 'candidate'):
        selections[side] = {
            'env_files': getattr(args, side + '_env_file'),
            'profiles': [] if getattr(args, side + '_no_profiles') else getattr(args, side + '_profile')}
    try:
        before, bw = resolve(args.baseline, args.project_directory, **selections['baseline'], environment=environment)
        after, aw = resolve(args.candidate, args.project_directory, **selections['candidate'], environment=environment)
        changes = differences(before, after)
    except (ValueError, OSError, RecursionError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({'version': 1, 'match': not changes, 'changes': changes,
                      'selections': selections,
                      'resolver_warnings': {'baseline': bw, 'candidate': aw}}, indent=2))
    return 1 if changes else 0


if __name__ == '__main__':
    sys.exit(main())
