#!/bin/sh
# Инициализация MinIO: бакет приложения и сервисный пользователь с правами
# только на этот бакет. Запускается контейнером minio-init из docker-compose.
set -eu

: "${MINIO_ROOT_USER:?}" "${MINIO_ROOT_PASSWORD:?}"
: "${MINIO_BUCKET:?}" "${MINIO_ACCESS_KEY:?}" "${MINIO_SECRET_KEY:?}"

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb -p "local/$MINIO_BUCKET"
mc anonymous set none "local/$MINIO_BUCKET"

cat > /tmp/app-policy.json <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET/*"]
    }
  ]
}
POLICY

mc admin policy create local syncscribe-app /tmp/app-policy.json
# Повторный запуск не должен падать на уже существующем пользователе.
mc admin user add local "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" || true
mc admin policy attach local syncscribe-app --user "$MINIO_ACCESS_KEY" || true
