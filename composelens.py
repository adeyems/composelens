"""Compare Docker Compose's resolved models without starting containers."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


MAX_MODEL_NODES = 100_000
MAX_MODEL_DEPTH = 64


def validate_model_budget(model):
    # Iterative validation avoids walking untrusted nesting recursively.
    pending = [(model, 0)]
    count = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > MAX_MODEL_NODES or depth > MAX_MODEL_DEPTH:
            raise ValueError('Resolved model exceeds 100000 nodes or 64 nesting levels')
        if isinstance(value, dict):
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        if count + len(pending) + len(value) > MAX_MODEL_NODES:
            raise ValueError('Resolved model exceeds 100000 nodes or 64 nesting levels')
        pending.extend((item, depth + 1) for item in children)


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
    validate_model_budget(model)
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
    validate_model_budget(before)
    validate_model_budget(after)
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


def filter_changes(changes, ignored):
    rules = sorted(set(ignored))
    for pointer in rules:
        if not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
            raise ValueError('Ignore rules must be non-root JSON Pointers with valid escapes')
        if any(part.isdecimal() for part in pointer.split('/')[1:]):
            raise ValueError('Numeric ignore segments are unsupported; ignore the entire list instead')
    def covered(path):
        return any(path == rule or path.startswith(rule + '/') for rule in rules)
    kept, suppressed = [], []
    for change in changes:
        # Both semantic identities must remain covered after a list reorder.
        target = suppressed if covered(change['path']) and covered(change.get('baseline_path', change['path'])) else kept
        target.append(change)
    return kept, suppressed, rules


MAX_SNAPSHOT = 8 * 1024 * 1024


def save_snapshot(path, model, warnings, provenance):
    validate_model_budget(model)
    snapshot = {'version': 1, 'model': model, 'resolver_warnings': warnings, 'provenance': provenance}
    encoded = json.dumps(snapshot, ensure_ascii=True, indent=2).encode('utf-8')
    if len(encoded) > MAX_SNAPSHOT:
        raise ValueError('Snapshot exceeds 8 MiB')
    # Resolved values may contain secrets: caller deliberately requests this file.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(encoded)


def load_snapshot(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate snapshot JSON key')
            value[key] = item
        return value
    with open(path, 'rb') as source:
        encoded = source.read(MAX_SNAPSHOT + 1)
    if len(encoded) > MAX_SNAPSHOT:
        raise ValueError('Snapshot exceeds 8 MiB')
    def reject_constant(value):
        raise ValueError('Snapshot cannot contain non-finite numbers')
    try:
        snapshot = json.loads(encoded, object_pairs_hook=unique, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('Snapshot must be valid JSON') from None
    if (not isinstance(snapshot, dict) or set(snapshot) != {'version', 'model', 'resolver_warnings', 'provenance'}
            or type(snapshot['version']) is not int or snapshot['version'] != 1
            or type(snapshot['resolver_warnings']) is not bool or not isinstance(snapshot['provenance'], dict)
            or not isinstance(snapshot['model'], dict) or not isinstance(snapshot['model'].get('services'), dict)):
        raise ValueError('Invalid version-1 snapshot')
    validate_model_budget(snapshot['model'])
    return snapshot


MAX_REPORT_BYTES = 8 * 1024 * 1024


def encode_report(report):
    chunks = []
    size = 0
    for chunk in json.JSONEncoder(indent=2, ensure_ascii=True).iterencode(report):
        size += len(chunk)
        if size > MAX_REPORT_BYTES:
            raise ValueError('Comparison report exceeds 8 MiB; no partial report emitted')
        chunks.append(chunk)
    return ''.join(chunks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for side, short in [('baseline', '-b'), ('candidate', '-c')]:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument(short, '--' + side, action='append', help='Repeat for ordered Compose files')
        source.add_argument('--' + side + '-snapshot', help='Read a saved resolved model without Docker')
        parser.add_argument('--save-' + side + '-snapshot', help='Explicitly save unredacted resolved values to a new local file')
    parser.add_argument('--project-directory', default='.', help='Shared path/interpolation base (default current directory)')
    for side in ('baseline', 'candidate'):
        parser.add_argument(f'--{side}-env-file', action='append', help='Repeat for ordered Compose interpolation env files')
        group = parser.add_mutually_exclusive_group()
        group.add_argument(f'--{side}-profile', action='append', help='Repeat to activate profiles; quote * for all')
        group.add_argument(f'--{side}-no-profiles', action='store_true', help='Disable inherited profiles')
    parser.add_argument('--ignore', action='append', default=[], help='Exact non-root JSON Pointer subtree; repeatable, no numeric segments')
    args = parser.parse_args(argv)
    environment = dict(os.environ)
    selections = {}
    for side in ('baseline', 'candidate'):
        selections[side] = {
            'env_files': getattr(args, side + '_env_file'),
            'profiles': [] if getattr(args, side + '_no_profiles') else getattr(args, side + '_profile')}
    try:
        filter_changes([], args.ignore)
        resolved = {}
        provenance = {}
        for side in ('baseline', 'candidate'):
            snapshot_path = getattr(args, side + '_snapshot')
            if snapshot_path:
                if selections[side]['env_files'] is not None or selections[side]['profiles'] is not None:
                    raise ValueError('Snapshot inputs cannot use environment or profile overrides')
                saved = load_snapshot(snapshot_path)
                resolved[side] = (saved['model'], saved['resolver_warnings'])
                provenance[side] = saved['provenance']
            else:
                files = getattr(args, side)
                resolved[side] = resolve(files, args.project_directory, **selections[side], environment=environment)
                provenance[side] = {'files': files, 'project_directory': args.project_directory, **selections[side]}
        before, bw = resolved['baseline']
        after, aw = resolved['candidate']
        notes = []
        changes, suppressed, rules = filter_changes(differences(before, after, notes), args.ignore)
        for side in ('baseline', 'candidate'):
            output = getattr(args, 'save_' + side + '_snapshot')
            if output:
                model, warning = resolved[side]
                save_snapshot(output, model, warning, provenance[side])
    except (ValueError, OSError, RecursionError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2
    report = {'version': 1, 'match': not changes, 'changes': changes,
                      'summary': service_summary(before, after, changes), 'comparison_notes': notes,
                      'snapshot_sources': {'baseline': args.baseline_snapshot, 'candidate': args.candidate_snapshot},
                      'selections': selections, 'ignored_paths': rules, 'suppressed_changes': suppressed,
                      'resolver_warnings': {'baseline': bw, 'candidate': aw}}
    try:
        encoded = encode_report(report)
    except ValueError as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2
    print(encoded)
    return 1 if changes else 0


if __name__ == '__main__':
    sys.exit(main())
