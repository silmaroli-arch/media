"""Testa a ÁREA DO PACIENTE UNIFICADA:

- O painel do paciente mostra os exames de TODOS os grupos/clínicas que
  ele frequenta, juntos, com o local identificado em cada linha.
- Preparo e resultado de QUALQUER clínica abrem na mesma conta (é tudo
  dado do próprio paciente).
- Segurança: outro paciente NÃO acessa preparo/resultado alheio.

Fatia 5 (nota importante): o cadastro (Paciente) passou a ser único e
GLOBAL por CPF (`uq_pacientes_cpf` em app/models.py) - uma mesma pessoa
NÃO pode mais ter dois registros Paciente (um por clínica) como no modelo
anterior de "conta única, fase 2/3"; agora a "unificação entre clínicas"
já é o padrão: um único cadastro Paciente é associado a quantos Grupos
forem necessários via GrupoPaciente. Por isso o cenário abaixo usa UM
cadastro ligado a DOIS grupos (não mais dois cadastros por CPF) - e o
"trocar de clínica"/seletor de múltiplos cadastros (baseado em
Usuario.pacientes, que agora só tem 1 elemento pra uma pessoa real) não é
mais exercitável nesse fluxo; não é testado aqui (ver
app/routes_paciente.py:trocar_clinica)."""
from datetime import datetime, date

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoPaciente, Paciente, Exame, Agendamento, ResultadoExame, normalizar_telefone,
)

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Cenário: Bia frequenta o Grupo Saúde Total (Centro) E a Clínica Vitória
# (UM cadastro global, associado aos dois grupos), com um exame futuro em
# cada e um resultado na Vitória.
with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_vit = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()

    ex_grupo = Exame(grupo_id=centro.id, medico_id=medico_grupo.id, nome="Exame Unificado Grupo",
                     descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    ex_vit = Exame(grupo_id=vitoria.id, medico_id=medico_vit.id, nome="Exame Unificado Vitoria",
                   descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    db.session.add_all([ex_grupo, ex_vit])
    db.session.flush()

    tel = normalizar_telefone("(27) 97070-0001")
    conta = Usuario(nome="Bia Unificada", telefone=tel, tipo="paciente")
    db.session.add(conta)
    db.session.flush()
    bia = Paciente(usuario_id=conta.id, nome="Bia Unificada",
                   cpf="820.930.140-05", data_nascimento=date(1988, 8, 8), telefone=tel,
                   status_cadastro="aprovado")
    db.session.add(bia)
    db.session.flush()

    # A associação de fato (visibilidade "é paciente deste grupo") é 100%
    # via GrupoPaciente - o MESMO cadastro global pode estar em vários
    # grupos ao mesmo tempo (Fatia 5).
    db.session.add(GrupoPaciente(grupo_id=centro.id, paciente_id=bia.id))
    db.session.add(GrupoPaciente(grupo_id=vitoria.id, paciente_id=bia.id))
    db.session.commit()

    ag_grupo = Agendamento(grupo_id=centro.id, paciente_id=bia.id, exame_id=ex_grupo.id,
                           medico_id=medico_grupo.id, data_hora=datetime(2026, 10, 1, 9, 0))
    ag_vit = Agendamento(grupo_id=vitoria.id, paciente_id=bia.id, exame_id=ex_vit.id,
                         medico_id=medico_vit.id, data_hora=datetime(2026, 10, 2, 10, 0))
    ag_vit_passado = Agendamento(grupo_id=vitoria.id, paciente_id=bia.id, exame_id=ex_vit.id,
                                 medico_id=medico_vit.id, data_hora=datetime(2026, 6, 1, 10, 0))
    db.session.add_all([ag_grupo, ag_vit, ag_vit_passado])
    db.session.flush()
    db.session.add(ResultadoExame(agendamento_id=ag_vit_passado.id, nome_arquivo="resultado.pdf",
                                  caminho_arquivo="nao-existe.pdf"))

    # Outro paciente (do Grupo), pra prova de isolamento entre CONTAS.
    tel2 = normalizar_telefone("(27) 97070-0002")
    outro_u = Usuario(nome="Outro Paciente", telefone=tel2, tipo="paciente")
    db.session.add(outro_u)
    db.session.flush()
    outro_p = Paciente(usuario_id=outro_u.id, nome="Outro Paciente",
                       cpf="820.930.140-16", data_nascimento=date(1970, 1, 1), telefone=tel2,
                       status_cadastro="aprovado")
    db.session.add(outro_p)
    db.session.flush()
    db.session.add(GrupoPaciente(grupo_id=centro.id, paciente_id=outro_p.id))
    db.session.commit()
    ag_grupo_id, ag_vit_id, ag_vit_passado_id = ag_grupo.id, ag_vit.id, ag_vit_passado.id

# ---------- Painel unificado ----------

r = client.post("/login-paciente", data={"cpf": "820.930.140-05", "data_nascimento": "08/08/1988"},
                follow_redirects=True)
html = r.get_data(as_text=True)
checar("Login entra direto no painel unificado", "Olá, Bia Unificada" in html)
checar("Painel mostra os exames dos DOIS grupos juntos",
       "Exame Unificado Grupo" in html and "Exame Unificado Vitoria" in html)
checar("Cada linha identifica o local",
       "Grupo Saúde Total - Centro" in html and "Clínica Vitória" in html)
checar("O histórico da Vitória também aparece (exame realizado)", "realizado" in html)
checar("O botão de baixar resultado aparece no histórico", "Baixar resultado" in html)

# ---------- Preparo/resultado de qualquer clínica (mesma conta) ----------

r = client.get(f"/paciente/exame/{ag_grupo_id}")
checar("Preparo do exame do Grupo abre", r.status_code == 200)
r = client.get(f"/paciente/exame/{ag_vit_id}")
checar("Preparo do exame da Vitória abre NA MESMA conta", r.status_code == 200)

# ---------- Tirar dúvida continua acessível com vínculo em 2 grupos ----------

r = client.get("/paciente/chat")
checar("Tela de tirar dúvida abre normalmente com vínculo em 2 grupos", r.status_code == 200)
client.get("/logout")

# ---------- Segurança entre contas ----------

r = client.post("/login-paciente", data={"cpf": "820.930.140-16", "data_nascimento": "01/01/1970"},
                follow_redirects=True)
checar("Outro paciente loga", "Outro Paciente" in r.get_data(as_text=True))
r = client.get(f"/paciente/exame/{ag_vit_id}")
checar("Outro paciente NÃO abre preparo alheio (404)", r.status_code == 404)
r = client.get(f"/paciente/exame/{ag_vit_passado_id}/resultado")
checar("Outro paciente NÃO baixa resultado alheio (404)", r.status_code == 404)
r = client.get("/paciente/")
checar("E não vê os exames da Bia no painel dele",
       "Exame Unificado Vitoria" not in r.get_data(as_text=True))
client.get("/logout")

print("\nTodos os testes da área unificada do paciente passaram.")
