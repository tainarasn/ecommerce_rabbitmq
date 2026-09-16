import json
from pathlib import Path

import pika

from shared.events import build_event, parse_event, serialize_event, validate_event

ECOMMERCE_EXCHANGE = "eCommerce"
PROMOCOES_EXCHANGE = "Promocoes"

RABBIT_HOST = "localhost"


def connect() -> tuple[pika.BlockingConnection, pika.adapters.blocking_connection.BlockingChannel]:
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBIT_HOST))
    channel = connection.channel()
    return connection, channel


def declare_ecommerce_exchange(channel) -> None:
    channel.exchange_declare(exchange=ECOMMERCE_EXCHANGE, exchange_type="direct", durable=True)


def declare_promocoes_exchange(channel) -> None:
    channel.exchange_declare(exchange=PROMOCOES_EXCHANGE, exchange_type="topic", durable=True)


def declare_queue(channel, queue_name: str) -> None:
    channel.queue_declare(queue=queue_name, durable=True)


def bind_queue(channel, queue_name: str, exchange: str, routing_key: str) -> None:
    channel.queue_bind(exchange=exchange, queue=queue_name, routing_key=routing_key)


def publish_signed_event(
    channel,
    exchange: str,
    routing_key: str,
    data: dict,
    origin: str,
    private_key_path: Path,
) -> None:
    envelope = build_event(data, origin, private_key_path)
    channel.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=serialize_event(envelope),
        properties=pika.BasicProperties(delivery_mode=2),
    )


def consume_signed_events(channel, queue_name: str, keys_dir: Path, callback) -> None:
    def on_message(ch, method, properties, body):
        envelope = parse_event(body)
        if envelope is None:
            print(" [x] Evento invalido (JSON malformado). Descartando.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        data = validate_event(envelope, keys_dir)
        if data is None:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        callback(ch, method, properties, data)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=on_message)


def start_consumer(service_name: str, queue_name: str, bindings: list[tuple[str, str]], keys_dir: Path, handler):
    connection, channel = connect()
    declare_ecommerce_exchange(channel)
    declare_queue(channel, queue_name)

    for exchange, routing_key in bindings:
        bind_queue(channel, queue_name, exchange, routing_key)

    print(f" [*] {service_name} aguardando eventos na fila '{queue_name}'...")
    consume_signed_events(channel, queue_name, keys_dir, handler)
    channel.start_consuming()


def start_promo_consumer(consumer_name: str, queue_name: str, bindings: list[str], handler):
    connection, channel = connect()
    declare_promocoes_exchange(channel)
    declare_queue(channel, queue_name)

    for routing_key in bindings:
        bind_queue(channel, queue_name, PROMOCOES_EXCHANGE, routing_key)

    def on_message(ch, method, properties, body):
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f" [{consumer_name}] Promocao invalida. Descartando.")
        else:
            handler(payload)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=on_message)

    print(f" [*] {consumer_name} aguardando promocoes na fila '{queue_name}'...")
    channel.start_consuming()
