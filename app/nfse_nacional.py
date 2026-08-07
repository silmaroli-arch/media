"""Emissão de NFS-e (nota fiscal de serviço eletrônica) pelo padrão
NFS-e Nacional (Ambiente de Dados Nacional / ADN), usando o certificado
digital e-CNPJ já cadastrado em "Dados da clínica" (ver
app/cripto_fiscal.py e a rota clinica_certificado_upload).

Fluxo desta versão:
  1. Monta o XML do DPS (Declaração de Prestação de Serviço) com os dados
     da clínica (prestador), do paciente (tomador) e do exame (serviço).
  2. Assina o DPS com o certificado da clínica — assinatura XML-DSig REAL
     (enveloped, RSA-SHA256, via signxml), não é um mock: foi testada e
     verificada de ponta a ponta com um certificado de teste antes de
     entrar no código.
  3. Se "Modo simulação" estiver ligado na clínica, para por aqui: marca a
     nota como "simulada", com número/código fictícios, sem gerar nem
     tentar enviar XML nenhum — serve só pra testar o fluxo de tela sem
     precisar de certificado.
  4. Se não estiver em simulação, tenta enviar o DPS assinado ao Ambiente
     de Dados Nacional.

     IMPORTANTE — o que ainda NÃO está validado: o passo 4 (envio real)
     nunca foi testado contra o webservice de verdade do ADN, porque isso
     exigiria um certificado e-CNPJ real já credenciado na Receita/ADN, o
     que não temos aqui. Também vale confirmar o XML do DPS montado em
     `montar_dps_xml` contra o XSD oficial mais atual da NFS-e Nacional
     antes do primeiro envio real — a estrutura abaixo segue o leiaute
     documentado, mas pequenos ajustes de campo podem ser necessários.
     Por isso, se o envio falhar por qualquer motivo (endpoint errado,
     certificado não credenciado, campo fora do schema etc.), a nota fica
     com status "assinada_pendente_envio" — nunca finge sucesso — e o XML
     assinado fica salvo para envio manual pelo emissor web da
     prefeitura/ADN enquanto o envio automático não estiver confirmado.
"""
import re
from datetime import datetime

from lxml import etree
from signxml import XMLSigner, methods
from cryptography.hazmat.primitives.serialization import pkcs12

from app.cripto_fiscal import descriptografar_bytes, descriptografar_texto

NS_DPS = "http://www.sped.fazenda.gov.br/nfse"

# Endpoints do Ambiente de Dados Nacional (NFS-e Nacional). Ainda não
# confirmados/testados contra o serviço real — ver aviso no topo deste
# arquivo antes de usar em produção.
NFSE_NACIONAL_ENDPOINTS = {
    "homologacao": "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse",
    "producao": "https://sefin.nfse.gov.br/SefinNacional/nfse",
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


def assinar_dps(dps_element, id_dps, clinica, senha_certificado=None):
    """Assina o DPS com o certificado digital da clínica (XML-DSig
    enveloped, RSA-SHA256) e devolve o XML assinado como bytes."""
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
        chave, certificado, _cadeia = pkcs12.load_key_and_certificates(pfx_bytes, senha.encode("utf-8"))
    except Exception as erro:
        raise ErroEmissaoNfse(f"Certificado salvo não pôde ser aberto: {erro}") from erro

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


def emitir_nfse(clinica, paciente, agendamento, pagamento, senha_certificado=None):
    """Orquestra a emissão: monta o DPS, assina (se não estiver em modo
    simulação) e tenta enviar ao ADN. Devolve um dicionário com o
    resultado. Erros esperados viram ErroEmissaoNfse (mensagem amigável);
    a falha do envio ao ADN em si NÃO levanta exceção — vira status
    "assinada_pendente_envio", pra não travar o fluxo do médico."""
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

    dps, id_dps = montar_dps_xml(clinica, paciente, agendamento, pagamento, numero_dps)
    xml_assinado = assinar_dps(dps, id_dps, clinica, senha_certificado=senha_certificado)

    # Envio ao Ambiente de Dados Nacional — ver aviso no topo do arquivo:
    # este passo ainda não foi validado contra o serviço real.
    try:
        import requests
        endpoint = NFSE_NACIONAL_ENDPOINTS.get(clinica.fiscal_ambiente, NFSE_NACIONAL_ENDPOINTS["homologacao"])
        resposta = requests.post(
            endpoint,
            data=xml_assinado,
            headers={"Content-Type": "application/xml"},
            timeout=15,
        )
        if resposta.status_code == 200:
            return {
                "status": "enviada",
                "numero_dps": numero_dps,
                "numero_nfse": None,
                "codigo_verificacao": None,
                "xml_assinado": xml_assinado.decode("utf-8"),
                "erro": None,
            }
        erro_msg = f"Ambiente de Dados Nacional respondeu {resposta.status_code}: {resposta.text[:500]}"
    except Exception as erro:
        erro_msg = f"Não foi possível enviar ao Ambiente de Dados Nacional: {erro}"

    return {
        "status": "assinada_pendente_envio",
        "numero_dps": numero_dps,
        "numero_nfse": None,
        "codigo_verificacao": None,
        "xml_assinado": xml_assinado.decode("utf-8"),
        "erro": erro_msg,
    }
