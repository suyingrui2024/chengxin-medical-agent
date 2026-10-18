# Investigation only: keep the actual host/SDK but bypass the outer Linux launcher on macOS.
from pathlib import Path
import os,sys,subprocess,shutil,unittest
from unittest.mock import patch
root=Path.cwd();sys.path[:0]=[str(root/'src'),str(root)]
os.environ['RUN_DSH_EXECUTION']='1'
from tests.test_execution_cancellation import TaskCancellationRuntimeTests
original_popen=subprocess.Popen; original_which=shutil.which
loader=(root/'.runtime/selective-runtime-replay/sdk-loader.mjs').as_uri()
def popen(command,*args,**kwargs):
 if isinstance(command,list) and len(command)>1 and str(command[1]).endswith('/integrations/dsh/execution-worker.mjs'):
  command=[command[0],'--no-warnings','--loader',loader,*command[1:]]
 return original_popen(command,*args,**kwargs)
def which(name,*args,**kwargs):
 return '/test-only/direct-sdk' if name=='bwrap' else original_which(name,*args,**kwargs)
with patch('medagent.execution.subprocess.Popen',side_effect=popen),patch('medagent.execution.shutil.which',side_effect=which):
 result=unittest.TextTestRunner(verbosity=2).run(((unittest.defaultTestLoader.loadTestsFromName(sys.argv[1]) if "." in sys.argv[1] else unittest.defaultTestLoader.loadTestsFromName(sys.argv[1], TaskCancellationRuntimeTests)) if len(sys.argv)>1 else unittest.defaultTestLoader.loadTestsFromTestCase(TaskCancellationRuntimeTests)))
sys.exit(not result.wasSuccessful())
