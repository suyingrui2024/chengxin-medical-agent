import unittest
from calculator import add
class NewTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 2), 5)
