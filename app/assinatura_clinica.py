"""Assinatura digital ICP-Brasil (por médico) das evoluções clínicas — ver
EvolucaoClinica em app/models.py e a conversa sobre CFM 1.821/2007 (Níveis
de Garantia de Segurança). Rumo ao NGS3: uma entrada assinada com o
certificado pessoal do médico (e-CPF) tem validade de documento assinado,
diferente de uma entrada só registrada no NGS2.

AVISO IMPORTANTE sobre o que isto é (e o que não é): esta é uma assinatura
RSA-SHA256 "crua" (PKCS#1 v1.5) sobre um hash do conteúdo canônico da
evolução — criptograficamente uma assinatura digital de verdade, testada
de ponta a ponta com um certificado de teste antes de entrar no código
(assina e depois verifica com a chave pública, confirmando que bate e que
qualquer alteração no conteúdo invalida a assinatura). O que isto NÃO é:
um envelope CAdES-BES/PAdES no formato padronizado que a ICP-Brasil e a
certificação SBIS esperam para reconhecimento formal — isso exigiria
estruturar a assinatura como CMS/PKCS#7 com os atributos assinados
exigidos pela ICP-Brasil (política de assinatura, carimbo de tempo de uma
Autoridade de Carimbo do Tempo etc.). Portanto: já é uma prova
criptográfica real de autoria e integridade (não é decorativo), mas ainda
não é uma assinatura no formato que uma auditoria SBIS formal exigiria
para reconhecimento pleno como NGS3.
"""
import base64
import hashlib
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12

from app.cripto_clinico import descriptografar_bytes, descriptografar_texto


class ErroAssinatura(Exception):
    """Erro esperado (sem certificado configurado, certificado não abre
    etc.) — sempre tratado com mensagem amigável, nunca chega cru ao
    usuário."""


def _carregar_certificado_medico(usuario):
    """Lê o .pfx pessoal do médico (criptografado no banco) e devolve a
    chave privada e o certificado decodificados."""
    pfx_bytes = descriptografar_bytes(usuario.certificado_digital_pfx)
    if not pfx_bytes:
        return None

    senha = descriptografar_texto(usuario.certificado_digital_senha_cripto)
    if senha is None:
        return None

    try:
        chave, certificado, _cadeia = pkcs12.load_key_and_certificates(pfx_bytes, senha.encode("utf-8"))
    except Exception:
        return None

    if chave is None or certificado is None:
        return None

    return chave, certificado


def montar_conteudo_canonico(evolucao_texto, paciente_id, agendamento_id, autor_id, criado_em, sinais_vitais):
    """Monta a representação exata (determinística) do conteúdo que é
    assinado — qualquer mudança em qualquer um destes campos depois de
    assinado invalida a assinatura, o que é o comportamento correto."""
    partes = [
        f"paciente_id={paciente_id}",
        f"agendamento_id={agendamento_id}",
        f"autor_id={autor_id}",
        f"criado_em={criado_em.isoformat()}",
        f"texto={evolucao_texto}",
    ]
    for chave in ("peso_kg", "altura_cm", "pressao_arterial", "frequencia_cardiaca_bpm", "temperatura_celsius"):
        partes.append(f"{chave}={sinais_vitais.get(chave)}")
    return "\n".join(partes).encode("utf-8")


def assinar_conteudo(conteudo_bytes, chave_privada, certificado):
    """Assina o conteúdo com RSA-SHA256/PKCS#1 v1.5 e devolve os dados
    prontos para gravar na EvolucaoClinica. Ver aviso no topo do arquivo
    sobre o que este formato de assinatura é e não é."""
    assinatura = chave_privada.sign(
        conteudo_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    certificado_pem = certificado.public_bytes(serialization.Encoding.PEM).decode("ascii")
    try:
        titular = certificado.subject.rfc4514_string()
    except Exception:
        titular = None

    return {
        "assinatura_base64": base64.b64encode(assinatura).decode("ascii"),
        "assinatura_certificado_titular": titular,
        "assinatura_certificado_serial": str(certificado.serial_number),
        "assinatura_certificado_pem": certificado_pem,
        "assinatura_hash_sha256": hashlib.sha256(conteudo_bytes).hexdigest(),
        "assinado_em": datetime.utcnow(),
    }


def assinar_evolucao_se_possivel(usuario, evolucao_texto, paciente_id, agendamento_id, autor_id, criado_em, sinais_vitais):
    """Tenta assinar a evolução com o certificado pessoal do autor. Devolve
    None (sem levantar erro) se o autor não é médico ou não tem certificado
    configurado — a entrada continua sendo salva normalmente, só sem
    assinatura (nível NGS2 em vez de NGS3). Só levanta ErroAssinatura se
    HÁ um certificado configurado mas ele não pôde ser usado (arquivo
    corrompido, senha não confere mais etc.) — nesse caso é melhor avisar
    o médico do que salvar silenciosamente sem assinar."""
    if usuario.tipo != "medico" or not usuario.certificado_digital_pfx:
        return None

    carregado = _carregar_certificado_medico(usuario)
    if carregado is None:
        raise ErroAssinatura(
            "Você tem um certificado digital cadastrado, mas não foi possível usá-lo para "
            "assinar esta evolução (arquivo ou senha podem estar corrompidos). Reenvie o "
            'certificado em "Meu certificado digital" e tente novamente.'
        )
    chave_privada, certificado = carregado

    conteudo = montar_conteudo_canonico(evolucao_texto, paciente_id, agendamento_id, autor_id, criado_em, sinais_vitais)
    return assinar_conteudo(conteudo, chave_privada, certificado)


def verificar_assinatura(evolucao):
    """Reconfere a assinatura de uma evolução já salva contra o conteúdo
    atual e o certificado público guardado junto com ela — usado nos
    testes automatizados e, futuramente, numa tela de conferência. Devolve
    True/False; nunca lança exceção (uma evolução sem assinatura, ou com
    dado corrompido, simplesmente não confere)."""
    if not evolucao.assinatura_base64 or not evolucao.assinatura_certificado_pem:
        return False
    try:
        certificado_pem = evolucao.assinatura_certificado_pem.encode("ascii")
        from cryptography.x509 import load_pem_x509_certificate
        certificado = load_pem_x509_certificate(certificado_pem)
        chave_publica = certificado.public_key()

        conteudo = montar_conteudo_canonico(
            evolucao.texto,
            evolucao.paciente_id,
            evolucao.agendamento_id,
            evolucao.autor_id,
            evolucao.criado_em,
            {
                "peso_kg": evolucao.peso_kg,
                "altura_cm": evolucao.altura_cm,
                "pressao_arterial": evolucao.pressao_arterial,
                "frequencia_cardiaca_bpm": evolucao.frequencia_cardiaca_bpm,
                "temperatura_celsius": evolucao.temperatura_celsius,
            },
        )
        assinatura = base64.b64decode(evolucao.assinatura_base64)
        chave_publica.verify(assinatura, conteudo, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, Exception):
        return False
