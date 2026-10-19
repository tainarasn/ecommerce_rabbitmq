"""Gera pares de chaves para todos os serviços e distribui as chaves públicas compartilhadas."""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.crypto import generate_key_pair

# Lista de serviços que participam do ecossistema de mensagens e assinaturas digitais.
SERVICES = ["principal", "estoque", "pagamento", "entrega", "promocoes"]


# Cria uma chave para cada serviço e distribui as chaves públicas para os demais módulos.
def main() -> None:
    public_keys: dict[str, Path] = {}

    for service in SERVICES:
        keys_dir = ROOT / service / "keys"
        private_path = keys_dir / f"{service}_private.pem"
        public_path = keys_dir / f"{service}_public.pem"

        # Cada serviço precisa de um par de chaves para assinar e validar eventos.
        generate_key_pair(private_path, public_path)
        public_keys[service] = public_path
        print(f"Gerado par de chaves para: {service}")

    for service in SERVICES:
        target_dir = ROOT / service / "keys"
        target_dir.mkdir(parents=True, exist_ok=True)

        # Distribui a chave pública de cada outro serviço para habilitar a verificação.
        for other_service, public_path in public_keys.items():
            if other_service == service:
                continue
            destination = target_dir / f"{other_service}_public.pem"
            shutil.copy2(public_path, destination)

        print(f"Chaves publicas distribuidas em: {target_dir}")

    print("\nConcluido! Todas as chaves foram geradas e distribuidas.")


if __name__ == "__main__":
    main()
