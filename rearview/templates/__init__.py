from rearview.templates.base import BaseTemplate
from rearview.templates.aloware import AlowareTemplate

REGISTRY: dict[str, type[BaseTemplate]] = {
    "aloware": AlowareTemplate,
}

def get_template(name: str) -> BaseTemplate:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown template '{name}'. Available: {list(REGISTRY)}")
    return cls()
