"""Testa a nova tela "Exames por filial" (medico.exames_por_filial), que
permite associar um exame já cadastrado numa filial a outra filial da mesma
empresa, escolhendo o médico responsável lá - antes só era possível
cadastrando o exame do zero de novo em cada filial (ou mexendo direto no
banco). Usa o seed's Grupo Saúde Total (filiais Centro e Praia)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Exame

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

login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# Cadastra um exame só na filial Centro.
r = client.post("/equipe/exames/novo", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30", "preco": "150",
    "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Cadastro do exame na filial Centro responde 200", r.status_code == 200)

# A tela "Exames por filial" mostra a matriz: Centro preenchido, Praia com botão de associar.
r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Tela responde 200", r.status_code == 200)
checar("Mostra o nome do exame na matriz", "Ultrassom Abdominal" in html)
checar("Mostra o médico responsável na filial Centro", medico_grupo.nome in html)
checar("Mostra o botão de associar para a filial Praia (ainda não tem)", "+ Associar" in html)

# Tentar associar sem informar preço é bloqueado (testado ANTES da associação bem-sucedida,
# senão o exame já existiria na Praia e o bloqueio seria por duplicidade, não por falta de preço).
r2b = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "clinica_destino_id": str(praia_id), "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Associar sem preço é bloqueado com aviso", "Informe o preço" in r2b.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou o exame na Praia sem o preço",
        Exame.query.filter_by(clinica_id=praia_id, nome="Ultrassom Abdominal").first() is None,
    )

# Associa o exame também à filial Praia, com o mesmo médico (que atende as duas) e um
# preço DIFERENTE do Centro — o preço é informado na hora da associação, não copiado.
r2 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "clinica_destino_id": str(praia_id), "medico_id": str(medico_id),
    "preco": "180,00",
}, follow_redirects=True)
checar("Associar à filial Praia responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "associado à filial" in r2.get_data(as_text=True))

with app.app_context():
    exame_centro = Exame.query.filter_by(clinica_id=centro_id, nome="Ultrassom Abdominal").first()
    exame_praia = Exame.query.filter_by(clinica_id=praia_id, nome="Ultrassom Abdominal").first()
    checar("Exame na Praia foi criado", exame_praia is not None)
    checar("São registros distintos (não é o mesmo exame duplicado)", exame_centro.id != exame_praia.id)
    checar("Duração copiada da filial de origem", exame_praia.duracao_minutos == 30)
    checar("Preço é o informado na associação (não o copiado do Centro)", float(exame_praia.preco) == 180.0)
    checar("Médico responsável na Praia é o escolhido no formulário", exame_praia.medico_id == medico_id)
    checar("Modelo de preparo NÃO foi copiado (é específico de cada filial)", exame_praia.preparo_modelo_id is None)

# Tentar associar de novo (já existe) deve ser bloqueado com aviso, sem duplicar.
r3 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "clinica_destino_id": str(praia_id), "medico_id": str(medico_id),
    "preco": "180,00",
}, follow_redirects=True)
checar("Segunda tentativa de associar mostra aviso de duplicidade", "já está associado" in r3.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou um segundo exame duplicado na Praia",
        Exame.query.filter_by(clinica_id=praia_id, nome="Ultrassom Abdominal").count() == 1,
    )

# A matriz mostra o preço de cada filial.
r4 = client.get("/equipe/exames/por-filial")
html4 = r4.get_data(as_text=True)
checar("Matriz mostra o preço do Centro", "150,00" in html4)
checar("Matriz mostra o preço da Praia", "180,00" in html4)

# Ajusta o preço da Praia direto pela tela de matriz (não mais pelo cadastro do exame).
r5 = client.post(f"/equipe/exames/por-filial/{exame_praia.id}/atualizar", data={
    "medico_id": str(medico_id), "preco": "200,00",
}, follow_redirects=True)
checar("Atualizar preço responde 200", r5.status_code == 200)
with app.app_context():
    checar("Preço da Praia foi atualizado", float(Exame.query.get(exame_praia.id).preco) == 200.0)

# O formulário de EDITAR o exame não pede mais preço (nem aceita alterá-lo) — troca a
# clínica ativa para a Praia primeiro, já que exames_editar exige que o exame seja da
# clínica atualmente selecionada.
client.post("/equipe/clinica", data={"clinica_id": str(praia_id)}, follow_redirects=True)
r6 = client.post(f"/equipe/exames/{exame_praia.id}/editar", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30",
    "preco": "999,00",  # mesmo enviando, não deve ser aplicado
}, follow_redirects=True)
checar("Editar exame responde 200", r6.status_code == 200)
with app.app_context():
    checar("Editar o exame NÃO altera o preço (só a tela de matriz altera)", float(Exame.query.get(exame_praia.id).preco) == 200.0)

client.get("/logout")
print("\nTodos os testes de exames por filial passaram.")
