"""Project paths and read-only resolution of paths in historical manifests."""
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[4]
LABORATORY = PROJECT / 'laboratory'
LAB_RESULTS = PROJECT / 'artifacts/laboratory'
SEARCH_RESULTS = PROJECT / 'artifacts/search'


def relocated_path(value):
    text = str(value).replace('\\', '/')
    aliases = {
        '/checkpoint-01/results/': '/artifacts/laboratory/',
        '/checkpoint-02/results/': '/artifacts/search/',
        '/checkpoint-01/.local/': '/artifacts/local/',
        '/checkpoint-01/data/': '/artifacts/library/',
        '/checkpoint-01/': '/laboratory/',
        '/checkpoint-02/': '/experiments/',
    }
    for old, new in aliases.items():
        if old in text:
            # Resolve under the current checkout even if the project was copied.
            return PROJECT / (new.lstrip('/') + text.split(old, 1)[1])
    return Path(value)
