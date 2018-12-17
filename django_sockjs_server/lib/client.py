import redis
import logging
import time
import json
import pika
from pika.exceptions import ChannelClosed, AMQPConnectionError
from django.core.serializers.json import DjangoJSONEncoder
from django_sockjs_server.lib.config import SockJSServerSettings
from django_sockjs_server.lib.redis_client import redis_client

from django_sockjs_server.lib.token import Token
import uuid


class SockJsServerClient(object):
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.config = SockJSServerSettings()
        self.connected = False
        self.retry_count = 0
        self.redis = redis_client

    def _connect(self, is_retry=False):
        cred = pika.PlainCredentials(self.config.rabbitmq_user, self.config.rabbitmq_password)
        param = pika.ConnectionParameters(
            host=self.config.rabbitmq_host,
            port=self.config.rabbitmq_port,
            virtual_host=self.config.rabbitmq_vhost,
            credentials=cred
        )
        self.connection = pika.BlockingConnection(param)
        self.channel = self.connection.channel()
        self.channel.exchange_declare(exchange=self.config.rabbitmq_exchange_name,
                                      exchange_type=self.config.rabbitmq_exchange_type)


        self.connected = True
        if not is_retry:
            self.retry_count = 0

    def _disconnect(self):
        self.connected = False
        self.connection.disconnect()

    def publish_message_old(self, message, host):
        room = message.pop('channel')
        connections = self.get_connections(room)
        for conn in connections:
            submessage = message.copy()
            submessage['host'] = host            # target queue
            submessage['uid'] = conn['id']       # connection id
            submessage['room'] = room            # channel_id
            self.channel.basic_publish(
                self.config.rabbitmq_exchange_name,
                routing_key=submessage['host'],
                body=json.dumps(submessage, cls=DjangoJSONEncoder)
            )


    def publish_message(self, message, host, is_retry=False):
        try:
            if not self.connected:
                self._connect(is_retry)
            self.logger.debug("PUBLISH: %s" % message)
            # from system.tools import user_has_channel
            # json_obj = json.loads(message)
            # token = message['token']
            # channel_id = self.redis.get(token)
            # has_channel = user_has_channel(token, channel_id)
            has_channel = True
            if has_channel:
                # backward compatibility
                if 'channel' in message:
                    self.publish_message_old(message, host)
                else:
                    routing_key = host
                    self.channel.basic_publish(self.config.rabbitmq_exchange_name,
                                               routing_key=routing_key,
                                               body=json.dumps(message, cls=DjangoJSONEncoder))
        except (ChannelClosed, AMQPConnectionError):
            if self.connected:
                self._disconnect()
            if self.retry_count < 4:
                self.retry_count += 1
                #wait 100 ms
                time.sleep(100 / 1000000.0)
                self.publish_message(message, True)


    def get_connections(self, room):
        if not self.connected:
            self._connect()
            parsed_connections = []
        connections = self.redis.lrange(room, 0, -1)    # return a slice of list
        res = []
        for i in connections:
            try:
                res.append(json.loads(i))
            except ValueError:
                pass
        return res

    def gen_channel(self):
        channel = uuid.uuid1()
        channel = str(channel)
        return channel

    def gen_c_token(self, channel):
        token = Token()
        c_token = token.get_secret_data(channel)
        self.redis.set(c_token, channel)
        return c_token

    def get_channel(self, c_token):
        channel = self.redis.get(c_token).decode()
        return channel



