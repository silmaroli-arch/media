"""Testa que a tela "Exames por filial" segue o MESMO fluxo pra médico e
pra secretária - não existe "médico principal" nem atalho especial: mesmo
um médico logado sozinho (clínica sem secretária) escolhe o médico
responsável numa lista, exatamente como a secretária faria. Antes o médico
era forçado automaticamente como responsável (sem select, sem escolha);
isso foi removido a pedido do usuário ("todos devem seguir o mesmo fluxo
de associação")."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Exame, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    centro_id, praia_id, medico_id = centro.id, praia.id, medico_grupo.id
    modelo = PreparoModelo(clinica_id=centro_id, nome="Preparo Raio-X", instrucoes="Retirar objetos metálicos.")
    db.session.add(modelo)
    db.session.commit()
    modelo_id = modelo.id

login("secretaria@gruposaude.com", "123456")
# Cadastro genérico (sem filial/médico/preço) - o médico/preço da filial de
# origem são definidos depois, na tela "Exames por filial".
client.post("/equipe/exames/novo", data={
    "nome": "Raio-X Torax", "descricao": "Raio-X", "duracao_minutos": "15",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
with app.app_context():
    exame_origem_id = Exame.query.filter_by(nome="Raio-X Torax").first().id
client.post(f"/equipe/exames/por-filial/{exame_origem_id}/atualizar", data={
    "tipo_origem": "filial", "medico_id": str(medico_id), "preco": "80,00",
}, follow_redirects=True)
client.get("/logout")

# O médico (que atende Centro e Praia) vê a tela normalmente e associa o
# exame na Praia - mas segue o MESMO fluxo da secretária: precisa escolher
# o médico responsável no select (mesmo sendo ele mesmo a única opção).
login("medico@gruposaude.com", "123456")

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Médico vê a tela normalmente", r.status_code == 200)
checar("Médico vê o próprio exame na matriz", "Raio-X Torax" in html)
checar("O select de médico aparece pro médico também (mesmo fluxo da secretária)",
       f'<option value="{medico_id}">' in html or f'value="{medico_id}" selected' in html)

# Tentar associar SEM escolher médico é bloqueado - não existe mais o atalho
# de auto-associação silenciosa.
r_sem_medico = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Raio-X Torax", "clinica_destino_id": str(praia_id), "preco": "90,00",
}, follow_redirects=True)
checar("Associar sem escolher médico é bloqueado", "Escolha um médico válido" in r_sem_medico.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou o exame na Praia sem escolher médico",
        Exame.query.filter_by(clinica_id=praia_id, nome="Raio-X Torax").first() is None,
    )

# Escolhendo o médico explicitamente (mesmo sendo ele mesmo) funciona.
r2 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Raio-X Torax", "clinica_destino_id": str(praia_id), "medico_id": str(medico_id), "preco": "90,00",
}, follow_redirects=True)
checar("Médico consegue se associar na Praia escolhendo a si mesmo no select", "associado à filial" in r2.get_data(as_text=True))

with app.app_context():
    exame_praia = Exame.query.filter_by(clinica_id=praia_id, nome="Raio-X Torax").first()
    checar("Exame criado na Praia tem o médico escolhido como responsável", exame_praia.medico_id == medico_id)
    checar("Preço informado na associação foi salvo", float(exame_praia.preco) == 90.0)

client.get("/logout")
print("\nTodos os testes do fluxo unificado (sem médico principal) em exames por filial passaram.")
