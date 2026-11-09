# Setting up a node

`hox` doesn't install SlapOS on a machine. It gives a node's installation what it needs from the master, without the web panel: the node's certificate, and its token for re6st, the IPv6 network that connects SlapOS nodes. Your provisioning tool (Abilian uses a pyinfra deploy) does the rest, then runs `hox deploy` for each software.

## The node's certificate

```
hox node-certificate > node.secret.json
chmod 600 node.secret.json
```

Prints the reference, certificate and key of the compute node named `[node] title`, as JSON. If the node doesn't exist, `hox` creates it in your project (the panel's "Add Compute Node"). If it exists, `hox` revokes its certificate and requests a new one (the master gives a certificate only once), so a node still configured with the old certificate loses access. Keep the file until the node is configured, so that a rerun of your provisioning doesn't need a new one.

`slapos node register` with a token isn't an option here: on Rapid.Space, the panel's "Add Compute Node Token" works only through `panel.rapid.space`, which doesn't accept a client certificate. Configure the node with the certificate instead.

## The re6st token

```
hox re6st-token
```

Prints the node's re6st token, and only that on stdout (progress goes to stderr). `hox` requests a shared instance of the re6st registry (`[node] re6st_release`) in the registry's project (`[node] re6st_project`, "Re6st Registries" on Rapid.Space), titled "re6st token for <node title>". The registry takes 10 minutes or more to publish a new token; asking again gives the same one.

A failed re6st registration uses the token up. Destroy the token instance so that the next request makes a new one:

```
hox destroy "re6st token for my-node"
```

## Then

Once the node runs SlapOS, `hox status` shows it, and `hox deploy` brings each software to an instance that passes its checks. Instances stay on the node they were first requested on, so a new node means new instance names in `hox.toml`.
