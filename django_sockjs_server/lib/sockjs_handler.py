# coding: utf-8
import hashlib
import datetime
import time
import logging
import json
from django.utils.timezone import now
import sockjs.tornado
from tornado.web import RequestHandler
from django_sockjs_server.lib.memory_stats import MemoryStats
from django_sockjs_server.lib.sockjs_server import SockjsServer
from django_sockjs_server.lib.subscribe import Subscribe
from django_sockjs_server.lib.redis_client import redis_client
from django_sockjs_server.lib.config import SockJSServerSettings

from django_sockjs_server.lib.client import SockJsServerClient

class SockJSConnection(sockjs.tornado.SockJSConnection):

    sockjs_server = None  # should be initialized by router

    def __init__(self, *args, **kw):
        self.subscribe = Subscribe(self)
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)
        self.conf = SockJSServerSettings()
        self.id = self._generate_connection_id()
        super(SockJSConnection, self).__init__(*args, **kw)

    def on_open(self, info):
        self.sockjs_server.add_event_listener(self)

    def on_close(self):
        self.subscribe.remove()
        self.sockjs_server.remove_event_listener(self)

    def on_message(self, message: str):
        '''
        message like this: {'token': '548121dc2872a5fcca14685eb5ae90c9', 'data': {'to_channel': 'Ezreal'}}
        '''
        # from system.tools import user_has_channel
        # json_obj = json.loads(message)
        # self.logger.debug('On message >>> %s' % json_obj)
        # if json_obj['host'] == 'MQ1':
        #     self.logger.debug('Get message %s' % message)
        #     self.subscribe.add(message)
        # token = json_obj['token']
        # channel_id = self.redis.get(token)
        # has_channel = user_has_channel(token, channel_id)
        # has_channel = True
        # if has_channel:
        #     s = SockJsServerClient()
        #     s.publish_message(json_obj, 'MQ1')    # put into MQ1
        self.logger.debug('Get message %s' % message)
        self.subscribe.add(message)




    def _generate_connection_id(self):
        client = self
        now = datetime.datetime.utcnow()
        seconds = time.mktime(now.timetuple()) + now.microsecond / 1e6
        connection_id = hashlib.md5(
            "{} {}".format(
                seconds,
                id(client)
            ).encode(encoding='utf-8')
        ).hexdigest()
        return connection_id

class StatsHandler(RequestHandler):

    def initialize(self, sockjs_server):
        self.sockjs_server = sockjs_server
        self.memory_stats = MemoryStats()
        self.redis = redis_client

    def get(self, type_stats='default'):
        self.clear()
        self.set_header("Content-Type", "text/plain")
        self.set_status(200)
        if type_stats == 'debug':
            self.finish("uptime_seconds: " + str(self.sockjs_server.get_uptime()) +
                        "\n memory_use_byte: " + str(int(self.memory_stats.memory())) +
                        "\n memory_resident_use_byte: " + str(int(self.memory_stats.resident())) +
                        "\n memory_stack_size_byte: " + str(int(self.memory_stats.stacksize())) +
                        "\n last_rabbitmq_reconnect: " + str(self.sockjs_server.get_last_reconnect()) +
                        "\n connect_rabbitmq_time_seconds: " + str((now() - self.sockjs_server.get_last_reconnect()).seconds) +
                        "\n event_listeners_count: " + str(self.sockjs_server.get_event_listeners_count()) +
                        "\n connects: " + str(self.sockjs_server.get_subscribe_connections()) +
                        "\n redis_connect_tries: %s" % (self.redis.connect_tries) +
                        "\n redis_uptime_seconds %s" % (self.redis.get_uptime()))
        else:
            self.finish("uptime_seconds: " + str(self.sockjs_server.get_uptime()) +
                        "\n memory_use_byte: " + str(int(self.memory_stats.memory())) +
                        "\n memory_resident_use_byte: " + str(int(self.memory_stats.resident())) +
                        "\n memory_stack_size_byte: " + str(int(self.memory_stats.stacksize())) +
                        "\n last_rabbitmq_reconnect: " + str(self.sockjs_server.get_last_reconnect()) +
                        "\n connect_rabbitmq_time_seconds: " + str((now() - self.sockjs_server.get_last_reconnect()).seconds) +
                        "\n event_listeners_count: " + str(self.sockjs_server.get_event_listeners_count()) +
                        "\n connects: " + str(len(self.sockjs_server.get_subscribe_connections())) +
                        "\n redis_connect_tries: %s" % (self.redis.connect_tries) +
                        "\n redis_uptime_seconds %s" % (self.redis.get_uptime()))


class SockJSRouterPika(sockjs.tornado.SockJSRouter):
    def __init__(self, *args, **kw):
        super(SockJSRouterPika, self).__init__(*args, **kw)
        self._connection.sockjs_server = SockjsServer(self.io_loop)
        self._connection.sockjs_server.connect()
