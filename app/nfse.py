"""Emissão de NFS-e da licença pelo MedIA (pedido do Silvan, 2026-10-27).

O EMITENTE é a empresa dona da plataforma (dados em PlataformaConfig.nfse_*,
tela do dono "Nota fiscal"); o TOMADOR é o médico (dados fiscais em
Usuario.fiscal_*, ou o CPF dele). A nota é de um mês JÁ PAGO
(LicencaPagamento.pago) e é emitida quando o dono clica em "Emitir nota".

Provedor: Focus NFe (API REST, https://focusnfe.com.br/doc/#nfse). A nota
sai de forma assíncrona: o POST devolve "processando_autorizacao" e a
prefeitura autoriza em seguida - `atualizar_status` consulta de novo. O
provedor "simulacao" gera uma nota de mentira para testar sem conta nem
valor fiscal. A montagem do JSON segue a documentação da Focus NFe e precisa
ser conferida em HOMOLOGAÇÃO com a conta real (cada prefeitura tem
particularidades de código de serviço e alíquota)."""
import logging
import re
from datetime import datetime

import requests

from app.cripto_fiscal import descriptografar_texto

logger = logging.getLogger(__name__)

URLS = {"homologacao": "https://homologacao.focusnfe.com.br", "producao": "https://api.focusnfe.com.br"}
FUSO_NF = "-03:00"
DISCRIMINACAO_PADRAO = "Licença de uso da plataforma MedIA (software) - competência {mes}"


class NfseErro(Exception):
    """Erro que pode ser mostrado ao dono (dados faltando, recusa do provedor...)."""


def so_digitos(valor):
    return re.sub(r"\D", "", valor or "")


def consultar_cep(cep):
    """Endereço do CEP pelo ViaCEP (inclui o código IBGE do município), ou
    None se não achar / sem rede."""
    cep = so_digitos(cep)
    if len(cep) != 8:
        return None
    try:
        r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=5)
        dados = r.json()
        if r.status_code != 200 or dados.get("erro"):
            return None
        return {"logradouro": dados.get("logradouro") or "", "bairro": dados.get("bairro") or "",
                "cidade": dados.get("localidade") or "", "uf": dados.get("uf") or "", "ibge": dados.get("ibge") or ""}
    except Exception:
        return None


def dados_faltando_tomador(usuario):
    """Rótulos dos dados fiscais do médico que ainda faltam."""
    faltando = []
    if so_digitos(usuario.fiscal_cnpj):
        if not (usuario.fiscal_razao_social or "").strip():
            faltando.append("razão social")
    elif not so_digitos(usuario.cpf):
        faltando.append("CPF ou CNPJ")
    for campo, rotulo in (("fiscal_cep", "CEP"), ("fiscal_logradouro", "rua"), ("fiscal_numero", "número"),
                          ("fiscal_bairro", "bairro"), ("fiscal_cidade", "cidade"), ("fiscal_uf", "UF"),
                          ("fiscal_ibge", "código do município (confira o CEP)")):
        if not (getattr(usuario, campo) or "").strip():
            faltando.append(rotulo)
    return faltando


def dados_faltando_emitente(config):
    faltando = []
    for campo, rotulo in (("nfse_cnpj", "CNPJ do emitente"), ("nfse_inscricao_municipal", "inscrição municipal"),
                          ("nfse_ibge_municipio", "código IBGE do município"),
                          ("nfse_item_lista_servico", "item da lista de serviços")):
        if not (getattr(config, campo) or "").strip():
            faltando.append(rotulo)
    if config.nfse_aliquota_iss is None:
        faltando.append("alíquota do ISS")
    if config.nfse_provedor == "focusnfe" and not config.nfse_token_cripto:
        faltando.append("token do Focus NFe")
    return faltando


def montar_payload(pagamento, config, agora=None):
    """JSON da NFS-e (formato Focus NFe) do mês `pagamento`."""
    usuario = pagamento.usuario
    agora = agora or datetime.utcnow()
    cnpj_tomador = so_digitos(usuario.fiscal_cnpj)
    tomador = {
        "razao_social": (usuario.fiscal_razao_social if cnpj_tomador else usuario.nome) or usuario.nome,
        "email": usuario.email,
        "endereco": {
            "logradouro": usuario.fiscal_logradouro, "numero": usuario.fiscal_numero,
            "complemento": usuario.fiscal_complemento or "", "bairro": usuario.fiscal_bairro,
            "codigo_municipio": usuario.fiscal_ibge, "uf": usuario.fiscal_uf, "cep": so_digitos(usuario.fiscal_cep),
        },
    }
    if cnpj_tomador:
        tomador["cnpj"] = cnpj_tomador
    else:
        tomador["cpf"] = so_digitos(usuario.cpf)
    mes = pagamento.mes.strftime("%m/%Y")
    servico = {
        "aliquota": float(config.nfse_aliquota_iss),
        "discriminacao": (config.nfse_discriminacao or DISCRIMINACAO_PADRAO).replace("{mes}", mes),
        "iss_retido": False,
        "item_lista_servico": config.nfse_item_lista_servico,
        "valor_servicos": float(pagamento.valor or 0),
    }
    if config.nfse_codigo_tributario_municipio:
        servico["codigo_tributario_municipio"] = config.nfse_codigo_tributario_municipio
    if config.nfse_cnae:
        servico["codigo_cnae"] = so_digitos(config.nfse_cnae)
    return {
        "data_emissao": agora.strftime("%Y-%m-%dT%H:%M:%S") + FUSO_NF,
        "natureza_operacao": "1",
        "optante_simples_nacional": bool(config.nfse_optante_simples),
        "prestador": {"cnpj": so_digitos(config.nfse_cnpj), "inscricao_municipal": config.nfse_inscricao_municipal,
                      "codigo_municipio": config.nfse_ibge_municipio},
        "tomador": tomador,
        "servico": servico,
    }


def _base_e_auth(config):
    token = descriptografar_texto(config.nfse_token_cripto) if config.nfse_token_cripto else None
    if not token:
        raise NfseErro("Token do Focus NFe não configurado (Painel do dono > Nota fiscal).")
    return URLS.get(config.nfse_ambiente, URLS["homologacao"]), (token, "")


def _erro_do_provedor(resposta):
    """Texto legível dos erros que o provedor devolveu."""
    try:
        dados = resposta.json()
    except Exception:
        return f"resposta inesperada do provedor (HTTP {resposta.status_code})"
    partes = []
    if dados.get("mensagem"):
        partes.append(str(dados["mensagem"]))
    for erro in dados.get("erros") or []:
        texto = erro.get("mensagem") or ""
        if erro.get("correcao"):
            texto += f" ({erro['correcao']})"
        partes.append(texto)
    return "; ".join(p for p in partes if p) or f"HTTP {resposta.status_code}"


def emitir(pagamento, config, agora=None):
    """Envia a nota deste mês pago. Atualiza `pagamento.nf_*` (sem commit).
    Levanta NfseErro se não puder emitir."""
    agora = agora or datetime.utcnow()
    if config.nfse_provedor not in ("focusnfe", "simulacao"):
        raise NfseErro("A emissão de nota fiscal não está ligada (Painel do dono > Nota fiscal).")
    if not pagamento.pago:
        raise NfseErro("Só é possível emitir nota de um mês já pago.")
    if pagamento.nf_status in ("autorizada", "processando"):
        raise NfseErro("Este mês já tem nota emitida ou em processamento.")
    if not pagamento.valor or float(pagamento.valor) <= 0:
        raise NfseErro("O mês não tem valor para constar na nota.")
    faltando = dados_faltando_emitente(config)
    if faltando:
        raise NfseErro("Faltam dados do emitente: " + ", ".join(faltando) + ".")
    faltando = dados_faltando_tomador(pagamento.usuario)
    if faltando:
        raise NfseErro(f"Faltam dados fiscais de {pagamento.usuario.nome}: " + ", ".join(faltando) + ".")

    pagamento.nf_ref = f"licenca-{pagamento.id}-{agora.strftime('%Y%m%d%H%M%S')}"
    pagamento.nf_erro = None
    pagamento.nf_emitida_em = agora
    if config.nfse_provedor == "simulacao":
        pagamento.nf_status = "autorizada"
        pagamento.nf_numero = f"SIM-{pagamento.id}"
        pagamento.nf_url = None
        return

    base, auth = _base_e_auth(config)
    payload = montar_payload(pagamento, config, agora)
    try:
        resposta = requests.post(f"{base}/v2/nfse", params={"ref": pagamento.nf_ref}, json=payload, auth=auth, timeout=30)
    except requests.RequestException as erro:
        raise NfseErro(f"Não foi possível falar com o Focus NFe: {erro}")
    if resposta.status_code not in (200, 201, 202):
        pagamento.nf_status = "erro"
        pagamento.nf_erro = _erro_do_provedor(resposta)
        raise NfseErro("O Focus NFe recusou a nota: " + pagamento.nf_erro)
    pagamento.nf_status = "processando"


def atualizar_status(pagamento, config):
    """Consulta a nota em processamento (ou com erro) no provedor e grava o
    resultado. Sem commit."""
    if config.nfse_provedor != "focusnfe" or not pagamento.nf_ref:
        return
    base, auth = _base_e_auth(config)
    try:
        resposta = requests.get(f"{base}/v2/nfse/{pagamento.nf_ref}", auth=auth, timeout=30)
        dados = resposta.json()
    except Exception as erro:
        raise NfseErro(f"Não foi possível consultar o Focus NFe: {erro}")
    situacao = dados.get("status")
    if situacao == "autorizado":
        pagamento.nf_status = "autorizada"
        pagamento.nf_numero = str(dados.get("numero") or "") or None
        pagamento.nf_url = dados.get("url_danfse") or dados.get("url") or None
        caminho_xml = dados.get("caminho_xml_nota_fiscal")
        pagamento.nf_xml_url = f"{base}{caminho_xml}" if caminho_xml else None
        pagamento.nf_erro = None
    elif situacao in ("erro_autorizacao", "nao_autorizado"):
        pagamento.nf_status = "erro"
        pagamento.nf_erro = _erro_do_provedor(resposta)
    elif situacao == "cancelado":
        pagamento.nf_status = "cancelada"
    # processando_autorizacao: continua "processando"


def validar_cnpj(cnpj):
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d[i]) * pesos[i] for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if digito != int(d[tamanho]):
            return False
    return True


def aplicar_dados_fiscais(usuario, form):
    """Grava no médico os dados fiscais enviados pelo formulário (campos
    `fiscal_*`). Devolve a mensagem de erro (nada é gravado) ou None. Os
    dados são opcionais na tela - só a emissão da nota exige que estejam
    completos. Preenche o que faltar do endereço (e o código IBGE) pelo CEP."""
    def campo(nome, limite):
        return (form.get(nome) or "").strip()[:limite] or None

    cnpj = so_digitos(form.get("fiscal_cnpj"))
    if cnpj and not validar_cnpj(cnpj):
        return "CNPJ inválido - confira os números digitados."
    cep = so_digitos(form.get("fiscal_cep"))
    if cep and len(cep) != 8:
        return "CEP inválido - digite os 8 números."
    uf = (form.get("fiscal_uf") or "").strip().upper()[:2] or None

    novo = {
        "fiscal_cnpj": cnpj or None, "fiscal_razao_social": campo("fiscal_razao_social", 200),
        "fiscal_cep": cep or None, "fiscal_logradouro": campo("fiscal_logradouro", 200),
        "fiscal_numero": campo("fiscal_numero", 20), "fiscal_complemento": campo("fiscal_complemento", 100),
        "fiscal_bairro": campo("fiscal_bairro", 100), "fiscal_cidade": campo("fiscal_cidade", 100), "fiscal_uf": uf,
    }
    if cep:
        # O código do município só vem do CEP: refaz a consulta quando o CEP
        # mudou ou ainda não há código, e completa o endereço que ficou em branco.
        if cep != so_digitos(usuario.fiscal_cep) or not usuario.fiscal_ibge:
            achou = consultar_cep(cep)
            novo["fiscal_ibge"] = (achou or {}).get("ibge") or None
            if achou:
                novo["fiscal_logradouro"] = novo["fiscal_logradouro"] or achou["logradouro"] or None
                novo["fiscal_bairro"] = novo["fiscal_bairro"] or achou["bairro"] or None
                novo["fiscal_cidade"] = novo["fiscal_cidade"] or achou["cidade"] or None
                novo["fiscal_uf"] = novo["fiscal_uf"] or achou["uf"] or None
        else:
            novo["fiscal_ibge"] = usuario.fiscal_ibge
    else:
        novo["fiscal_ibge"] = None
    for nome, valor in novo.items():
        setattr(usuario, nome, valor)
    return None
