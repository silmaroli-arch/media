"""Atribui um CPF real ao usuário "Dono da Plataforma" (dono@plataforma.com,
Usuario.tipo == "dono").

Contexto: essa conta é a única do sistema sem CPF (foi criada uma vez,
via app/routes_auth.py, só com e-mail) — o login principal (app/routes_auth.py:
login()) já cai automaticamente para busca por e-mail quando ninguém bate
por CPF, então a conta NUNCA parou de funcionar. Mas para deixar o sistema
consistente (toda conta representando uma pessoa física com CPF, e
login/auditoria uniformes), o Silvan decidiu atribuir o CPF da pessoa real
responsável por administrar a plataforma a essa conta.

Rodar interativamente (pede o CPF na hora, não aceita como argumento de
linha de comando nem variável de ambiente, para não deixar o valor em
histórico de shell/logs):

    python atualizar_cpf_dono.py

Idempotente: se rodar de novo, mostra o CPF atual (mascarado) e pergunta
se quer substituir.
"""
import getpass
import re

from app import create_app, db
from app.models import Usuario, validar_cpf

app = create_app()


def _mascarar(cpf):
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) != 11:
        return "(inválido)"
    return f"{digitos[:3]}.***.***-{digitos[-2:]}"


with app.app_context():
    dono = Usuario.query.filter_by(email="dono@plataforma.com", tipo="dono").first()
    if not dono:
        print('Nenhum usuário encontrado com email="dono@plataforma.com" e tipo="dono".')
        raise SystemExit(1)

    print(f"Conta encontrada: {dono.nome} <{dono.email}>")
    if dono.cpf:
        print(f"CPF atual: {_mascarar(dono.cpf)}")
        resposta = input("Já existe um CPF cadastrado. Substituir? [s/N]: ").strip().lower()
        if resposta != "s":
            print("Nada foi alterado.")
            raise SystemExit(0)

    cpf_digitado = getpass.getpass("Digite o CPF (só números ou com pontuação, não aparece na tela): ").strip()

    if not validar_cpf(cpf_digitado):
        print("CPF inválido (dígito verificador não bate). Nada foi salvo.")
        raise SystemExit(1)

    digitos = re.sub(r"\D", "", cpf_digitado)
    conflito = None
    for candidato in Usuario.query.filter(Usuario.cpf.isnot(None), Usuario.id != dono.id).all():
        if re.sub(r"\D", "", candidato.cpf or "") == digitos:
            conflito = candidato
            break
    if conflito:
        print(
            f"Esse CPF já está cadastrado em outra conta ({conflito.nome} <{conflito.email}>, "
            f"tipo={conflito.tipo}). Nada foi salvo."
        )
        raise SystemExit(1)

    dono.cpf = cpf_digitado
    db.session.commit()
    print(f"CPF salvo com sucesso para {dono.nome} <{dono.email}>: {_mascarar(dono.cpf)}")
    print("O login por e-mail continua funcionando normalmente; agora também dá para logar por CPF + senha.")
