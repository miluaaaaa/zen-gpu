"""Registry calls always execute on the same Slurm login host."""
import json
from pathlib import Path
import shlex


def call(remote, root, request):
    scheduler = remote('squeue -h -u "$(id -un)" -o "%i|%T|%N|%b"')
    source = (Path(__file__).parent / 'peer_registry.py').read_text()
    return json.loads(remote(shlex.join(['python3', '-c', source, root, json.dumps(request), scheduler])))
