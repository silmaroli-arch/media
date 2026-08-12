"""Testa a evolução do caso "Cadastrei 3 exames e o médico que cadastrou
na plataforma ainda é dado como se fizesse o exame":

1) Hoje, cadastrar um exame NÃO cria associação nenhuma (nasce só como
   item de catálogo, associado=False) - então o exame recém-cadastrado nem
   aparece na tela "Associar exames", e nenhum médico é mostrado como
   responsável por ele em lugar nenhum.

2) Para dados LEGADOS (associações criadas antes dessa mudança, quando o
   cadastro genérico preenchia um médico técnico/provisório), o aviso
   amarelo de "não confirmado" continua aparecendo na linha da associação
   até alguém escolher e salvar o médico pelo Editar - que é o que marca
   medico_confirmado=True."""
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


login("secretaria@clinicavitoria.com", "123456")

with app.app_context():
    clinica_vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
    modelo_id = PreparoModelo.query.filter_by(clinica_id=clinica_vitoria_id).first().id
    dr_carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    dr_carlos_id = dr_carlos.id

# ---------- (1) Cadastro genérico: nenhum médico "assumido" em lugar nenhum ----------

r = client.post("/equipe/exames/novo", data={
    "nome": "Endoscopia Digestiva Alta", "descricao": "Endoscopia", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Cadastro genérico do exame responde 200", r.status_code == 200)

with app.app_context():
    exame = Exame.query.filter_by(nome="Endoscopia Digestiva Alta").first()
    exame_id = exame.id
    checar("Exame nasce com medico_confirmado=False (valor só provisório)", exame.medico_confirmado is False)
    checar("Exame nasce como catálogo, SEM associação (associado=False)", exame.associado is False)

r2 = client.get("/equipe/exames/por-filial")
html2 = r2.get_data(as_text=True)
checar("O exame recém-cadastrado NÃO aparece como associação (nenhum médico é mostrado como responsável)",
       "<td>Endoscopia Digestiva Alta</td>" not in html2)
checar("Ele aparece só como opção pra associar no formulário Adicionar",
       'value="Endoscopia Digestiva Alta"' in html2)

# ---------- (2) Associação LEGADA não confirmada: aviso até confirmar ----------

with app.app_context():
    # Simula um dado legado: associação criada antes da separação
    # catálogo/associação, com o médico técnico/provisório.
    exame_legado = Exame.query.get(exame_id)
    exame_legado.associado = True  # legado: era associação desde o cadastro
    db.session.commit()

r3 = client.get("/equipe/exames/por-filial")
html3 = r3.get_data(as_text=True)
checar("Associação legada aparece na lista", "<td>Endoscopia Digestiva Alta</td>" in html3)
checar("A linha mostra o aviso de 'não confirmado' (não trata como já resolvido)",
       "não confirmado" in html3.rsplit("Endoscopia Digestiva Alta", 1)[1].split("</tr>")[0])

# Confirmando o médico pelo Editar marca medico_confirmado=True.
r4 = client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "medico_id": str(dr_carlos_id), "preco": "200,00",
}, follow_redirects=True)
checar("Confirmar médico responde 200", r4.status_code == 200)
with app.app_context():
    checar("medico_confirmado agora é True", Exame.query.get(exame_id).medico_confirmado is True)

r5 = client.get("/equipe/exames/por-filial")
html5 = r5.get_data(as_text=True)
checar(
    "Depois de confirmado, NÃO mostra mais o aviso pra este exame",
    "não confirmado" not in html5.rsplit("Endoscopia Digestiva Alta", 1)[1].split("</tr>")[0],
)

client.get("/logout")
print("\nTodos os testes de médico não confirmado passaram.")
