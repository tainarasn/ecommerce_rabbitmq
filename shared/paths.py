"""Configuração de caminhos do projeto para facilitar imports em todos os módulos."""

import sys
from pathlib import Path


# Adiciona a raiz do repositório ao sys.path para que os imports funcionem em qualquer serviço.
def setup_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root
