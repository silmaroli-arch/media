"""Testa a exclusão PERMANENTE de um médico/secretária pelo dono da
plataforma (app/exclusao_usuario.py + dono.usuario_excluir) - a
funcionalidade que substitui a antiga tela "Limpar dados de teste"
(auth.dev_limpar_base, removida - apagava o banco inteiro sem login).

Cobre:
1. verificar_bloqueios_exclusao: os dois casos que devem ser BLOQUEADOS
   (dono de um Grupo com outras pessoas ativas; médico responsável de um
   Exame que já tem agendamento de OUTRO médico contra ele) - e o caso
   sem bloqueio nenhum.
2. excluir_usuario_e_dados: caminho feliz completo - conta, GrupoMembro,
   Exame/Agendamento próprios, LicencaPagamento, PushSubscription somem;
   Paciente cadastrado por ela sobrevive só perdendo a atribuição
   (cadastrado_por_id vira None); grupo do qual ela era a ÚNICA integrante
   ativa é apagado junto.
3. A rota dono.usuario_excluir: senha errada não apaga nada; bloqueios
   impedem a exclusão e mostram flash; sucesso exclui de verdade; 404
   para usuário que não é médico/secretária (ex.: paciente ou dono).

Roda com banco isolado (SQLite fresco por execução, como os demais
arquivos de teste deste projeto): `rm -f preparo_exames.db && python3
test_exclusao_usuario.py`.
"""
from datetime import date, datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoMembro, Paciente, GrupoPaciente,
    Exame, Agendamento, LicencaPagamento, PushSubscription,
)
from app.exclusao_usuario import verificar_bloqueios_exclusao, excluir_usuario_e_dados

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    db.create_all()

    dono_plataforma = Usuario(nome="Dono da Plataforma", email="dono@plataforma.com", tipo="dono")
    dono_plataforma.set_senha("senha-dono-123")
    db.session.add(dono_plataforma)
    db.session.commit()

    # --- Cenário 1: médico solo, sem bloqueios, com paciente/exame/agendamento
    # e licença/push - caminho feliz completo. ---
    grupo_solo = Grupo(nome="Consultório Solo", status="ativa")
    db.session.add(grupo_solo)
    db.session.commit()

    medico_solo = Usuario(nome="Dr. Solo", email="solo@teste.com", tipo="medico")
    medico_solo.set_senha("123456")
    medico_solo.valor_licenca_mensal = 100.00
    db.session.add(medico_solo)
    db.session.commit()

    db.session.add(GrupoMembro(grupo_id=grupo_solo.id, usuario_id=medico_solo.id, papel="dono", ativo=True))
    db.session.commit()

    paciente_do_solo = Paciente(
        nome="Paciente do Solo", cpf="11111111111", cadastrado_por_id=medico_solo.id,
    )
    db.session.add(paciente_do_solo)
    db.session.commit()
    db.session.add(GrupoPaciente(grupo_id=grupo_solo.id, paciente_id=paciente_do_solo.id))

    exame_do_solo = Exame(grupo_id=grupo_solo.id, medico_id=medico_solo.id, criado_por_id=medico_solo.id, nome="Exame Solo")
    db.session.add(exame_do_solo)
    db.session.commit()

    agendamento_do_solo = Agendamento(
        grupo_id=grupo_solo.id, paciente_id=paciente_do_solo.id, exame_id=exame_do_solo.id,
        medico_id=medico_solo.id, data_hora=datetime.utcnow() + timedelta(days=1),
    )
    db.session.add(agendamento_do_solo)
    db.session.add(PushSubscription(usuario_id=medico_solo.id, endpoint="https://push.teste/x", p256dh="a", auth="b"))
    db.session.commit()

    mes_atual = date.today().replace(day=1)
    db.session.add(LicencaPagamento(usuario_id=medico_solo.id, mes=mes_atual, pago=False, valor=100.00))
    db.session.commit()

    bloqueios_solo = verificar_bloqueios_exclusao(medico_solo)
    checar("Médico solo (único dono do grupo, sem mais ninguém) não tem bloqueios", bloqueios_solo == [])

    medico_solo_id = medico_solo.id
    grupo_solo_id = grupo_solo.id
    paciente_do_solo_id = paciente_do_solo.id
    exame_do_solo_id = exame_do_solo.id
    agendamento_do_solo_id = agendamento_do_solo.id

    excluir_usuario_e_dados(medico_solo)
    db.session.commit()

    checar("Conta do médico solo foi apagada", Usuario.query.get(medico_solo_id) is None)
    checar("Grupo em que ele era o ÚNICO membro ativo foi apagado junto", Grupo.query.get(grupo_solo_id) is None)
    checar("Exame dele foi apagado", Exame.query.get(exame_do_solo_id) is None)
    checar("Agendamento dele foi apagado", Agendamento.query.get(agendamento_do_solo_id) is None)
    checar("LicencaPagamento dele foi apagada", LicencaPagamento.query.filter_by(usuario_id=medico_solo_id).count() == 0)
    checar("PushSubscription dele foi apagada", PushSubscription.query.filter_by(usuario_id=medico_solo_id).count() == 0)
    paciente_sobrevivente = Paciente.query.get(paciente_do_solo_id)
    checar("Paciente cadastrado por ele SOBREVIVE", paciente_sobrevivente is not None)
    checar("Paciente perde a atribuição pessoal (cadastrado_por_id vira None)",
           paciente_sobrevivente.cadastrado_por_id is None)

    # --- Cenário 2: bloqueio por ser dono de um Grupo com outras pessoas ativas. ---
    grupo_equipe = Grupo(nome="Clínica em Equipe", status="ativa")
    db.session.add(grupo_equipe)
    db.session.commit()

    dono_do_grupo = Usuario(nome="Dra. Titular", email="titular@teste.com", tipo="medico")
    dono_do_grupo.set_senha("123456")
    outro_membro = Usuario(nome="Secretária da Equipe", email="secretaria@teste.com", tipo="secretaria")
    outro_membro.set_senha("123456")
    db.session.add_all([dono_do_grupo, outro_membro])
    db.session.commit()

    db.session.add_all([
        GrupoMembro(grupo_id=grupo_equipe.id, usuario_id=dono_do_grupo.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_equipe.id, usuario_id=outro_membro.id, papel="membro", ativo=True),
    ])
    db.session.commit()

    bloqueios_dono = verificar_bloqueios_exclusao(dono_do_grupo)
    checar("Dono de grupo com outra pessoa ativa É bloqueado", len(bloqueios_dono) == 1)
    checar("Mensagem de bloqueio menciona transferir titularidade", "titularidade" in bloqueios_dono[0])

    # Exclusão da secretária (membro comum, não dono) não é bloqueada por isso.
    bloqueios_outro_membro = verificar_bloqueios_exclusao(outro_membro)
    checar("Membro comum (não-dono) do mesmo grupo não é bloqueado por isso", bloqueios_outro_membro == [])

    # --- Cenário 3: bloqueio por ser médico responsável de um Exame já
    # usado em agendamento de OUTRO médico (exame compartilhado). ---
    grupo_compartilhado = Grupo(nome="Grupo com Exame Compartilhado", status="ativa")
    db.session.add(grupo_compartilhado)
    db.session.commit()

    medico_responsavel = Usuario(nome="Dr. Responsável", email="responsavel@teste.com", tipo="medico")
    medico_responsavel.set_senha("123456")
    medico_colega = Usuario(nome="Dr. Colega", email="colega@teste.com", tipo="medico")
    medico_colega.set_senha("123456")
    terceira_dona = Usuario(nome="Dra. Titular do Grupo", email="titular3@teste.com", tipo="medico")
    terceira_dona.set_senha("123456")
    db.session.add_all([medico_responsavel, medico_colega, terceira_dona])
    db.session.commit()

    # Nem medico_responsavel nem medico_colega são donos do grupo aqui de
    # propósito - o cenário testado é só o bloqueio pelo exame
    # compartilhado, isolado do bloqueio por titularidade de grupo (já
    # coberto no Cenário 2 acima).
    db.session.add_all([
        GrupoMembro(grupo_id=grupo_compartilhado.id, usuario_id=terceira_dona.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_compartilhado.id, usuario_id=medico_colega.id, papel="membro", ativo=True),
        GrupoMembro(grupo_id=grupo_compartilhado.id, usuario_id=medico_responsavel.id, papel="membro", ativo=True),
    ])
    db.session.commit()

    paciente_compartilhado = Paciente(nome="Paciente Compartilhado", cpf="22222222222")
    db.session.add(paciente_compartilhado)
    db.session.commit()

    exame_compartilhado = Exame(
        grupo_id=grupo_compartilhado.id, medico_id=medico_responsavel.id,
        criado_por_id=medico_responsavel.id, nome="Exame Compartilhado",
    )
    db.session.add(exame_compartilhado)
    db.session.commit()

    # Agendamento feito pelo COLEGA contra o exame do médico_responsavel.
    db.session.add(Agendamento(
        grupo_id=grupo_compartilhado.id, paciente_id=paciente_compartilhado.id,
        exame_id=exame_compartilhado.id, medico_id=medico_colega.id,
        data_hora=datetime.utcnow() + timedelta(days=2),
    ))
    db.session.commit()

    bloqueios_responsavel = verificar_bloqueios_exclusao(medico_responsavel)
    checar("Médico responsável por exame usado por outro médico É bloqueado", len(bloqueios_responsavel) == 1)
    checar("Mensagem de bloqueio menciona reatribuir médico responsável", "reatribua" in bloqueios_responsavel[0])

    # O colega (não é o responsável pelo exame) não é bloqueado por isso.
    bloqueios_colega = verificar_bloqueios_exclusao(medico_colega)
    checar("Colega (não é o médico responsável do exame) não é bloqueado por isso", bloqueios_colega == [])

    # --- Fixtures para os testes de rota abaixo. ---
    medico_para_rota = Usuario(nome="Dr. Rota Feliz", email="rota.feliz@teste.com", tipo="medico")
    medico_para_rota.set_senha("123456")
    db.session.add(medico_para_rota)
    db.session.commit()
    grupo_da_rota = Grupo(nome="Grupo da Rota", status="ativa")
    db.session.add(grupo_da_rota)
    db.session.commit()
    db.session.add(GrupoMembro(grupo_id=grupo_da_rota.id, usuario_id=medico_para_rota.id, papel="dono", ativo=True))
    db.session.commit()
    medico_para_rota_id = medico_para_rota.id

    paciente_avulso = Paciente(nome="Paciente Avulso", cpf="33333333333")
    db.session.add(paciente_avulso)
    db.session.commit()
    paciente_avulso_id = paciente_avulso.id
    # Paciente e Usuario são tabelas com contadores de id independentes -
    # por coincidência um Usuario pode ter o mesmo id numérico deste
    # Paciente (não é o mesmo registro, IDs de tabelas diferentes não têm
    # relação nenhuma). O teste abaixo depende de testar a rota contra um
    # id que NÃO corresponde a nenhum Usuario de verdade - senão o 404
    # esperado nunca aconteceria (a rota acharia esse outro Usuario e
    # seguiria em frente). Garantimos isso pegando um id bem acima de
    # qualquer Usuario já criado neste teste.
    while Usuario.query.get(paciente_avulso_id) is not None:
        outro_paciente = Paciente(nome="Paciente Avulso (ajuste de id)", cpf=f"3333333{paciente_avulso_id}")
        db.session.add(outro_paciente)
        db.session.commit()
        paciente_avulso_id = outro_paciente.id


# ---------- Testes de rota (dono.usuario_excluir) ----------

with client.session_transaction() as sess:
    pass
resp_login = client.post("/login", data={"identificador": "dono@plataforma.com", "senha": "senha-dono-123"}, follow_redirects=True)
checar("Dono da plataforma consegue logar", resp_login.status_code == 200)

# Senha errada não apaga nada.
r1 = client.post(
    f"/dono/usuarios/{medico_para_rota_id}/excluir",
    data={"senha_confirmacao": "senha-completamente-errada"},
    follow_redirects=True,
)
checar("Rota responde 200 mesmo com senha errada (via redirect)", r1.status_code == 200)
checar("Flash de senha incorreta aparece", "Senha incorreta" in r1.get_data(as_text=True))
with app.app_context():
    checar("Conta NÃO foi excluída com senha errada", Usuario.query.get(medico_para_rota_id) is not None)

# 404 para tipo que não é médico/secretária (paciente).
r2 = client.post(
    f"/dono/usuarios/{paciente_avulso_id}/excluir",
    data={"senha_confirmacao": "senha-dono-123"},
)
checar("Excluir um Paciente pela rota de médico/secretária dá 404", r2.status_code == 404)

# Bloqueio real via rota: cria de novo o cenário do dono-de-grupo-com-equipe
# (o das checagens diretas acima já foi consumido só como unidade).
with app.app_context():
    grupo_bloq = Grupo(nome="Grupo Bloqueado via Rota", status="ativa")
    db.session.add(grupo_bloq)
    db.session.commit()
    dono_bloq = Usuario(nome="Dr. Bloqueado", email="bloqueado@teste.com", tipo="medico")
    dono_bloq.set_senha("123456")
    membro_bloq = Usuario(nome="Secretária Bloqueio", email="secbloqueio@teste.com", tipo="secretaria")
    membro_bloq.set_senha("123456")
    db.session.add_all([dono_bloq, membro_bloq])
    db.session.commit()
    db.session.add_all([
        GrupoMembro(grupo_id=grupo_bloq.id, usuario_id=dono_bloq.id, papel="dono", ativo=True),
        GrupoMembro(grupo_id=grupo_bloq.id, usuario_id=membro_bloq.id, papel="membro", ativo=True),
    ])
    db.session.commit()
    dono_bloq_id = dono_bloq.id

r3 = client.post(
    f"/dono/usuarios/{dono_bloq_id}/excluir",
    data={"senha_confirmacao": "senha-dono-123"},
    follow_redirects=True,
)
checar("Rota responde 200 quando há bloqueio (via redirect)", r3.status_code == 200)
checar("Flash de bloqueio (titularidade) aparece", "titularidade" in r3.get_data(as_text=True))
with app.app_context():
    checar("Conta NÃO foi excluída quando há bloqueio", Usuario.query.get(dono_bloq_id) is not None)

# Caminho feliz via rota: senha certa, sem bloqueios, exclui de verdade.
r4 = client.post(
    f"/dono/usuarios/{medico_para_rota_id}/excluir",
    data={"senha_confirmacao": "senha-dono-123"},
    follow_redirects=True,
)
checar("Rota responde 200 no caminho feliz", r4.status_code == 200)
checar("Flash de sucesso aparece", "excluídos permanentemente" in r4.get_data(as_text=True))
with app.app_context():
    checar("Conta foi excluída de verdade pela rota", Usuario.query.get(medico_para_rota_id) is None)

print("\nTodos os testes de exclusão de usuário passaram.")
