"""Generate font-independent SVG labels from editable local illustrations.

Development-only dependency: fontTools (verified with 4.25.0).
Font files stay unmodified. Patient runtime does not import fontTools.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/medagent/web"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def tag(name):
    return f"{{{NS}}}{name}"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def generate(name, font):
    source = ROOT / "assets/illustrations" / name
    original = source.read_bytes()
    svg = ET.fromstring(original)
    defs = svg.find(tag("defs"))
    if defs is None:
        defs = ET.SubElement(svg, tag("defs"))
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    metrics = font["hmtx"].metrics
    units = font["head"].unitsPerEm
    drawn = set()
    labels = []

    def visit(parent, inherited):
        for child in list(parent):
            style = {**inherited, **child.attrib}
            if child.tag != tag("text"):
                visit(child, style)
                continue
            assert len(child) == 0, "Review tspan or other shaped-text source manually"
            text = child.text or ""
            labels.append(text)
            size = float(style.get("font-size", "16"))
            scale = size / units
            width = sum(metrics[cmap[ord(c)]][0] for c in text) * scale
            x, y = float(style.get("x", "0")), float(style.get("y", "0"))
            anchor = style.get("text-anchor", "start")
            assert anchor in {"start", "middle", "end"}
            x -= width / 2 if anchor == "middle" else width if anchor == "end" else 0
            attrs = {
                key: value
                for key, value in child.attrib.items()
                if key
                not in {
                    "x",
                    "y",
                    "font-family",
                    "font-size",
                    "font-weight",
                    "text-anchor",
                }
            }
            attrs["aria-label"] = text
            attrs["transform"] = f"translate({x:g} {y:g}) scale({scale:g} {-scale:g})"
            if int(style.get("font-weight", "400")) >= 600:
                attrs.update(
                    stroke=style.get("fill", "#000"),
                    **{"stroke-width": "10", "stroke-linejoin": "round"},
                )
            group = ET.Element(tag("g"), attrs)
            ET.SubElement(group, tag("title")).text = text
            advance = 0
            for character in text:
                codepoint = ord(character)
                glyph = cmap[codepoint]
                identity = f"label-glyph-{codepoint:x}"
                if codepoint not in drawn:
                    pen = SVGPathPen(glyphs)
                    glyphs[glyph].draw(pen)
                    ET.SubElement(
                        defs, tag("path"), {"id": identity, "d": pen.getCommands()}
                    )
                    drawn.add(codepoint)
                ET.SubElement(
                    group, tag("use"), {"href": "#" + identity, "x": str(advance)}
                )
                advance += metrics[glyph][0]
            group.tail = child.tail
            position = list(parent).index(child)
            parent.remove(child)
            parent.insert(position, group)

    visit(svg, {"font-size": "16", "font-weight": "400", "fill": "#000"})
    assert labels and not svg.findall(".//" + tag("text"))
    ET.indent(svg, space="  ")
    result = ET.tostring(svg, encoding="utf-8") + b"\n"
    return result, {
        "file": name,
        "editable_source": str(source.relative_to(ROOT)),
        "source_sha256": digest(original),
        "output_sha256": digest(result),
        "labels": labels,
        "outlined_glyphs": len(drawn),
    }


def neutral_temperature_entry():
    """Describe the hand-drawn, text-free temperature asset without rewriting it."""
    target = WEB / "neutral-temperature.svg"
    original = target.read_bytes()
    svg = ET.fromstring(original)
    labels = [
        group.attrib["aria-label"]
        for group in svg.findall(".//" + tag("g"))
        if group.attrib.get("aria-label")
    ]
    assert labels == ["擦干腋窝", "腋窝中央", "数值、腋温和测量时间"]
    assert not svg.findall(".//" + tag("text"))
    return {
        "file": target.name,
        "editable_source": str(target.relative_to(ROOT)),
        "source_sha256": digest(original),
        "output_sha256": digest(original),
        "labels": labels,
        "outlined_glyphs": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = WEB / "NotoSansCJKsc-Regular.otf"
    font = TTFont(path)
    entries = [neutral_temperature_entry()]
    for name in ("body-location.svg",):
        data, entry = generate(name, font)
        target = WEB / name
        if args.check:
            assert target.read_bytes() == data, (
                "Generated illustration is stale: " + name
            )
        else:
            target.write_bytes(data)
        entries.append(entry)
    font.close()
    manifest = {
        "schema": "illustration-labels/v1",
        "font": path.name,
        "font_sha256": digest(path.read_bytes()),
        "method": "The active temperature illustration uses local vector primitives with title and description text only; labels remain in the accessible HTML step instructions. Body-location labels use local glyph outlines and explicit advances. No external font requests inside SVG images.",
        "files": entries,
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    output = WEB / "illustration-labels.json"
    if args.check:
        assert output.read_bytes() == encoded, "Illustration manifest is stale"
    else:
        output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "passed": True,
                "check_only": args.check,
                "illustrations": len(entries),
                "labels": sum(len(e["labels"]) for e in entries),
            }
        )
    )


if __name__ == "__main__":
    main()
