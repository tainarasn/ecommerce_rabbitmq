"""Configurações compartilhadas: endereços dos serviços e leitura opcional do arquivo .env."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# Lê o arquivo .env da raiz (se existir) sem sobrescrever variáveis já definidas no ambiente.
def _carregar_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for linha in env_path.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env()

# Portas HTTP de cada processo.
GATEWAY_PORT = 5000
ESTOQUE_PORT = 5001
PAGAMENTO_PORT = 5002
MOCK_PAGAMENTO_PORT = 5003

# URLs usadas na comunicação REST entre os serviços.
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://localhost:{GATEWAY_PORT}")
ESTOQUE_URL = os.getenv("ESTOQUE_URL", f"http://localhost:{ESTOQUE_PORT}")
PAGAMENTO_URL = os.getenv("PAGAMENTO_URL", f"http://localhost:{PAGAMENTO_PORT}")
MOCK_PAGAMENTO_URL = os.getenv("MOCK_PAGAMENTO_URL", f"http://localhost:{MOCK_PAGAMENTO_PORT}")

# API externa de e-mail (Resend). Sem chave, os e-mails são apenas exibidos no log.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "E-commerce SD <onboarding@resend.dev>")

# Intervalo (segundos) entre as promoções geradas pelo MS Promoções.
PROMOCAO_INTERVALO = int(os.getenv("PROMOCAO_INTERVALO", "30"))

CATEGORIAS = ["A", "B", "C"]
