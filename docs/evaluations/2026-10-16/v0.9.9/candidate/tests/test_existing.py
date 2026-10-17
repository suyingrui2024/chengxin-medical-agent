import unittest
from calculator import add
class Existing(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
