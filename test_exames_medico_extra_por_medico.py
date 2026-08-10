"""Testa que um MÉDICO (não só a secretária) consegue associar outro
médico da equipe como "médico extra" de um exame pelo qual já é
responsável - antes o formulário de editar exame escondia essa seção
inteira (checkboxes de "outros médicos") sempre que quem estava logado
era do tipo "medico", então uma clínica com dois médicos e nenhuma
secretária não tinha NENHUM jeito de vincular um segundo médico a um
exame depois de criado. A troca do médico RESPONSÁVEL principal continua
restrita à secretária - só os médicos "extras" (compartilhamento) passam
a ser editáveis por um médico também."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Exame

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    dr_carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    dra_fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    exame = Exame.query.filter_by(nome="Glicemia de jejum").first()
    checar("Exame do seed já está com Dr. Carlos como responsável (pré-condição)", exame.medico_id == dr_carlos.id)
    checar("Exame do seed ainda não tem médico extra (pré-condição)", len(exame.medicos_extra) == 0)
    exame_id = exame.id
    clinica_id = exame.clinica_id
    dra_fernanda_id = dra_fernanda.id

login("medico@clinicavitoria.com", "123456")
# Dr. Carlos atende em mais de uma clínica (Vitória e São Paulo) - garante
# que a Clínica Vitória (dona deste exame) está selecionada como ativa.
client.post("/equipe/clinica", data={"clinica_id": str(clinica_id)}, follow_redirects=True)

# O formulário de editar exame mostra a seção de "outros médicos" mesmo
# para um médico logado (antes só aparecia para secretária).
r0 = client.get(f"/equipe/exames/{exame_id}/editar")
html0 = r0.get_data(as_text=True)
checar("Formulário de editar exame responde 200", r0.status_code == 200)
checar("Mostra a seção 'Outros médicos que também atendem este exame'", "Outros médicos que também atendem este exame" in html0)
checar("Tem o checkbox da Dra. Fernanda", f'name="medicos_extra_ids" value="{dra_fernanda_id}"' in html0)
checar("NÃO mostra o select de 'Médico responsável' (continua só p/ secretária)", 'name="medico_id"' not in html0)

# Marca a Dra. Fernanda como médica extra e salva.
r1 = client.post(f"/equipe/exames/{exame_id}/editar", data={
    "nome": "Glicemia de jejum", "descricao": "Exame de sangue para medir glicose", "duracao_minutos": "15",
    "medicos_extra_ids": [str(dra_fernanda_id)],
}, follow_redirects=True)
checar("Salvar o exame com médico extra responde 200", r1.status_code == 200)
checar("Mensagem de sucesso aparece", "Exame atualizado" in r1.get_data(as_text=True))

with app.app_context():
    exame_atualizado = Exame.query.get(exame_id)
    checar("Dra. Fernanda foi adicionada como médica extra", any(m.id == dra_fernanda_id for m in exame_atualizado.medicos_extra))
    checar("Dr. Carlos continua sendo o médico responsável (não mudou)", exame_atualizado.medico_id == dr_carlos.id)

# Desmarcando a Dra. Fernanda (não enviando o checkbox) remove ela da lista.
r2 = client.post(f"/equipe/exames/{exame_id}/editar", data={
    "nome": "Glicemia de jejum", "descricao": "Exame de sangue para medir glicose", "duracao_minutos": "15",
}, follow_redirects=True)
checar("Salvar sem marcar nenhum médico extra responde 200", r2.status_code == 200)
with app.app_context():
    exame_final = Exame.query.get(exame_id)
    checar("Dra. Fernanda foi removida da lista de médicos extras", len(exame_final.medicos_extra) == 0)

client.get("/logout")
print("\nTodos os testes de médico extra editável por médico passaram.")
