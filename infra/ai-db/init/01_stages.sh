#!/bin/bash
# 스테이지별 DB·앱 계정(SELECT·INSERT·UPDATE·DELETE만)을 만들고 V001 스키마를 적용한다. 비밀번호는 셸 변수로만 받는다.
set -euo pipefail
read -r -a stages <<< "${AI_STAGES}"
read -r -a passwords <<< "${AI_DB_PASSWORDS}"
for i in "${!stages[@]}"; do
  s="${stages[$i]}"; pw="${passwords[$i]}"
  mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS ai_${s} CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'ai_${s}'@'${AI_DB_HOST}' IDENTIFIED BY '${pw}';
GRANT SELECT, INSERT, UPDATE, DELETE ON ai_${s}.* TO 'ai_${s}'@'${AI_DB_HOST}';
SQL
  mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" "ai_${s}" < /schema/V001__init.sql
done
