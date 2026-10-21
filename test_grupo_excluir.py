"""Excluir grupo de trabalho (pedido do Silvan, 2026-10-20, opção 1): só o
dono do grupo exclui, e só se o grupo estiver VAZIO (sem outras pessoas,
pacientes, agenda, exames, preparos, FAQ nem perguntas). Com dados, a
mensagem diz o que ainda existe e nada é apagado."""
from datetime import date

from app import create_app
from app.extensions import db
from app.models import Grupo, GrupoMembro, GrupoConvite, GrupoPaciente, Paciente, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


dono = app.test_client()
dono.post("/cadastro", data={
    "nome": "Medica Exclui Grupo", "email": "exclui.grupo@teste.com", "senha": "123456", "senha_confirmacao": "123456",
    "cpf": "529.982.247-25", "telefone": "(27) 98888-3333", "papel": "medico",
    "crm_numero": "33333", "crm_uf": "ES", "data_nascimento": "01/01/1980",
}, follow_redirects=True)
dono.post("/grupos/novo", data={"nome": "Grupo Vazio"}, follow_redirects=True)
dono.post("/grupos/novo", data={"nome": "Grupo Com Paciente"}, follow_redirects=True)
with app.app_context():
    vazio = Grupo.query.filter_by(nome="Grupo Vazio").first()
    cheio = Grupo.query.filter_by(nome="Grupo Com Paciente").first()
    vazio_id, cheio_id = vazio.id, cheio.id
    p = Paciente(nome="Paciente Do Grupo", cpf="390.533.447-05", data_nascimento=date(1990, 1, 1), telefone="27911112222")
    db.session.add(p)
    db.session.flush()
    db.session.add(GrupoPaciente(grupo_id=cheio_id, paciente_id=p.id))
    paciente_real_id = p.id
    # convite pendente no grupo vazio: vai junto na exclusão
    outra = Usuario.query.filter_by(email="secretaria@clinicavitoria.com").first()
    db.session.add(GrupoConvite(grupo_id=vazio_id, usuario_convidado_id=outra.id, convidado_por_id=outra.id))
    db.session.commit()

html = dono.get("/grupos/").get_data(as_text=True)
checar("A lista mostra 'Excluir' para o dono do grupo", f"/grupos/{vazio_id}/excluir" in html and f"/grupos/{cheio_id}/excluir" in html)

# ---------- Com dados: não exclui e explica ----------
r = dono.post(f"/grupos/{cheio_id}/excluir", follow_redirects=True)
html = r.get_data(as_text=True)
checar("Grupo com paciente NÃO é excluído", "não pode ser excluído" in html and "1 paciente(s)" in html)
with app.app_context():
    checar("... e continua no banco, com o paciente", Grupo.query.get(cheio_id) is not None
           and GrupoPaciente.query.filter_by(grupo_id=cheio_id, paciente_id=paciente_real_id).count() == 1)

# ---------- Quem não é dono não exclui ----------
with app.app_context():
    sec = Usuario.query.filter_by(email="secretaria@clinicavitoria.com").first()
    sec_id = sec.id
    db.session.add(GrupoMembro(grupo_id=vazio_id, usuario_id=sec_id, papel="administrador", ativo=True))
    db.session.commit()
outro = app.test_client()
outro.post("/login", data={"identificador": "secretaria@clinicavitoria.com", "senha": "123456"})
r = outro.post(f"/grupos/{vazio_id}/excluir", follow_redirects=True)
checar("Administrador (não dono) não exclui", "Só o dono do grupo pode excluí-lo." in r.get_data(as_text=True))
r = dono.post(f"/grupos/{vazio_id}/excluir", follow_redirects=True)
checar("Com outra pessoa no grupo, também não exclui", "1 membro(s) além de você" in r.get_data(as_text=True))
with app.app_context():
    GrupoMembro.query.filter_by(grupo_id=vazio_id, usuario_id=sec_id).update({"ativo": False})
    db.session.commit()

# ---------- Vazio: exclui ----------
# O próprio médico entra como paciente em todo grupo que cria (para testar o
# WhatsApp) - isso não conta: o "Grupo Vazio" tem esse vínculo e é excluído.
with app.app_context():
    medica = Usuario.query.filter_by(email="exclui.grupo@teste.com").first()
    proprio = Paciente.query.filter_by(cadastrado_por_id=medica.id).first()
    checar("O grupo vazio tem só o vínculo do próprio médico como paciente",
           proprio is not None and GrupoPaciente.query.filter_by(grupo_id=vazio_id).count() == 1)
    proprio_id = proprio.id
r = dono.post(f"/grupos/{vazio_id}/excluir", follow_redirects=True)
html = r.get_data(as_text=True)
checar("Grupo vazio é excluído", "Grupo &#34;Grupo Vazio&#34; excluído." in html)
with app.app_context():
    checar("... e some do banco, junto com membros e convites",
           Grupo.query.get(vazio_id) is None
           and GrupoMembro.query.filter_by(grupo_id=vazio_id).count() == 0
           and GrupoConvite.query.filter_by(grupo_id=vazio_id).count() == 0
           and GrupoPaciente.query.filter_by(grupo_id=vazio_id).count() == 0)
    checar("O cadastro do médico como paciente continua (só o vínculo saiu)", Paciente.query.get(proprio_id) is not None)
checar("A lista não mostra mais o grupo excluído", "Grupo Vazio" not in dono.get("/grupos/").get_data(as_text=True))
r = dono.get("/equipe/", follow_redirects=True)
checar("O sistema continua funcionando depois de excluir o grupo ativo", r.status_code == 200)

print("\nTodos os testes de excluir grupo passaram.")
