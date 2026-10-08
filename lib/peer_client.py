"""Registry calls always execute on the same Slurm login host."""
import json
from pathlib import Path
import shlex


def call(remote, root, request):
    scheduler = remote('squeue -h -u "$(id -un)" -o "%i|%T|%N|%b"')
    source = (Path(__file__).parent / 'peer_registry.py').read_text()
    module = (Path(__file__).parent / 'peer_mailbox.py').read_text()
    source = source.replace('from peer_mailbox import operate as mailbox', module + '\nmailbox = operate')
    return json.loads(remote(shlex.join(['python3', '-c', source, root, json.dumps(request), scheduler])))
