"""Teste de ponta a ponta do fluxo de "trabalho compartilhado" (grupo)
descrito no BBP MedIA (seção 5.1.4 a 5.1.7 e seção 6.1/6.2): cadastro de
usuário (já existente) -> login por CPF -> criar grupo -> convidar membro
por CPF -> aprovar convite -> promover a administrador -> remover membro
-> sair do grupo. Cria seus próprios usuários (não depende do seed.py)
para rodar isolado."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, GrupoMembro, GrupoConvite, Empresa, Clinica, ClinicaMembro

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    # Limpa execuções anteriores deste teste (idempotente).
    for email in ("dono.bbp@teste.com", "membro.bbp@teste.com", "terceiro.bbp@teste.com"):
        u = Usuario.query.filter_by(email=email).first()
        if u:
            GrupoConvite.query.filter_by(usuario_convidado_id=u.id).delete()
            GrupoConvite.query.filter_by(convidado_por_id=u.id).delete()
            GrupoMembro.query.filter_by(usuario_id=u.id).delete()
            db.session.delete(u)
    db.session.commit()

    dono = Usuario(nome="Dra. Ana Ferreira", email="dono.bbp@teste.com", tipo="medico",
                    cpf="111.111.111-11", crm_numero="1111", crm_uf="ES")
    dono.set_senha("123456")
    dono.definir_permissoes_padrao()

    membro = Usuario(nome="Carlos Souza", email="membro.bbp@teste.com", tipo="secretaria",
                      cpf="222.222.222-22")
    membro.set_senha("123456")
    membro.definir_permissoes_padrao()

    terceiro = Usuario(nome="Dr. Bruno Lima", email="terceiro.bbp@teste.com", tipo="medico",
                        cpf="333.333.333-33", crm_numero="2222", crm_uf="ES")
    terceiro.set_senha("123456")
    terceiro.definir_permissoes_padrao()

    db.session.add_all([dono, membro, terceiro])
    db.session.commit()
    dono_id, membro_id, terceiro_id = dono.id, membro.id, terceiro.id

    # Vínculo mínimo com Empresa/Clínica (modelo legado, ainda usado pelo
    # restante do sistema — ex.: medico.dashboard exige ao menos uma
    # clínica ativa) só para os três conseguirem acessar o painel e
    # exercitar o fluxo novo de grupo por cima dele. A reformulação
    # completa (remover essa exigência do modelo antigo) é um trabalho
    # futuro maior, fora do escopo desta primeira fatia do BBP.
    empresa_teste = Empresa.query.filter_by(nome="Empresa Teste BBP").first()
    if not empresa_teste:
        empresa_teste = Empresa(nome="Empresa Teste BBP", status="ativa")
        db.session.add(empresa_teste)
        db.session.commit()
    clinica_teste = Clinica.query.filter_by(nome="Clínica Teste BBP", empresa_id=empresa_teste.id).first()
    if not clinica_teste:
        clinica_teste = Clinica(nome="Clínica Teste BBP", empresa_id=empresa_teste.id)
        db.session.add(clinica_teste)
        db.session.commit()
    for uid in (dono_id, membro_id, terceiro_id):
        if not ClinicaMembro.query.filter_by(clinica_id=clinica_teste.id, usuario_id=uid).first():
            db.session.add(ClinicaMembro(clinica_id=clinica_teste.id, usuario_id=uid))
    db.session.commit()


client_dono = app.test_client()
client_membro = app.test_client()
client_terceiro = app.test_client()


def login(client, cpf, senha):
    return client.post("/login", data={"identificador": cpf, "senha": senha}, follow_redirects=True)


# ---------- 5.1.2 — Login por CPF ----------
r = login(client_dono, "111.111.111-11", "123456")
checar("Login do dono por CPF funciona (5.1.2)", r.status_code == 200 and "Painel" in r.get_data(as_text=True))

r = login(client_membro, "222.222.222-22", "123456")
checar("Login do membro por CPF funciona", r.status_code == 200)

r = login(client_terceiro, "333.333.333-33", "123456")
checar("Login do terceiro médico por CPF funciona", r.status_code == 200)

r_falha = app.test_client().post("/login", data={"identificador": "111.111.111-11", "senha": "errada"}, follow_redirects=True)
checar("Login falha com senha errada", "CPF/e-mail ou senha inv" in r_falha.get_data(as_text=True))


# ---------- 5.1.4 — Criar trabalho compartilhado (grupo) ----------
r = client_dono.post("/grupos/novo", data={"nome": "Clínica Vitória BBP"}, follow_redirects=True)
checar("Criar grupo redireciona para Meus grupos com sucesso", r.status_code == 200 and "criado com sucesso" in r.get_data(as_text=True))

with app.app_context():
    grupo = Grupo.query.filter_by(nome="Clínica Vitória BBP").order_by(Grupo.id.desc()).first()
    checar("Grupo foi persistido no banco", grupo is not None)
    grupo_id = grupo.id
    m_dono = GrupoMembro.query.filter_by(grupo_id=grupo_id, usuario_id=dono_id).first()
    checar("Dono vira membro do grupo com papel 'dono'", m_dono is not None and m_dono.papel == "dono")

r = client_dono.get("/grupos/")
html = r.get_data(as_text=True)
checar("Tela 'Meus grupos' lista o grupo recém-criado", "Clínica Vitória BBP" in html)
checar("Papel do dono aparece como 'Dono (criador)'", "Dono (criador)" in html)


# ---------- 5.1.5 — Convidar membro (só por CPF de conta já existente) ----------
r = client_dono.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "222.222.222-22"}, follow_redirects=True)
checar("Convite enviado com sucesso", "Convite enviado" in r.get_data(as_text=True))

r_cpf_inexistente = client_dono.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "999.999.999-99"}, follow_redirects=True)
checar("CPF sem conta cadastrada não gera convite (sem cadastro de equipe)", "Nenhum usuário cadastrado" in r_cpf_inexistente.get_data(as_text=True))

with app.app_context():
    convite = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=membro_id).first()
    checar("Convite foi criado com status 'pendente'", convite is not None and convite.status == "pendente")
    convite_id = convite.id

# Um membro comum (ainda não é membro nenhum) não pode convidar.
r_terceiro_tenta_convidar = client_terceiro.get(f"/grupos/{grupo_id}/convidar", follow_redirects=True)
checar("Quem não é membro do grupo não pode acessar a tela de convite", "Somente um administrador" in r_terceiro_tenta_convidar.get_data(as_text=True))


# ---------- 5.1.6 — Meus convites (aprovar) ----------
r = client_membro.get("/grupos/convites")
html = r.get_data(as_text=True)
checar("Convite pendente aparece em 'Meus convites' do convidado", "Clínica Vitória BBP" in html)

r = client_membro.post(f"/grupos/convites/{convite_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)
checar("Aprovar convite confirma entrada no grupo", "agora faz parte do grupo" in r.get_data(as_text=True))

with app.app_context():
    convite_atualizado = GrupoConvite.query.get(convite_id)
    checar("Status do convite vira 'aceito'", convite_atualizado.status == "aceito")
    m_membro = GrupoMembro.query.filter_by(grupo_id=grupo_id, usuario_id=membro_id).first()
    checar("Convidado vira membro ativo com papel 'membro'", m_membro is not None and m_membro.ativo and m_membro.papel == "membro")


# ---------- 5.1.7 — Meus grupos (do lado do membro) ----------
r = client_membro.get("/grupos/")
html = r.get_data(as_text=True)
checar("Grupo aparece na lista do membro", "Clínica Vitória BBP" in html)
checar("Papel do membro aparece como 'Membro'", "Membro</span>" in html)

# Membro comum não pode conceder papel de administrador (só o dono pode).
r = client_dono.get(f"/grupos/{grupo_id}/convidar")
html_convidar_dono = r.get_data(as_text=True)
checar("Dono vê o botão 'Tornar administrador' para o membro", "Tornar administrador" in html_convidar_dono)


# ---------- Conceder papel de administrador (só o dono concede) ----------
with app.app_context():
    m_membro = GrupoMembro.query.filter_by(grupo_id=grupo_id, usuario_id=membro_id).first()
    membro_grupo_membro_id = m_membro.id

r = client_dono.post(f"/grupos/{grupo_id}/convidar", data={"acao": "tornar_administrador", "membro_id": membro_grupo_membro_id}, follow_redirects=True)
checar("Dono concede papel de administrador com sucesso", "agora é administrador" in r.get_data(as_text=True))

with app.app_context():
    m_membro = GrupoMembro.query.get(membro_grupo_membro_id)
    checar("Papel do membro vira 'administrador' no banco", m_membro.papel == "administrador")

# Agora o (ex-)membro, já administrador, também pode convidar.
r = client_membro.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "333.333.333-33"}, follow_redirects=True)
checar("Administrador (não-dono) também pode convidar membros", "Convite enviado" in r.get_data(as_text=True))

with app.app_context():
    convite2 = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=terceiro_id).first()
    checar("Segundo convite (enviado pelo administrador) foi criado", convite2 is not None)
    convite2_id = convite2.id

client_terceiro.post(f"/grupos/convites/{convite2_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)


# ---------- Remover membro (só administrador/dono) ----------
r = client_dono.post(f"/grupos/{grupo_id}/convidar", data={"acao": "remover_membro", "membro_id": membro_grupo_membro_id}, follow_redirects=True)
checar("Dono remove membro (agora administrador) do grupo", "Membro removido" in r.get_data(as_text=True))
with app.app_context():
    m_membro = GrupoMembro.query.get(membro_grupo_membro_id)
    checar("Membro removido fica inativo (não é apagado, fica histórico)", m_membro.ativo is False)

r = client_membro.get("/grupos/")
checar("Removido não vê mais o grupo em 'Meus grupos'", "Clínica Vitória BBP" not in r.get_data(as_text=True))


# ---------- Sair do grupo (não-dono) ----------
r = client_terceiro.post(f"/grupos/{grupo_id}/sair", follow_redirects=True)
checar("Membro comum consegue sair do grupo", "Você saiu do grupo" in r.get_data(as_text=True))

r = client_dono.post(f"/grupos/{grupo_id}/sair", follow_redirects=True)
checar("Dono NÃO consegue sair do grupo (precisa transferir titularidade antes)", "não pode sair" in r.get_data(as_text=True))

print("\nTodas as verificações do fluxo de trabalho compartilhado passaram.")
