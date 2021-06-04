# Modules

Synapse supports extending its functionalities by configuring external modules.

## Using modules

To use a module on Synapse, add it to the `modules` section of the configuration file:

```yaml
modules:
  - module: my_super_module.MySuperClass
    config:
      do_thing: true
  - module: my_other_super_module.SomeClass
    config: {}
```

Each module is defined by a path to a Python class as well as a configuration. This
information for a given module should be available in the module's own documentation.

**Note**: When using third-party modules, you effectively allow someone else to run
custom code on your Synapse homeserver. Server admins are encouraged to verify the
provenance of the modules they use on their homeserver and make sure the modules aren't
running malicious code on their instance.

## Writing a module

A module is a Python class that uses Synapse's module API to interact with the
homeserver. It can register callbacks that Synapse will call on specific operations, as
well as web resources to attach to Synapse's web server.

A module must implement the following static method:

```python
@staticmethod
def parse_config(config: dict) -> dict
```

This method is given a dictionary resulting from parsing the YAML configuration for the
module. It may modify it (for example by parsing durations expressed as strings (e.g.
"5d") into milliseconds, etc.), and return the modified dictionary. If no change is
necessary, this method should just return `config`.

When instantiated, a module is given its parsed configuration (i.e. the output of
`parse_config`) as well as an instance of the `synapse.module_api.ModuleApi` class.

See the documentation for the `ModuleApi` class [here](/synapse/module_api/__init__.py).

### Registering a web resource

Modules can register web resources onto Synapse's web server using the following module
API method:

```python
def ModuleApi.register_web_resource(path: str, resource: IResource)
```

The path is the full absolute path to register the resource at. For example, if you
register a resource for the path `/_synapse/client/my_super_module/say_hello`, Synapse
will serve it at `https://[HS_URL]/_synapse/client/my_super_module/say_hello`. Note
that Synapse does not allow registering resources for several sub-paths in the `/_matrix`
namespace (such as anything under `/_matrix/client` for example). It is strongly
recommended that modules register their web resources under the `/_synapse/client`
namespace.

The provided resource is a Python class that implements Twisted's [IResource](https://twistedmatrix.com/documents/current/api/twisted.web.resource.IResource.html)
interface (such as [Resource](https://twistedmatrix.com/documents/current/api/twisted.web.resource.Resource.html)).

Only one resource can be registered for a given path. If several modules attempt to
register a resource for the same path, the module that appears first in Synapse's
configuration file takes priority.

Modules **must** register their web resources in their `__init__` method.

### Registering a callback

Modules can use Synapse's module API to register callbacks. Callbacks are functions that
Synapse will call when performing specific actions. Callbacks can be either asynchronous
or synchronous, and are split in categories. A single module may implement callbacks from
multiple categories, and is under no obligation to implement all callbacks from the
category(ies) it registers callbacks for.

#### Spam checker callbacks

To register one of the callbacks described in this section, a module needs to use the
module API's `register_spam_checker_callbacks` method. The callback functions are passed
to `register_spam_checker_callbacks` as keyword arguments, with the callback name as the
argument name and the function as its value. This is demonstrated in the example below.

The available spam checker callbacks are:

```python
def check_event_for_spam(event: "synapse.events.EventBase") -> Union[bool, str]
```

Called when receiving an event from a client or via federation. The module can return
either a `bool` to indicate whether the event must be rejected because of spam, or a `str`
to indicate the event must be rejected because of spam and to give a rejection reason to
forward to clients.

```python
def user_may_invite(inviter: str, invitee: str, room_id: str) -> bool
```

Called when processing an invitation. The module must return a `bool` indicating whether
the inviter can invite the invitee to the given room. Both inviter and invitee are
represented by their Matrix user ID (i.e. `@alice:example.com`).

```python
def user_may_create_room(user: str) -> bool
```

Called when processing a room creation request. The module must return a `bool` indicating
whether the given user (represented by their Matrix user ID) is allowed to create a room.

```python
def user_may_create_room_alias(user: str, room_alias: "synapse.types.RoomAlias") -> bool
```

Called when trying to associate an alias with an existing room. The module must return a
`bool` indicating whether the given user (represented by their Matrix user ID) is allowed
to set the given alias.

```python
def user_may_publish_room(user: str, room_id: str) -> bool
```

Called when trying to publish a room to the homeserver's public rooms directory. The
module must return a `bool` indicating whether the given user (represented by their
Matrix user ID) is allowed to publish the given room.

```python
def check_username_for_spam(user_profile: Dict[str, str]) -> bool
```

Called when computing search results in the user directory. The module must return a
`bool` indicating whether the given user profile can appear in search results. The profile
is represented as a dictionary with the following keys:

* `user_id`: The Matrix ID for this user.
* `display_name`: The user's display name.
* `avatar_url`: The `mxc://` URL to the user's avatar.

The module is given a copy of the original dictionary, so modifying it from within the
module cannot modify a user's profile when included in user directory search results.

```python
def check_registration_for_spam(
    email_threepid: Optional[dict],
    username: Optional[str],
    request_info: Collection[Tuple[str, str]],
    auth_provider_id: Optional[str] = None,
) -> "synapse.spam_checker_api.RegistrationBehaviour"
```

Called when registering a new user. The module must return a `RegistrationBehaviour`
indicating whether the registration can go through or must be denied, or whether the user
may be allowed to register but will be shadow banned.

The arguments passed to this callback are:

* `email_threepid`: The email address used for registering, if any.
* `username`: The username the user would like to register. Can be `None`, meaning that
  Synapse will generate one later.
* `request_info`: A collection of tuples, which first item is a user agent, and which
  second item is an IP address. These user agents and IP addresses are the ones that were
  used during the registration process.
* `auth_provider_id`: The identifier of the SSO authentication provider, if any.

```python
def check_media_file_for_spam(
    file_wrapper: "synapse.rest.media.v1.media_storage.ReadableFileWrapper",
    file_info: "synapse.rest.media.v1._base.FileInfo"
) -> bool
```

Called when storing a local or remote file. The module must return a boolean indicating
whether the given file can be stored in the homeserver's media store.

### Example

```python
import json

from twisted.web.resource import Resource
from twisted.web.server import Request

from synapse.module_api import ModuleApi


class DemoResource(Resource):
    def __init__(self, config):
        super(DemoResource, self).__init__()
        self.config = config

    async def render_GET(self, request: Request):
        name = request.args.get(b"name")[0]
        return json.dumps({"name": name})


class DemoModule:
    def __init__(self, config: dict, api: ModuleApi):
        self.config = config
        self.api = api

        self.api.register_web_resource(
            path="/_synapse/client/demo/hello",
            resource=DemoResource(self.config),
        )

        self.api.register_spam_checker_callbacks(
            user_may_create_room=self.user_may_create_room,
        )

    @staticmethod
    def parse_config(config):
        return config

    async def user_may_create_room(self, userid: str) -> bool:
        pass
```