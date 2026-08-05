"""
Inicializa o banco de dados **vazio** (sem empresas, filiais, pacientes,
exames ou qualquer outro dado de exemplo) e cria só a conta do dono da
plataforma, já que não existe cadastro público para esse papel — só para
empresas (via /cadastro).

A partir daí, tudo mais (empresas, filiais, médicos, secretárias,
pacientes, exames) é cadastrado pela própria interface da plataforma.

Rodar com: python init_db.py

Atenção: assim como o seed.py, este script apaga e recria o banco do zero
(no Postgres, apaga e recria o schema inteiro — ver app/db_utils.py).
Nunca rode isso apontando para um banco com dados reais sem ter certeza.
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario

DONO_EMAIL = "silmaroli@gmail.com"
DONO_SENHA = "SUx4cNXZkcdYcM"  # temporária — troque após o primeiro login

app = create_app()

with app.app_context():
    resetar_banco(db)

    dono = Usuario(nome="Silvan Oliveira", email=DONO_EMAIL, tipo="dono")
    dono.set_senha(DONO_SENHA)
    db.session.add(dono)
    db.session.commit()

    print("Banco de dados inicializado vazio, com a conta do dono da plataforma:")
    print(f"  E-mail: {DONO_EMAIL}")
    print(f"  Senha temporária: {DONO_SENHA}")
    print()
    print("Nenhuma empresa, filial, médico, secretária ou paciente foi criado.")
    print("Tudo o mais deve ser cadastrado pela própria interface da plataforma.")
