# Node Identity

`nodes.yaml` is the registry of OQTOPUS Manager nodes. The current release
registers only the local Manager, whose entry is marked `self: true`.

The file defaults to `./config/nodes.yaml` relative to the Manager's working directory.
Use [`server.nodes_file`](configuration.md#server) to configure a different path.

On its first startup, the Manager creates the file with a UUID version 7 as its
`node_id` and the short hostname as its `name`. For example, the hostname
`deviceA.lab.example.jp` produces a name of `deviceA`.

```yaml
nodes:
  - name: deviceA
    self: true
    node_id: 0193f2a7-8c31-7b4e-9f02-6d1a5c8e4b77
```

The Manager validates and loads the file at each startup. Startup fails if the
file is invalid or if a missing file cannot be created. The Manager needs write
access to the containing directory on first startup. With the default layout,
this is the same directory it already writes to when registering or removing
environments in `environments.yaml`.

Keep `nodes.yaml` across restarts and upgrades. If the hostname is hard to
recognize, as it may be in a container, edit `name` in the self entry and restart
the Manager. Names are case-sensitive and must start with an ASCII letter or
digit. The remaining characters can also include `_`, `.`, `:`, and `-`. Keep
the `node_id` unchanged when renaming a node. Quote names such as `"on"` or
`"123"` so YAML reads them as strings.

[`GET /health`](health.md) returns the node ID as `serviceId`. A generated UUID
version 7 includes its creation time, so anyone who can reach the public health
endpoint can determine when the node identity was first created.

## Cloning a host

A VM clone, disk image copy, or container image that includes `nodes.yaml` copies
the original node's ID. In a fleet, duplicate IDs make `?node=` selection
ambiguous, and a control node would reject registration of a clone that shares
its own ID.

On the **clone**, delete `nodes.yaml` and start or restart the Manager to create
a new identity. If the cloned file contains registrations from a control node,
record them before deleting it. Deleting the file also removes those
registrations from the clone.
