import ast
import json
import os
import re
from datetime import datetime


class DependencyScanner:
    def __init__(self, project_path: str):
        self.project_path = project_path

        # 1. FOLDER BLACKLIST (their entire contents are skipped)
        self.ignored_dirs = {
            ".git",
            "node_modules",
            "venv",
            ".venv",
            "env",
            ".env",
            "__pycache__",
            ".pytest_cache",
            ".idea",
            ".vscode",
            ".orion",
            "dist",
            "build",
            ".next",
            "out",
            "public",
            "assets", 
            "_probe", 
            "test", 
            "tests",
            "target",
        }

        # 2. SPECIFIC FILE BLACKLIST (files that are often huge but need not be read by the AI)
        self.ignored_files = {
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "bun.lockb",
            "poetry.lock",
            "Pipfile.lock",
            "requirements.txt",
            ".env",
            ".env.local",
            ".env.production",
            ".env.example",
            "tsconfig.json",
            "pyproject.toml",
            "setup.py",  # Optional: remove if you want the AI to read the config
        }

        # 3. EXTENSION WHITELIST (only files with these extensions are scanned)
        self.supported_extensions = {
            ".py",
            ".ts",
            ".js",
            ".tsx",
            ".jsx",
            ".md",
            ".txt",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".cfg",
            ".ini",
        }

    def scan(self) -> dict:
        """Scan the whole project and produce the dependency map."""
        files_data = {}

        for root, dirs, files in os.walk(self.project_path):
            # Filter ignored folders (prevents os.walk from entering venv/node_modules)
            dirs[:] = [d for d in dirs if d not in self.ignored_dirs]

            for file in files:
                # Filter specifically blacklisted files
                if file in self.ignored_files:
                    continue

                ext = os.path.splitext(file)[1].lower()

                # Only process files with supported extensions
                if ext not in self.supported_extensions:
                    continue

                file_path = os.path.join(root, file)
                # Use forward slashes for consistency on Windows & Linux
                rel_path = os.path.relpath(file_path, self.project_path).replace(
                    "\\", "/"
                )

                try:
                    if ext == ".py":
                        file_info = self._parse_python(file_path)
                    elif ext in {".ts", ".js", ".tsx", ".jsx"}:
                        file_info = self._parse_typescript(file_path)
                    else:
                        # For config/md/json files we only store metadata (name & size)
                        # so the AI knows the file exists without reading its contents (saves tokens)
                        file_size = os.path.getsize(file_path)
                        file_info = {
                            "language": "config/doc",
                            "size_bytes": file_size,
                            "imports": [],
                            "exports": {
                                "classes": [],
                                "functions": [],
                                "variables": [],
                            },
                        }

                    files_data[rel_path] = file_info
                except Exception as e:
                    # Skip files that error (e.g. syntax errors) so scanning never crashes
                    print(f"[SCANNER WARNING] Failed to parse {rel_path}: {e}")
                    continue

        # Build reverse map (Reverse map: which files contain Class/Function X?)
        reverse_map = self._build_reverse_map(files_data)

        result = {
            "generated_at": datetime.now().isoformat(),
            "total_files": len(files_data),
            "files": files_data,
            "reverse_map": reverse_map,
        }

        # Save to .orion/dependency_map.json
        self._save_map(result)

        return result

    def _parse_python(self, file_path: str) -> dict:
        """Python-specific parser using the built-in 'ast' library."""
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)

        imports = []
        classes = []
        functions = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        {"module": alias.name, "names": [alias.asname or alias.name]}
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names = [alias.name for alias in node.names]
                    imports.append({"module": node.module, "names": names})
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)

        return {
            "language": "python",
            "imports": imports,
            "exports": {"classes": classes, "functions": functions, "variables": []},
        }

    def _parse_typescript(self, file_path: str) -> dict:
        """TypeScript/JavaScript-specific parser using Regex."""
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()

        imports = []
        classes = []
        functions = []

        # Match: import { X, Y } from "module" or import X from "module"
        import_pattern = re.compile(
            r'import\s+(?:(?:\{([^}]+)\})|(\w+))\s+from\s+["\']([^"\']+)["\']'
        )
        for match in import_pattern.finditer(source):
            if match.group(1):  # { X, Y }
                names = [
                    n.strip().split(" as ")[0].strip()
                    for n in match.group(1).split(",")
                    if n.strip()
                ]
            else:  # default import
                names = [match.group(2)]
            module = match.group(3)
            imports.append({"module": module, "names": names})

        # Match: export class ClassName / class ClassName
        class_pattern = re.compile(r"(?:export\s+)?class\s+(\w+)")
        classes = class_pattern.findall(source)

        # Match: export function funcName / function funcName
        func_pattern = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)")
        functions = func_pattern.findall(source)

        return {
            "language": "typescript",
            "imports": imports,
            "exports": {"classes": classes, "functions": functions, "variables": []},
        }

    def _build_reverse_map(self, files_data: dict) -> dict:
        """Build the reverse map tracking where a Class/Function is defined."""
        reverse_map = {}

        for file_path, info in files_data.items():
            for export_type in ["classes", "functions"]:
                for name in info["exports"].get(export_type, []):
                    if name not in reverse_map:
                        reverse_map[name] = []
                    if file_path not in reverse_map[name]:
                        reverse_map[name].append(file_path)

        return reverse_map

    def _save_map(self, data: dict):
        """Save the scan result to a JSON file in the .orion/ folder"""
        orion_dir = os.path.join(self.project_path, ".orion")
        if not os.path.exists(orion_dir):
            os.makedirs(orion_dir)

        map_path = os.path.join(orion_dir, "dependency_map.json")
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[SCANNER] Dependency map saved to {map_path}")
