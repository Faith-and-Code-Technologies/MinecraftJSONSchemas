#!/usr/bin/env python3

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any
from urllib.parse import urlparse, unquote


class SchemaValidator:
    def __init__(self, schema_dir: str):
        self.schema_dir = Path(schema_dir).resolve()
        self.schemas: Dict[str, Any] = {}
        self.file_mappings: Dict[str, Path] = {}
        self.errors: List[str] = []
        self.warnings: List[str] = []
        
    def load_schemas(self) -> None:
        print(f"Loading schemas from: {self.schema_dir}")
        
        base_dir = self.schema_dir.parent if self.schema_dir.parent else self.schema_dir
        
        json_files = list(self.schema_dir.rglob("*.json"))
        print(f"Found {len(json_files)} JSON files in schema directory")
        
        self.file_mappings = {}
        
        for file_path in json_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    schema_data = json.load(f)
                
                rel_path = file_path.relative_to(self.schema_dir)
                rel_path_str = str(rel_path).replace('\\', '/')
                self.schemas[rel_path_str] = schema_data
                self.file_mappings[rel_path_str] = file_path
                
            except json.JSONDecodeError as e:
                self.errors.append(f"Invalid JSON in {file_path}: {e}")
            except Exception as e:
                self.errors.append(f"Error loading {file_path}: {e}")
        
        self._load_referenced_files()
    
    def _load_referenced_files(self) -> None:
        print("Scanning for additional referenced files...")
        
        additional_files = set()
        
        for file_path, schema in self.schemas.items():
            refs = self.extract_refs(schema)
            for ref in refs:
                if not ref.startswith("#") and ("../" in ref or ref.startswith("../")):
                    file_part = ref.split("#")[0] if "#" in ref else ref
                    
                    current_file_abs = self.file_mappings[file_path]
                    target_file_abs = (current_file_abs.parent / file_part).resolve()
                    
                    if target_file_abs.exists() and target_file_abs.suffix == '.json':
                        additional_files.add(target_file_abs)
        
        for file_path in additional_files:
            if file_path not in self.file_mappings.values():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        schema_data = json.load(f)
                    
                    try:
                        rel_path = file_path.relative_to(self.schema_dir)
                        key = str(rel_path).replace('\\', '/')
                    except ValueError:
                        key = str(file_path).replace('\\', '/')
                    
                    self.schemas[key] = schema_data
                    self.file_mappings[key] = file_path
                    print(f"  Loaded additional file: {key}")
                    
                except Exception as e:
                    print(f"  Could not load referenced file {file_path}: {e}")
    
    def extract_refs(self, obj: Any, current_path: str = "") -> List[str]:
        refs = []
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "$ref" and isinstance(value, str):
                    refs.append(value)
                else:
                    refs.extend(self.extract_refs(value, f"{current_path}.{key}" if current_path else key))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                refs.extend(self.extract_refs(item, f"{current_path}[{i}]" if current_path else f"[{i}]"))
        
        return refs
    
    def resolve_ref_path(self, ref: str, current_file: str) -> Tuple[str, str]:
        if ref.startswith("#"):
            return current_file, ref[1:]
        
        if "#" in ref:
            file_part, definition_part = ref.split("#", 1)
            definition_part = definition_part if definition_part.startswith("/") else f"/{definition_part}"
        else:
            file_part = ref
            definition_part = ""
        
        current_file_abs = self.file_mappings[current_file]
        target_file_abs = (current_file_abs.parent / file_part).resolve()
        
        target_key = None
        for key, abs_path in self.file_mappings.items():
            if abs_path.resolve() == target_file_abs:
                target_key = key
                break
        
        if target_key is None:
            try:
                target_key = str(target_file_abs.relative_to(self.schema_dir)).replace('\\', '/')
            except ValueError:
                target_key = str(target_file_abs).replace('\\', '/')
        
        return target_key, definition_part[1:] if definition_part.startswith("/") else definition_part
    
    def navigate_to_definition(self, schema: Any, path: str) -> Any:
        if not path:
            return schema
            
        parts = path.split("/")
        current = schema
        
        for part in parts:
            if not part:
                continue
                
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        
        return current
    
    def validate_refs(self) -> None:
        print(f"\nValidating references in {len(self.schemas)} schemas...")
        
        total_refs = 0
        valid_refs = 0
        
        for file_path, schema in self.schemas.items():
            refs = self.extract_refs(schema)
            total_refs += len(refs)
            
            for ref in refs:
                try:
                    target_file, definition_path = self.resolve_ref_path(ref, file_path)
                    
                    if target_file not in self.schemas:
                        self.errors.append(f"In {file_path}: Reference '{ref}' points to non-existent file '{target_file}'")
                        continue
                    
                    if definition_path:
                        target_schema = self.schemas[target_file]
                        definition = self.navigate_to_definition(target_schema, definition_path)
                        
                        if definition is None:
                            self.errors.append(f"In {file_path}: Reference '{ref}' points to non-existent definition '{definition_path}' in '{target_file}'")
                            continue
                    
                    valid_refs += 1
                    
                except Exception as e:
                    self.errors.append(f"In {file_path}: Error validating reference '{ref}': {e}")
        
        print(f"Validated {total_refs} references ({valid_refs} valid, {total_refs - valid_refs} invalid)")
    
    def find_unused_definitions(self) -> None:
        print("\nChecking for unused definitions...")
        
        all_definitions = {}
        for file_path, schema in self.schemas.items():
            if isinstance(schema, dict) and "definitions" in schema:
                for def_name in schema["definitions"].keys():
                    all_definitions[f"{file_path}#/definitions/{def_name}"] = False
        
        for file_path, schema in self.schemas.items():
            refs = self.extract_refs(schema)
            for ref in refs:
                try:
                    target_file, definition_path = self.resolve_ref_path(ref, file_path)
                    if definition_path:
                        full_ref = f"{target_file}#{definition_path if definition_path.startswith('/') else '/' + definition_path}"
                        if full_ref in all_definitions:
                            all_definitions[full_ref] = True
                except:
                    pass
        
        unused = [ref for ref, used in all_definitions.items() if not used]
        if unused:
            print(f"Found {len(unused)} unused definitions:")
            for ref in unused:
                self.warnings.append(f"Unused definition: {ref}")
        else:
            print("No unused definitions found!")
    
    def print_summary(self) -> None:
        print("\n" + "="*60)
        print("VALIDATION SUMMARY")
        print("="*60)
        
        print(f"Schemas loaded: {len(self.schemas)}")
        print(f"Errors found: {len(self.errors)}")
        print(f"Warnings: {len(self.warnings)}")
        
        if self.errors:
            print(f"\n🚫 ERRORS ({len(self.errors)}):")
            for error in self.errors:
                print(f"  • {error}")
        
        if self.warnings:
            print(f"\n⚠️  WARNINGS ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"  • {warning}")
        
        if not self.errors and not self.warnings:
            print("\n✅ All schema references are valid!")
        elif not self.errors:
            print(f"\n✅ All references are valid (with {len(self.warnings)} warnings)")
        else:
            print(f"\n❌ Validation failed with {len(self.errors)} errors")
    
    def run_validation(self) -> bool:
        print("Schema Reference Validator")
        print("="*40)
        
        self.load_schemas()
        
        if not self.schemas:
            print("No schemas found to validate!")
            return False
        
        self.validate_refs()
        self.find_unused_definitions()
        self.print_summary()
        
        return len(self.errors) == 0


def main():
    if len(sys.argv) != 2:
        print("Usage: python schema_validator.py <schema_directory>")
        print("Example: python schema_validator.py schemas/1.21.5/")
        sys.exit(1)
    
    schema_dir = sys.argv[1]
    
    if not os.path.exists(schema_dir):
        print(f"Error: Directory '{schema_dir}' does not exist!")
        sys.exit(1)
    
    validator = SchemaValidator(schema_dir)
    success = validator.run_validation()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()