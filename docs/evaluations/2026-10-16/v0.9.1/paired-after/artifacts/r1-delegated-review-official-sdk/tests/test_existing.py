import unittest
from invoice import total
class ExistingTests(unittest.TestCase):
    def test_existing(self):
        self.assertEqual(total([{"price":"1.25","quantity":2}]), "2.50")
if __name__ == "__main__": unittest.main()
