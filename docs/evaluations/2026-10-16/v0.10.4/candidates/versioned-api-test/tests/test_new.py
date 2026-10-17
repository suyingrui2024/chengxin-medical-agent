import unittest
from project_api import signature
class NewTests(unittest.TestCase):
    def test_signature(self):
        self.assertEqual(signature(3, show_annotations=True), '3:int')
