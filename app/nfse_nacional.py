"""Emissão de NFS-e (nota fiscal de serviço eletrônica) pelo padrão
NFS-e Nacional (Ambiente de Dados Nacional / ADN — SEFIN Nacional), usando
o certificado digital e-CNPJ já cadastrado em "Dados da clínica" (ver
app/cripto_fiscal.py e a rota clinica_certificado_upload).

Fluxo desta versão:
  1. Monta o XML do DPS (Declaração de Prestação de Serviço) com os dados
     da clínica (prestador), do paciente (tomador) e do exame (serviço).
  2. Assina o DPS com o certificado da clínica — assinatura XML-DSig REAL
     (enveloped, RSA-SHA256, via signxml), testada e verificada de ponta a
     ponta com um certificado de teste antes de entrar no código.
  3. Se "Modo simulação" estiver ligado na clínica, para por aqui.
  4. Se não estiver em simulação, envia o DPS assinado ao SEFIN Nacional /
     Ambiente de Dados Nacional via mTLS (o certificado da clínica é usado
     na própria conexão TLS, não só na assinatura do XML).

  IMPORTANTE — o que é confirmado pela documentação pública e o que é
  inferência de melhor esforço, pesquisado em agosto/2026 (não existe, até
  onde encontramos, um manual único e completo publicado com todos os
  campos — a documentação oficial em gov.br/nfse está fragmentada entre
  vários PDFs e um Swagger renderizado em JavaScript que não conseguimos
  ler programaticamente):

  CONFIRMADO (documentação oficial gov.br/nfse + manuais de prefeituras
  conveniadas):
    - Autenticação é mTLS: o certificado digital do emitente (e-CNPJ) é
      apresentado na própria conexão TLS, além de assinar o XML.
    - Endpoints REST do ADN "Contribuintes": POST /nfse (emite a NFS-e a
      partir da DPS), GET /nfse/{chaveAcesso}, GET /dps/{id}, POST
      /nfse/{chaveAcesso}/eventos (cancelamento etc.) — hosts abaixo.
    - Existe também o endpoint mais antigo do "SEFIN Nacional":
      POST {host}/SefinNacional/nfse.
    - O corpo do envio usa um campo "dpsXmlGZipB64": o XML do DPS
      assinado é compactado em gzip e depois codificado em base64 —
      citado por desenvolvedores que implementaram a integração
      (não está no PDF oficial resumido que conseguimos abrir, mas é
      consistente entre as fontes técnicas encontradas).
    - Erros voltam com campos "Codigo"/"Descricao" (ex.: E0714, E0004).

  INFERÊNCIA / NÃO CONFIRMADO — revisar contra o Swagger real
  (https://adn.producaorestrita.nfse.gov.br/contribuintes/docs/index.html)
  assim que houver um certificado e-CNPJ real credenciado para testar:
    - O nome exato do campo de retorno com o XML da NFS-e gerada (aqui
      assumimos "nfseXmlGZipB64", por simetria com o campo de envio).
    - Os nomes das tags XML de onde extraímos chave de acesso / número /
      código de verificação da NFS-e (infNFSe/@Id, nNFSe, cVerif).
    - Se o endpoint certo para o primeiro teste é o ADN "Contribuintes"
      (mais novo) ou o "SEFIN Nacional" (mais antigo, mas ainda citado
      como ativo pela documentação de 2025/2026) — por padrão usamos o
      SEFIN Nacional aqui por ser o mais documentado publicamente, mas os
      hosts do ADN Contribuintes ficam disponíveis em
      NFSE_NACIONAL_ENDPOINTS_ADN_CONTRIBUINTES para trocar facilmente.

  Por isso, se o envio falhar ou a resposta não tiver o formato esperado,
  a nota fica com status "assinada_pendente_envio" — nunca finge sucesso —
  e o XML assinado + a resposta crua do servidor ficam salvos para
  conferência manual e ajuste fino deste código no primeiro teste real.
"""
import base64
import gzip
import re
import tempfile
from datetime import datetime

from lxml import etree
from signxml import XMLSigner, methods
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from app.cripto_fiscal import descriptografar_bytes, descriptografar_texto

NS_DPS = "http://www.sped.fazenda.gov.br/nfse"

# Endpoint "SEFIN Nacional" — mais antigo, mas com documentação pública
# mais completa. Usado por padrão nesta primeira versão do envio real.
NFSE_NACIONAL_ENDPOINTS = {
    "homologacao": "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse",
    "producao": "https://sefin.nfse.gov.br/SefinNacional/nfse",
}

# Endpoint mais novo, "ADN Contribuintes" (REST). Mantido aqui documentado
# para facilitar a troca caso o SEFIN Nacional acima esteja descontinuado
# — ver aviso no topo do arquivo sobre qual usar no primeiro teste real.
NFSE_NACIONAL_ENDPOINTS_ADN_CONTRIBUINTES = {
    "homologacao": "https://adn.producaorestrita.nfse.gov.br/contribuintes/nfse",
    "producao": "https://adn.nfse.gov.br/contribuintes/nfse",
}


class ErroEmissaoNfse(Exception):
    """Erro esperado (dados faltando, certificado inválido etc.) — sempre
    tratado com mensagem amigável na tela, nunca chega cru ao usuário."""


def _sub(pai, tag, texto):
    el = etree.SubElement(pai, tag)
    el.text = "" if texto is None else str(texto)
    return el


def montar_dps_xml(clinica, paciente, agendamento, pagamento, numero_dps):
    """Monta o XML do DPS. Estrutura baseada no leiaute da NFS-e Nacional
    (grupo infDPS: prestador, tomador, serviço e valores) — ver aviso no
    topo do arquivo sobre validação contra o XSD oficial antes de emitir
    de verdade."""
    ambiente_codigo = "1" if clinica.fiscal_ambiente == "producao" else "2"
    agora = datetime.now().astimezone()
    cnpj_limpo = re.sub(r"\D", "", clinica.cnpj or "")
    cpf_limpo = re.sub(r"\D", "", paciente.cpf or "")
    serie = clinica.fiscal_rps_serie or "1"

    id_dps = f"DPS{cnpj_limpo}{serie}{int(numero_dps):015d}"

    dps = etree.Element("DPS", nsmap={None: NS_DPS})
    infDPS = etree.SubElement(dps, "infDPS", Id=id_dps)

    _sub(infDPS, "tpAmb", ambiente_codigo)
    _sub(infDPS, "dhEmi", agora.strftime("%Y-%m-%dT%H:%M:%S%z"))
    _sub(infDPS, "verAplic", "media-1.0")
    _sub(infDPS, "serie", serie)
    _sub(infDPS, "nDPS", numero_dps)
    _sub(infDPS, "dCompet", agora.strftime("%Y-%m-%d"))
    _sub(infDPS, "cLocEmi", clinica.codigo_ibge_municipio or "")

    prest = etree.SubElement(infDPS, "prest")
    _sub(prest, "CNPJ", cnpj_limpo)
    _sub(prest, "IM", clinica.fiscal_inscricao_municipal or "")
    _sub(prest, "xNome", clinica.razao_social or clinica.nome)

    toma = etree.SubElement(infDPS, "toma")
    _sub(toma, "CPF", cpf_limpo)
    _sub(toma, "xNome", paciente.nome)

    serv = etree.SubElement(infDPS, "serv")
    cServ = etree.SubElement(serv, "cServ")
    _sub(cServ, "cTribNac", clinica.fiscal_codigo_servico or "")
    _sub(cServ, "xDescServ", f"Exame/consulta: {agendamento.exame.nome}")

    valores = etree.SubElement(infDPS, "valores")
    vServPrest = etree.SubElement(valores, "vServPrest")
    _sub(vServPrest, "vServ", f"{pagamento.valor_final:.2f}")

    trib = etree.SubElement(valores, "trib")
    tribMun = etree.SubElement(trib, "tribMun")
    _sub(tribMun, "tribISSQN", "1")  # 1 = operação tributável no município
    if clinica.fiscal_aliquota_iss is not None:
        _sub(tribMun, "pAliq", f"{clinica.fiscal_aliquota_iss:.2f}")

    return dps, id_dps


def _carregar_certificado(clinica, senha_certificado=None):
    """Lê o .pfx salvo (criptografado no banco) e devolve a chave privada
    e o certificado já decodificados (objetos da lib `cryptography`),
    junto com a senha usada — reaproveitado tanto para assinar o XML
    quanto para autenticar a conexão mTLS ao enviar."""
    pfx_bytes = descriptografar_bytes(clinica.fiscal_certificado_pfx)
    if not pfx_bytes:
        raise ErroEmissaoNfse(
            "Certificado digital não configurado ou não pôde ser lido — "
            'envie o certificado em "Dados da clínica" antes de emitir.'
        )

    senha = senha_certificado or descriptografar_texto(clinica.fiscal_certificado_senha_cripto)
    if senha is None:
        raise ErroEmissaoNfse("Não foi possível recuperar a senha do certificado salvo.")

    try:
        chave, certificado, cadeia = pkcs12.load_key_and_certificates(pfx_bytes, senha.encode("utf-8"))
    except Exception as erro:
        raise ErroEmissaoNfse(f"Certificado salvo não pôde ser aberto: {erro}") from erro

    if chave is None or certificado is None:
        raise ErroEmissaoNfse("O certificado salvo não contém chave privada e certificado válidos.")

    return chave, certificado, cadeia


def assinar_dps(dps_element, id_dps, clinica, senha_certificado=None):
    """Assina o DPS com o certificado digital da clínica (XML-DSig
    enveloped, RSA-SHA256) e devolve o XML assinado como bytes."""
    chave, certificado, _cadeia = _carregar_certificado(clinica, senha_certificado=senha_certificado)

    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
    )
    try:
        assinado = signer.sign(dps_element, key=chave, cert=[certificado], reference_uri=id_dps)
    except Exception as erro:
        raise ErroEmissaoNfse(f"Falha ao assinar o DPS: {erro}") from erro

    return etree.tostring(assinado, xml_declaration=True, encoding="UTF-8")


def _extrair_texto(xml_bytes, *nomes_tag):
    """Procura pelo primeiro elemento cujo nome local bate com um dos
    `nomes_tag` (ignorando namespace) e devolve o texto, ou None."""
    try:
        raiz = etree.fromstring(xml_bytes)
    except Exception:
        return None
    for el in raiz.iter():
        tag_local = etree.QName(el.tag).localname if "}" in el.tag else el.tag
        if tag_local in nomes_tag:
            return el.text
    return None


def _enviar_ao_adn(xml_assinado_bytes, clinica, chave_privada, certificado, cadeia):
    """Envia o DPS assinado ao SEFIN Nacional / ADN via HTTPS com mTLS
    (o certificado da clínica autentica a própria conexão TLS, além de já
    ter assinado o XML). Devolve um dicionário cru com o resultado —
    quem chama decide o que fazer com status_code/corpo.

    O certificado precisa de arquivos em disco para o parâmetro `cert=`
    do `requests` (não aceita bytes em memória) — por isso usamos
    arquivos temporários que existem só durante a chamada e são apagados
    logo em seguida, mesmo se a requisição falhar.
    """
    import requests

    endpoint = NFSE_NACIONAL_ENDPOINTS.get(clinica.fiscal_ambiente, NFSE_NACIONAL_ENDPOINTS["homologacao"])

    corpo_gzip_b64 = base64.b64encode(gzip.compress(xml_assinado_bytes)).decode("ascii")
    payload = {"dpsXmlGZipB64": corpo_gzip_b64}

    cert_pem = certificado.public_bytes(serialization.Encoding.PEM)
    if cadeia:
        for c in cadeia:
            cert_pem += c.public_bytes(serialization.Encoding.PEM)
    chave_pem = chave_privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    with tempfile.NamedTemporaryFile(suffix=".pem") as arq_cert, \
         tempfile.NamedTemporaryFile(suffix=".pem") as arq_chave:
        arq_cert.write(cert_pem)
        arq_cert.flush()
        arq_chave.write(chave_pem)
        arq_chave.flush()

        resposta = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            cert=(arq_cert.name, arq_chave.name),
            timeout=30,
        )

    return resposta


def emitir_nfse(clinica, paciente, agendamento, pagamento, senha_certificado=None):
    """Orquestra a emissão: monta o DPS, assina (se não estiver em modo
    simulação) e envia ao SEFIN Nacional/ADN via mTLS. Devolve um
    dicionário com o resultado. Erros esperados viram ErroEmissaoNfse
    (mensagem amigável); a falha do envio em si NÃO levanta exceção —
    vira status "assinada_pendente_envio", pra não travar o fluxo do
    médico (ver aviso no topo do arquivo sobre o que ainda não está
    confirmado no formato exato da resposta do ADN)."""
    numero_dps = (clinica.fiscal_rps_proximo_numero or 0) + 1

    if clinica.fiscal_modo_simulacao:
        return {
            "status": "simulada",
            "numero_dps": numero_dps,
            "numero_nfse": f"SIM-{numero_dps:09d}",
            "codigo_verificacao": "SIMULACAO-SEM-VALOR-FISCAL",
            "xml_assinado": None,
            "erro": None,
        }

    if not clinica.fiscal_certificado_pfx:
        raise ErroEmissaoNfse(
            "Nenhum certificado digital configurado — envie o certificado "
            'e-CNPJ em "Dados da clínica" antes de emitir, ou ligue o modo '
            "simulação para apenas testar o fluxo."
        )

    if not clinica.fiscal_codigo_servico or not clinica.fiscal_inscricao_municipal:
        raise ErroEmissaoNfse(
            'Complete a inscrição municipal e o código do serviço em "Dados '
            'da clínica" antes de emitir uma NFS-e real.'
        )

    chave_privada, certificado, cadeia = _carregar_certificado(clinica, senha_certificado=senha_certificado)

    dps, id_dps = montar_dps_xml(clinica, paciente, agendamento, pagamento, numero_dps)
    xml_assinado = assinar_dps(dps, id_dps, clinica, senha_certificado=senha_certificado)

    # Envio ao SEFIN Nacional / Ambiente de Dados Nacional via mTLS — ver
    # aviso no topo do arquivo: o formato exato da resposta ainda não foi
    # confirmado contra o serviço real (falta certificado credenciado).
    try:
        resposta = _enviar_ao_adn(xml_assinado, clinica, chave_privada, certificado, cadeia)
    except Exception as erro:
        return {
            "status": "assinada_pendente_envio",
            "numero_dps": numero_dps,
            "numero_nfse": None,
            "codigo_verificacao": None,
            "xml_assinado": xml_assinado.decode("utf-8"),
            "erro": f"Não foi possível conectar ao Ambiente de Dados Nacional: {erro}",
        }

    if resposta.status_code not in (200, 201):
        erro_msg = f"Ambiente de Dados Nacional respondeu {resposta.status_code}: {resposta.text[:800]}"
        # Tenta extrair Codigo/Descricao do corpo de erro (formato citado
        # pela documentação técnica pública), sem depender disso existir.
        try:
            corpo_erro = resposta.json()
            lista_erros = corpo_erro if isinstance(corpo_erro, list) else [corpo_erro]
            partes = [
                f"{e.get('Codigo', '?')}: {e.get('Descricao', '')}"
                for e in lista_erros if isinstance(e, dict) and ("Codigo" in e or "Descricao" in e)
            ]
            if partes:
                erro_msg = "Ambiente de Dados Nacional rejeitou a DPS — " + "; ".join(partes)
        except Exception:
            pass
        return {
            "status": "assinada_pendente_envio",
            "numero_dps": numero_dps,
            "numero_nfse": None,
            "codigo_verificacao": None,
            "xml_assinado": xml_assinado.decode("utf-8"),
            "erro": erro_msg,
        }

    # Sucesso (2xx) — tenta decodificar a NFS-e retornada e extrair os
    # campos principais. Ver aviso no topo do arquivo: nomes de campo
    # ainda não confirmados contra uma resposta real.
    numero_nfse = None
    codigo_verificacao = None
    xml_nfse_texto = xml_assinado.decode("utf-8")
    try:
        corpo = resposta.json()
        nfse_gzip_b64 = corpo.get("nfseXmlGZipB64") if isinstance(corpo, dict) else None
        if nfse_gzip_b64:
            xml_nfse_bytes = gzip.decompress(base64.b64decode(nfse_gzip_b64))
            xml_nfse_texto = xml_nfse_bytes.decode("utf-8")
            numero_nfse = _extrair_texto(xml_nfse_bytes, "nNFSe")
            codigo_verificacao = _extrair_texto(xml_nfse_bytes, "cVerif")
        else:
            numero_nfse = corpo.get("chaveAcesso") if isinstance(corpo, dict) else None
    except Exception:
        # Resposta não veio no formato esperado — a nota foi aceita
        # (status 2xx) mas não conseguimos extrair os dados dela
        # automaticamente. Fica registrada como enviada, com o texto cru
        # da resposta guardado no campo de erro só para consulta manual.
        return {
            "status": "enviada",
            "numero_dps": numero_dps,
            "numero_nfse": None,
            "codigo_verificacao": None,
            "xml_assinado": xml_nfse_texto,
            "erro": f"Nota aceita (HTTP {resposta.status_code}), mas não foi possível interpretar "
                    f"automaticamente a resposta para extrair número/código de verificação. "
                    f"Resposta crua: {resposta.text[:800]}",
        }

    return {
        "status": "enviada",
        "numero_dps": numero_dps,
        "numero_nfse": numero_nfse,
        "codigo_verificacao": codigo_verificacao,
        "xml_assinado": xml_nfse_texto,
        "erro": None,
    }
