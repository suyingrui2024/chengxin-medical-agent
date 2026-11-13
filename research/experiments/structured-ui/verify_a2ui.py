"""Check an existing capability result against pinned, official A2UI schemas.

Research experiment only: no desktop renderer, model call, or patient session.
Requires the development package jsonschema; application dependencies are unchanged.
"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import urllib.request

from jsonschema import Draft202012Validator, ValidationError

from medagent.extensions.builtin import load_registry


COMMIT = "1657e70119eaa3b76c2a106ac60c7c4dcfe206e0"
PREFIX = "specification/v0_9_1/"
FILES = {
    "json/server_to_client.json": "21693ba612f029940222ac788ff381c5fe0ef08f612312ff934db7a2daaca289",
    "json/common_types.json": "a13897d7dbe14c37512c5cbc9b8561782fc3b01e3a6ff114c1f12c82275b5884",
    "catalogs/basic/catalog.json": "8cc94d0a482e67048f9fc989964ca5da56fe42f531d919315a508989fb22e13e",
}


def read_schemas(directory, download=False):
    schemas = {}
    for name, digest in FILES.items():
        target = directory / PREFIX / name
        if download:
            url = f"https://raw.githubusercontent.com/a2ui-project/a2ui/{COMMIT}/{PREFIX}{name}"
            with urllib.request.urlopen(url, timeout=25) as response:
                raw = response.read()
        else:
            raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"Official schema checksum differs: {name}")
        if download:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        schemas[name] = json.loads(raw)
    return schemas


def make_validator(schemas):
    store = {}
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        store[schema["$id"]] = schema
    # v0.9.1 deliberately retains v0_9 canonical IDs. The envelope and common
    # types resolve their relative catalog.json reference to this alias.
    catalog = schemas["catalogs/basic/catalog.json"]
    store["https://a2ui.org/specification/v0_9/catalog.json"] = catalog
    envelope = schemas["json/server_to_client.json"]
    try:
        from referencing import Registry, Resource
    except ImportError:
        # Compatibility with the existing local jsonschema 4.17 development tool.
        from jsonschema import RefResolver

        def reject_remote(uri):
            raise ValueError(f"Unregistered schema reference: {uri}")

        resolver = RefResolver.from_schema(
            envelope,
            store=store,
            handlers={"http": reject_remote, "https": reject_remote},
        )
        return Draft202012Validator(envelope, resolver=resolver)
    registry = Registry().with_resources(
        (uri, Resource.from_contents(schema)) for uri, schema in store.items()
    )
    # No remote retrieval callback: all validation references must resolve locally.
    return Draft202012Validator(envelope, registry=registry)


def adapt_calculation(result, catalog_id):
    """Map one current first-party capability, without inferring clinical status."""
    if (
        result["capability_id"] != "calculate.units"
        or result["renderer"] != "calculation"
        or result["input_status"] != "unconfirmed"
        or result["quality"] != "not_assessed"
    ):
        raise ValueError(
            "This experiment supports current unconfirmed unit results only"
        )
    data = result["data"]
    surface_id = "research-unit-conversion"
    fields = {
        "title": "单位换算研究样例",
        "result": f"{data['value']} {data['unit']}",
        "formula": data["formula"],
        "method": data["method"],
        "status": "已计算；输入待核对；质量未评价",
        "provenance": (
            f"{result['capability_id']} @ {result['version']} · "
            f"{result['input_origin']} · {result['registry_sha256']}"
        ),
        "limitations": "；".join(data["limitations"]),
    }
    components = [{"id": "root", "component": "Column", "children": list(fields)}]
    for key in fields:
        components.append(
            {
                "id": key,
                "component": "Text",
                "text": {"path": f"/calculation/{key}"},
                "variant": "h2" if key == "result" else "body",
            }
        )
    return [
        {
            "version": "v0.9.1",
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": catalog_id,
            },
        },
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": components,
            },
        },
        {
            "version": "v0.9.1",
            "updateDataModel": {
                "surfaceId": surface_id,
                "path": "/",
                "value": {
                    "calculation": fields,
                    "input": deepcopy(result["input"]),
                    "input_status": result["input_status"],
                    "quality": result["quality"],
                    "review_status": result["review_status"],
                },
            },
        },
    ]


def verify(messages, validator):
    for message in messages:
        validator.validate(message)
    # JSON Schema alone does not check cross-message IDs or bound data paths.
    surface = messages[0]["createSurface"]["surfaceId"]
    update = messages[1]["updateComponents"]
    model = messages[2]["updateDataModel"]
    if update["surfaceId"] != surface or model["surfaceId"] != surface:
        raise ValueError("Surface identity mismatch")
    components = update["components"]
    ids = [component["id"] for component in components]
    if len(ids) != len(set(ids)) or ids[0] != "root":
        raise ValueError("Duplicate or missing root component")
    if set(components[0]["children"]) != set(ids[1:]):
        raise ValueError("Unresolved child references")
    for component in components[1:]:
        value = model["value"]
        for part in component["text"]["path"].lstrip("/").split("/"):
            value = value[part]
        if not isinstance(value, str):
            raise ValueError("Text binding does not resolve to a string")


def negative_checks(messages, validator):
    cases = []
    version = deepcopy(messages[0])
    version["version"] = "v1.0"
    cases.append(version)
    component = deepcopy(messages[1])
    component["updateComponents"]["components"][1]["component"] = "ArbitraryHTML"
    cases.append(component)
    executable = deepcopy(messages[1])
    executable["updateComponents"]["components"][1]["javascript"] = "alert(1)"
    cases.append(executable)
    binding = deepcopy(messages[1])
    binding["updateComponents"]["components"][1]["text"] = {"path": 42}
    cases.append(binding)
    for case in cases:
        try:
            validator.validate(case)
        except ValidationError:
            continue
        raise AssertionError("An invalid message unexpectedly passed")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema-dir", type=Path, default=Path("/tmp/medagent-a2ui-v091")
    )
    parser.add_argument(
        "--download", action="store_true", help="Fetch only pinned public schemas"
    )
    parser.add_argument(
        "--output", type=Path, help="Write the synthetic example as JSON"
    )
    args = parser.parse_args()
    schemas = read_schemas(args.schema_dir, args.download)
    validator = make_validator(schemas)
    registry = load_registry()
    result = registry.execute(
        "calculate.units",
        {
            "value": "1.25",
            "from_unit": "kg",
            "to_unit": "g",
        },
        "developer_preview",
    )
    if result["data"]["value"] != "1250":
        raise AssertionError("Unexpected capability result")
    messages = adapt_calculation(
        result, schemas["catalogs/basic/catalog.json"]["catalogId"]
    )
    verify(messages, validator)
    rejected = negative_checks(messages, validator)
    for target in ("mL", "unknown"):
        try:
            registry.execute(
                "calculate.units",
                {
                    "value": "1.25",
                    "from_unit": "kg",
                    "to_unit": target,
                },
                "developer_preview",
            )
        except ValueError:
            continue
        raise AssertionError("An invalid conversion unexpectedly passed")
    if args.output:
        args.output.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "upstream_commit": COMMIT,
                "protocol": "v0.9.1",
                "official_schemas_verified": len(FILES),
                "messages_validated": len(messages),
                "invalid_messages_rejected": rejected,
                "invalid_conversions_rejected": 2,
                "result": "1250 g",
                "desktop_renderer_integrated": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
