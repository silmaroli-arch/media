"""Testa a correção do "tenho uma solicitação, ao clicar não aparece nada":

O contador "Solicitações de agendamento" do Painel só restringe pro médico
logado quando ele NÃO tem a permissão administrativa de pacientes - mas a
tela de solicitações (e a ação de confirmar/recusar) filtrava SEMPRE pelo
médico logado. Resultado: um médico fundador (com todas as permissões) via
"1" no Painel e a lista vazia, porque a solicitação era endereçada a outro
médico.

Regra alinhada (a mesma do contador do Painel):
- médico COM perm_pacientes (ex.: fundador) vê e decide TODAS as
  solicitações das filiais dele;
- médico SEM perm_pacientes segue vendo/decidindo só as endereçadas a ele.
"""
from datetime import date, datetime

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Clinica, ClinicaMembro, Exame, Paciente, Agendamento, normalizar_telefone,
)

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


def contador_solicitacoes_do_painel(html):
    """Extrai o número do cartão "Solicitações de agendamento" do Painel."""
    bloco = html.split("Ver solicitações")[0]
    trecho = bloco.rsplit("fs-3", 1)[1]          # último cartão antes do link
    numero = trecho.split(">", 1)[1].split("<", 1)[0].strip()
    return int(numero)


# ---------- Cenário ----------
# Grupo Saúde Total (filial Centro):
# - Dr. Eduardo Nunes (seed): médico SEM perm_pacientes.
# - Dr. Bruno Fundador (criado aqui): médico COM perm_pacientes - o caso
#   do fundador que administra tudo.
# - Duas solicitações de pacientes: uma endereçada a Eduardo, outra a Bruno.
with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    empresa_id = centro.empresa_id
    eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    eduardo_id = eduardo.id

    bruno = Usuario(nome="Dr. Bruno Fundador", email="bruno@gruposaude.com", tipo="medico")
    bruno.set_senha("123456")
    bruno.definir_permissoes_padrao()
    bruno.perm_pacientes = True  # a permissão administrativa que muda a regra
    db.session.add(bruno)
    db.session.flush()
    db.session.add(ClinicaMembro(clinica_id=centro_id, usuario_id=bruno.id))

    exame_edu = Exame(clinica_id=centro_id, medico_id=eduardo_id, nome="Solicitação Exame Edu",
                      descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    exame_bruno = Exame(clinica_id=centro_id, medico_id=bruno.id, nome="Solicitação Exame Bruno",
                        descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    db.session.add_all([exame_edu, exame_bruno])

    def novo_paciente(nome, cpf, tel):
        t = normalizar_telefone(tel)
        u = Usuario(nome=nome, telefone=t, tipo="paciente")
        db.session.add(u)
        db.session.flush()
        p = Paciente(empresa_id=empresa_id, usuario_id=u.id, nome=nome, cpf=cpf,
                     data_nascimento=date(1990, 1, 1), telefone=t, status_cadastro="aprovado")
        db.session.add(p)
        db.session.flush()
        return p

    pac_a = novo_paciente("Paciente Solicitou A", "808.101.202-30", "(27) 94444-0001")
    pac_b = novo_paciente("Paciente Solicitou B", "808.101.202-41", "(27) 94444-0002")
    db.session.flush()

    sol_edu = Agendamento(clinica_id=centro_id, paciente_id=pac_a.id, exame_id=exame_edu.id,
                          medico_id=eduardo_id, data_hora=datetime(2026, 9, 10, 9, 0), status="solicitado")
    sol_bruno = Agendamento(clinica_id=centro_id, paciente_id=pac_b.id, exame_id=exame_bruno.id,
                            medico_id=bruno.id, data_hora=datetime(2026, 9, 10, 10, 0), status="solicitado")
    db.session.add_all([sol_edu, sol_bruno])
    db.session.commit()
    bruno_id, sol_edu_id, sol_bruno_id = bruno.id, sol_edu.id, sol_bruno.id

# ---------- Médico SEM perm_pacientes: só as solicitações dele ----------

login("medico@gruposaude.com")
r = client.get("/equipe/")
checar("Painel do Eduardo conta SÓ a solicitação endereçada a ele (1)",
       contador_solicitacoes_do_painel(r.get_data(as_text=True)) == 1)

r = client.get("/equipe/agenda/solicitacoes")
html = r.get_data(as_text=True)
checar("Eduardo vê a solicitação endereçada a ele", "Paciente Solicitou A" in html)
checar("Eduardo NÃO vê a solicitação endereçada ao Bruno", "Paciente Solicitou B" not in html)

r = client.post(f"/equipe/agenda/{sol_bruno_id}/confirmar-solicitacao",
                data={"acao": "confirmar"})
checar("Eduardo não consegue decidir a solicitação do Bruno (404)", r.status_code == 404)
client.get("/logout")

# ---------- Médico COM perm_pacientes (fundador): vê e decide todas ----------

login("bruno@gruposaude.com")
r = client.get("/equipe/")
checar("Painel do Bruno conta as DUAS solicitações da filial",
       contador_solicitacoes_do_painel(r.get_data(as_text=True)) == 2)

r = client.get("/equipe/agenda/solicitacoes")
html = r.get_data(as_text=True)
checar("A tela de solicitações lista as duas (o Painel e a lista batem)",
       "Paciente Solicitou A" in html and "Paciente Solicitou B" in html)
checar("Não aparece o aviso de lista vazia", "Nenhuma solicitação pendente" not in html)

# O caso exato do bug: a solicitação é endereçada a OUTRO médico (Eduardo),
# e o fundador confirma - antes dava 404 (a query filtrava pelo logado).
r = client.post(f"/equipe/agenda/{sol_edu_id}/confirmar-solicitacao",
                data={"acao": "confirmar"}, follow_redirects=True)
checar("Bruno confirma a solicitação endereçada ao Eduardo",
       "Agendamento confirmado" in r.get_data(as_text=True))
with app.app_context():
    checar("A solicitação virou agendamento (status 'agendado')",
           Agendamento.query.get(sol_edu_id).status == "agendado")

r = client.get("/equipe/")
checar("Depois de confirmar, o contador do Painel cai pra 1",
       contador_solicitacoes_do_painel(r.get_data(as_text=True)) == 1)
r = client.get("/equipe/agenda/solicitacoes")
checar("E a lista mostra só a que restou (Painel e lista continuam batendo)",
       "Paciente Solicitou B" in r.get_data(as_text=True)
       and "Paciente Solicitou A" not in r.get_data(as_text=True))

# Recusar também funciona pra solicitação do próprio Bruno.
r = client.post(f"/equipe/agenda/{sol_bruno_id}/confirmar-solicitacao",
                data={"acao": "recusar"}, follow_redirects=True)
checar("Recusar continua funcionando", "recusada" in r.get_data(as_text=True))

client.get("/logout")

# ---------- Secretária segue vendo tudo (nada mudou pra ela) ----------

login("secretaria@gruposaude.com")
r = client.get("/equipe/agenda/solicitacoes")
checar("Secretária continua acessando a tela normalmente", r.status_code == 200)
client.get("/logout")

print("\nTodos os testes de solicitações visíveis conforme a permissão passaram.")
