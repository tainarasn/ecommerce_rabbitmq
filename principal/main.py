import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.catalog import PRODUTOS
from shared.rabbit import ECOMMERCE_EXCHANGE, connect, declare_ecommerce_exchange, publish_signed_event, start_consumer

SERVICE = "principal"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"

pedidos: dict[int, dict] = {}
lock = threading.Lock()
proximo_id = 1


def atualizar_status(pedido_id: int, status: str, detalhe: str = "") -> None:
    with lock:
        if pedido_id in pedidos:
            pedidos[pedido_id]["status"] = status
            if detalhe:
                pedidos[pedido_id]["detalhe"] = detalhe


def publicar_exclusao(channel, pedido_id: int, motivo: str) -> None:
    publish_signed_event(
        channel,
        ECOMMERCE_EXCHANGE,
        "pedido.excluido",
        {"pedido_id": pedido_id, "motivo": motivo},
        SERVICE,
        PRIVATE_KEY,
    )
    atualizar_status(pedido_id, "Excluido", motivo)


def handle_event(ch, method, properties, data):
    routing_key = method.routing_key
    pedido_id = data.get("pedido_id")

    if routing_key == "pedido.estoque_ok":
        atualizar_status(pedido_id, "Estoque OK", "Produtos reservados")
        print(f"\n [>] Pedido {pedido_id}: estoque confirmado. Aguardando pagamento...")

    elif routing_key == "estoque.indisponivel":
        print(f"\n [>] Pedido {pedido_id}: estoque indisponivel. Excluindo pedido...")
        publicar_exclusao(ch, pedido_id, "Estoque indisponivel")

    elif routing_key == "pagamento.aprovado":
        atualizar_status(pedido_id, "Pagamento aprovado", "Aguardando envio")
        print(f"\n [>] Pedido {pedido_id}: pagamento aprovado. Aguardando entrega...")

    elif routing_key == "pagamento.recusado":
        print(f"\n [>] Pedido {pedido_id}: pagamento recusado. Excluindo pedido...")
        publicar_exclusao(ch, pedido_id, "Pagamento recusado")

    elif routing_key == "pedido.enviado":
        nota = data.get("nota_fiscal", "")
        atualizar_status(pedido_id, "Enviado", f"Nota fiscal: {nota}")
        print(f"\n [>] Pedido {pedido_id}: enviado! {nota}")


def iniciar_consumidor():
    bindings = [
        (ECOMMERCE_EXCHANGE, "pedido.estoque_ok"),
        (ECOMMERCE_EXCHANGE, "estoque.indisponivel"),
        (ECOMMERCE_EXCHANGE, "pagamento.aprovado"),
        (ECOMMERCE_EXCHANGE, "pagamento.recusado"),
        (ECOMMERCE_EXCHANGE, "pedido.enviado"),
    ]
    start_consumer("Microsservico Principal (consumer)", "principal_queue", bindings, KEYS_DIR, handle_event)


def listar_produtos() -> None:
    print("\n=== Produtos disponiveis ===")
    for pid, info in PRODUTOS.items():
        print(
            f"  ID {pid} | {info['nome']} | Categoria {info['categoria']} | "
            f"R$ {info['preco']:.2f}"
        )


def criar_pedido(channel) -> None:
    global proximo_id

    listar_produtos()
    try:
        produto_id = int(input("\nID do produto: "))
        quantidade = int(input("Quantidade: "))
    except ValueError:
        print("Entrada invalida.")
        return

    if produto_id not in PRODUTOS:
        print("Produto nao encontrado.")
        return

    if quantidade <= 0:
        print("Quantidade deve ser maior que zero.")
        return

    with lock:
        pedido_id = proximo_id
        proximo_id += 1
        pedidos[pedido_id] = {
            "status": "Criado",
            "produtos": [{"id": produto_id, "quantidade": quantidade}],
            "detalhe": "Aguardando confirmacao de estoque",
        }

    publish_signed_event(
        channel,
        ECOMMERCE_EXCHANGE,
        "pedido.criado",
        {"pedido_id": pedido_id, "produtos": [{"id": produto_id, "quantidade": quantidade}]},
        SERVICE,
        PRIVATE_KEY,
    )
    print(f"\nPedido {pedido_id} criado e publicado. Aguardando processamento...")


def excluir_pedido(channel) -> None:
    try:
        pedido_id = int(input("\nID do pedido a excluir: "))
    except ValueError:
        print("Entrada invalida.")
        return

    with lock:
        if pedido_id not in pedidos:
            print("Pedido nao encontrado.")
            return
        if pedidos[pedido_id]["status"] == "Excluido":
            print("Pedido ja esta excluido.")
            return

    publicar_exclusao(channel, pedido_id, "Exclusao solicitada pelo usuario")
    print(f"Pedido {pedido_id} marcado para exclusao.")


def consultar_pedidos() -> None:
    print("\n=== Pedidos ===")
    with lock:
        if not pedidos:
            print("  Nenhum pedido registrado.")
            return
        for pedido_id, info in pedidos.items():
            produtos_str = ", ".join(
                f"produto {p['id']} x{p['quantidade']}" for p in info["produtos"]
            )
            print(f"  Pedido {pedido_id} | Status: {info['status']} | {produtos_str}")
            if info.get("detalhe"):
                print(f"    Detalhe: {info['detalhe']}")


def menu(channel) -> None:
    while True:
        print("\n=== E-Commerce (Microsservico Principal) ===")
        print("1 - Visualizar produtos")
        print("2 - Realizar pedido")
        print("3 - Excluir pedido")
        print("4 - Consultar pedidos")
        print("0 - Sair")

        opcao = input("\nOpcao: ").strip()

        if opcao == "1":
            listar_produtos()
        elif opcao == "2":
            criar_pedido(channel)
        elif opcao == "3":
            excluir_pedido(channel)
        elif opcao == "4":
            consultar_pedidos()
        elif opcao == "0":
            print("Encerrando...")
            break
        else:
            print("Opcao invalida.")


def main():
    consumer_thread = threading.Thread(target=iniciar_consumidor, daemon=True)
    consumer_thread.start()

    connection, channel = connect()
    declare_ecommerce_exchange(channel)

    try:
        menu(channel)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
