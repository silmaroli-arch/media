"""Testa a correção do bug relatado: exames cadastrados pelo formulário
genérico (medico.exames_novo) mostravam, na tela "Exames por filial", o
médico técnico/provisório (quem cadastrou, ou o primeiro médico da
empresa) como se já fosse o responsável CONFIRMADO - com o mesmo selo
verde de "associação já feita" usado para associações de verdade. O
usuário reportou: "Cadastrei 3 exames e o médico que cadastrou na
plataforma ainda é dado como se fizesse o exame".

Agora um exame recém-criado pelo cadastro genérico nasce com
medico_confirmado=False, e a tela mostra um aviso amarelo em vez do selo
verde, deixando claro que é só um valor provisório - até alguém escolher
(ou confirmar) o médico de propósito em "Exames por filial", que marca
medico_confirmado=True.

Usa a Clínica Vitória (uma filial só - a grade "Exame × Médico" é a única
disponível/relevante nesse caso, exatamente o cenário do usuário) para o
aviso na grade de médico, e o Grupo Saúde Total (duas filiais) pra também
confirmar o mesmo aviso (em vez do selo verde) na grade "Exame × Filial"."""
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


# ---------- Lista de associações (Clínica Vitória, uma filial só) ----------

login("secretaria@clinicavitoria.com", "123456")

with app.app_context():
    clinica_vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
    modelo_id = PreparoModelo.query.filter_by(clinica_id=clinica_vitoria_id).first().id
    dr_carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    dr_carlos_id = dr_carlos.id
    dr_carlos_nome = dr_carlos.nome

r = client.post("/equipe/exames/novo", data={
    "nome": "Endoscopia Digestiva Alta", "descricao": "Endoscopia", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Cadastro genérico do exame responde 200", r.status_code == 200)

with app.app_context():
    exame = Exame.query.filter_by(nome="Endoscopia Digestiva Alta").first()
    exame_id = exame.id
    checar("Exame nasce com medico_confirmado=False (valor só provisório)", exame.medico_confirmado is False)

r2 = client.get("/equipe/exames/por-filial")
html2 = r2.get_data(as_text=True)
checar("Mostra o exame recém-criado", "Endoscopia Digestiva Alta" in html2)
# A tela virou um cadastro básico (lista) - o aviso aparece na linha do exame.
checar("A linha do exame mostra aviso de não confirmado (não trata como já resolvido)",
       "não confirmado" in html2.rsplit("Endoscopia Digestiva Alta", 1)[1].split("</tr>")[0])

# Confirmando o médico responsável em "Exames por filial" marca medico_confirmado=True.
r3 = client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "atualizar_extras": "1", "medico_id": str(dr_carlos_id),
}, follow_redirects=True)
checar("Confirmar médico responde 200", r3.status_code == 200)
with app.app_context():
    exame_confirmado = Exame.query.get(exame_id)
    checar("medico_confirmado agora é True", exame_confirmado.medico_confirmado is True)

r4 = client.get("/equipe/exames/por-filial")
html4 = r4.get_data(as_text=True)
checar(
    "Depois de confirmado, NÃO mostra mais o aviso pra este exame",
    "não confirmado" not in html4.rsplit("Endoscopia Digestiva Alta", 1)[1].split("</tr>")[0],
)

client.get("/logout")

# ---------- Empresa com duas filiais (Grupo Saúde Total) ----------

login("secretaria@gruposaude.com", "123456")
with app.app_context():
    centro_id = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first().id
    modelo_grupo = PreparoModelo(clinica_id=centro_id, nome="Preparo Não Confirmado", instrucoes="Jejum de 8 horas.")
    db.session.add(modelo_grupo)
    db.session.commit()
    modelo_grupo_id = modelo_grupo.id

r5 = client.post("/equipe/exames/novo", data={
    "nome": "Exame Nao Confirmado Teste", "descricao": "Exame", "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo_grupo_id),
}, follow_redirects=True)
checar("Cadastro genérico no Grupo Saúde Total responde 200", r5.status_code == 200)

r6 = client.get("/equipe/exames/por-filial")
html6 = r6.get_data(as_text=True)
checar("Mostra o exame na lista", "Exame Nao Confirmado Teste" in html6)
checar(
    "A linha mostra o AVISO de não confirmado pra um exame recém-criado",
    "não confirmado" in html6.rsplit("Exame Nao Confirmado Teste", 1)[1].split("</tr>")[0],
)

client.get("/logout")
print("\nTodos os testes de médico não confirmado em exame recém-criado passaram.")
