import copy
import json
from pathlib import Path
import tempfile
import unittest

from composelens import differences, resolve, service_summary
import test_comparison


def model(resource, items):
    return {'services': {'web': {resource: items}}}


class SemanticTest(unittest.TestCase):
    def test_port_reordering_ignored_and_attributes_compared(self):
        a = {'target': 80, 'published': '8080', 'protocol': 'tcp', 'mode': 'ingress'}
        b = {'target': 443, 'published': '8443', 'protocol': 'tcp'}
        before = model('ports', [a, b])
        after = model('ports', [b, a])
        self.assertEqual(differences(before, after), [])
        after = copy.deepcopy(after)
        after['services']['web']['ports'][1]['mode'] = 'host'
        self.assertEqual(differences(before, after), [{'path': '/services/web/ports/1/mode',
                         'baseline_path': '/services/web/ports/0/mode', 'kind': 'changed'}])

    def test_port_identity_changes_are_remove_add(self):
        a = {'target': 80, 'published': '8080', 'protocol': 'tcp', 'host_ip': '127.0.0.1'}
        for field, value in [('target', 81), ('published', '8081'), ('protocol', 'udp'), ('host_ip', '0.0.0.0')]:
            b = dict(a, **{field: value})
            self.assertEqual(sorted(c['kind'] for c in differences(model('ports', [a]), model('ports', [b]))), ['added', 'removed'])

    def test_mount_reordering_and_source_readonly_type_changes(self):
        a = {'target': '/data', 'type': 'volume', 'source': 'old', 'read_only': False}
        b = {'target': '/cache', 'type': 'tmpfs'}
        before = model('volumes', [a, b])
        self.assertEqual(differences(before, model('volumes', [b, a])), [])
        changed = dict(a, source='new', type='bind', read_only=True)
        changes = differences(before, model('volumes', [b, changed]))
        self.assertEqual({c['path'].split('/')[-1] for c in changes}, {'source', 'type', 'read_only'})
        self.assertNotIn('old', json.dumps(changes))
        self.assertNotIn('/data', json.dumps(changes))

    def test_duplicate_or_missing_identity_falls_back(self):
        for items in [[{'target': 80, 'name': 'a'}, {'target': 80, 'name': 'b'}], ['80', '443']]:
            notes = []
            changes = differences(model('ports', items), model('ports', items[::-1]), notes)
            self.assertTrue(changes)
            self.assertEqual(notes[0]['matching'], 'positional')

    def test_other_lists_remain_order_sensitive(self):
        self.assertTrue(differences(model('command', ['a', 'b']), model('command', ['b', 'a'])))
        self.assertTrue(differences({'ports': [1, 2]}, {'ports': [2, 1]}))

    def test_summary_and_escaped_service_names(self):
        before = {'services': {'a/b~c': {'image': 'old'}, 'gone': {}, 'same': {}}, 'networks': {'old': {}}}
        after = {'services': {'a/b~c': {'image': 'new'}, 'added': {}, 'same': {}}, 'networks': {}}
        summary = service_summary(before, after, differences(before, after))
        self.assertEqual([s['status'] for s in summary['services']], ['changed', 'added', 'removed', 'unchanged'])
        self.assertEqual(summary['services'][0]['fields'], ['image'])
        self.assertEqual(summary['non_service_changes'], 1)

    def test_empty_lists_additions_and_removals(self):
        item = {'target': '/data', 'type': 'tmpfs'}
        self.assertEqual(differences(model('volumes', []), model('volumes', [item])),
                         [{'path': '/services/web/volumes/0', 'kind': 'added'}])
        self.assertEqual(differences(model('volumes', [item]), model('volumes', []))[0]['kind'], 'removed')

    def test_removed_field_points_to_original_index(self):
        a = {'target': '/a', 'read_only': True}
        b = {'target': '/b'}
        changes = differences(model('volumes', [a, b]), model('volumes', [b, {'target': '/a'}]))
        self.assertEqual(changes, [{'path': '/services/web/volumes/0/read_only',
                                   'baseline_path': '/services/web/volumes/0/read_only', 'kind': 'removed'}])


class SemanticIntegrationTest(unittest.TestCase):
    setUpClass = classmethod(test_comparison.ComposeIntegrationTest.setUpClass.__func__)

    def test_real_resolved_reordering_and_mount_change(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / 'a.json', Path(folder) / 'b.json'
            source = {'services': {'web': {'image': 'nginx:1.27', 'ports': ['8080:80', '8443:443'],
                       'volumes': [{'type': 'bind', 'source': './data', 'target': '/data'},
                                   {'type': 'tmpfs', 'target': '/cache'}]}}}
            a.write_text(json.dumps(source))
            source['services']['web']['ports'].reverse()
            source['services']['web']['volumes'].reverse()
            b.write_text(json.dumps(source))
            old, _ = resolve([a], folder)
            new, _ = resolve([b], folder)
            self.assertEqual(differences(old, new), [])
            source['services']['web']['volumes'][1]['read_only'] = True
            b.write_text(json.dumps(source))
            new, _ = resolve([b], folder)
            changes = differences(old, new)
            self.assertEqual(len(changes), 1)
            self.assertTrue(changes[0]['path'].endswith('/read_only'))
