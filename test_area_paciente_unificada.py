"""Testa a ÁREA DO PACIENTE UNIFICADA (fase 2 da conta única):

- O painel do paciente mostra os exames de TODAS as clínicas que ele
  frequenta, juntos, com o local identificado em cada linha.
- Preparo e resultado de QUALQUER clínica abrem na mesma conta (é tudo
  dado do próprio paciente).
- "Trocar de clínica" muda o cadastro ativo - usado pelas ações
  endereçadas a uma clínica (solicitar agendamento).
- Segurança: outro paciente NÃO acessa preparo/resultado alheio.
"""
from datetime import datetime, date

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Clinica, Paciente, Exame, Agendamento, ResultadoExame, normalizar_telefone,
)

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Cenário: Bia frequenta o Grupo Saúde Total E a Clínica Vitória (uma
# conta, dois cadastros), com um exame futuro em cada e um resultado na
# Vitória.
with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_vit = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()

    ex_grupo = Exame(clinica_id=centro.id, medico_id=medico_grupo.id, nome="Exame Unificado Grupo",
                     descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    ex_vit = Exame(clinica_id=vitoria.id, medico_id=medico_vit.id, nome="Exame Unificado Vitoria",
                   descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    db.session.add_all([ex_grupo, ex_vit])
    db.session.flush()

    tel = normalizar_telefone("(27) 97070-0001")
    conta = Usuario(nome="Bia Unificada", telefone=tel, tipo="paciente")
    db.session.add(conta)
    db.session.flush()
    pac_grupo = Paciente(empresa_id=centro.empresa_id, usuario_id=conta.id, nome="Bia Unificada",
                         cpf="820.930.140-05", data_nascimento=date(1988, 8, 8), telefone=tel,
                         status_cadastro="aprovado")
    pac_vit = Paciente(empresa_id=vitoria.empresa_id, usuario_id=conta.id, nome="Bia Unificada",
                       cpf="820.930.140-05", data_nascimento=date(1988, 8, 8), telefone=tel,
                       status_cadastro="aprovado")
    db.session.add_all([pac_grupo, pac_vit])
    db.session.flush()

    ag_grupo = Agendamento(clinica_id=centro.id, paciente_id=pac_grupo.id, exame_id=ex_grupo.id,
                           medico_id=medico_grupo.id, data_hora=datetime(2026, 10, 1, 9, 0), status="agendado")
    ag_vit = Agendamento(clinica_id=vitoria.id, paciente_id=pac_vit.id, exame_id=ex_vit.id,
                         medico_id=medico_vit.id, data_hora=datetime(2026, 10, 2, 10, 0), status="agendado")
    ag_vit_passado = Agendamento(clinica_id=vitoria.id, paciente_id=pac_vit.id, exame_id=ex_vit.id,
                                 medico_id=medico_vit.id, data_hora=datetime(2026, 6, 1, 10, 0), status="realizado")
    db.session.add_all([ag_grupo, ag_vit, ag_vit_passado])
    db.session.flush()
    db.session.add(ResultadoExame(agendamento_id=ag_vit_passado.id, nome_arquivo="resultado.pdf",
                                  caminho_arquivo="nao-existe.pdf"))

    # Outro paciente (do Grupo), pra prova de isolamento entre CONTAS.
    tel2 = normalizar_telefone("(27) 97070-0002")
    outro_u = Usuario(nome="Outro Paciente", telefone=tel2, tipo="paciente")
    db.session.add(outro_u)
    db.session.flush()
    outro_p = Paciente(empresa_id=centro.empresa_id, usuario_id=outro_u.id, nome="Outro Paciente",
                       cpf="820.930.140-16", data_nascimento=date(1970, 1, 1), telefone=tel2,
                       status_cadastro="aprovado")
    db.session.add(outro_p)
    db.session.commit()
    ag_grupo_id, ag_vit_id, ag_vit_passado_id = ag_grupo.id, ag_vit.id, ag_vit_passado.id
    pac_grupo_id, pac_vit_id = pac_grupo.id, pac_vit.id

# ---------- Painel unificado ----------

r = client.post("/login-paciente", data={"cpf": "820.930.140-05", "data_nascimento": "08/08/1988"},
                follow_redirects=True)
html = r.get_data(as_text=True)
checar("Login entra direto no painel unificado", "Olá, Bia Unificada" in html)
checar("Painel mostra os exames das DUAS clínicas juntos",
       "Exame Unificado Grupo" in html and "Exame Unificado Vitoria" in html)
checar("Cada linha identifica o local",
       "Grupo Saúde Total - Centro" in html and "Clínica Vitória" in html)
checar("O seletor 'paciente em 2 clínicas' aparece", "paciente em 2 clínicas" in html)
checar("O histórico da Vitória também aparece (exame realizado)", "realizado" in html)
checar("O botão de baixar resultado aparece no histórico", "Baixar resultado" in html)

# ---------- Preparo/resultado de qualquer clínica ----------

r = client.get(f"/paciente/exame/{ag_grupo_id}")
checar("Preparo do exame do Grupo abre", r.status_code == 200)
r = client.get(f"/paciente/exame/{ag_vit_id}")
checar("Preparo do exame da Vitória abre NA MESMA conta", r.status_code == 200)

# ---------- Trocar de clínica muda o alvo do 'solicitar' ----------

r = client.get("/paciente/agendar")
html = r.get_data(as_text=True)
checar("Solicitar mostra a clínica ativa e o botão de trocar",
       "Solicitando na clínica" in html and "Trocar de clínica" in html)
checar("Exames ofertados são os da clínica ativa",
       ("Exame Unificado Grupo" in html) != ("Exame Unificado Vitoria" in html))

# Descobre a clínica ativa e troca pra outra.
ativa_grupo = "Exame Unificado Grupo" in html
destino_id = pac_vit_id if ativa_grupo else pac_grupo_id
r = client.post("/paciente/trocar-clinica",
                data={"paciente_id": str(destino_id), "proxima": "/paciente/agendar"},
                follow_redirects=True)
html2 = r.get_data(as_text=True)
checar("Trocar de clínica funciona (flash de confirmação)", "Agora você está usando o app" in html2)
checar("Depois da troca, os exames ofertados são da OUTRA clínica",
       ("Exame Unificado Vitoria" in html2) == ativa_grupo)
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
