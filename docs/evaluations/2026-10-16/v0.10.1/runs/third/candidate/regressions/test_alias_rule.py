import unittest

from alias_rule import alias_warning


class AliasWarningWording(unittest.TestCase):
    def test_without_join_keeps_warning_with_new_wording(self):
        self.assertEqual(alias_warning(False), 'Avoid table aliases')

    def test_with_join_uses_new_wording(self):
        self.assertEqual(alias_warning(True), 'Avoid table aliases')

    def test_old_wording_is_gone(self):
        self.assertNotEqual(alias_warning(False), 'Avoid alias')
        self.assertNotEqual(alias_warning(True), 'Avoid alias')

    def test_join_flag_does_not_change_output_type(self):
        self.assertIsInstance(alias_warning(True), str)
        self.assertIsInstance(alias_warning(False), str)


if __name__ == '__main__':
    unittest.main()
