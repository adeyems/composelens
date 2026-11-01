import unittest
from unittest.mock import patch
from composelens import differences, validate_model_budget

class ModelBudgetTest(unittest.TestCase):
    def test_width_and_depth_fail_without_partial_diff(self):
        with patch('composelens.MAX_MODEL_NODES', 5):
            validate_model_budget({'a':[1,2,3]})
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                differences({}, {'a':[1,2,3,4]})
        with patch('composelens.MAX_MODEL_DEPTH', 2):
            validate_model_budget({'a':[1]})
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                differences({'a':[[1]]}, {})
    def test_normal_models_keep_semantics(self):
        self.assertEqual(differences({'services':{}}, {'services':{'web':{}}}),
                         [{'path':'/services/web','kind':'added'}])
