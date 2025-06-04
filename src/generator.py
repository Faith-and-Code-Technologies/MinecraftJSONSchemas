# Generates common/generated/*

import os
import shutil
import zipfile
import json
from pathlib import Path
from collections import defaultdict, OrderedDict
import platform

def infer_jsonschema_type(values):
    if all(v in ("true", "false") for v in values):
        return "boolean", None
    return "string", sorted(values)

def extract_blockstates(version: str):
    if platform.system() == "Windows":
        base_path = os.path.expandvars(r"%APPDATA%\.minecraft")
    elif platform.system() == "Darwin":
        base_path = os.path.expanduser("~/Library/Application Support/minecraft")
    else:
        base_path = os.path.expanduser("~/.minecraft")

    minecraft_path = os.path.join(base_path, "versions", version, f"{version}.jar")

    if not os.path.exists(minecraft_path):
        print(f"Minecraft version {version} not found at {minecraft_path}")
        return

    current_directory = Path(__file__).parent
    zip_path = current_directory / f"{version}.jar"

    shutil.copy(minecraft_path, zip_path)
    zip_file_path = zip_path.with_suffix(".zip")
    os.rename(zip_path, zip_file_path)

    extract_folder = current_directory / "extracted_files"
    extract_folder.mkdir(exist_ok=True)

    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        zip_ref.extractall(extract_folder)

    blocks_path = extract_folder / "assets/minecraft/blockstates"
    blocks = []
    blockstates_data = {}

    blockstates_schema = {}

    for file in blocks_path.glob("*.json"):
        block_name = file.name.removesuffix(".json")
        blocks.append(block_name)

        with open(file, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue

            variants = data.get("variants", {})
            multipart = data.get("multipart", [])

            state_values = defaultdict(set)

            if variants:
                for variant_key in variants:
                    if variant_key == "":
                        continue
                    pairs = variant_key.split(",")
                    for pair in pairs:
                        key, value = pair.split("=")
                        state_values[key.strip()].add(value.strip())

            elif multipart:
                for part in multipart:
                    when = part.get("when")
                    if isinstance(when, dict):
                        for key, value in when.items():
                            if isinstance(value, list):
                                for v in value:
                                    if isinstance(v, dict):
                                        state_values[key].add(json.dumps(v, sort_keys=True))
                                    else:
                                        state_values[key].add(str(v))
                            else:
                                if isinstance(value, dict):
                                    state_values[key].add(json.dumps(value, sort_keys=True))
                                else:
                                    state_values[key].add(str(value))

            if state_values:
                properties = OrderedDict()
                for key, values in sorted(state_values.items()):
                    inferred_type, enum_vals = infer_jsonschema_type(values)
                    schema = {"type": inferred_type}
                    if enum_vals:
                        schema["enum"] = enum_vals
                    properties[key] = schema

                blockstates_schema[block_name] = {
                    "type": "object",
                    "properties": properties,
                    "required": []
                }

    # Cleanup
    zip_file_path.unlink()
    shutil.rmtree(extract_folder)

    blocks.sort()

    # Create detailed oneOf schema
    blockstate_oneof = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "blockStates": {
                "oneOf": []
            }
        }
    }

    for block_name in blocks:
        state_schema = blockstates_schema.get(block_name)

        if state_schema:
            # Block has defined state properties
            properties_schema = {
                **state_schema,
                "additionalProperties": False
            }
        else:
            # Block has no states — allow empty object only
            properties_schema = {
                "type": "object",
                "maxProperties": 0
            }

        wrapped_schema = {
            "type": "object",
            "properties": {
                "block": {
                    "type": "string",
                    "title": "Block",
                    "const": block_name
                },
                "properties": properties_schema
            },
            "required": ["block"],
            "additionalProperties": False
        }

        blockstate_oneof["definitions"]["blockStates"]["oneOf"].append(wrapped_schema)

    # Save full version (detailed oneOf)
    with open(f"schemas/{version}/common/generated/blockstates_oneof.json", "w") as f:
        json.dump(blockstate_oneof, f, indent=4)

    # Save simple original version (just definitions)
    blockstates_simple = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": {
            "blockStates": blockstates_schema
        }
    }

    with open(f"schemas/{version}/common/generated/blockstates.json", "w") as f:
        json.dump(blockstates_simple, f, indent=4)

if __name__ == "__main__":
    extract_blockstates("1.21.5") # input version