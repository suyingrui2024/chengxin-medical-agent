"""Real Node JSON transport must preserve Win32 128-bit file identities."""
import json
import shutil
import subprocess
import unittest

from medagent.execution_identity import encode_identity, decode_identity


class IdentityTransportTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node"), "requires Node for actual JSON transport"
    )
    def test_large_ids_survive_a_real_javascript_json_roundtrip(self):
        original = ((1 << 64) - 17, (1 << 127) + 12345)
        source = "let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s))));"
        wire = subprocess.check_output(
            [shutil.which("node"), "-e", source],
            input=json.dumps(encode_identity(original)),
            text=True,
            timeout=10,
        )
        self.assertEqual(decode_identity(json.loads(wire)), original)

    def test_unsafe_numbers_and_malformed_identifiers_are_rejected(self):
        self.assertEqual(decode_identity([1, 2]), (1, 2))
        for invalid in [
            [1, 2**64],
            [True, 2],
            ["01", "2"],
            ["1", "-2"],
            ["1", str(2**128)],
            ["1", "２"],
            ["1", 2],
            [1.0, 2],
            ["1"],
        ]:
            with self.subTest(identity=invalid), self.assertRaises(ValueError):
                decode_identity(invalid)


if __name__ == "__main__":
    unittest.main()
