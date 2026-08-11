"""Testa que a tela de EDITAR exame não pede mais o médico responsável -
reatribuir esse papel saiu desta tela e fica para uma funcionalidade
própria, futura (junto com filial e preço, que já tinham saído antes).
Mesmo enviando um "medico_id" no POST, ele é ignorado (não deve alterar o
responsável)."""
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


login("secretaria@clinicavitoria.com", "123456")

with app.app_context():
    exame = Exame.query.filter_by(nome="Colonoscopia").first()
    exame_id = exame.id
    medico_original_id = exame.medico_id
    outro_medico = Usuario.query.filter(Usuario.tipo == "medico", Usuario.id != medico_original_id).first()
    outro_medico_id = outro_medico.id if outro_medico else None

r0 = client.get(f"/equipe/exames/{exame_id}/editar")
html0 = r0.get_data(as_text=True)
checar("Tela de editar exame responde 200", r0.status_code == 200)
checar("NÃO mostra mais 'Médico responsável'", "Médico responsável" not in html0)
checar("NÃO tem mais o select de medico_id", 'name="medico_id"' not in html0)

if outro_medico_id:
    r1 = client.post(f"/equipe/exames/{exame_id}/editar", data={
        "nome": "Colonoscopia", "descricao": "Exame de colonoscopia", "duracao_minutos": "60",
        "medico_id": str(outro_medico_id),  # mesmo enviando, deve ser ignorado
    }, follow_redirects=True)
    checar("Editar exame responde 200", r1.status_code == 200)
    with app.app_context():
        checar(
            "Médico responsável NÃO mudou mesmo enviando medico_id no POST",
            Exame.query.get(exame_id).medico_id == medico_original_id,
        )

client.get("/logout")
print("\nTodos os testes de exame sem médico responsável na edição passaram.")
