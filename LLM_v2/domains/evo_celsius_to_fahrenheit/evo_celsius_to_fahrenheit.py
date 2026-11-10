from pathlib import Path
from LLM_v2.domains.evolve.evolved_domain import build_evolved_domain

_DIR = Path(__file__).resolve().parent

def build_evo_celsius_to_fahrenheit_domain():
    return build_evolved_domain(_DIR)
