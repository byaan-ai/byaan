#!/bin/sh
set -e
groupadd --system byaan-handoff
useradd --system --create-home --home-dir /home/byaan-agent --shell /usr/sbin/nologin --gid byaan-handoff byaan-agent
if id byaan >/dev/null 2>&1; then usermod -aG byaan-handoff byaan; fi
install -m 0755 /tmp/handoff/byaan-agent-claude /usr/local/bin/byaan-agent-claude
install -m 0755 /tmp/handoff/byaan-agent-exec /usr/local/bin/byaan-agent-exec
install -m 0440 /tmp/handoff/sudoers /etc/sudoers.d/byaan-handoff
visudo -cf /etc/sudoers.d/byaan-handoff
rm -rf /tmp/handoff
