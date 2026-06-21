#!/usr/bin/env bash
#
# deploy_hkssl_payroll_prod.sh — Deploy hkssl_payroll to PRODUCTION.
# Adapted from deploy_hksf_prod.sh (hkssl-rental).

set -euo pipefail

PROD_HOST="root@192.168.0.115"
# Source the module from the odoo19 monorepo (single source of truth),
# not the standalone hkssl-payroll repo. Trailing slash matters for rsync.
LOCAL_MODULE="$HOME/Documents/GitHub/odoo19/odoo-custom-addons/hksf/hkssl_payroll/"
REMOTE_MODULE="/odoo/custom/addons/hkssl_payroll"
DB="hkssl"
ODOO_BIN="/odoo/odoo-server/odoo-bin"
ODOO_CONF="/etc/odoo-server.conf"
BACKUP_DIR="/var/lib/postgresql/backups"
LOG="/var/log/odoo/odoo-server.log"
FILESTORE_DIR="/odoo/.local/share/Odoo/filestore/${DB}"
KEEP_BACKUPS=20

MODULE_VERSION="$(grep -oE "'version'[[:space:]]*:[[:space:]]*'[^']+'" "${LOCAL_MODULE}__manifest__.py" 2>/dev/null | grep -oE "[0-9][0-9.]+" || true)"
MODULE_VERSION="${MODULE_VERSION:-unknown}"
echo ">>> Deploying hkssl_payroll version ${MODULE_VERSION}"

echo ">>> Syncing module to ${PROD_HOST}:${REMOTE_MODULE} ..."
rsync -av --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  "${LOCAL_MODULE}" \
  "${PROD_HOST}:${REMOTE_MODULE}/"

echo ">>> Sync OK. Running remote upgrade ..."
ssh "${PROD_HOST}" "
  set -euo pipefail

  chown -R odoo:odoo ${REMOTE_MODULE}

  TS=\$(date +%Y%m%d_%H%M)

  echo '>>> Backing up ${DB} database ...'
  install -d -o postgres -g postgres ${BACKUP_DIR}
  sudo -u postgres pg_dump -Fc ${DB} -f ${BACKUP_DIR}/${DB}_\${TS}.dump
  ls -la ${BACKUP_DIR}/${DB}_\${TS}.dump

  echo '>>> Backing up ${DB} filestore ...'
  if [ -d '${FILESTORE_DIR}' ]; then
    tar -czf ${BACKUP_DIR}/${DB}_filestore_\${TS}.tar.gz -C \"\$(dirname '${FILESTORE_DIR}')\" \"\$(basename '${FILESTORE_DIR}')\"
    chown postgres:postgres ${BACKUP_DIR}/${DB}_filestore_\${TS}.tar.gz
    ls -la ${BACKUP_DIR}/${DB}_filestore_\${TS}.tar.gz
  else
    echo '>>> WARNING: filestore dir not found -- skipping.'
  fi

  echo '>>> Pruning old backups (keeping newest ${KEEP_BACKUPS}) ...'
  ls -1t ${BACKUP_DIR}/${DB}_*.dump 2>/dev/null | tail -n +\$((${KEEP_BACKUPS}+1)) | xargs -r rm -f
  ls -1t ${BACKUP_DIR}/${DB}_filestore_*.tar.gz 2>/dev/null | tail -n +\$((${KEEP_BACKUPS}+1)) | xargs -r rm -f

  echo '>>> Stopping odoo-server ...'
  systemctl stop odoo-server || true
  sleep 3
  for pid in \$(pgrep -f 'odoo-server/odoo-bin' || true); do
    if [ \"\$pid\" != \"\$\$\" ] && [ \"\$pid\" != \"\$PPID\" ]; then
      kill -9 \"\$pid\" 2>/dev/null || true
    fi
  done
  sleep 3

  echo '>>> Upgrading hkssl_payroll ...'
  # cd to a world-readable dir before launching: docutils renders module
  # descriptions to HTML and probes './html4css1.css' in the process CWD.
  # SSHing as root lands in /root, which the 'odoo' user cannot stat ->
  # PermissionError: [Errno 13] Permission denied: 'html4css1.css'.
  cd /tmp
  if sudo -u odoo sh -c \"cd /tmp && exec ${ODOO_BIN} -c ${ODOO_CONF} -d ${DB} -u hkssl_payroll --stop-after-init\"; then
    echo '>>> Upgrade OK. Starting server.'
    systemctl start odoo-server
    sleep 8
    systemctl is-active odoo-server
  else
    echo '>>> UPGRADE FAILED. Last 40 log lines:'
    tail -n 40 ${LOG}
    exit 1
  fi
"

echo ">>> Deploy finished (version ${MODULE_VERSION})."
