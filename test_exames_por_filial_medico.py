"""Testa que a tela "Associar exames" segue o MESMO fluxo pra médico e
pra secretária - não existe "médico principal" nem atalho especial: mesmo
um médico logado sozinho (grupo sem secretária) escolhe o médico
responsável numa lista, exatamente como a secretária faria. Antes o médico
era forçado automaticamente como responsável (sem select, sem escolha);
isso foi removido a pedido do usuário ("todos devem seguir o mesmo fluxo
de associação").

Fatia 5: a tela não escolhe mais uma FILIAL DE DESTINO (cada Grupo já é
sua própria unidade) - a associação é sempre feita diretamente no Grupo
atual (ver app/routes_medico.py:exames_por_filial)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, Exame, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_id = medico_grupo.id
    modelo = PreparoModelo(grupo_id=centro_id, nome="Preparo Raio-X", instrucoes="Retirar objetos metálicos.")
    db.session.add(modelo)
    db.session.commit()
    modelo_id = modelo.id

login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)
# Cadastro genérico (sem médico/preço) - médico/preço são definidos depois,
# na tela "Associar exames".
client.post("/equipe/exames/novo", data={
    "nome": "Raio-X Torax", "descricao": "Raio-X", "duracao_minutos": "15",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
with app.app_context():
    exame_origem_id = Exame.query.filter_by(nome="Raio-X Torax").first().id
client.get("/logout")

# O médico (do mesmo Grupo) vê a tela normalmente e associa o exame - mas
# segue o MESMO fluxo da secretária: precisa escolher o médico responsável
# no select (mesmo sendo ele mesmo a única opção).
login("medico@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Médico vê a tela normalmente", r.status_code == 200)
checar("Médico vê o próprio exame na lista", "Raio-X Torax" in html)
# No formulário "Adicionar", o select de médico existe pro médico também
# (mesmo fluxo da secretária).
checar("O select de médico aparece pro médico também (mesmo fluxo da secretária)",
       'name="medico_id"' in html and f'value="{medico_id}"' in html)

# Tentar associar SEM escolher médico é bloqueado - não existe mais o
# atalho de auto-associação silenciosa.
r_sem_medico = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Raio-X Torax",
}, follow_redirects=True)
checar("Associar sem escolher médico é bloqueado", "Escolha um médico válido" in r_sem_medico.get_data(as_text=True))
with app.app_context():
    checar(
        "Não associou o exame sem escolher médico",
        Exame.query.filter_by(nome="Raio-X Torax").first().associado is False,
    )

# Escolhendo o médico explicitamente (mesmo sendo ele mesmo) funciona.
r2 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Raio-X Torax", "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Médico consegue se associar escolhendo a si mesmo no select", "associado com" in r2.get_data(as_text=True))

with app.app_context():
    exame_atualizado = Exame.query.filter_by(nome="Raio-X Torax").first()
    checar("Exame associado tem o médico escolhido como responsável", exame_atualizado.medico_id == medico_id)

client.get("/logout")
print("\nTodos os testes do fluxo unificado (sem médico principal) em exames por filial passaram.")
