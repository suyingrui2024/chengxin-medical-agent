import io, unittest
from reader import read_rows
class ExistingTests(unittest.TestCase):
    def test_existing(self):
        self.assertEqual(read_rows(io.StringIO("group,value\na,1\n")), [{"group":"a","value":"1"}])
if __name__ == "__main__": unittest.main()
