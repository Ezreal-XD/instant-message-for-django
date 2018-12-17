from collections import defaultdict
import json
import logging
from django.conf import settings
from django.utils.timezone import now
import pika
from pika.adapters.tornado_connection import TornadoConnection
from pika.exceptions import AMQPConnectionError
import time
from django_sockjs_server.lib.config import SockJSServerSettings
from django_sockjs_server.lib.redis_client import redis_client


class SockjsServer(object):
    def __init__(self, io_loop):
        self.logger = logging.getLogger(__name__)
        self.logger.info('SockjsServer: __init__')
        self.io_loop = io_loop

        self.connected = False
        self.connecting = False
        self.connection = None
        self.channel = None

        self.redis = redis_client
        self.event_listeners_count = 0
        self.event_listeners = set()
        self.connection_dict = dict()
        self.subscription_dict = defaultdict(set)
        self.last_reconnect = now()
        self.uptime_start = now()



        self.config = SockJSServerSettings()

    def connect(self):
        if self.connecting:
            self.logger.info('django-sockjs-server(SockjsServer): Already connecting to RabbitMQ')
            return

        self.logger.info('django-sockjs-server(SockjsServer): Connecting to RabbitMQ')
        self.connecting = True

        cred = pika.PlainCredentials(self.config.rabbitmq_user, self.config.rabbitmq_password)
        param = pika.ConnectionParameters(
            host=self.config.rabbitmq_host,
            port=self.config.rabbitmq_port,
            virtual_host=self.config.rabbitmq_vhost,
            credentials=cred
        )

        try:
            self.connection = TornadoConnection(param,
                                                on_open_callback=self.on_connected)
            self.connection.add_on_close_callback(self.on_closed)
        except AMQPConnectionError:
            self.logger.info('django-sockjs-server(SockjsServer): error connect, wait 5 sec')
            time.sleep(5)
            self.reconnect()

        self.last_reconnect = now()

    def on_connected(self, connection):
        self.logger.info('django-sockjs-server(SockjsServer): connected to RabbitMQ')
        self.connected = True
        self.connection = connection
        self.connection.channel(self.on_channel_open)

    def on_channel_open(self, channel):
        self.logger.info('django-sockjs-server(SockjsServer): Channel open, Declaring exchange')
        self.channel = channel
        self.channel.exchange_declare(exchange=self.config.rabbitmq_exchange_name,
                                      exchange_type=self.config.rabbitmq_exchange_type)
        self.channel.queue_declare(    
            queue=self.config.rabbitmq_queue1_name,    # declare MQ1
            exclusive=False, 
            auto_delete=True, 
            callback=self.on_queue1_declared
        )
        self.channel.queue_declare(    
            queue=self.config.rabbitmq_queue2_name,    # declare MQ2
            exclusive=False, 
            auto_delete=True, 
            callback=self.on_queue2_declared
        )

    # queue binding
    def on_queue1_declared(self, frame):
        # self.logger.info(frame)
        self.logger.info('django-sockjs-server(SockjsServer): queue1 bind')
        # self.queue = frame.method.queue
        self.channel.queue_bind(callback=None, exchange=self.config.rabbitmq_exchange_name, queue=frame.method.queue)
        self.channel.basic_consume(self.handle_delivery, queue=frame.method.queue, no_ack=True)

    def on_queue2_declared(self, frame):
        self.logger.info('django-sockjs-server(SockjsServer): queue2 bind')
        self.queue = frame.method.queue    # declare the host--MQ2
        # self.logger.info(self.queue)
        self.channel.queue_bind(callback=None, exchange=self.config.rabbitmq_exchange_name, queue=frame.method.queue)
        self.channel.basic_consume(self.handle_delivery, queue=frame.method.queue, no_ack=True)

    def handle_delivery(self, channel, method, header, body):
        """Called when we receive a message from RabbitMQ"""
        # call zhenfeng's function(body: message) here without caring the return
        # to simulate
        json_obj = json.loads(body.decode())
        # self.logger.info('json_obj >>> :', json_obj)
        if json_obj['host'] == 'MQ1':
            self.logger.info("host = MQ1!!!")
            from django_sockjs_server.lib.client import SockJsServerClient
            s = SockJsServerClient()
            json_obj['host'] = 'MQ2'
            s.publish_message(json_obj, 'MQ2')    # put into MQ2
            self.logger.info("Has put into MQ2")
        else:
            self.logger.info("host = MQ2!!!")
            self.notify_listeners(body)


    def handle_delivery2(self, channel, method, header, body: bytes):
        """
        Called when we receive a message from RabbitMQ
        body like this: (b'{"data": {"user_name": "Sergey Kravchuk", "user_id": 1}, 
        "host": "MQ2", "uid": "262ef88da87537086e0d93f9fc2c40ba", "room": "user2"}',)
        """
        # json_obj = json.loads(body.decode())
        # self.logger.info('handle_delivery2 body >>> :', json_obj, json_obj['host'])
        # self.logger.info('handle_delivery2 body >>> :', type(channel) , type(method), type(header))
        self.notify_listeners(body)

    def on_closed(self, connection, error_code, error_message):
        self.logger.info('django-sockjs-server(SockjsServer): rabbit connection closed, wait 5 seconds')
        connection.add_timeout(5, self.reconnect)

    def reconnect(self):
        self.connecting = False
        self.logger.info('django-sockjs-server(SockjsServer): reconnect')
        self.connect()

    def notify_listeners(self, event_json):
        event_obj = json.loads(event_json)

        self.logger.debug('django-sockjs-server(SockjsServer): send message %s ' % event_obj)
        try:
            client = self.connection_dict[event_obj['uid']]
        except KeyError:
            self.redis.lrem(event_obj['room'], 0, json.dumps({'id': event_obj['uid'], 'host': event_obj['host']}))
        else:
            new_event_json = json.dumps({'data': event_obj['data']})
            client.broadcast([client], new_event_json)

    def add_event_listener(self, listener):
        self.event_listeners_count += 1
        self.event_listeners.add(listener)
        self.logger.debug('django-sockjs-server(SockjsServer): listener %s added' % repr(listener))

    def remove_event_listener(self, listener):
        try:
            self.event_listeners_count -= 1
            self.event_listeners.remove(listener)
            self.logger.debug('django-sockjs-server(SockjsServer): listener %s removed' % repr(listener))
        except KeyError:
            pass

    def add_subscriber_room(self, room, conn):
        try:
            conn_id = conn.id
            self.connection_dict.setdefault(conn_id, conn)
            client = self.connection_dict[conn_id]
            self.subscription_dict[conn_id].add(room)
            self.logger.debug('django-sockjs-server(SockjsServer): listener %s add to room %s' % (repr(client), room))
        except KeyError as exc:
            pass


    def remove_subscriber(self, conn_id):
        try:
            client = self.connection_dict[conn_id]
            del self.subscription_dict[conn_id]
            del self.connection_dict[conn_id]
            self.logger.debug('django-sockjs-server(SockjsServer): listener %s del connection %s' % (repr(client),
                              conn_id))
        except KeyError as exc:
            pass

    def get_event_listeners_count(self):
        return self.event_listeners_count

    def get_subscribe_connection_count(self):
        return len(self.connection_dict.keys())

    def get_subscribe_connections(self):
        return self.connection_dict.keys()

    def get_last_reconnect(self):
        return self.last_reconnect

    def get_uptime(self):
        return (now() - self.uptime_start).seconds
