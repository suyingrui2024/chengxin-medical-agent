"""Retain measured rectangles from JSON reporter and the original failure traces."""
import base64
import json
from pathlib import Path
import zipfile
w=Path(__file__).resolve().parent
report=json.loads((w/'geometry-report.json').read_text())
assert report['stats']['expected']==3 and report['stats']['unexpected']==0 and not report['errors']
records=[]
def walk(suites):
 for suite in suites:
  for spec in suite.get('specs',[]):
   for test in spec.get('tests',[]):
    for result in test.get('results',[]):
     assert result['status']=='passed'
     for attachment in result.get('attachments',[]):
      if attachment['name']!='location-targets':continue
      value=json.loads(base64.b64decode(attachment['body']))
      assert len(value['targets'])==5 and not value['overlaps'] and len(value['states'])==5
      assert all(target['width']>=44 and target['height']>=44 for target in value['targets']+value['alternatives'])
      assert all(item['visible'] for state in value['states'] for item in state['geometry'])
      viewport=value['states'][0]['geometry'][0]['viewport']
      assert all(t['bottom']<=viewport['height'] for t in value['alternatives'])
      label=spec['title'].rsplit(' ',1)[-1];(w/f'geometry-{label}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
      records.append({'label':label,'viewport':viewport,'targets':len(value['targets']),'alternatives':len(value['alternatives']),'selected_categories':len(value['states']),'geometry_observations':sum(len(s['geometry']) for s in value['states']),'overlap_pairs':0,'all_measured_targets_at_least_44px':True})
  walk(suite.get('suites',[]))
walk(report['suites']);assert len(records)==3
baseline=[]
for path in (w/'baseline-failure').rglob('trace.zip'):
 with zipfile.ZipFile(path) as archive:
  for entry in archive.infolist():
   if entry.is_dir() or entry.file_size>30000:continue
   try:value=json.loads(archive.read(entry))
   except (ValueError,UnicodeError):continue
   if isinstance(value,dict) and isinstance(value.get('targets'),list) and 'overlaps' in value:
    baseline.append({'trace':str(path.relative_to(w)),'member':entry.filename,'measurement':value})
assert len(baseline)==2 and all(b['measurement']['overlaps']==[['胸部','腹部']] for b in baseline)
(w/'baseline-geometry.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2)+'\n')
summary={'baseline_traces_with_overlap':len(baseline),'final':records,'new_provider_calls':0,'scope':'Actual browser rectangles and controlled selections; does not measure patient comprehension or clinical validity.','passed':True}
(w/'geometry-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))
