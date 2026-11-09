# SlapOS master behaviour

What `hox` relies on and works around, as observed on Rapid.Space's master (`slap.vifib.com`) in October and November 2026. The panel's forms aren't a public API: if one of these changes, the command that depends on it fails with the list of what it found instead.

## Two endpoints

Both authenticate with your client certificate.

- **The slap protocol**, on the master's URL: `supplySupply` (supply or remove a release on a node) and `requestComputerPartition` (request, stop, start or destroy an instance). This is what the `slapos` client uses.
- **The API** (hateoas), at the URL that `GET getHateoasUrl` returns (`api.vifib.com`): catalog searches, documents, and the panel's forms. `hox` reads everything it shows from there, as JSON.

`api.vifib.com` serves the TLS certificate of `slap.vifib.com`. `hox` verifies it under that name (`api_tls_name`), where `slapos.core` turns verification off.

## Requests

- An answer of **HTTP 408** to a request means "requested, still provisioning", not a timeout.
- An existing instance accepts only its **original request**: the same URL, software type and filter. Anything else gets **HTTP 500**, including a new release URL. So `hox` reads the original request back from the API (`url_string`, `source_reference`, `sla_xml`, `text_content`) to re-request, stop, start or destroy an instance, and moving to another URL means a new instance.
- **Destroying** works only by resending the original request with the state `destroyed`.

## Allocation

- An instance is allocated only if its release is ticked, with its software type, in a **validated Allocation Supply** of the project, on a node whose allocation scope is open. Without that, the instance stays unallocated, with no error anywhere. `hox status NAME` checks for this.
- Allocation takes minutes; shared frontends take longer. Commands that wait poll with a timeout and say what they wait for.

## Forms

`hox setup`, `hox release` and `hox node-certificate` submit the panel's forms with the client certificate, as the panel would:

- A document's actions are in its `_links`. Reading an action's own link returns its form as JSON, with each field's key and default; the form is submitted by POSTing every field's default, plus `dialog_method` and the values to change.
- `Base_edit` skips the fields absent from the POST, so an edit sends only what it changes. The Allocation Supply's matrix is the exception: it is validated as a whole, so every field of every cell has to be in the POST.
- What a form creates shows up in the catalog a few seconds later, since ERP5 indexes in the background. `hox` waits for it.
- A release that a supply cell refers to can't be deleted from its product (HTTP 400, with no message).
- "Add Compute Node Token" works only through `panel.rapid.space`, which doesn't accept a client certificate. The older `slapos token` API answers 410. `hox node-certificate` uses "Add Compute Node", and on an existing node "Revoke Certificate" then "Request Certificate", which give the certificate directly.

## The node

`hox` reaches the node over SSH, and reads:

- release directories, `/opt/slapgrid/<md5 of the URL>`, with `.completed` once built;
- slapgrid's logs, `/opt/slapos/log/slapos-node-{software,instance}.log`;
- partitions, `/srv/slapgrid/slappartN`, and their service logs;
- `slapos node status` for the services.

`slapos node` exits with 10 when another run holds its lock, and with 2 when promises fail.
