"""Emissão de NFS-e da licença pelo MedIA (pedido do Silvan, 2026-10-27).

O EMITENTE é a empresa dona da plataforma (dados em PlataformaConfig.nfse_*,
tela do dono "Nota fiscal"); o TOMADOR é o médico (dados fiscais em
Usuario.fiscal_*, ou o CPF dele). A nota é de um mês JÁ PAGO
(LicencaPagamento.pago) e é emitida quando o dono clica em "Emitir nota".

Emissão: SEM intermediário (decisão do Silvan: nada de Focus NFe) - o XML da
nota será assinado e enviado direto à NFS-e nacional com o certificado A1 da
empresa, importado em "Nota fiscal" (ver ler_certificado). Essa transmissão
está em STANDBY (a empresa emitente ainda não existe oficialmente): o código
antigo de emissão direta está no histórico do git
(`git show 75b405f6^:app/nfse_nacional.py`) e será reaproveitado. Enquanto
isso só existe o provedor "simulacao" (nota de mentira, para testar a tela)."""
import logging
import re
from datetime import datetime

import requests


logger = logging.getLogger(__name__)

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
    return faltando


def emitir(pagamento, config, agora=None):
    """Envia a nota deste mês pago. Atualiza `pagamento.nf_*` (sem commit).
    Levanta NfseErro se não puder emitir."""
    agora = agora or datetime.utcnow()
    if config.nfse_provedor == "certificado":
        raise NfseErro("A emissão direta com o certificado digital ainda está em desenvolvimento (standby).")
    if config.nfse_provedor != "simulacao":
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
    pagamento.nf_status = "autorizada"
    pagamento.nf_numero = f"SIM-{pagamento.id}"
    pagamento.nf_url = None


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


def ler_certificado(conteudo, senha):
    """Abre um .pfx/.p12 e devolve {"cnpj", "titular", "validade"}, ou levanta
    NfseErro (arquivo inválido / senha errada / sem certificado). O CNPJ vem do
    nome do titular (e-CNPJ ICP-Brasil: "RAZAO SOCIAL:CNPJ")."""
    from cryptography.hazmat.primitives.serialization import pkcs12

    try:
        chave, certificado, _cadeia = pkcs12.load_key_and_certificates(conteudo, (senha or "").encode("utf-8"))
    except Exception:
        raise NfseErro("Não foi possível abrir o certificado - confira se é um arquivo .pfx/.p12 e se a senha está correta.")
    if certificado is None or chave is None:
        raise NfseErro("O arquivo não contém um certificado com chave privada.")
    titular = certificado.subject.rfc4514_string()
    achados = re.findall(r"\d{14}", titular)
    fim = certificado.not_valid_after_utc if hasattr(certificado, "not_valid_after_utc") else certificado.not_valid_after
    cn = next((p.value for p in certificado.subject if p.oid.dotted_string == "2.5.4.3"), titular)
    return {"cnpj": achados[0] if achados else None, "titular": str(cn)[:200], "validade": fim.date()}


def certificado_situacao(config, hoje=None):
    """("sem", "vencido", "vencendo" (menos de 30 dias) ou "ok")."""
    from datetime import date

    if not config.nfse_cert_pfx or not config.nfse_cert_validade:
        return "sem"
    dias = (config.nfse_cert_validade - (hoje or date.today())).days
    return "vencido" if dias < 0 else ("vencendo" if dias < 30 else "ok")
