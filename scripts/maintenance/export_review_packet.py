"""Export an offline, unreviewed snapshot of the current medical content.

Uses only a fixed source allowlist. Does not import the application, read .env,
call a model, or treat an exported form as clinical approval.
"""

import argparse
import ast
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    "package.json",
    "scripts/maintenance/export_review_packet.py",
    "src/medagent/content/questions.json",
    "src/medagent/content/agent_prompt.txt",
    "src/medagent/triage.py",
    "src/medagent/interpreter.py",
    "src/medagent/extensions/builtin.py",
    "src/medagent/extensions/glucose.py",
    "src/medagent/extensions/input_evidence.py",
    "src/medagent/extensions/packs/temperature.json",
    "src/medagent/extensions/packs/location.json",
    "src/medagent/extensions/packs/units.json",
    "src/medagent/extensions/packs/glucose.json",
    "src/medagent/extensions/templates/general.json",
    "src/medagent/web/NotoSansCJKsc-Regular.otf",
    "src/medagent/web/fonts-OFL.txt",
    "src/medagent/web/font-sources.json",
    "src/medagent/web/neutral-temperature.svg",
    "src/medagent/web/body-location.svg",
    "src/medagent/web/illustration-labels.json",
    "assets/illustrations/body-location.svg",
]
LABELS = {
    "question": "问诊问题",
    "risk": "风险规则",
    "self_check": "图示任务",
    "calculation": "计算能力",
    "context": "完整规则与上下文",
}
FIELDS = [
    "item_id",
    "kind",
    "title",
    "source_sha256",
    "decision",
    "reviewer",
    "reviewed_at",
    "concern",
    "recommendation",
    "evidence_reference",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def collect(root):
    records = []

    def add(key, kind, title, content, source, version=None):
        records.append(
            {
                "id": key,
                "kind": kind,
                "title": title,
                "content": content,
                "source": source,
                "source_sha256": digest(root / source),
                "content_version": version,
                "clinical_review": "pending",
            }
        )

    path = "src/medagent/content/questions.json"
    catalog = read_json(root / path)
    for question in catalog["questions"]:
        add(
            "question." + question["id"],
            "question",
            question["prompt"],
            question,
            path,
            catalog["version"],
        )

    path = "src/medagent/triage.py"
    code = (root / path).read_text(encoding="utf-8")
    tree = ast.parse(code)
    rule_assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "EMERGENCY_RULES" for t in node.targets
        )
    ]
    if len(rule_assignments) != 1 or not isinstance(
        rule_assignments[0].value, ast.Tuple
    ):
        raise ValueError("Risk rule structure changed; update the exporter explicitly")
    for node in rule_assignments[0].value.elts:
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "PhraseRule"
            or node.keywords
            or len(node.args) != 3
        ):
            raise ValueError("Unsupported risk rule; refusing a partial export")
        key, phrases, label = [ast.literal_eval(value) for value in node.args]
        add(
            "risk." + key,
            "risk",
            label,
            {"id": key, "phrases": list(phrases), "label": label},
            path,
        )
    add("context.triage", "context", "完整风险逻辑：否定处理、阈值、分流与急症保留", code, path)

    for name in ["temperature", "location", "units", "glucose"]:
        path = "src/medagent/extensions/packs/" + name + ".json"
        pack = read_json(root / path)
        add(pack["id"], pack["kind"], pack["name"], pack, path, pack["version"])

    path = "src/medagent/content/agent_prompt.txt"
    add(
        "context.planner",
        "context",
        "当前问诊规划提示",
        (root / path).read_text(encoding="utf-8"),
        path,
    )
    path = "src/medagent/extensions/templates/general.json"
    template = read_json(root / path)
    add(
        "context.template",
        "context",
        template["title"],
        template,
        path,
        template["version"],
    )
    if len({item["id"] for item in records}) != len(records):
        raise ValueError("Duplicate review IDs")
    return records


def render(records, version):
    escape = html.escape
    cards = []
    for item in records:
        content = item["content"]
        preview = ""
        if item["kind"] == "question":
            options = content.get("options", [])
            preview = (
                '<div class="chips">'
                + "".join("<span>" + escape(o["label"]) + "</span>" for o in options)
                + "</div>"
            )
        elif item["kind"] == "self_check":
            task = content["task"]
            steps = task["steps"]
            assets = task.get("step_assets", [task["asset"]] * len(steps))
            if len(assets) != len(steps):
                raise ValueError("Each illustrated step must have a matching asset")
            panels = []
            for i, (step, asset) in enumerate(zip(steps, assets), 1):
                if asset.split("#")[0] not in [
                    "/neutral-temperature.svg",
                    "/body-location.svg",
                ]:
                    raise ValueError("Unapproved illustration path")
                panels.append(
                    '<figure><img src="sources/src/medagent/web'
                    + escape(asset, quote=True)
                    + '" alt="'
                    + escape(step, quote=True)
                    + '"><figcaption><b>'
                    + str(i)
                    + "</b> "
                    + escape(step)
                    + "</figcaption></figure>"
                )
            preview = '<div class="steps">' + "".join(panels) + "</div>"
            preview += (
                '<p class="limits">停止条件：'
                + escape("；".join(task["stop_conditions"]))
                + "</p>"
            )
            preview += (
                '<p class="limits">适用限制：'
                + escape("；".join(task["clinical_limits"]))
                + "</p>"
            )
        elif item["kind"] == "risk":
            preview = (
                '<div class="chips">'
                + "".join("<span>" + escape(p) + "</span>" for p in content["phrases"])
                + '</div><p class="limits">单条短语不等于完整分流逻辑，请同时审阅下方完整规则。</p>'
            )
        elif item["kind"] == "calculation":
            preview = (
                "<p>"
                + escape(content["description"])
                + '</p><p class="limits">源审核状态：'
                + escape(content["review_status"])
                + "</p>"
            )
        raw = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, indent=2)
        )
        search = item["title"] + " " + raw + " " + item["id"]
        cards.append(
            '<article data-kind="'
            + item["kind"]
            + '" data-search="'
            + escape(search, quote=True)
            + '" id="'
            + escape(item["id"], quote=True)
            + '"><header><small>'
            + LABELS[item["kind"]]
            + " · "
            + escape(item["id"])
            + '</small><span class="pending">待审核</span></header><h2>'
            + escape(item["title"])
            + "</h2>"
            + preview
            + "<details><summary>查看完整原文、条件与字段</summary><pre>"
            + escape(raw)
            + '</pre></details><footer><a href="sources/'
            + escape(item["source"], quote=True)
            + '">'
            + escape(item["source"])
            + "</a><br>内容版本："
            + escape(str(item["content_version"] or "以文件摘要标识"))
            + "<br>SHA256：<code>"
            + item["source_sha256"]
            + "</code></footer></article>"
        )
    counts = {kind: sum(r["kind"] == kind for r in records) for kind in LABELS}
    options = "".join(
        '<option value="'
        + kind
        + '">'
        + label
        + " · "
        + str(counts[kind])
        + "</option>"
        for kind, label in LABELS.items()
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;self&#39;; script-src &#39;self&#39;; img-src &#39;self&#39;; font-src &#39;self&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;"><title>知微 · 内容审阅材料</title><link rel="stylesheet" href="review.css"><script src="review.js" defer></script></head><body><main><div class="masthead"><p>知微 · '
        + escape(version)
        + " · 离线内容快照</p><h1>逐项审阅，保留依据。</h1><p>这是待审核材料，共 "
        + str(len(records))
        + ' 项。没有临床签署、用户研究结果或医疗有效性结论。</p><p><a href="review-form.csv">下载空白审核表</a> · <a href="records.json">结构化记录</a> · <a href="manifest.json">文件摘要</a> · <a href="readme.txt">范围与用法</a></p></div><nav aria-label="审阅筛选"><label>内容类型 <select id="kind"><option value="">全部 · '
        + str(len(records))
        + "</option>"
        + options
        + '</select></label><label>查找原文 <input id="search" type="search" placeholder="问题、步骤、关键词或ID"></label><output id="count" aria-live="polite"></output></nav><p class="notice">图示按当前源文件呈现，不表示动作正确性已经审核。条件、阈值和计算实现需结合完整来源文件检查；本包不覆盖全部运行时行为。</p><div id="cards">'
        + "".join(cards)
        + '</div><p id="empty" hidden>没有匹配内容。</p></main></body></html>'
    )


CSS = """@font-face{font-family:ReviewCJK;src:url('sources/src/medagent/web/NotoSansCJKsc-Regular.otf')}*{box-sizing:border-box}body{margin:0;background:#f4f6f1;color:#203c33;font:15px/1.65 ReviewCJK,system-ui,sans-serif}main{max-width:1140px;margin:auto;padding:40px 28px 80px}.masthead{padding:10px 0 24px}h1{font-size:38px;letter-spacing:-1px;margin:10px 0}h2{font-size:21px;line-height:1.5;margin:16px 0}p{margin:10px 0}a{color:#235f4e;text-underline-offset:4px}nav{position:sticky;top:0;z-index:1;background:#f4f6f1;display:flex;align-items:end;gap:20px;padding:18px 0;border-bottom:1px solid #d8e3d6}label{display:grid;gap:5px}input,select{font:inherit;padding:10px 12px;border:1px solid #b6c9b1;border-radius:8px;background:#fff;color:inherit}input{width:min(48vw,420px)}output{margin-left:auto;padding-bottom:12px;white-space:nowrap}.notice,.limits,footer{font-size:13px;color:#596c5c}.notice{padding:14px 0}article{background:white;border:1px solid #dce5d8;border-radius:16px;padding:24px;margin:16px 0}article header{display:flex;justify-content:space-between;gap:12px;align-items:center}small{color:#667960}.pending{font-size:12px;background:#fff2d8;color:#79591d;padding:3px 10px;border-radius:20px;white-space:nowrap}.chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.chips span{background:#edf3e7;padding:5px 12px;border-radius:8px}.steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}figure{margin:0;background:#f0f5eb;border-radius:12px;padding:12px}img{width:100%;height:235px;object-fit:contain}figcaption{font-size:14px}figcaption b{display:inline-block;background:#d7e7c4;width:24px;text-align:center;border-radius:50%;margin-right:5px}summary{cursor:pointer;padding:12px 0}details{margin-top:12px;border-top:1px solid #e4ebe0}pre{max-height:460px;overflow:auto;background:#f5f7f3;padding:16px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 ReviewCJK,ui-monospace,monospace}footer{margin-top:12px;overflow-wrap:anywhere}code{font-family:ReviewCJK,ui-monospace,monospace}input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #235f4e;outline-offset:3px}[hidden]{display:none!important}@media(max-width:700px){main{padding:20px 16px}h1{font-size:30px}nav{flex-wrap:wrap;gap:10px}input{width:100%}label{flex:1;min-width:160px}output{width:100%;padding:0}.steps{grid-template-columns:1fr}article{padding:18px}img{height:260px}}"""
JS = """const kind=document.getElementById('kind'),search=document.getElementById('search'),cards=[...document.querySelectorAll('article')];function filter(){const query=search.value.trim().toLocaleLowerCase();let count=0;for(const card of cards){card.hidden=!!((kind.value&&card.dataset.kind!==kind.value)||(query&&!card.dataset.search.toLocaleLowerCase().includes(query)));if(!card.hidden)count++;}document.getElementById('count').textContent=count+' / '+cards.length+' 项';document.getElementById('empty').hidden=count!==0;}kind.addEventListener('change',filter);search.addEventListener('input',filter);filter();\n"""


def verify(output):
    manifest = read_json(output / "manifest.json")
    if any(p.is_symlink() for p in output.rglob("*")):
        raise ValueError("Review snapshot must not contain symlinks")
    actual = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("Unexpected or missing packet files")
    for name, expected in manifest["files"].items():
        path = output / name
        if (
            not path.resolve().is_relative_to(output.resolve())
            or digest(path) != expected
        ):
            raise ValueError("Packet drift: " + name)
    records = read_json(output / "records.json")
    if records != collect(output / "sources"):
        raise ValueError("Review items differ from their frozen sources")
    with (output / "review-form.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if [row["item_id"] for row in rows] != [item["id"] for item in records]:
        raise ValueError("Review form coverage differs")
    if any(row[field] for row in rows for field in FIELDS[4:]):
        raise ValueError("Initial review template must remain unsigned and blank")
    return {
        "passed": True,
        "items": len(records),
        "files_checked": len(manifest["files"]),
        "clinical_review": "pending",
        "model_requests": 0,
    }


def export(root, output):
    if output.exists():
        raise ValueError("A fresh output directory is required")
    records = collect(root)
    version = read_json(root / "package.json")["version"]
    for name in SOURCES:
        if not (root / name).is_file() or (root / name).is_symlink():
            raise ValueError("Missing or linked source: " + name)
    page = render(records, version)
    output.mkdir(parents=True)
    for name in SOURCES:
        target = output / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    write_json(output / "records.json", records)
    for name, text in [("index.html", page), ("review.css", CSS), ("review.js", JS)]:
        (output / name).write_text(text, encoding="utf-8")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for item in records:
        writer.writerow(
            {
                "item_id": item["id"],
                "kind": item["kind"],
                "title": item["title"],
                "source_sha256": item["source_sha256"],
            }
        )
    with (output / "review-form.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as review_form:
        review_form.write(stream.getvalue())
    (output / "readme.txt").write_text(
        "知微 " + version + " · 待审核内容快照\n\n"
        "打开index.html可离线筛选问题、规则、图示和计算能力。所有项目均待审核。\n"
        "请复制review-form.csv后填写审核人、日期、问题、修改建议及依据；不要覆盖此原始快照。\n"
        "临床审核、独立病例评价、用户理解与操作试验均未在本包中完成。\n"
        "风险短语需结合完整triage.py审核，不能把单条短语当作完整分流逻辑。\n"
        "sources包含固定白名单源码和原始字节的图示／字体；不包含患者数据或.env。\n"
        "本包只呈现静态内容及相关实现，不是全量运行时行为、安全或临床有效性审计。\n"
        "字体版权、OFL与来源见sources/src/medagent/web/，本包仅使用其中Sans字体。\n"
        "核对命令：python3 -B scripts/maintenance/export_review_packet.py --verify 此目录\n",
        encoding="utf-8",
    )
    write_json(
        output / "manifest.json",
        {
            "schema": "medical-content-review-packet/v1",
            "app_version": version,
            "clinical_review": "pending",
            "source_files": len(SOURCES),
            "items_by_kind": {
                kind: sum(r["kind"] == kind for r in records) for kind in LABELS
            },
            "files": {
                str(p.relative_to(output)): digest(p)
                for p in sorted(output.rglob("*"))
                if p.is_file()
            },
        },
    )
    return verify(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = (
        verify(args.verify.resolve())
        if args.verify
        else export(ROOT, args.output.resolve())
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
