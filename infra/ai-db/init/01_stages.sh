#!/bin/bash
# 로컬 DB ai_local과 계정(RDS 스테이지 계정과 같은 권한: AI DB 안에서만 DDL+DML)을 만들고 V001 스키마를 적용한다.
set -euo pipefail
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS ai_local CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'ai_local'@'%' IDENTIFIED BY '${AI_DB_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP ON ai_local.* TO 'ai_local'@'%';
SQL
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" ai_local < /schema/V001__init.sql
