#!/bin/bash
# Roda automaticamente a migração do banco (app/../migrar_banco.py) em TODO
# deploy, antes do servidor da nova versão subir - assim o schema já está
# atualizado quando o app começar a receber requisições. Roda em qualquer
# ambiente (media-dev, media-qa, media-prod), sempre contra o banco daquele
# ambiente, porque usa a mesma variável DATABASE_URL que o app já usa.
#
# Seguro rodar em todo deploy mesmo sem mudança de schema: todos os comandos
# em migrar_banco.py são "ALTER TABLE ... ADD COLUMN IF NOT EXISTS", então
# rodar de novo quando já foi aplicado antes não faz nada.
set -e

# As "Environment properties" configuradas no Elastic Beanstalk (ex.:
# DATABASE_URL) não ficam automaticamente disponíveis para hooks de deploy -
# precisam ser carregadas explicitamente deste arquivo, que o próprio
# Elastic Beanstalk gera a cada deploy.
if [ -f /opt/elasticbeanstalk/deployment/env ]; then
  source /opt/elasticbeanstalk/deployment/env
fi

# As dependências do projeto (psycopg etc.) ficam instaladas no virtualenv
# que o Elastic Beanstalk cria para o app, não no Python do sistema -
# precisamos ativar esse virtualenv antes de rodar o script.
VENV_ACTIVATE=$(find /var/app/venv -maxdepth 2 -name activate 2>/dev/null | head -n1)
if [ -n "$VENV_ACTIVATE" ]; then
  source "$VENV_ACTIVATE"
fi

cd /var/app/staging

python3 migrar_banco.py
