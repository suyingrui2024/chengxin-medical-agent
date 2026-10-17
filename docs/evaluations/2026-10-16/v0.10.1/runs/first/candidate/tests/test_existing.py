import unittest
from alias_rule import alias_warning
class Existing(unittest.TestCase):
    def test_without_join(self):
        self.assertEqual(alias_warning(False), 'Avoid table aliases')
    def test_with_join(self):
        self.assertEqual(alias_warning(True), 'Avoid table aliases')
