from pathlib import Path
import unittest
Path('tests/transient-output.txt').write_text('disposable test output\n')
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tests'))
raise SystemExit(0 if result.wasSuccessful() else 1)
