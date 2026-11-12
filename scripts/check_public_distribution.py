"""Reject common private evidence artifacts from Git's public file set.

This filename guard is defense in depth, not content classification. Review new
text fixtures for identifying details and secrets before publishing them.
"""
from pathlib import PurePosixPath
import subprocess
import sys


def main():
    files = subprocess.run(['git', 'ls-files', '-z'], check=True, capture_output=True).stdout
    violations = []
    for raw in files.split(b'\0'):
        if not raw:
            continue
        name = raw.decode('utf-8')
        path = PurePosixPath(name)
        lowered = name.lower()
        blocked_directory = any(part.lower() in {
            'captures', 'private-evidence', 'private_evidence', 'raw-evidence', 'raw_evidence',
        } for part in path.parts)
        blocked_result = lowered.startswith('evaluation/results/')
        blocked_artifact = path.suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.patch', '.log', '.xml'}
        database_sidecar = any(marker in path.name.lower() for marker in ('.sqlite3-', '.db-'))
        if blocked_directory or blocked_result or blocked_artifact or database_sidecar:
            violations.append(name)
    if violations:
        print('Private evidence artifacts must not be tracked in the public source tree:')
        for name in violations:
            print(name)
        return 1
    print('Public file set contains no capture directories, result dumps or raw evidence artifacts.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
