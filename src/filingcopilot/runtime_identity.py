"""Detect on-disk source drift before a live worker accepts new mutations."""

import ast
import hashlib
from pathlib import Path


class RuntimeIdentity:
    def __init__(self, loaded_version: str, root: Path | None = None):
        self.root = root or Path(__file__).parent
        self.loaded_version = loaded_version
        self.paths = self.source_paths()
        self.hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths}
        self.fingerprint = hashlib.sha256(''.join(self.hashes.values()).encode()).hexdigest()
        if self.resource_version() != loaded_version:
            raise RuntimeError('Loaded code version differs from package resources; restart required')
        self.drifted = False

    def source_paths(self):
        paths = [*self.root.glob('*.py'), *self.root.glob('*.json'), *self.root.joinpath('static').glob('*')]
        return sorted(p for p in paths if p.is_file())

    def resource_version(self):
        try:
            tree = ast.parse((self.root/'__init__.py').read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
                    return ast.literal_eval(node.value)
        except (OSError, ValueError, SyntaxError):
            return None
        return None

    def inspect(self):
        # Windows can retain the same timestamp on two rapid same-size writes.
        # Compare content until drift is detected; the result is then monotonic.
        if not self.drifted:
            self.drifted = self.source_paths() != self.paths
            for path in self.paths:
                try:
                    different = hashlib.sha256(path.read_bytes()).hexdigest() != self.hashes[path]
                except OSError:
                    different = True
                if different:
                    self.drifted = True
                    break
        resource_version = self.resource_version()
        return {'loaded_version': self.loaded_version, 'resource_version': resource_version,
                'source_fingerprint': self.fingerprint,
                'needs_restart': self.drifted or resource_version != self.loaded_version}
