"""Testa a nova tela "Exames por filial" (medico.exames_por_filial), que
permite associar um exame já cadastrado numa filial a outra filial da mesma
empresa, escolhendo o médico responsável lá - antes só era possível
cadastrando o exame do zero de novo em cada filial (ou mexendo direto no
banco). Usa o seed's Grupo Saúde Total (filiais Centro e Praia)."""
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
    centro_id, praia_id, medico_id, medico_nome = centro.id, praia.id, medico_grupo.id, medico_grupo.nome
    # Modelo de preparo é obrigatório no cadastro do exame - o Grupo Saúde
    # Total não tem nenhum no seed, então criamos um aqui.
    modelo = PreparoModelo(clinica_id=centro_id, nome="Preparo Grupo Saúde", instrucoes="Jejum de 8 horas.")
    db.session.add(modelo)
    db.session.commit()
    modelo_id = modelo.id

login("secretaria@gruposaude.com", "123456")

# Cadastro do exame é genérico (sem filial, sem médico, sem preço) - ele
# nasce em algum local técnico (o primeiro acessível), e o médico/preço
# desse local são definidos logo em seguida, na própria tela "Exames por
# filial" (mesmo fluxo usado para os OUTROS locais).
r = client.post("/equipe/exames/novo", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Cadastro genérico do exame responde 200", r.status_code == 200)

with app.app_context():
    exame_origem = Exame.query.filter_by(nome="Ultrassom Abdominal").first()
    origem_id = exame_origem.clinica_id
    destino_id = praia_id if origem_id == centro_id else centro_id
    nome_origem = "Centro" if origem_id == centro_id else "Praia"
    nome_destino = "Praia" if origem_id == centro_id else "Centro"

# Define médico responsável e preço do local de origem pela tela de matriz
# (mesmo mecanismo usado para atualizar qualquer local já associado).
with app.app_context():
    exame_origem_id = Exame.query.filter_by(nome="Ultrassom Abdominal").first().id
r0 = client.post(f"/equipe/exames/por-filial/{exame_origem_id}/atualizar", data={
    "medico_id": str(medico_id), "preco": "150,00",
}, follow_redirects=True)
checar("Definir médico/preço do local de origem responde 200", r0.status_code == 200)

# A tela "Exames por filial" mostra a matriz: origem preenchida, destino com botão de associar.
r = client.get("/equipe/exames/por-filial?tipo=filial")  # a tela abre sem seleção; o teste pede a matriz explicitamente
html = r.get_data(as_text=True)
checar("Tela responde 200", r.status_code == 200)
checar("Mostra o nome do exame na matriz", "Ultrassom Abdominal" in html)
checar(f"Mostra o médico responsável na filial {nome_origem}", medico_nome in html)
checar(f"Mostra o botão de associar para a filial {nome_destino} (ainda não tem)", "+ Associar" in html)

# Tentar associar sem informar preço é bloqueado (testado ANTES da associação bem-sucedida,
# senão o exame já existiria no destino e o bloqueio seria por duplicidade, não por falta de preço).
r2b = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "clinica_destino_id": str(destino_id), "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Associar sem preço é bloqueado com aviso", "Informe o preço" in r2b.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou o exame no destino sem o preço",
        Exame.query.filter_by(clinica_id=destino_id, nome="Ultrassom Abdominal").first() is None,
    )

# Associa o exame também ao local de destino, com o mesmo médico (que atende as duas) e um
# preço DIFERENTE do de origem — o preço é informado na hora da associação, não copiado.
r2 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "clinica_destino_id": str(destino_id), "medico_id": str(medico_id),
    "preco": "180,00",
}, follow_redirects=True)
checar("Associar ao local de destino responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "associado à filial" in r2.get_data(as_text=True))

with app.app_context():
    exame_origem = Exame.query.filter_by(clinica_id=origem_id, nome="Ultrassom Abdominal").first()
    exame_destino = Exame.query.filter_by(clinica_id=destino_id, nome="Ultrassom Abdominal").first()
    checar("Exame no destino foi criado", exame_destino is not None)
    checar("São registros distintos (não é o mesmo exame duplicado)", exame_origem.id != exame_destino.id)
    checar("Duração copiada da filial de origem", exame_destino.duracao_minutos == 30)
    checar("Preço é o informado na associação (não o copiado da origem)", float(exame_destino.preco) == 180.0)
    checar("Médico responsável no destino é o escolhido no formulário", exame_destino.medico_id == medico_id)
    checar("Modelo de preparo NÃO foi copiado (é específico de cada filial)", exame_destino.preparo_modelo_id is None)

# Tentar associar de novo (já existe) deve ser bloqueado com aviso, sem duplicar.
r3 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "clinica_destino_id": str(destino_id), "medico_id": str(medico_id),
    "preco": "180,00",
}, follow_redirects=True)
checar("Segunda tentativa de associar mostra aviso de duplicidade", "já está associado" in r3.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou um segundo exame duplicado no destino",
        Exame.query.filter_by(clinica_id=destino_id, nome="Ultrassom Abdominal").count() == 1,
    )

# A matriz mostra o preço de cada filial.
r4 = client.get("/equipe/exames/por-filial?tipo=filial")  # a tela abre sem seleção; o teste pede a matriz explicitamente
html4 = r4.get_data(as_text=True)
checar("Matriz mostra o preço da origem", "150,00" in html4)
checar("Matriz mostra o preço do destino", "180,00" in html4)

# Ajusta o preço do destino direto pela tela de matriz (não mais pelo cadastro do exame).
r5 = client.post(f"/equipe/exames/por-filial/{exame_destino.id}/atualizar", data={
    "medico_id": str(medico_id), "preco": "200,00",
}, follow_redirects=True)
checar("Atualizar preço responde 200", r5.status_code == 200)
with app.app_context():
    checar("Preço do destino foi atualizado", float(Exame.query.get(exame_destino.id).preco) == 200.0)

# O formulário de EDITAR o exame não pede mais preço (nem aceita alterá-lo). Não é
# mais preciso "trocar de filial" antes: exames_editar aceita qualquer exame das
# filiais em que a pessoa atua.
r6 = client.post(f"/equipe/exames/{exame_destino.id}/editar", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30",
    "preco": "999,00",  # mesmo enviando, não deve ser aplicado
}, follow_redirects=True)
checar("Editar exame responde 200", r6.status_code == 200)
with app.app_context():
    checar("Editar o exame NÃO altera o preço (só a tela de matriz altera)", float(Exame.query.get(exame_destino.id).preco) == 200.0)

client.get("/logout")
print("\nTodos os testes de exames por filial passaram.")
