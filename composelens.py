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


def resource_index(items, resource):
    """Return unique resolved Compose identities, or None for ambiguous input."""
    result = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or 'target' not in item:
            return None
        identity = ([item.get('host_ip'), item['target'], item.get('published'), item.get('protocol', 'tcp')]
                    if resource == 'ports' else [item['target']])
        if any(isinstance(value, (dict, list)) for value in identity):
            return None
        key = json.dumps(identity, sort_keys=True)
        if key in result:
            return None
        result[key] = (index, item)
    return result


def differences(before, after, notes=None):
    """Semantic service ports/volumes; other lists remain positional."""
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
            parts = path.split('/')
            if len(parts) == 4 and parts[1] == 'services' and parts[3] in ('ports', 'volumes'):
                old, new = resource_index(left, parts[3]), resource_index(right, parts[3])
                if old is not None and new is not None:
                    for key in sorted(old.keys() | new.keys()):
                        if key not in old:
                            changes.append({'path': path + '/' + str(new[key][0]), 'kind': 'added'})
                        elif key not in new:
                            changes.append({'path': path + '/' + str(old[key][0]), 'kind': 'removed'})
                        else:
                            oi, ov = old[key]
                            ni, nv = new[key]
                            for change in differences(ov, nv):
                                changes.append({'path': path + '/' + str(oi if change['kind'] == 'removed' else ni) + change['path'],
                                                'baseline_path': path + '/' + str(oi) + change['path'],
                                                'kind': change['kind']})
                    return
                if notes is not None:
                    notes.append({'path': path, 'reason': 'ambiguous_resource_identity', 'matching': 'positional'})
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


def service_summary(before, after, changes):
    old, new = before.get('services', {}), after.get('services', {})
    services = []
    other = 0
    affected = {}
    for change in changes:
        parts = [p.replace('~1', '/').replace('~0', '~') for p in change['path'].split('/')[1:]]
        if len(parts) >= 2 and parts[0] == 'services':
            affected.setdefault(parts[1], set()).add(parts[2] if len(parts) > 2 else '(service)')
        else:
            other += 1
    for name in sorted(old.keys() | new.keys()):
        status = 'added' if name not in old else 'removed' if name not in new else 'changed' if name in affected else 'unchanged'
        services.append({'service': name, 'status': status, 'fields': sorted(affected.get(name, set()))})
    return {'services': services, 'non_service_changes': other}


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
        notes = []
        changes = differences(before, after, notes)
    except (ValueError, OSError, RecursionError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({'version': 1, 'match': not changes, 'changes': changes,
                      'summary': service_summary(before, after, changes), 'comparison_notes': notes,
                      'selections': selections,
                      'resolver_warnings': {'baseline': bw, 'candidate': aw}}, indent=2))
    return 1 if changes else 0


if __name__ == '__main__':
    sys.exit(main())
