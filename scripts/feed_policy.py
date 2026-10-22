"""Source switches shared by producers and health checks."""
import json


def arxiv_enabled(root):
    path = root/'config/sources.json'
    if not path.exists():
        return True
    return json.loads(path.read_text()).get('arxiv', {}).get('enabled', True) is not False
