import unittest
from units import convert
class ExistingTests(unittest.TestCase):
    def test_existing(self):
        self.assertEqual(float(convert("1.25", "kg", "g")), 1250)
if __name__ == "__main__": unittest.main()
