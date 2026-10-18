#!/bin/bash
# Atlantic.net ships images without cloud-init; this runs post-boot via SSH.
# Equivalent to the DO skill's base-rocky9 cloud-init preset.

dnf -y install vim git curl wget tar firewalld policycoreutils-python-utils

systemctl enable --now firewalld
firewall-cmd --add-service=ssh --permanent
firewall-cmd --reload

id dev >/dev/null 2>&1 || useradd -m -G wheel -s /bin/bash dev
echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev
chmod 440 /etc/sudoers.d/dev

# Propagate the root authorized_keys to the dev user (Atlantic injects
# your registered key_id as root's authorized_keys on first boot).
mkdir -p /home/dev/.ssh
if [ -f /root/.ssh/authorized_keys ]; then
    cp /root/.ssh/authorized_keys /home/dev/.ssh/authorized_keys
    chown -R dev:dev /home/dev/.ssh
    chmod 700 /home/dev/.ssh
    chmod 600 /home/dev/.ssh/authorized_keys
fi

echo "---- dev server: managed by atlanticnet skill ----" > /etc/motd
