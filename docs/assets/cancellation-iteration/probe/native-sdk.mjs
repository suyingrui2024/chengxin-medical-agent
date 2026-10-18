// Private investigation only: real SDK and worker, no outer filesystem isolation.
import { DeepSeekHarness as OfficialHarness } from '../../node_modules/@deepseek-ai/dsh-sdk-client/lib/index.js';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
export class DeepSeekHarness extends OfficialHarness {
  constructor(options) {
    const state=options.env.CHENGXIN_STATE, workspace=options.env.CHENGXIN_WORKSPACE;
    const patch=join(state,'mac-investigation.yml');
    const text=readFileSync(join(root,'integrations/dsh/execution.patch.yml'),'utf8')
      .replaceAll('/opt/medical-agent/integrations/dsh/', pathToFileURL(join(root,'integrations/dsh/')).href)
      .replaceAll('/workspace',workspace);
    writeFileSync(patch,text);
    super({...options,dshBin:undefined,patches:[patch],dshHome:join(state,'home'),cwd:workspace,
      env:{...options.env,HOME:join(state,'home'),DSH_HOME:join(state,'home')}});
  }
}
