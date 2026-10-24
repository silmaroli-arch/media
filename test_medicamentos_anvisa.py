"""Importação manual da lista de medicamentos da ANVISA (pedido do Silvan,
2026-10-23): CSV de dados abertos (ponto e vírgula, Latin-1) -> nome
comercial e princípio ativo, com busca para testar."""
import io

from app import create_app, db
from app.medicamentos_anvisa import buscar, importar_csv
from app.models import MedicamentoAnvisa, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


CSV = (
    "TIPO_PRODUTO;NOME_PRODUTO;DATA_FINALIZACAO_PROCESSO;CATEGORIA_REGULATORIA;NUMERO_REGISTRO_PRODUTO;"
    "DATA_VENCIMENTO_REGISTRO;NUMERO_PROCESSO;CLASSE_TERAPEUTICA;EMPRESA_DETENTORA_REGISTRO;SITUACAO_REGISTRO;PRINCIPIO_ATIVO\n"
    "MEDICAMENTO;POVIZTRA;01/01/2025;NOVO;123;01/01/2035;1;ANTIDIABETICOS;EUROFARMA;VÁLIDO;SEMAGLUTIDA\n"
    "MEDICAMENTO;POVIZTRA;01/01/2025;NOVO;124;01/01/2035;2;ANTIDIABETICOS;EUROFARMA;VÁLIDO;SEMAGLUTIDA\n"
    "MEDICAMENTO;MAREVAN;01/01/2000;SIMILAR;125;01/01/2030;3;ANTICOAGULANTES;FARMOQUIMICA;VÁLIDO;VARFARINA SÓDICA\n"
    "MEDICAMENTO;;01/01/2000;SIMILAR;126;01/01/2030;4;X;Y;VÁLIDO;\n"
).encode("latin-1")

with app.app_context():
    quantidade, erro = importar_csv(CSV)
    checar("Importa o CSV em Latin-1, sem duplicar e sem linha vazia", erro is None and quantidade == 2)
    r = buscar("Poviztra")
    checar("Busca ignora maiúsculas: Poviztra = semaglutida", r and r[0].principio_ativo == "SEMAGLUTIDA")
    checar("Acento no princípio ativo preservado", buscar("marevan")[0].principio_ativo == "VARFARINA SÓDICA")
    quantidade, erro = importar_csv("NOME;OUTRA\nA;B\n".encode())
    checar("Arquivo sem as colunas: avisa e não apaga a lista atual", erro and MedicamentoAnvisa.query.count() == 2)
    dono_email = Usuario.query.filter_by(tipo="dono").first().email

d = app.test_client()
d.post("/login", data={"identificador": dono_email, "senha": "123456"})
html = d.get("/dono/").get_data(as_text=True)
checar("Menu do dono tem 'Medicamentos (ANVISA)'", "Medicamentos (ANVISA)" in html)
r = d.post("/dono/medicamentos-anvisa", data={"arquivo": (io.BytesIO(CSV.replace(b"MAREVAN", b"COUMADIN")), "medicamentos.csv")},
           content_type="multipart/form-data", follow_redirects=True)
checar("Tela importa o arquivo e substitui a lista", "Lista da ANVISA importada: 2" in r.get_data(as_text=True))
html = d.get("/dono/medicamentos-anvisa?nome=coumadin").get_data(as_text=True)
checar("Tela mostra o total e testa um nome", "2 medicamento(s)" in html and "VARFARINA SÓDICA" in html)
checar("... e o nome antigo saiu", "Nenhum medicamento" in d.get("/dono/medicamentos-anvisa?nome=marevan").get_data(as_text=True))
m = app.test_client()
m.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
checar("Só o dono acessa", m.get("/dono/medicamentos-anvisa").status_code in (302, 403))

print("\nTodos os testes da lista da ANVISA passaram.")
