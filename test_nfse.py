"""Emissão de NFS-e da licença pelo MedIA (pedido do Silvan, 2026-10-27)."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app, db
from app.cripto_fiscal import criptografar_texto
from app.models import LicencaPagamento, PlataformaConfig, Usuario
from app import nfse

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


viacep = {"logradouro": "Rua das Flores", "bairro": "Centro", "cidade": "Vitória", "uf": "ES", "ibge": "3205309"}

checar("CNPJ válido e inválido", nfse.validar_cnpj("11.222.333/0001-81") and not nfse.validar_cnpj("11.222.333/0001-82")
       and not nfse.validar_cnpj("11111111111111"))

with app.app_context():
    dono_email = Usuario.query.filter_by(tipo="dono").first().email
    medico = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    medico_id = medico.id
    checar("Médico sem dados fiscais: faltam campos", "rua" in nfse.dados_faltando_tomador(medico))
    db.session.add(LicencaPagamento(usuario_id=medico_id, mes=date(2027, 4, 1), pago=True, valor=150, pago_em=datetime.utcnow()))
    db.session.commit()
    pag_id = LicencaPagamento.query.filter_by(usuario_id=medico_id, mes=date(2027, 4, 1)).first().id

# ---------- médico preenche os dados fiscais em Meus dados ----------
medico_c = app.test_client()
medico_c.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
medico_c.post("/meus-dados", data={"acao": "confirmar_senha", "senha_atual": "123456"})
with app.app_context():
    m = db.session.get(Usuario, medico_id)
    base = {"acao": "salvar", "nome": m.nome, "email": m.email, "cpf": "529.982.247-25", "telefone": "(27) 99999-0000",
            "data_nascimento": "01/01/1980", "especialidade": ""}
html = medico_c.get("/meus-dados").get_data(as_text=True)
checar("Meus dados mostra o bloco de dados fiscais", "Dados para a nota fiscal da licença" in html and 'name="fiscal_cep"' in html)
with patch("app.nfse.consultar_cep", return_value=viacep):
    r = medico_c.post("/meus-dados", data={**base, "fiscal_cep": "29010-120", "fiscal_numero": "100", "fiscal_complemento": "sala 2"}, follow_redirects=True)
with app.app_context():
    m = db.session.get(Usuario, medico_id)
    checar("CEP completa rua, bairro, cidade, UF e o código do município",
           (m.fiscal_logradouro, m.fiscal_bairro, m.fiscal_cidade, m.fiscal_uf, m.fiscal_ibge) == ("Rua das Flores", "Centro", "Vitória", "ES", "3205309")
           and m.fiscal_numero == "100" and nfse.dados_faltando_tomador(m) == [])
r = medico_c.post("/meus-dados", data={**base, "fiscal_cnpj": "11.222.333/0001-82"}, follow_redirects=True)
checar("CNPJ inválido é recusado", "CNPJ inválido" in r.get_data(as_text=True))

# ---------- dono configura o emitente ----------
dono = app.test_client()
dono.post("/login", data={"identificador": dono_email, "senha": "123456"})
html = dono.get("/dono/nota-fiscal").get_data(as_text=True)
checar("Tela 'Nota fiscal' do dono abre e está no menu", "Nota fiscal (NFS-e) da licença" in html and "nfse_cnpj" in html
       and "Nota fiscal" in dono.get("/dono/").get_data(as_text=True))
url = f"/dono/usuarios/{medico_id}/licenca/pagamentos/{pag_id}/nota"
r = dono.post(url, follow_redirects=True)
checar("Desligada: não emite", "não está ligada" in r.get_data(as_text=True))
dono.post("/dono/nota-fiscal", data={"nfse_provedor": "simulacao", "nfse_ambiente": "homologacao"})
r = dono.post(url, follow_redirects=True)
checar("Sem dados do emitente: avisa o que falta", "Faltam dados do emitente" in r.get_data(as_text=True))
emitente = {"nfse_provedor": "focusnfe", "nfse_ambiente": "homologacao", "nfse_token": "tok-secreto", "nfse_cnpj": "11.222.333/0001-81",
            "nfse_razao_social": "MedIA Ltda", "nfse_inscricao_municipal": "12345", "nfse_ibge_municipio": "3205309",
            "nfse_item_lista_servico": "0107", "nfse_aliquota_iss": "2,5", "nfse_optante_simples": "on",
            "nfse_codigo_tributario_municipio": "010701", "nfse_cnae": "6201-5/01"}
dono.post("/dono/nota-fiscal", data=emitente)
with app.app_context():
    c = PlataformaConfig.obter()
    checar("Emitente salvo; token criptografado e nunca volta na tela",
           c.nfse_cnpj == "11222333000181" and float(c.nfse_aliquota_iss) == 2.5 and c.nfse_token_cripto and b"tok-secreto" not in c.nfse_token_cripto)
checar("O token não aparece no HTML", "tok-secreto" not in dono.get("/dono/nota-fiscal").get_data(as_text=True))

# ---------- payload ----------
with app.app_context():
    c = PlataformaConfig.obter()
    p = db.session.get(LicencaPagamento, pag_id)
    corpo = nfse.montar_payload(p, c, datetime(2027, 4, 20, 10, 0, 0))
    checar("Payload: prestador, tomador (CPF + endereço) e serviço",
           corpo["prestador"] == {"cnpj": "11222333000181", "inscricao_municipal": "12345", "codigo_municipio": "3205309"}
           and corpo["tomador"]["cpf"] and corpo["tomador"]["endereco"]["codigo_municipio"] == "3205309"
           and corpo["servico"]["valor_servicos"] == 150.0 and corpo["servico"]["aliquota"] == 2.5
           and corpo["servico"]["item_lista_servico"] == "0107" and "04/2027" in corpo["servico"]["discriminacao"]
           and corpo["data_emissao"].endswith("-03:00") and corpo["optante_simples_nacional"] is True)

# ---------- emitir (Focus NFe simulado) ----------
chamadas = []
def post_ok(url, params=None, json=None, auth=None, timeout=None):
    chamadas.append(("POST", url, params, auth))
    return SimpleNamespace(status_code=202, json=lambda: {"status": "processando_autorizacao"})
def post_erro(url, params=None, json=None, auth=None, timeout=None):
    return SimpleNamespace(status_code=422, json=lambda: {"codigo": "x", "mensagem": "Código de serviço inválido"})
with patch("app.nfse.requests.post", post_erro):
    r = dono.post(url, follow_redirects=True)
with app.app_context():
    p = db.session.get(LicencaPagamento, pag_id)
    checar("Provedor recusa: erro guardado e mostrado", p.nf_status == "erro" and "Código de serviço inválido" in p.nf_erro
           and "recusou a nota" in r.get_data(as_text=True))
with patch("app.nfse.requests.post", post_ok):
    r = dono.post(url, follow_redirects=True)
with app.app_context():
    p = db.session.get(LicencaPagamento, pag_id)
    checar("Envio ok: fica 'processando', usando o ambiente de homologação e o token como usuário",
           p.nf_status == "processando" and p.nf_ref.startswith(f"licenca-{pag_id}-")
           and chamadas[-1][1] == "https://homologacao.focusnfe.com.br/v2/nfse" and chamadas[-1][3] == ("tok-secreto", ""))
r = dono.post(url, follow_redirects=True)
checar("Não emite duas vezes o mesmo mês", "já tem nota" in r.get_data(as_text=True))

def get_autorizado(url, auth=None, timeout=None):
    return SimpleNamespace(status_code=200, json=lambda: {"status": "autorizado", "numero": "77", "url": "https://prefeitura/nota/77",
                                                          "caminho_xml_nota_fiscal": "/arquivos/77.xml"})
with patch("app.nfse.requests.get", get_autorizado):
    dono.post(url + "/atualizar", follow_redirects=True)
with app.app_context():
    p = db.session.get(LicencaPagamento, pag_id)
    checar("Atualizar: nota autorizada com número e link", p.nf_status == "autorizada" and p.nf_numero == "77"
           and p.nf_url == "https://prefeitura/nota/77" and p.nf_xml_url.endswith("/arquivos/77.xml"))
html = dono.get(f"/dono/usuarios/{medico_id}/licenca/pagamentos").get_data(as_text=True)
checar("Calendário do dono mostra a nota", "Nota 77" in html and "ver nota" in html)
from app.models import Grupo
with app.app_context():
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first().id
medico_c.post("/equipe/clinica", data={"clinica_id": str(vitoria)}, follow_redirects=True)
html = medico_c.get("/equipe/minha-licenca").get_data(as_text=True)
checar("O médico vê e baixa a nota em Minha licença", "Nota fiscal 77" in html and "https://prefeitura/nota/77" in html)

# mês não pago não emite; simulação gera nota sem rede
with app.app_context():
    db.session.add(LicencaPagamento(usuario_id=medico_id, mes=date(2027, 5, 1), pago=False, valor=150))
    db.session.commit()
    outro = LicencaPagamento.query.filter_by(usuario_id=medico_id, mes=date(2027, 5, 1)).first().id
r = dono.post(f"/dono/usuarios/{medico_id}/licenca/pagamentos/{outro}/nota", follow_redirects=True)
checar("Mês não pago não emite nota", "já pago" in r.get_data(as_text=True))
dono.post("/dono/nota-fiscal", data={**emitente, "nfse_provedor": "simulacao", "nfse_token": ""})
with app.app_context():
    db.session.get(LicencaPagamento, outro).pago = True
    db.session.commit()
dono.post(f"/dono/usuarios/{medico_id}/licenca/pagamentos/{outro}/nota", follow_redirects=True)
with app.app_context():
    p = db.session.get(LicencaPagamento, outro)
    checar("Simulação: nota 'autorizada' de mentira, sem chamar a rede", p.nf_status == "autorizada" and p.nf_numero.startswith("SIM-"))
print("TUDO OK")

# ---------- certificado digital importado pelo dono ----------
import io
from datetime import timedelta
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


def gerar_pfx(senha, dias=365):
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MEDIA LTDA:11222333000181")])
    agora = datetime.utcnow()
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - timedelta(days=1))
            .not_valid_after(agora + timedelta(days=dias)).sign(chave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"cert", chave, cert, None, serialization.BestAvailableEncryption(senha.encode()))


pfx = gerar_pfx("segredo123")
html = dono.get("/dono/nota-fiscal").get_data(as_text=True)
checar("Tela mostra a área do certificado digital", "Certificado digital (A1 / e-CNPJ)" in html and "Importar certificado" in html)
r = dono.post("/dono/nota-fiscal/certificado", data={"certificado_arquivo": (io.BytesIO(pfx), "empresa.pfx"), "certificado_senha": "errada"}, follow_redirects=True)
checar("Senha errada: recusa e não guarda", "senha está correta" in r.get_data(as_text=True))
with app.app_context():
    checar("... nada gravado", PlataformaConfig.obter().nfse_cert_pfx is None)
r = dono.post("/dono/nota-fiscal/certificado", data={"certificado_arquivo": (io.BytesIO(b"lixo"), "x.pfx"), "certificado_senha": "x"}, follow_redirects=True)
checar("Arquivo que não é certificado é recusado", "Não foi possível abrir o certificado" in r.get_data(as_text=True))
r = dono.post("/dono/nota-fiscal/certificado", data={"certificado_arquivo": (io.BytesIO(pfx), "empresa.pfx"), "certificado_senha": "segredo123"}, follow_redirects=True)
with app.app_context():
    c = PlataformaConfig.obter()
    checar("Certificado válido: CNPJ, titular e validade lidos; arquivo e senha criptografados",
           c.nfse_cert_cnpj == "11222333000181" and "MEDIA LTDA" in c.nfse_cert_titular and c.nfse_cert_validade > date.today()
           and c.nfse_cert_pfx != pfx and b"segredo123" not in c.nfse_cert_senha_cripto and "validado e salvo" in r.get_data(as_text=True))
    from app.cripto_fiscal import descriptografar_bytes, descriptografar_texto
    checar("... e dá para recuperar o arquivo e a senha para usar na emissão",
           descriptografar_bytes(c.nfse_cert_pfx) == pfx and descriptografar_texto(c.nfse_cert_senha_cripto) == "segredo123")
    checar("Situação 'ok'", nfse.certificado_situacao(c) == "ok" and nfse.certificado_situacao(c, hoje=c.nfse_cert_validade - timedelta(days=5)) == "vencendo"
           and nfse.certificado_situacao(c, hoje=c.nfse_cert_validade + timedelta(days=1)) == "vencido")
html = dono.get("/dono/nota-fiscal").get_data(as_text=True)
checar("Tela mostra titular, CNPJ e validade (sem expor senha)", "MEDIA LTDA" in html and "11222333000181" in html and "segredo123" not in html)
dono.post("/dono/nota-fiscal/certificado", data={"acao": "remover"})
with app.app_context():
    checar("Remover apaga o certificado", PlataformaConfig.obter().nfse_cert_pfx is None and PlataformaConfig.obter().nfse_cert_validade is None)
print("CERTIFICADO OK")
