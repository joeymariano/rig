#!/bin/bash
# Run once with sudo to allow controller.py to stop/start argononed without a password prompt.

RULE_FILE=/etc/sudoers.d/rig-argon
USER=${SUDO_USER:-$(whoami)}

cat > "$RULE_FILE" <<EOF
# Allow $USER to manage argononed for the performance rig without a password
$USER ALL=(ALL) NOPASSWD: /bin/systemctl stop argononed, /bin/systemctl start argononed, /bin/systemctl stop argone-oled, /bin/systemctl start argone-oled
EOF

chmod 440 "$RULE_FILE"
echo "Sudoers rule installed for user: $USER"
echo "File: $RULE_FILE"
