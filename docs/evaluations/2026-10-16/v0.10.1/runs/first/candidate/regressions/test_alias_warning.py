import unittest
from alias_rule import alias_warning


class AliasWarningRegression(unittest.TestCase):
    def test_without_join_keeps_warning_text(self):
        self.assertEqual(alias_warning(False), 'Avoid table aliases')

    def test_with_join_uses_new_text(self):
        self.assertEqual(alias_warning(True), 'Avoid table aliases')

    def test_old_wording_removed(self):
        self.assertNotEqual(alias_warning(False), 'Avoid alias')
        self.assertNotEqual(alias_warning(True), 'Avoid alias')

    def test_returns_str(self):
        for has_join in (True, False):
            with self.subTest(has_join=has_join):
                self.assertIsInstance(alias_warning(has_join), str)


if __name__ == '__main__':
    unittest.main()
