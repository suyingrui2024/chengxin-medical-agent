import unittest
from calculator import add


class AddRegression(unittest.TestCase):
    def test_positive_integers(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative_integers(self):
        self.assertEqual(add(-4, -6), -10)

    def test_mixed_sign(self):
        self.assertEqual(add(-7, 3), -4)

    def test_zero_identity(self):
        self.assertEqual(add(0, 0), 0)
        self.assertEqual(add(9, 0), 9)

    def test_is_not_subtraction(self):
        self.assertEqual(add(10, 3), 13)


if __name__ == '__main__':
    unittest.main()
