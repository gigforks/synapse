# -*- coding: utf-8 -*-
# Copyright 2018 New Vector Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from twisted.internet import defer

from synapse.api.errors import SynapseError, MatrixCodeMessageException
from synapse.http.servlet import RestServlet, parse_json_object_from_request
from synapse.types import Requester

import logging
import re

logger = logging.getLogger(__name__)


@defer.inlineCallbacks
def remote_join(client, host, port, requester, remote_room_hosts,
                room_id, user_id, content):
    uri = "http://%s:%s/_synapse/replication/remote_join" % (host, port)

    payload = {
        "requester": requester.serialize(),
        "remote_room_hosts": remote_room_hosts,
        "room_id": room_id,
        "user_id": user_id,
        "content": content,
    }

    try:
        result = yield client.post_json_get_json(uri, payload)
    except MatrixCodeMessageException as e:
        # We convert to SynapseError as we know that it was a SynapseError
        # on the master process that we should send to the client. (And
        # importantly, not stack traces everywhere)
        raise SynapseError(e.code, e.msg, e.errcode)
    defer.returnValue(result)


@defer.inlineCallbacks
def remote_leave(client, host, port, requester, remote_room_hosts,
                 room_id, user_id):
    uri = "http://%s:%s/_synapse/replication/remote_leave" % (host, port)

    payload = {
        "requester": requester.serialize(),
        "remote_room_hosts": remote_room_hosts,
        "room_id": room_id,
        "user_id": user_id,
    }

    try:
        result = yield client.post_json_get_json(uri, payload)
    except MatrixCodeMessageException as e:
        # We convert to SynapseError as we know that it was a SynapseError
        # on the master process that we should send to the client. (And
        # importantly, not stack traces everywhere)
        raise SynapseError(e.code, e.msg, e.errcode)
    defer.returnValue(result)


class ReplicationRemoteJoinRestServlet(RestServlet):
    PATTERNS = [re.compile("^/_synapse/replication/remote_join$")]

    def __init__(self, hs):
        super(ReplicationRemoteJoinRestServlet, self).__init__()

        self.federation_handler = hs.get_handlers().federation_handler
        self.store = hs.get_datastore()
        self.clock = hs.get_clock()

    @defer.inlineCallbacks
    def on_POST(self, request):
        content = parse_json_object_from_request(request)

        remote_room_hosts = content["remote_room_hosts"]
        room_id = content["room_id"]
        user_id = content["user_id"]
        content = content["content"]

        requester = Requester.deserialize(self.store, content["requester"])

        if requester.user:
            request.authenticated_entity = requester.user.to_string()

        logger.info(
            "remote_join: %s into room: %s",
            user_id, room_id,
        )

        yield self.federation_handler.do_invite_join(
            remote_room_hosts,
            room_id,
            user_id,
            content,
        )

        defer.returnValue((200, {}))


class ReplicationRemoteLeaveRestServlet(RestServlet):
    PATTERNS = [re.compile("^/_synapse/replication/remote_leave$")]

    def __init__(self, hs):
        super(ReplicationRemoteLeaveRestServlet, self).__init__()

        self.federation_handler = hs.get_handlers().federation_handler
        self.store = hs.get_datastore()
        self.clock = hs.get_clock()

    @defer.inlineCallbacks
    def on_POST(self, request):
        content = parse_json_object_from_request(request)

        remote_room_hosts = content["remote_room_hosts"]
        room_id = content["room_id"]
        user_id = content["user_id"]

        requester = Requester.deserialize(self.store, content["requester"])

        if requester.user:
            request.authenticated_entity = requester.user.to_string()

        logger.info(
            "remote_leave: %s out of room: %s",
            user_id, room_id,
        )

        try:
            event = yield self.federation_handler.do_remotely_reject_invite(
                remote_room_hosts,
                room_id,
                user_id,
            )
            ret = event.get_pdu_json()
        except Exception as e:
            # if we were unable to reject the exception, just mark
            # it as rejected on our end and plough ahead.
            #
            # The 'except' clause is very broad, but we need to
            # capture everything from DNS failures upwards
            #
            logger.warn("Failed to reject invite: %s", e)

            yield self.store.locally_reject_invite(
                user_id, room_id
            )
            ret = {}

        defer.returnValue((200, ret))


def register_servlets(hs, http_server):
    ReplicationRemoteJoinRestServlet(hs).register(http_server)
    ReplicationRemoteLeaveRestServlet(hs).register(http_server)
