#!/usr/bin/env bash
# Create the VM that runs the SlapOS node, on a libvirt host: Debian 12 cloud
# image, a fixed address on one of the host's libvirt networks, root SSH with
# your keys (~/.ssh/id_*.pub), started with the host. Sizes and names come
# from node.env.
#
#   ./create-vm.sh --check   read-only: what exists and what would be done
#   ./create-vm.sh           do it (nothing if the VM exists)
#
# bootstrap.sh runs it first. The libvirt host needs virt-install, qemu-img
# and curl; it reaches the internet to download the image.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=node.env
. "$HERE/node.env"
IMAGE_URL=https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2
IMAGES=/var/lib/libvirt/images
mode=apply
if [[ ${1:-} == --check ]]; then mode=check; fi

if [[ $(awk '$1 == "Host" {h = $2} $1 == "HostName" {print h, $2}' "$HERE/ssh_config") != "$NODE $IP" ]]; then
  echo "ssh_config should have Host $NODE with HostName $IP, as in node.env" >&2
  exit 1
fi
shopt -s nullglob
pubs=(~/.ssh/id_*.pub)
if ((${#pubs[@]} == 0)); then
  echo "no ~/.ssh/id_*.pub to put in root's authorized_keys" >&2
  exit 1
fi

# Runs on the libvirt host as root. Arguments are passed base64-encoded: root's
# login shell, which runs the command line, may not be bash.
REMOTE=$(cat <<'EOF'
set -euo pipefail
decode() { echo "$1" | base64 -d; }
mode=$(decode "$1") name=$(decode "$2") vcpus=$(decode "$3") mem_gib=$(decode "$4") disk_gib=$(decode "$5")
network=$(decode "$6") ip=$(decode "$7") mac=$(decode "$8") image_url=$(decode "$9") images=$(decode "${10}")
keys=$(decode "${11}")
disk=$images/$name.qcow2
if ! command -v virt-install >/dev/null; then
  echo "virt-install is not installed on the libvirt host" >&2
  exit 1
fi

echo "libvirt host: $(free -g | awk '/^Mem/ {print $7}') GiB of memory available," \
     "$(df -h --output=avail "$images" | tail -1 | tr -d ' ') free in $images"

if virsh dominfo "$name" >/dev/null 2>&1; then
  echo "$name exists ($(virsh domstate "$name")): nothing to create"
  if [[ $mode == apply ]]; then
    virsh autostart "$name" >/dev/null
    if [[ $(virsh domstate "$name") == "shut off" ]]; then virsh start "$name"; fi
  fi
  exit 0
fi

# A fixed address: a DHCP reservation for the VM's MAC on the libvirt network.
net=$(virsh net-dumpxml "$network")
if [[ $net == *"mac='$mac'"* ]]; then
  echo "DHCP reservation for $mac: exists"
elif [[ $net == *"ip='$ip'"* || $(virsh net-dhcp-leases "$network") == *" $ip/"* ]]; then
  echo "$ip is already reserved or leased on $network: pick another IP (here and in ssh_config)" >&2
  exit 1
elif [[ $mode == check ]]; then
  echo "would reserve $ip for $mac on $network ($(grep -o "<ip address=[^>]*>" <<<"$net"))"
else
  virsh net-update "$network" add ip-dhcp-host "<host mac='$mac' name='$name' ip='$ip'/>" --live --config
fi

if [[ $mode == check ]]; then
  echo "would create $name: $vcpus vCPU, $mem_gib GiB, $disk ($disk_gib GiB) from $image_url"
  exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "downloading $image_url"
curl -fsSL -o "$tmp/image" "$image_url"
expected=$(curl -fsSL "${image_url%/*}/SHA512SUMS" | awk -v f="${image_url##*/}" '$2 == f {print $1}')
if [[ -z $expected ]]; then
  echo "no checksum for ${image_url##*/} in SHA512SUMS" >&2
  exit 1
fi
echo "$expected  $tmp/image" | sha512sum -c --quiet
cp "$tmp/image" "$disk"  # cp, not mv: the copy gets the directory's SELinux label
qemu-img resize -q "$disk" "${disk_gib}G"

cat >"$tmp/meta-data" <<META
instance-id: $name
local-hostname: $name
META
{
  echo "#cloud-config"
  echo "disable_root: false"  # root gets the keys below, as deploy.py expects
  echo "ssh_pwauth: false"
  echo "ssh_authorized_keys:"
  while IFS= read -r key; do if [[ -n $key ]]; then echo "  - $key"; fi; done <<<"$keys"
} >"$tmp/user-data"

virt-install --name "$name" --vcpus "$vcpus" --memory $((mem_gib * 1024)) --cpu host-passthrough \
  --osinfo detect=on,require=off --import --disk "path=$disk,format=qcow2,bus=virtio" \
  --network "network=$network,mac=$mac,model=virtio" \
  --cloud-init "user-data=$tmp/user-data,meta-data=$tmp/meta-data" \
  --graphics none --noautoconsole
virsh autostart "$name" >/dev/null
EOF
)

args=()
for value in "$mode" "$NODE" "$VCPUS" "$MEM_GIB" "$DISK_GIB" "$NETWORK" "$IP" "$MAC" "$IMAGE_URL" "$IMAGES" \
             "$(cat "${pubs[@]}")"; do
  args+=("$(printf '%s' "$value" | base64 | tr -d '\n')")
done
# Expanded here on purpose (shellcheck SC2029): base64 needs no quoting there.
ssh "$KVM_HOST" "bash -s -- ${args[*]}" <<<"$REMOTE"

if [[ $mode == apply ]]; then
  echo "waiting for $NODE to answer on SSH"
  for _ in $(seq 60); do
    if ssh -F "$HERE/ssh_config" -o LogLevel=ERROR -o ConnectTimeout=5 "$NODE" true 2>/dev/null; then
      echo "$NODE is up"
      exit 0
    fi
    sleep 5
  done
  echo "$NODE doesn't answer on SSH after 5 minutes" >&2
  exit 1
fi
