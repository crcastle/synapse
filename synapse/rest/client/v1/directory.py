# -*- coding: utf-8 -*-
# Copyright 2014-2016 OpenMarket Ltd
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


import logging
from typing import Tuple

from synapse.api.errors import (
    AuthError,
    Codes,
    InvalidClientCredentialsError,
    NotFoundError,
    SynapseError,
)
from synapse.http.servlet import RestServlet, parse_json_object_from_request
from synapse.http.site import SynapseRequest
from synapse.rest.client.v2_alpha._base import client_patterns
from synapse.types import JsonDict, RoomAlias

logger = logging.getLogger(__name__)


def register_servlets(hs, http_server):
    ClientDirectoryServer(hs).register(http_server)
    ClientDirectoryListServer(hs).register(http_server)
    ClientAppserviceDirectoryListServer(hs).register(http_server)


class ClientDirectoryServer(RestServlet):
    PATTERNS = client_patterns("/directory/room/(?P<room_alias>[^/]*)$", v1=True)

    def __init__(self, hs):
        super().__init__()
        self.store = hs.get_datastore()
        self.directory_handler = hs.get_directory_handler()
        self.auth = hs.get_auth()

    async def on_GET(self, request, room_alias):
        room_alias = RoomAlias.from_string(room_alias)

        res = await self.directory_handler.get_association(room_alias)

        return 200, res

    async def on_PUT(self, request, room_alias):
        room_alias = RoomAlias.from_string(room_alias)

        content = parse_json_object_from_request(request)
        if "room_id" not in content:
            raise SynapseError(
                400, 'Missing params: ["room_id"]', errcode=Codes.BAD_JSON
            )

        logger.debug("Got content: %s", content)
        logger.debug("Got room name: %s", room_alias.to_string())

        room_id = content["room_id"]
        servers = content["servers"] if "servers" in content else None

        logger.debug("Got room_id: %s", room_id)
        logger.debug("Got servers: %s", servers)

        # TODO(erikj): Check types.

        room = await self.store.get_room(room_id)
        if room is None:
            raise SynapseError(400, "Room does not exist")

        requester = await self.auth.get_user_by_req(request)

        await self.directory_handler.create_association(
            requester, room_alias, room_id, servers
        )

        return 200, {}

    async def on_DELETE(self, request, room_alias):
        try:
            service = self.auth.get_appservice_by_req(request)
            room_alias = RoomAlias.from_string(room_alias)
            await self.directory_handler.delete_appservice_association(
                service, room_alias
            )
            logger.info(
                "Application service at %s deleted alias %s",
                service.url,
                room_alias.to_string(),
            )
            return 200, {}
        except InvalidClientCredentialsError:
            # fallback to default user behaviour if they aren't an AS
            pass

        requester = await self.auth.get_user_by_req(request)
        user = requester.user

        room_alias = RoomAlias.from_string(room_alias)

        await self.directory_handler.delete_association(requester, room_alias)

        logger.info(
            "User %s deleted alias %s", user.to_string(), room_alias.to_string()
        )

        return 200, {}


class ClientDirectoryListServer(RestServlet):
    PATTERNS = client_patterns("/directory/list/room/(?P<room_id>[^/]*)$", v1=True)

    def __init__(self, hs):
        super().__init__()
        self.store = hs.get_datastore()
        self.directory_handler = hs.get_directory_handler()
        self.auth = hs.get_auth()

    async def on_GET(self, request, room_id):
        room = await self.store.get_room(room_id)
        if room is None:
            raise NotFoundError("Unknown room")

        return 200, {"visibility": "public" if room["is_public"] else "private"}

    async def on_PUT(self, request, room_id):
        requester = await self.auth.get_user_by_req(request)

        content = parse_json_object_from_request(request)
        visibility = content.get("visibility", "public")

        await self.directory_handler.edit_published_room_list(
            requester, room_id, visibility
        )

        return 200, {}

    async def on_DELETE(self, request, room_id):
        requester = await self.auth.get_user_by_req(request)

        await self.directory_handler.edit_published_room_list(
            requester, room_id, "private"
        )

        return 200, {}


class ClientAppserviceDirectoryListServer(RestServlet):
    PATTERNS = client_patterns(
        "/directory/list/appservice/(?P<network_id>[^/]*)/(?P<room_id>[^/]*)$", v1=True
    )

    def __init__(self, hs):
        super().__init__()
        self.store = hs.get_datastore()
        self.directory_handler = hs.get_directory_handler()
        self.auth = hs.get_auth()

    async def on_PUT(
        self, request: SynapseRequest, network_id: str, room_id: str
    ) -> Tuple[int, JsonDict]:
        content = parse_json_object_from_request(request)
        visibility = content.get("visibility", "public")
        return await self._edit_appservice_room_visibility(
            request, network_id, room_id, visibility
        )

    async def on_DELETE(
        self, request: SynapseRequest, network_id: str, room_id: str
    ) -> Tuple[int, JsonDict]:
        return await self._edit_appservice_room_visibility(
            request, network_id, room_id, "private"
        )

    async def _edit_appservice_room_visibility(
        self, request: SynapseRequest, network_id: str, room_id: str, visibility: str
    ) -> Tuple[int, JsonDict]:
        """Adds or removes a room ID from the appservice room list.

        Args:
            request: The initiating request.
            network_id: The third-party network ID of the appservice.
            room_id: The ID of the room to add or remove.
            visibility: The visibility of the room. "public" to list, "private" to delist.

        Returns:
            A tuple of (http status code, empty json dict).

        Raises:
            AuthError if the request was not performed by an appservice or homeserver admin.
        """
        requester = await self.auth.get_user_by_req(request)
        is_server_admin = await self.auth.is_server_admin(requester.user)

        if requester.app_service:
            appservice_id = requester.app_service.id
        elif is_server_admin and visibility == "private":
            # We allow homeserver admins to remove entries from the appservice room list,
            # while we don't allow them to add entries. This limitation is because:
            #
            # 1. There shouldn't be much reason for a homeserver admin to use the appservice
            #    room list to list a room over the client room list.
            # 2. We can't retrieve a proper appservice ID for a homeserver admin.
            appservice_id = ""
        else:
            raise AuthError(
                403, "Only appservices can edit the appservice published room list"
            )

        await self.directory_handler.edit_published_appservice_room_list(
            appservice_id, network_id, room_id, visibility
        )

        return 200, {}
