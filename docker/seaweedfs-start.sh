#!/bin/sh
# Запуск SeaweedFS для compose и CI: собирает конфиг S3-пользователя из тех же
# переменных, что читает приложение, и передаёт управление штатному entrypoint образа
# (он сбрасывает привилегии до пользователя seaweed).
#
# Единственный пользователь получает Read/Write/List только на бакет приложения;
# админских учёток нет, бакет создаётся самим SeaweedFS при старте (-bucket).
set -eu

: "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}" "${S3_BUCKET:?}"

# Экранирование для JSON-строки: обратный слеш и кавычка в секрете не должны ломать конфиг.
json_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

ACCESS_KEY=$(json_escape "$S3_ACCESS_KEY")
SECRET_KEY=$(json_escape "$S3_SECRET_KEY")
BUCKET=$(json_escape "$S3_BUCKET")

CONFIG=/etc/seaweedfs/s3.json
umask 077
cat > "$CONFIG" <<JSON
{
  "identities": [
    {
      "name": "$ACCESS_KEY",
      "credentials": [{"accessKey": "$ACCESS_KEY", "secretKey": "$SECRET_KEY"}],
      "actions": ["Read:$BUCKET", "Write:$BUCKET", "List:$BUCKET"]
    }
  ]
}
JSON
if [ "$(id -u)" = "0" ]; then
  chown seaweed:seaweed "$CONFIG"
fi

# WebDAV, Iceberg, Lance и admin UI приложению не нужны; телеметрия выключена.
exec /entrypoint.sh mini -dir=/data \
  -s3.config="$CONFIG" -bucket="$S3_BUCKET" \
  -webdav=false -admin.ui=false -s3.port.iceberg=0 -s3.port.lance=0 \
  -master.telemetry=false "$@"
